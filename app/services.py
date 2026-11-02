"""Общие сервисные функции: журнал активности, уведомления, события после commit."""
from sqlalchemy import event as sa_event, select
from sqlalchemy.orm import Session

from . import config
from .db import SessionLocal
from .events import broker
from .models import Activity, Attachment, Board, Card, Column, Notification, ProjectMember, Sprint, TeamMember, User
from .security import bump_perm_version
from .serializers import activity_out, notification_out


# ---------- события публикуются только после успешного commit ----------

def queue_event(db: Session, type_: str, data: dict | None = None, **target):
    db.info.setdefault("pending_events", []).append((type_, data, target))


@sa_event.listens_for(SessionLocal, "after_commit")
def _flush_events(session):
    events = session.info.pop("pending_events", [])
    for type_, data, target in events:
        broker.publish(type_, data, **target)
    # файлы удалённых вложений стираем с диска только после успешного commit
    for name in session.info.pop("files_to_delete", []):
        (config.UPLOAD_DIR / name).unlink(missing_ok=True)


@sa_event.listens_for(SessionLocal, "after_rollback")
def _drop_events(session):
    session.info.pop("pending_events", None)
    session.info.pop("files_to_delete", None)
    session.info.pop("sprint_boards", None)
    session.info.pop("sprint_projects", None)


# ---------- burndown: снимок активного спринта перед каждым commit с изменениями карточек ----------

@sa_event.listens_for(SessionLocal, "after_flush")
def _track_sprint_changes(session, _ctx):
    boards = session.info.setdefault("sprint_boards", set())
    projects = session.info.setdefault("sprint_projects", set())
    for obj in (*session.new, *session.dirty, *session.deleted):
        if isinstance(obj, (Card, Column)):
            boards.add(obj.board_id)
        elif isinstance(obj, Board):  # удалили целую доску — её доски уже нет в БД, запоминаем проект
            projects.add(obj.project_id)


@sa_event.listens_for(SessionLocal, "before_commit")
def _snapshot_active_sprints(session):
    session.flush()  # изменения, ещё не отправленные в БД, тоже должны попасть в снимок
    boards = session.info.pop("sprint_boards", None) or set()
    project_ids = session.info.pop("sprint_projects", None) or set()
    if boards:
        project_ids |= set(session.scalars(select(Board.project_id).where(Board.id.in_(boards))))
    if not project_ids:
        return
    from .sprints import record_snapshot
    for sprint in session.scalars(select(Sprint).where(Sprint.project_id.in_(project_ids), Sprint.status == "active")):
        record_snapshot(session, sprint)
    session.flush()
    session.info.pop("sprint_boards", None)
    session.info.pop("sprint_projects", None)


@sa_event.listens_for(SessionLocal, "persistent_to_deleted")
def _attachment_deleted(session, instance):
    # срабатывает и при каскадном удалении (карточка, доска, проект, команда)
    if isinstance(instance, Attachment):
        session.info.setdefault("files_to_delete", []).append(instance.stored_name)


# ---------- активность / уведомления ----------

def log_activity(db: Session, *, project_id: int, user_id: int, action: str, message: str,
                 board_id: int | None = None, card_id: int | None = None):
    a = Activity(project_id=project_id, user_id=user_id, action=action, message=message,
                 board_id=board_id, card_id=card_id)
    db.add(a)
    db.flush()
    db.refresh(a)
    queue_event(db, "activity", activity_out(a), project_id=project_id, actor_id=user_id)


def notify(db: Session, user_ids, text: str, link: dict | None = None, exclude: int | None = None):
    for uid in set(user_ids):
        if uid == exclude:
            continue
        n = Notification(user_id=uid, text=text, link=link)
        db.add(n)
        db.flush()
        queue_event(db, "notification", notification_out(n), user_ids=[uid])


# ---------- права ----------

def refresh_permissions(db: Session, user_ids):
    """Права пользователей изменились: инвалидируем их токены и просим клиентов обновить JWT."""
    ids = bump_perm_version(db, user_ids)
    if ids:
        queue_event(db, "auth.refresh", {}, user_ids=ids)


def team_user_ids(db: Session, team_id: int) -> list[int]:
    return list(db.scalars(select(TeamMember.user_id).where(TeamMember.team_id == team_id)))


def project_explicit_user_ids(db: Session, project_id: int) -> list[int]:
    return list(db.scalars(select(ProjectMember.user_id).where(ProjectMember.project_id == project_id)))


def display_name(u: User) -> str:
    return u.full_name or u.username
