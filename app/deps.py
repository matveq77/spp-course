from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from .db import get_db
from .permissions import P
from .security import decode_access_token, get_perm_version


@dataclass
class Principal:
    """Текущий пользователь, восстановленный ТОЛЬКО из JWT."""
    id: int
    username: str
    name: str
    role: str
    sys: list[str]
    teams: dict[str, dict]
    projects: dict[str, dict]
    sid: str | None = None

    @property
    def is_admin(self) -> bool:
        return P.SYS_ADMIN in self.sys

    def project_ids(self) -> set[int]:
        return {int(k) for k in self.projects}

    def team_ids(self) -> set[int]:
        return {int(k) for k in self.teams}

    def can_project(self, project_id: int, perm: str) -> bool:
        if self.is_admin:
            return True
        entry = self.projects.get(str(project_id))
        return bool(entry and perm in entry["perms"])

    def can_team(self, team_id: int, perm: str) -> bool:
        if self.is_admin:
            return True
        entry = self.teams.get(str(team_id))
        return bool(entry and perm in entry["perms"])

    def require_project(self, project_id: int, perm: str):
        if not self.can_project(project_id, perm):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Нет права {perm} в проекте {project_id}")

    def require_team(self, team_id: int, perm: str):
        if not self.can_team(team_id, perm):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Нет права {perm} в команде {team_id}")

    def require_admin(self):
        if not self.is_admin:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Требуются права администратора")


def principal_from_token(token: str, db: Session) -> Principal:
    try:
        payload = decode_access_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token_expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token_invalid")

    uid = int(payload["sub"])
    current_pv = get_perm_version(db, uid)
    if current_pv is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "user_inactive")
    if payload.get("pv") != current_pv:
        # права изменились после выдачи токена -> клиент должен сделать /auth/refresh
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token_outdated")

    return Principal(
        id=uid,
        username=payload["usr"],
        name=payload.get("name", payload["usr"]),
        role=payload.get("role", "user"),
        sys=payload.get("sys", []),
        teams=payload.get("teams", {}),
        projects=payload.get("projects", {}),
        sid=payload.get("sid"),
    )


def get_principal(request: Request, db: Session = Depends(get_db)) -> Principal:
    auth = request.headers.get("Authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token_missing")
    return principal_from_token(auth[7:], db)
