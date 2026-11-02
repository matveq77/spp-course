from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import Principal, get_principal
from ..models import (
    Activity, Board, Card, CardAssignee, Column, Label, Project, ProjectMember, Team, TeamMember, User,
    utcnow,
)
from ..permissions import P, PROJECT_ROLES, effective_project_role
from ..schemas import BoardIn, LabelIn, MemberAdd, MemberRole, ProjectIn, ProjectUpdate
from ..serializers import activity_out, board_brief, label_out, project_out, user_brief
from ..services import (
    display_name, log_activity, notify, project_explicit_user_ids, queue_event, refresh_permissions,
    team_user_ids,
)

router = APIRouter(prefix="/api", tags=["projects"])

TEMPLATES = {
    "basic": [("К выполнению", None, False), ("В работе", 3, False), ("Готово", None, True)],
    "scrum": [("Бэклог", None, False), ("К выполнению", None, False), ("В работе", 4, False),
              ("На ревью", 2, False), ("Тестирование", None, False), ("Готово", None, True)],
    "empty": [],
}
DEFAULT_LABELS = [("Баг", "#ef4444"), ("Фича", "#22c55e"), ("Улучшение", "#3b82f6"),
                  ("Документация", "#a855f7"), ("Срочно", "#f97316")]


def get_project(db: Session, project_id: int) -> Project:
    p = db.get(Project, project_id)
    if not p:
        raise HTTPException(404, "Проект не найден")
    return p


def my_project_role(me: Principal, project_id: int) -> str | None:
    if me.is_admin and str(project_id) not in me.projects:
        return "manager"
    return (me.projects.get(str(project_id)) or {}).get("role")


def create_columns(db: Session, board: Board, template: str):
    for i, (name, wip, done) in enumerate(TEMPLATES[template]):
        db.add(Column(board_id=board.id, name=name, position=i, wip_limit=wip, is_done=done))


def affected_users(db: Session, project: Project) -> list[int]:
    """Все, чьи права на проект могут поменяться."""
    return team_user_ids(db, project.team_id) + project_explicit_user_ids(db, project.id)


# ---------- проекты ----------

@router.get("/projects")
def list_projects(me: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    q = select(Project).order_by(Project.is_archived, Project.name)
    if not me.is_admin:
        q = q.where(Project.id.in_(me.project_ids()))
    return [project_out(p, my_project_role(me, p.id)) for p in db.scalars(q)]


@router.post("/teams/{team_id}/projects")
def create_project(team_id: int, body: ProjectIn, me: Principal = Depends(get_principal),
                   db: Session = Depends(get_db)):
    me.require_team(team_id, P.PROJECT_CREATE)
    if not db.get(Team, team_id):
        raise HTTPException(404, "Команда не найдена")
    p = Project(team_id=team_id, name=body.name, key=body.key, description=body.description,
                visibility=body.visibility)
    db.add(p)
    db.flush()
    # создатель — явный менеджер (важно, если его позже понизят в команде)
    db.add(ProjectMember(project_id=p.id, user_id=me.id, role="manager"))
    for name, color in DEFAULT_LABELS:
        db.add(Label(project_id=p.id, name=name, color=color))
    board = Board(project_id=p.id, name="Основная доска", position=0)
    db.add(board)
    db.flush()
    create_columns(db, board, body.template)
    log_activity(db, project_id=p.id, user_id=me.id, action="project.created",
                 message=f"создал(а) проект «{p.name}»")
    refresh_permissions(db, team_user_ids(db, team_id) + [me.id])
    queue_event(db, "team.updated", {"team_id": team_id}, team_id=team_id, actor_id=me.id)
    db.commit()
    db.refresh(p)
    return project_out(p, "manager")


@router.get("/projects/{project_id}")
def project_detail(project_id: int, me: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    me.require_project(project_id, P.PROJECT_READ)
    p = get_project(db, project_id)
    role = my_project_role(me, project_id)
    boards = sorted(p.boards, key=lambda b: (b.is_archived, b.position, b.id))
    return {
        **project_out(p, role),
        "my_perms": PROJECT_ROLES.get(role, []) if role else [],
        "boards": [board_brief(b) for b in boards],
        "labels": [label_out(l) for l in p.labels],
        "members": project_members(project_id, me, db),
    }


@router.patch("/projects/{project_id}")
def update_project(project_id: int, body: ProjectUpdate, me: Principal = Depends(get_principal),
                   db: Session = Depends(get_db)):
    me.require_project(project_id, P.PROJECT_UPDATE)
    p = get_project(db, project_id)
    changes = body.model_dump(exclude_unset=True)
    visibility_changed = "visibility" in changes and changes["visibility"] != p.visibility
    for k, v in changes.items():
        setattr(p, k, v)
    if visibility_changed:
        refresh_permissions(db, team_user_ids(db, p.team_id))
    if "is_archived" in changes:
        log_activity(db, project_id=p.id, user_id=me.id, action="project.archived",
                     message="архивировал(а) проект" if p.is_archived else "восстановил(а) проект из архива")
    queue_event(db, "project.updated", {"project_id": p.id}, project_id=p.id, actor_id=me.id)
    queue_event(db, "team.updated", {"team_id": p.team_id}, team_id=p.team_id, actor_id=me.id)
    db.commit()
    return project_out(p, my_project_role(me, project_id))


@router.delete("/projects/{project_id}")
def delete_project(project_id: int, me: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    me.require_project(project_id, P.PROJECT_DELETE)
    p = get_project(db, project_id)
    users = affected_users(db, p)
    team_id = p.team_id
    queue_event(db, "project.deleted", {"project_id": p.id}, project_id=p.id, actor_id=me.id)
    queue_event(db, "team.updated", {"team_id": team_id}, team_id=team_id, actor_id=me.id)
    db.delete(p)
    refresh_permissions(db, users)
    db.commit()
    return {"ok": True}


# ---------- участники проекта ----------

@router.get("/projects/{project_id}/members")
def project_members(project_id: int, me: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    me.require_project(project_id, P.PROJECT_READ)
    p = get_project(db, project_id)
    team_roles = {tm.user_id: tm.role for tm in db.scalars(select(TeamMember).where(TeamMember.team_id == p.team_id))}
    explicit = {pm.user_id: pm.role for pm in p.members}
    ids = set(team_roles) | set(explicit)
    users = {u.id: u for u in db.scalars(select(User).where(User.id.in_(ids)))}
    result = []
    for uid in ids:
        role = effective_project_role(team_roles.get(uid), explicit.get(uid), p.visibility)
        if not role:
            continue
        result.append({
            **user_brief(users[uid]), "role": role, "explicit_role": explicit.get(uid),
            "team_role": team_roles.get(uid),
        })
    order = {"manager": 0, "member": 1, "viewer": 2}
    return sorted(result, key=lambda m: (order[m["role"]], m["username"]))


def _check_project_role(role: str):
    if role not in PROJECT_ROLES:
        raise HTTPException(422, f"Недопустимая роль: {role}")


@router.post("/projects/{project_id}/members")
def add_project_member(project_id: int, body: MemberAdd, me: Principal = Depends(get_principal),
                       db: Session = Depends(get_db)):
    me.require_project(project_id, P.PROJECT_MEMBERS)
    _check_project_role(body.role)
    p = get_project(db, project_id)
    user = db.scalar(select(User).where(func.lower(User.username) == body.username.lower()))
    if not user:
        raise HTTPException(404, "Пользователь не найден")
    pm = db.scalar(select(ProjectMember).where(ProjectMember.project_id == project_id,
                                               ProjectMember.user_id == user.id))
    if pm:
        pm.role = body.role
    else:
        db.add(ProjectMember(project_id=project_id, user_id=user.id, role=body.role))
    log_activity(db, project_id=p.id, user_id=me.id, action="member.added",
                 message=f"добавил(а) {display_name(user)} в проект как {body.role}")
    notify(db, [user.id], f"{me.name} добавил(а) вас в проект «{p.name}»", {"project_id": p.id}, exclude=me.id)
    refresh_permissions(db, [user.id])
    queue_event(db, "project.members", {"project_id": p.id}, project_id=p.id, actor_id=me.id)
    db.commit()
    return {"ok": True}


@router.patch("/projects/{project_id}/members/{user_id}")
def change_project_member(project_id: int, user_id: int, body: MemberRole,
                          me: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    me.require_project(project_id, P.PROJECT_MEMBERS)
    _check_project_role(body.role)
    p = get_project(db, project_id)
    pm = db.scalar(select(ProjectMember).where(ProjectMember.project_id == project_id,
                                               ProjectMember.user_id == user_id))
    if pm:
        pm.role = body.role
    else:
        db.add(ProjectMember(project_id=project_id, user_id=user_id, role=body.role))
    user = db.get(User, user_id)
    log_activity(db, project_id=p.id, user_id=me.id, action="member.role",
                 message=f"изменил(а) роль {display_name(user)} на {body.role}")
    refresh_permissions(db, [user_id])
    queue_event(db, "project.members", {"project_id": p.id}, project_id=p.id, actor_id=me.id)
    db.commit()
    return {"ok": True}


@router.delete("/projects/{project_id}/members/{user_id}")
def remove_project_member(project_id: int, user_id: int, me: Principal = Depends(get_principal),
                          db: Session = Depends(get_db)):
    me.require_project(project_id, P.PROJECT_MEMBERS)
    p = get_project(db, project_id)
    pm = db.scalar(select(ProjectMember).where(ProjectMember.project_id == project_id,
                                               ProjectMember.user_id == user_id))
    if not pm:
        raise HTTPException(400, "У пользователя нет явной роли в проекте (доступ идёт от роли в команде)")
    user = db.get(User, user_id)
    log_activity(db, project_id=p.id, user_id=me.id, action="member.removed",
                 message=f"убрал(а) явную роль у {display_name(user)}")
    queue_event(db, "project.members", {"project_id": p.id}, project_id=p.id, actor_id=me.id)
    db.delete(pm)
    refresh_permissions(db, [user_id])
    db.commit()
    return {"ok": True}


# ---------- метки ----------

@router.post("/projects/{project_id}/labels")
def create_label(project_id: int, body: LabelIn, me: Principal = Depends(get_principal),
                 db: Session = Depends(get_db)):
    me.require_project(project_id, P.LABEL_MANAGE)
    get_project(db, project_id)
    label = Label(project_id=project_id, name=body.name, color=body.color)
    db.add(label)
    db.flush()
    queue_event(db, "project.labels", {"project_id": project_id}, project_id=project_id, actor_id=me.id)
    db.commit()
    return label_out(label)


@router.patch("/labels/{label_id}")
def update_label(label_id: int, body: LabelIn, me: Principal = Depends(get_principal),
                 db: Session = Depends(get_db)):
    label = db.get(Label, label_id)
    if not label:
        raise HTTPException(404, "Метка не найдена")
    me.require_project(label.project_id, P.LABEL_MANAGE)
    label.name, label.color = body.name, body.color
    queue_event(db, "project.labels", {"project_id": label.project_id}, project_id=label.project_id,
                actor_id=me.id)
    db.commit()
    return label_out(label)


@router.delete("/labels/{label_id}")
def delete_label(label_id: int, me: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    label = db.get(Label, label_id)
    if not label:
        raise HTTPException(404, "Метка не найдена")
    me.require_project(label.project_id, P.LABEL_MANAGE)
    queue_event(db, "project.labels", {"project_id": label.project_id}, project_id=label.project_id,
                actor_id=me.id)
    db.delete(label)
    db.commit()
    return {"ok": True}


# ---------- доски проекта ----------

@router.post("/projects/{project_id}/boards")
def create_board(project_id: int, body: BoardIn, me: Principal = Depends(get_principal),
                 db: Session = Depends(get_db)):
    me.require_project(project_id, P.BOARD_MANAGE)
    p = get_project(db, project_id)
    pos = db.scalar(select(func.coalesce(func.max(Board.position), -1)).where(Board.project_id == project_id)) + 1
    board = Board(project_id=project_id, name=body.name, description=body.description,
                  color=body.color, position=pos)
    db.add(board)
    db.flush()
    if body.with_default_columns:
        create_columns(db, board, "basic")
    log_activity(db, project_id=p.id, user_id=me.id, action="board.created",
                 message=f"создал(а) доску «{board.name}»", board_id=board.id)
    queue_event(db, "project.updated", {"project_id": p.id}, project_id=p.id, actor_id=me.id)
    db.commit()
    return board_brief(board)


# ---------- активность и статистика ----------

@router.get("/projects/{project_id}/activity")
def project_activity(project_id: int, limit: int = 50, card_id: int | None = None,
                     me: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    me.require_project(project_id, P.PROJECT_READ)
    q = select(Activity).where(Activity.project_id == project_id)
    if card_id:
        q = q.where(Activity.card_id == card_id)
    items = db.scalars(q.order_by(Activity.created_at.desc(), Activity.id.desc()).limit(min(limit, 200)))
    return [activity_out(a) for a in items]


@router.get("/projects/{project_id}/stats")
def project_stats(project_id: int, me: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    me.require_project(project_id, P.PROJECT_READ)
    p = get_project(db, project_id)
    today = date.today()
    rows = db.execute(
        select(Card, Column).join(Column, Column.id == Card.column_id)
        .join(Board, Board.id == Card.board_id)
        .where(Board.project_id == project_id, Card.is_archived.is_(False), Board.is_archived.is_(False))
    ).all()
    total = len(rows)
    done = sum(1 for c, col in rows if col.is_done)
    overdue = sum(1 for c, col in rows if c.due_date and c.due_date < today and not col.is_done)
    by_priority = {k: 0 for k in ("critical", "high", "medium", "low")}
    for c, _ in rows:
        by_priority[c.priority] += 1

    by_assignee: dict[int, dict] = {}
    card_ids = [c.id for c, _ in rows]
    done_ids = {c.id for c, col in rows if col.is_done}
    if card_ids:
        for ca in db.scalars(select(CardAssignee).where(CardAssignee.card_id.in_(card_ids))):
            e = by_assignee.setdefault(ca.user_id, {**user_brief(ca.user), "open": 0, "done": 0})
            e["done" if ca.card_id in done_ids else "open"] += 1

    # активность по дням за 14 дней
    since = utcnow() - timedelta(days=13)
    per_day = {(today - timedelta(days=i)).isoformat(): 0 for i in range(13, -1, -1)}
    for (created,) in db.execute(select(Activity.created_at).where(
            Activity.project_id == project_id, Activity.created_at >= since.replace(hour=0, minute=0))):
        key = created.date().isoformat()
        if key in per_day:
            per_day[key] += 1

    return {
        "project": project_out(p, my_project_role(me, project_id)),
        "total": total, "done": done, "open": total - done, "overdue": overdue,
        "story_points": sum(c.estimate or 0 for c, _ in rows),
        "story_points_done": sum(c.estimate or 0 for c, col in rows if col.is_done),
        "by_priority": by_priority,
        "by_assignee": sorted(by_assignee.values(), key=lambda e: -(e["open"] + e["done"])),
        "activity_per_day": [{"date": d, "count": n} for d, n in per_day.items()],
    }
