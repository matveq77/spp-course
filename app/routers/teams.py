from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import Principal, get_principal
from ..models import Team, TeamMember, User
from ..permissions import P, TEAM_ROLES
from ..schemas import MemberAdd, MemberRole, TeamIn, TeamUpdate
from ..serializers import project_out, team_out, user_brief
from ..services import notify, queue_event, refresh_permissions, team_user_ids

router = APIRouter(prefix="/api/teams", tags=["teams"])


def _get_team(db: Session, team_id: int) -> Team:
    team = db.get(Team, team_id)
    if not team:
        raise HTTPException(404, "Команда не найдена")
    return team


def _role_of(me: Principal, team_id: int) -> str | None:
    entry = me.teams.get(str(team_id))
    return entry["role"] if entry else None


@router.get("")
def list_teams(me: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    q = select(Team).order_by(Team.name)
    if not me.is_admin:
        q = q.where(Team.id.in_(me.team_ids()))
    return [team_out(t, _role_of(me, t.id)) for t in db.scalars(q)]


@router.post("")
def create_team(body: TeamIn, me: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    team = Team(name=body.name, description=body.description, created_by=me.id)
    team.members.append(TeamMember(user_id=me.id, role="owner"))
    db.add(team)
    db.flush()
    refresh_permissions(db, [me.id])
    db.commit()
    return team_out(team, "owner")


@router.get("/{team_id}")
def get_team(team_id: int, me: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    me.require_team(team_id, P.TEAM_READ)
    team = _get_team(db, team_id)
    members = sorted(team.members, key=lambda m: (["owner", "admin", "member"].index(m.role), m.user.username))
    projects = [p for p in team.projects if me.can_project(p.id, P.PROJECT_READ)]
    return {
        **team_out(team, _role_of(me, team_id)),
        "members": [{**user_brief(m.user), "role": m.role, "joined_at": m.joined_at} for m in members],
        "projects": [
            project_out(p, (me.projects.get(str(p.id)) or {}).get("role")) for p in
            sorted(projects, key=lambda p: (p.is_archived, p.name))
        ],
    }


@router.patch("/{team_id}")
def update_team(team_id: int, body: TeamUpdate, me: Principal = Depends(get_principal),
                db: Session = Depends(get_db)):
    me.require_team(team_id, P.TEAM_UPDATE)
    team = _get_team(db, team_id)
    if body.name is not None:
        team.name = body.name
    if body.description is not None:
        team.description = body.description
    queue_event(db, "team.updated", {"team_id": team_id}, team_id=team_id, actor_id=me.id)
    db.commit()
    return team_out(team, _role_of(me, team_id))


@router.delete("/{team_id}")
def delete_team(team_id: int, me: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    me.require_team(team_id, P.TEAM_DELETE)
    team = _get_team(db, team_id)
    affected = team_user_ids(db, team_id)
    for p in team.projects:
        affected += [m.user_id for m in p.members]
    queue_event(db, "team.deleted", {"team_id": team_id}, team_id=team_id, actor_id=me.id)
    db.delete(team)
    refresh_permissions(db, affected)
    db.commit()
    return {"ok": True}


# ---------- участники ----------

def _owners_count(db: Session, team_id: int) -> int:
    return db.scalar(select(func.count(TeamMember.id)).where(
        TeamMember.team_id == team_id, TeamMember.role == "owner"))


def _check_role(role: str):
    if role not in TEAM_ROLES:
        raise HTTPException(422, f"Недопустимая роль: {role}")


@router.post("/{team_id}/members")
def add_member(team_id: int, body: MemberAdd, me: Principal = Depends(get_principal),
               db: Session = Depends(get_db)):
    me.require_team(team_id, P.TEAM_MEMBERS)
    _check_role(body.role)
    if body.role == "owner" and _role_of(me, team_id) != "owner" and not me.is_admin:
        raise HTTPException(403, "Назначать владельцев может только владелец")
    _get_team(db, team_id)
    user = db.scalar(select(User).where(func.lower(User.username) == body.username.lower()))
    if not user:
        raise HTTPException(404, "Пользователь не найден")
    if db.scalar(select(TeamMember).where(TeamMember.team_id == team_id, TeamMember.user_id == user.id)):
        raise HTTPException(409, "Пользователь уже в команде")
    db.add(TeamMember(team_id=team_id, user_id=user.id, role=body.role))
    refresh_permissions(db, [user.id])
    queue_event(db, "team.updated", {"team_id": team_id}, team_id=team_id, actor_id=me.id)
    team = db.get(Team, team_id)
    notify(db, [user.id], f"{me.name} добавил(а) вас в команду «{team.name}»", {"team_id": team_id})
    db.commit()
    return {"ok": True}


@router.patch("/{team_id}/members/{user_id}")
def change_member_role(team_id: int, user_id: int, body: MemberRole, me: Principal = Depends(get_principal),
                       db: Session = Depends(get_db)):
    me.require_team(team_id, P.TEAM_MEMBERS)
    _check_role(body.role)
    tm = db.scalar(select(TeamMember).where(TeamMember.team_id == team_id, TeamMember.user_id == user_id))
    if not tm:
        raise HTTPException(404, "Участник не найден")
    i_am_owner = _role_of(me, team_id) == "owner" or me.is_admin
    if (body.role == "owner" or tm.role == "owner") and not i_am_owner:
        raise HTTPException(403, "Менять роль владельца может только владелец")
    if tm.role == "owner" and body.role != "owner" and _owners_count(db, team_id) <= 1:
        raise HTTPException(400, "В команде должен остаться хотя бы один владелец")
    tm.role = body.role
    refresh_permissions(db, [user_id])
    queue_event(db, "team.updated", {"team_id": team_id}, team_id=team_id, actor_id=me.id)
    db.commit()
    return {"ok": True}


@router.delete("/{team_id}/members/{user_id}")
def remove_member(team_id: int, user_id: int, me: Principal = Depends(get_principal),
                  db: Session = Depends(get_db)):
    if user_id != me.id:  # выйти из команды может любой участник
        me.require_team(team_id, P.TEAM_MEMBERS)
    tm = db.scalar(select(TeamMember).where(TeamMember.team_id == team_id, TeamMember.user_id == user_id))
    if not tm:
        raise HTTPException(404, "Участник не найден")
    if tm.role == "owner":
        if _owners_count(db, team_id) <= 1:
            raise HTTPException(400, "Нельзя удалить последнего владельца")
        if user_id != me.id and _role_of(me, team_id) != "owner" and not me.is_admin:
            raise HTTPException(403, "Удалить владельца может только владелец")
    queue_event(db, "team.updated", {"team_id": team_id}, team_id=team_id, actor_id=me.id)
    db.delete(tm)
    refresh_permissions(db, [user_id])
    db.commit()
    return {"ok": True}
