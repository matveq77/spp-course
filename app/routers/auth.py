import random

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import Principal, get_principal
from ..models import User
from ..schemas import LoginIn, RefreshIn, RegisterIn, TwoFactorLoginIn
from ..security import (
    consume_refresh_token, create_2fa_challenge, create_access_token, create_refresh_token,
    decode_2fa_challenge, hash_password, register_2fa_attempt, revoke_refresh_token, verify_password,
)
from ..serializers import user_full
from ..twofactor import check_second_factor

router = APIRouter(prefix="/api/auth", tags=["auth"])

COLORS = ["#6366f1", "#0ea5e9", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6", "#ec4899", "#14b8a6"]


def client_info(request: Request) -> dict:
    return {
        "user_agent": request.headers.get("user-agent"),
        "ip": request.client.host if request.client else None,
    }


def issue_tokens(db: Session, user: User, request: Request, *, session_id: str | None = None,
                 started_at=None) -> dict:
    refresh, sid = create_refresh_token(db, user, session_id=session_id, started_at=started_at,
                                        **client_info(request))
    db.commit()
    return {
        "access_token": create_access_token(db, user, sid),
        "refresh_token": refresh,
        "token_type": "bearer",
        "user": user_full(user),
    }


@router.post("/register")
def register(body: RegisterIn, request: Request, db: Session = Depends(get_db)):
    exists = db.scalar(select(User).where(
        or_(func.lower(User.username) == body.username.lower(), func.lower(User.email) == body.email.lower())
    ))
    if exists:
        raise HTTPException(409, "Пользователь с таким логином или email уже существует")
    is_first = db.scalar(select(func.count(User.id))) == 0
    user = User(
        username=body.username, email=body.email.lower(), full_name=body.full_name,
        password_hash=hash_password(body.password),
        system_role="admin" if is_first else "user",
        color=random.choice(COLORS),
    )
    db.add(user)
    db.flush()
    return issue_tokens(db, user, request)


@router.post("/login")
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(
        or_(func.lower(User.username) == body.username.lower(), func.lower(User.email) == body.username.lower())
    ))
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Неверный логин или пароль")
    if not user.is_active:
        raise HTTPException(403, "Учётная запись заблокирована")
    if user.totp_enabled:
        # пароль верный, но токены выдадим только после кода из приложения-аутентификатора
        return {"two_factor_required": True, "challenge_token": create_2fa_challenge(user)}
    return issue_tokens(db, user, request)


@router.post("/login/2fa")
def login_2fa(body: TwoFactorLoginIn, request: Request, db: Session = Depends(get_db)):
    try:
        payload = decode_2fa_challenge(body.challenge_token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Время на ввод кода истекло, войдите заново")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Неверный запрос входа")
    if not register_2fa_attempt(payload["jti"]):
        raise HTTPException(429, "Слишком много попыток, войдите заново")
    user = db.get(User, int(payload["sub"]))
    if not user or not user.is_active or not user.totp_enabled:
        raise HTTPException(401, "Неверный запрос входа")
    if not check_second_factor(db, user, body.code):
        db.rollback()
        raise HTTPException(400, "Неверный код")
    return issue_tokens(db, user, request)


@router.post("/refresh")
def refresh(body: RefreshIn, request: Request, db: Session = Depends(get_db)):
    """Выдаёт новый access-токен с АКТУАЛЬНЫМИ правами (и новый refresh — ротация в той же сессии)."""
    found = consume_refresh_token(db, body.refresh_token)
    if not found:
        db.rollback()
        raise HTTPException(401, "refresh_invalid")
    user, old = found
    return issue_tokens(db, user, request, session_id=old.session_id, started_at=old.session_started_at)


@router.post("/logout")
def logout(body: RefreshIn, db: Session = Depends(get_db)):
    revoke_refresh_token(db, body.refresh_token)
    db.commit()
    return {"ok": True}


@router.get("/token-info")
def token_info(me: Principal = Depends(get_principal)):
    """Что сервер 'видит' в токене — удобно для демонстрации."""
    return {
        "user_id": me.id, "username": me.username, "system_role": me.role, "sys_perms": me.sys,
        "teams": me.teams, "projects": me.projects, "session_id": me.sid,
    }
