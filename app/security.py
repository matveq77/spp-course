import hashlib
import hmac
import secrets
import threading
from datetime import datetime, timedelta, timezone

import jwt
from sqlalchemy.orm import Session

from . import config
from .models import RefreshToken, User, utcnow
from .permissions import build_claims

# ---------- пароли (PBKDF2-SHA256, стандартная библиотека) ----------

_PBKDF2_ITER = 200_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _PBKDF2_ITER)
    return f"pbkdf2_sha256${_PBKDF2_ITER}${salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iters, salt, digest = stored.split("$")
    except ValueError:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iters))
    return hmac.compare_digest(dk.hex(), digest)


# ---------- версия прав ----------
# Кэш user_id -> perm_version. Позволяет мгновенно "отозвать" токены со старыми правами
# без похода в БД на каждый запрос.

_pv_cache: dict[int, int] = {}
_pv_lock = threading.Lock()


def get_perm_version(db: Session, user_id: int) -> int | None:
    with _pv_lock:
        if user_id in _pv_cache:
            return _pv_cache[user_id]
    user = db.get(User, user_id)
    if not user or not user.is_active:
        return None
    with _pv_lock:
        _pv_cache[user_id] = user.perm_version
    return user.perm_version


def bump_perm_version(db: Session, user_ids) -> list[int]:
    """Инвалидирует access-токены пользователей. Вызывать перед commit."""
    ids = list({uid for uid in user_ids if uid})
    for uid in ids:
        user = db.get(User, uid)
        if user:
            user.perm_version += 1
    with _pv_lock:
        for uid in ids:
            _pv_cache.pop(uid, None)
    return ids


def reset_perm_cache():
    with _pv_lock:
        _pv_cache.clear()


# ---------- JWT ----------

def create_access_token(db: Session, user: User, sid: str | None = None) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id),
        "usr": user.username,
        "name": user.full_name or user.username,
        "pv": user.perm_version,
        "sid": sid,  # id сессии (для списка «Активные сессии»)
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=config.ACCESS_TOKEN_TTL_MIN),
        **build_claims(db, user),
    }
    return jwt.encode(payload, config.JWT_SECRET, algorithm=config.JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    payload = jwt.decode(token, config.JWT_SECRET, algorithms=[config.JWT_ALGORITHM])
    if payload.get("type") != "access":
        raise jwt.InvalidTokenError("wrong token type")
    return payload


def _hash_refresh(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_refresh_token(db: Session, user: User, *, session_id: str | None = None,
                         started_at: datetime | None = None, user_agent: str | None = None,
                         ip: str | None = None) -> tuple[str, str]:
    """Возвращает (refresh_token, session_id). Без session_id начинается новая сессия."""
    token = secrets.token_urlsafe(48)
    session_id = session_id or secrets.token_hex(12)
    db.add(RefreshToken(
        user_id=user.id,
        token_hash=_hash_refresh(token),
        expires_at=utcnow() + timedelta(days=config.REFRESH_TOKEN_TTL_DAYS),
        session_id=session_id,
        session_started_at=started_at or utcnow(),
        user_agent=(user_agent or "")[:300] or None,
        ip=ip,
    ))
    return token, session_id


def consume_refresh_token(db: Session, token: str) -> tuple[User, RefreshToken] | None:
    """Ротация: старый refresh-токен отзывается, возвращается пользователь и его сессия."""
    rt = db.query(RefreshToken).filter_by(token_hash=_hash_refresh(token)).first()
    if not rt or rt.revoked or rt.expires_at < utcnow():
        return None
    rt.revoked = True
    user = db.get(User, rt.user_id)
    if not user or not user.is_active:
        return None
    return user, rt


def revoke_refresh_token(db: Session, token: str):
    rt = db.query(RefreshToken).filter_by(token_hash=_hash_refresh(token)).first()
    if rt:
        rt.revoked = True


def revoke_all_refresh_tokens(db: Session, user_id: int, except_session: str | None = None):
    q = db.query(RefreshToken).filter_by(user_id=user_id, revoked=False)
    if except_session:
        q = q.filter(RefreshToken.session_id != except_session)
    return q.update({"revoked": True}, synchronize_session=False)


# ---------- второй шаг входа (2FA) ----------
# После верного пароля выдаётся короткоживущий challenge-токен; токены доступа — только после кода.

_challenge_attempts: dict[str, int] = {}


def create_2fa_challenge(user: User) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id), "type": "2fa", "jti": secrets.token_hex(8), "iat": now,
        "exp": now + timedelta(minutes=config.TWO_FACTOR_CHALLENGE_TTL_MIN),
    }
    return jwt.encode(payload, config.JWT_SECRET, algorithm=config.JWT_ALGORITHM)


def decode_2fa_challenge(token: str) -> dict:
    payload = jwt.decode(token, config.JWT_SECRET, algorithms=[config.JWT_ALGORITHM])
    if payload.get("type") != "2fa":
        raise jwt.InvalidTokenError("wrong token type")
    return payload


def register_2fa_attempt(jti: str) -> bool:
    """Считает попытки ввода кода. False — лимит исчерпан."""
    with _pv_lock:
        n = _challenge_attempts.get(jti, 0) + 1
        _challenge_attempts[jti] = n
        if len(_challenge_attempts) > 10_000:  # не даём словарю расти бесконечно
            _challenge_attempts.clear()
    return n <= config.TWO_FACTOR_MAX_ATTEMPTS
