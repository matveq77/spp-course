"""
Модель прав доступа (RBAC) и сборка прав для JWT.

Три уровня ролей:
  * системная роль (users.system_role):  admin | user
  * роль в команде (team_members.role):   owner | admin | member
  * роль в проекте (project_members.role): manager | member | viewer

Роли раскрываются в конкретные права (permission strings), которые и зашиваются
в access-токен. Сервер при проверке доступа смотрит только в токен — без запросов в БД.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Project, ProjectMember, TeamMember, User


class P:
    # системные
    SYS_ADMIN = "sys:admin"
    # команда
    TEAM_READ = "team:read"
    TEAM_UPDATE = "team:update"
    TEAM_DELETE = "team:delete"
    TEAM_MEMBERS = "team:members"
    PROJECT_CREATE = "project:create"
    # проект
    PROJECT_READ = "project:read"
    PROJECT_UPDATE = "project:update"
    PROJECT_DELETE = "project:delete"
    PROJECT_MEMBERS = "project:members"
    BOARD_MANAGE = "board:manage"        # создать/переименовать/удалить доску и колонки
    LABEL_MANAGE = "label:manage"
    CARD_CREATE = "card:create"
    CARD_UPDATE = "card:update"
    CARD_MOVE = "card:move"
    CARD_DELETE = "card:delete"
    CARD_ASSIGN = "card:assign"
    COMMENT_CREATE = "comment:create"
    COMMENT_MODERATE = "comment:moderate"  # удалять чужие комментарии


SYSTEM_ROLES = {
    "admin": [P.SYS_ADMIN],
    "user": [],
}

TEAM_ROLES = {
    "owner": [P.TEAM_READ, P.TEAM_UPDATE, P.TEAM_DELETE, P.TEAM_MEMBERS, P.PROJECT_CREATE],
    "admin": [P.TEAM_READ, P.TEAM_UPDATE, P.TEAM_MEMBERS, P.PROJECT_CREATE],
    "member": [P.TEAM_READ],
}

PROJECT_ROLES = {
    "manager": [
        P.PROJECT_READ, P.PROJECT_UPDATE, P.PROJECT_DELETE, P.PROJECT_MEMBERS, P.BOARD_MANAGE,
        P.LABEL_MANAGE, P.CARD_CREATE, P.CARD_UPDATE, P.CARD_MOVE, P.CARD_DELETE, P.CARD_ASSIGN,
        P.COMMENT_CREATE, P.COMMENT_MODERATE,
    ],
    "member": [
        P.PROJECT_READ, P.CARD_CREATE, P.CARD_UPDATE, P.CARD_MOVE, P.CARD_ASSIGN, P.COMMENT_CREATE,
    ],
    "viewer": [P.PROJECT_READ, P.COMMENT_CREATE],
}

ROLE_RANK = {"viewer": 1, "member": 2, "manager": 3}


def effective_project_role(team_role: str | None, explicit_role: str | None, visibility: str) -> str | None:
    """Итоговая роль в проекте с учётом роли в команде.

    owner/admin команды автоматически менеджеры всех её проектов;
    в проекте с visibility=team любой участник команды минимум viewer.
    """
    candidates = []
    if explicit_role:
        candidates.append(explicit_role)
    if team_role in ("owner", "admin"):
        candidates.append("manager")
    elif team_role == "member" and visibility == "team":
        candidates.append("viewer")
    if not candidates:
        return None
    return max(candidates, key=lambda r: ROLE_RANK[r])


def build_claims(db: Session, user: User) -> dict:
    """Собирает все права пользователя для зашивания в JWT."""
    team_roles: dict[int, str] = {
        tm.team_id: tm.role
        for tm in db.scalars(select(TeamMember).where(TeamMember.user_id == user.id))
    }
    explicit: dict[int, str] = {
        pm.project_id: pm.role
        for pm in db.scalars(select(ProjectMember).where(ProjectMember.user_id == user.id))
    }

    # проекты, к которым потенциально есть доступ: из своих команд + явные
    project_rows = db.execute(
        select(Project.id, Project.team_id, Project.visibility).where(
            (Project.team_id.in_(team_roles.keys())) | (Project.id.in_(explicit.keys()))
        )
    ).all()

    teams = {
        str(tid): {"role": role, "perms": TEAM_ROLES[role]} for tid, role in team_roles.items()
    }
    projects = {}
    for pid, tid, visibility in project_rows:
        role = effective_project_role(team_roles.get(tid), explicit.get(pid), visibility)
        if role:
            projects[str(pid)] = {"role": role, "perms": PROJECT_ROLES[role]}

    return {
        "role": user.system_role,
        "sys": SYSTEM_ROLES.get(user.system_role, []),
        "teams": teams,
        "projects": projects,
    }
