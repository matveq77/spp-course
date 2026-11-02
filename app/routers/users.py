from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import Principal, get_principal
from ..models import (
    Board, Card, CardAssignee, Column, Notification, Project, Team, User,
)
from ..schemas import AdminUserUpdate, PasswordChange, ProfileUpdate
from ..security import hash_password, revoke_all_refresh_tokens, verify_password
from ..serializers import card_brief, notification_out, user_brief, user_full
from ..services import refresh_permissions
from ..twofactor import disable_2fa

router = APIRouter(prefix="/api", tags=["users"])


@router.get("/me")
def get_me(me: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    return user_full(db.get(User, me.id))


@router.patch("/me")
def update_me(body: ProfileUpdate, me: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    user = db.get(User, me.id)
    if body.email and body.email.lower() != user.email:
        if db.scalar(select(User).where(func.lower(User.email) == body.email.lower())):
            raise HTTPException(409, "Email уже занят")
        user.email = body.email.lower()
    if body.full_name is not None:
        user.full_name = body.full_name
    if body.color:
        user.color = body.color
    db.commit()
    return user_full(user)


@router.post("/me/password")
def change_password(body: PasswordChange, me: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    user = db.get(User, me.id)
    if not verify_password(body.old_password, user.password_hash):
        raise HTTPException(400, "Неверный текущий пароль")
    user.password_hash = hash_password(body.new_password)
    # смена пароля завершает все остальные сессии
    revoked = revoke_all_refresh_tokens(db, me.id, except_session=me.sid)
    refresh_permissions(db, [me.id])
    db.commit()
    return {"ok": True, "sessions_revoked": revoked}


@router.get("/me/cards")
def my_cards(me: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    rows = db.execute(
        select(Card, Column, Board, Project)
        .join(CardAssignee, CardAssignee.card_id == Card.id)
        .join(Column, Column.id == Card.column_id)
        .join(Board, Board.id == Card.board_id)
        .join(Project, Project.id == Board.project_id)
        .where(CardAssignee.user_id == me.id, Card.is_archived.is_(False),
               Board.is_archived.is_(False), Project.is_archived.is_(False))
        .order_by(Card.due_date.is_(None), Card.due_date, Card.id)
    ).all()
    result = []
    for card, col, board, project in rows:
        if not me.can_project(project.id, "project:read"):
            continue
        result.append({
            **card_brief(card, project.key), "column_name": col.name, "column_done": col.is_done,
            "board_name": board.name, "project_id": project.id, "project_name": project.name,
        })
    return result


# ---------- уведомления ----------

@router.get("/me/notifications")
def notifications(me: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    items = db.scalars(
        select(Notification).where(Notification.user_id == me.id)
        .order_by(Notification.created_at.desc()).limit(50)
    ).all()
    unread = db.scalar(select(func.count(Notification.id)).where(
        Notification.user_id == me.id, Notification.is_read.is_(False)))
    return {"items": [notification_out(n) for n in items], "unread": unread}


@router.post("/me/notifications/read-all")
def read_all(me: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    db.query(Notification).filter_by(user_id=me.id, is_read=False).update({"is_read": True})
    db.commit()
    return {"ok": True}


@router.post("/me/notifications/{nid}/read")
def read_one(nid: int, me: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    n = db.get(Notification, nid)
    if not n or n.user_id != me.id:
        raise HTTPException(404, "Не найдено")
    n.is_read = True
    db.commit()
    return {"ok": True}


# ---------- поиск пользователей (для приглашения) ----------

@router.get("/users/search")
def search_users(q: str = "", me: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    q = q.strip().lower()
    if len(q) < 1:
        return []
    like = f"%{q}%"
    users = db.scalars(
        select(User).where(User.is_active.is_(True), or_(
            func.lower(User.username).like(like), func.lower(User.full_name).like(like),
            func.lower(User.email).like(like),
        )).limit(10)
    ).all()
    return [user_brief(u) for u in users]


# ---------- администрирование ----------

@router.get("/admin/users")
def admin_users(me: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    me.require_admin()
    return [user_full(u) for u in db.scalars(select(User).order_by(User.id))]


@router.patch("/admin/users/{uid}")
def admin_update_user(uid: int, body: AdminUserUpdate, me: Principal = Depends(get_principal),
                      db: Session = Depends(get_db)):
    me.require_admin()
    user = db.get(User, uid)
    if not user:
        raise HTTPException(404, "Пользователь не найден")
    if uid == me.id and (body.system_role == "user" or body.is_active is False):
        raise HTTPException(400, "Нельзя понизить или заблокировать самого себя")
    if body.system_role is not None:
        user.system_role = body.system_role
    if body.is_active is not None:
        user.is_active = body.is_active
        if not body.is_active:
            revoke_all_refresh_tokens(db, uid)
    if body.reset_2fa:  # пользователь потерял телефон и резервные коды
        disable_2fa(db, user)
    refresh_permissions(db, [uid])
    db.commit()
    return user_full(user)


@router.get("/admin/stats")
def admin_stats(me: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    me.require_admin()
    from ..events import broker
    return {
        "users": db.scalar(select(func.count(User.id))),
        "teams": db.scalar(select(func.count(Team.id))),
        "projects": db.scalar(select(func.count(Project.id))),
        "boards": db.scalar(select(func.count(Board.id))),
        "cards": db.scalar(select(func.count(Card.id))),
        "online_connections": len(broker.subs),
        "online_users": len({s.user_id for s in broker.subs.values()}),
    }
