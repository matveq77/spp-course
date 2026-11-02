"""Безопасность аккаунта: двухфакторная аутентификация и активные сессии."""
import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import totp
from ..db import get_db
from ..deps import Principal, get_principal
from ..models import RefreshToken, User, utcnow
from ..schemas import TwoFactorCodeIn, TwoFactorDisableIn
from ..security import revoke_all_refresh_tokens, verify_password
from ..services import refresh_permissions
from ..twofactor import backup_codes_left, check_second_factor, disable_2fa, replace_backup_codes

router = APIRouter(prefix="/api/me", tags=["account"])


# ===================== 2FA =====================

@router.get("/2fa")
def twofa_status(me: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    user = db.get(User, me.id)
    return {"enabled": user.totp_enabled, "backup_codes_left": backup_codes_left(db, me.id) if user.totp_enabled else 0}


@router.post("/2fa/setup")
def twofa_setup(me: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Шаг 1: генерируем секрет и QR. 2FA ещё не включена, пока пользователь не подтвердит код."""
    user = db.get(User, me.id)
    if user.totp_enabled:
        raise HTTPException(409, "2FA уже включена")
    user.totp_secret = totp.generate_secret()
    user.totp_last_step = None
    db.commit()
    uri = totp.provisioning_uri(user.totp_secret, user.username)
    return {"secret": user.totp_secret, "otpauth_uri": uri, "qr": totp.qr_data_uri(uri)}


@router.post("/2fa/enable")
def twofa_enable(body: TwoFactorCodeIn, me: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Шаг 2: проверяем первый код из приложения и включаем 2FA. Резервные коды показываются один раз."""
    user = db.get(User, me.id)
    if user.totp_enabled:
        raise HTTPException(409, "2FA уже включена")
    if not user.totp_secret:
        raise HTTPException(400, "Сначала начните настройку")
    step = totp.verify(user.totp_secret, body.code, None)
    if step is None:
        raise HTTPException(400, "Неверный код. Проверьте время на телефоне и попробуйте ещё раз")
    user.totp_last_step = step
    user.totp_enabled = True
    codes = replace_backup_codes(db, user)
    db.commit()
    return {"enabled": True, "backup_codes": codes}


@router.post("/2fa/disable")
def twofa_disable(body: TwoFactorDisableIn, me: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    user = db.get(User, me.id)
    if not user.totp_enabled:
        raise HTTPException(400, "2FA не включена")
    if not verify_password(body.password, user.password_hash):
        raise HTTPException(400, "Неверный пароль")
    if not check_second_factor(db, user, body.code):
        db.rollback()
        raise HTTPException(400, "Неверный код")
    disable_2fa(db, user)
    db.commit()
    return {"enabled": False}


@router.post("/2fa/backup-codes")
def twofa_new_backup_codes(body: TwoFactorCodeIn, me: Principal = Depends(get_principal),
                           db: Session = Depends(get_db)):
    user = db.get(User, me.id)
    if not user.totp_enabled:
        raise HTTPException(400, "2FA не включена")
    if not check_second_factor(db, user, body.code):
        db.rollback()
        raise HTTPException(400, "Неверный код")
    codes = replace_backup_codes(db, user)
    db.commit()
    return {"backup_codes": codes}


# ===================== СЕССИИ =====================

def describe_ua(ua: str | None) -> dict:
    if not ua:
        return {"browser": "Неизвестное устройство", "os": "нет данных", "mobile": False}
    browser = "Неизвестный браузер"
    for name, pattern in [("Edge", r"Edg/"), ("Opera", r"OPR/|Opera"), ("Яндекс Браузер", r"YaBrowser"),
                          ("Firefox", r"Firefox/"), ("Chrome", r"Chrome/"), ("Safari", r"Safari/"),
                          ("python-httpx", r"python-httpx|testclient")]:
        if re.search(pattern, ua):
            browser = name
            break
    os_name = "Неизвестная ОС"
    for name, pattern in [("Android", r"Android"), ("iOS", r"iPhone|iPad"), ("Windows", r"Windows"),
                          ("macOS", r"Mac OS X|Macintosh"), ("Linux", r"Linux")]:
        if re.search(pattern, ua):
            os_name = name
            break
    mobile = bool(re.search(r"Mobile|Android|iPhone", ua))
    return {"browser": browser, "os": os_name, "mobile": mobile}


def _active_sessions(db: Session, user_id: int) -> list[RefreshToken]:
    return list(db.scalars(
        select(RefreshToken).where(RefreshToken.user_id == user_id, RefreshToken.revoked.is_(False),
                                   RefreshToken.expires_at > utcnow())
        .order_by(RefreshToken.created_at.desc())
    ))


@router.get("/sessions")
def list_sessions(me: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    result, seen = [], set()
    for rt in _active_sessions(db, me.id):
        if rt.session_id in seen:
            continue
        seen.add(rt.session_id)
        result.append({
            "id": rt.session_id, **describe_ua(rt.user_agent), "user_agent": rt.user_agent, "ip": rt.ip,
            "started_at": rt.session_started_at or rt.created_at,
            "last_active_at": rt.created_at,  # refresh-токен перевыпускается при активности
            "current": rt.session_id == me.sid,
        })
    result.sort(key=lambda s: not s["current"])
    return result


def _kill_access_tokens(db: Session, me: Principal):
    """Отзывает все access-токены пользователя через perm_version.
    Текущая вкладка тихо получит новый токен (её refresh жив), остальные — вылетят на экран входа."""
    refresh_permissions(db, [me.id])


@router.delete("/sessions/{session_id}")
def revoke_session(session_id: str, me: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    if session_id == me.sid:
        raise HTTPException(400, "Чтобы завершить текущую сессию, нажмите «Выйти»")
    n = db.query(RefreshToken).filter_by(user_id=me.id, session_id=session_id, revoked=False) \
        .update({"revoked": True}, synchronize_session=False)
    if not n:
        raise HTTPException(404, "Сессия не найдена")
    _kill_access_tokens(db, me)
    db.commit()
    return {"ok": True}


@router.post("/sessions/revoke-others")
def revoke_other_sessions(me: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    n = revoke_all_refresh_tokens(db, me.id, except_session=me.sid)
    _kill_access_tokens(db, me)
    db.commit()
    return {"revoked": n}


@router.post("/sessions/revoke-all")
def revoke_all_sessions(me: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Выход на всех устройствах, включая текущее."""
    n = revoke_all_refresh_tokens(db, me.id)
    _kill_access_tokens(db, me)
    db.commit()
    return {"revoked": n}
