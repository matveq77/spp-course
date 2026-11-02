from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, field_validator

TeamRole = Literal["owner", "admin", "member"]
ProjectRole = Literal["manager", "member", "viewer"]
Priority = Literal["low", "medium", "high", "critical"]


# ---------- auth ----------
class RegisterIn(BaseModel):
    username: str = Field(min_length=3, max_length=50, pattern=r"^[a-zA-Z0-9_.-]+$")
    email: str = Field(min_length=3, max_length=120, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    full_name: str = Field(default="", max_length=120)
    password: str = Field(min_length=6, max_length=128)


class LoginIn(BaseModel):
    username: str
    password: str


class RefreshIn(BaseModel):
    refresh_token: str


class TwoFactorLoginIn(BaseModel):
    challenge_token: str
    code: str = Field(min_length=6, max_length=20)


class TwoFactorCodeIn(BaseModel):
    code: str = Field(min_length=6, max_length=20)


class TwoFactorDisableIn(BaseModel):
    password: str
    code: str = Field(min_length=6, max_length=20)


class ProfileUpdate(BaseModel):
    full_name: str | None = Field(default=None, max_length=120)
    email: str | None = Field(default=None, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")


class PasswordChange(BaseModel):
    old_password: str
    new_password: str = Field(min_length=6, max_length=128)


# ---------- admin ----------
class AdminUserUpdate(BaseModel):
    system_role: Literal["admin", "user"] | None = None
    is_active: bool | None = None
    reset_2fa: bool = False


# ---------- teams ----------
class TeamIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = ""


class TeamUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None


class MemberAdd(BaseModel):
    username: str
    role: str = "member"


class MemberRole(BaseModel):
    role: str


# ---------- projects ----------
class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    key: str = Field(min_length=1, max_length=10, pattern=r"^[A-Za-z][A-Za-z0-9]*$")
    description: str = ""
    visibility: Literal["team", "private"] = "team"
    template: Literal["basic", "scrum", "empty"] = "basic"

    @field_validator("key")
    @classmethod
    def upper(cls, v: str) -> str:
        return v.upper()


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None
    visibility: Literal["team", "private"] | None = None
    is_archived: bool | None = None


class LabelIn(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    color: str = Field(default="#64748b", pattern=r"^#[0-9a-fA-F]{6}$")


# ---------- boards ----------
class BoardIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = ""
    color: str = Field(default="#0ea5e9", pattern=r"^#[0-9a-fA-F]{6}$")
    with_default_columns: bool = True


class BoardUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    is_archived: bool | None = None


class ColumnIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    wip_limit: int | None = Field(default=None, ge=1)
    is_done: bool = False


class ColumnUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    wip_limit: int | None = Field(default=None, ge=0)  # 0 = снять лимит
    is_done: bool | None = None


class MoveIn(BaseModel):
    index: int = Field(ge=0)


class CardIn(BaseModel):
    column_id: int
    title: str = Field(min_length=1, max_length=200)
    description: str = ""
    priority: Priority = "medium"
    due_date: date | None = None
    estimate: int | None = Field(default=None, ge=0, le=100)
    assignee_ids: list[int] = []
    label_ids: list[int] = []


class CardUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    priority: Priority | None = None
    due_date: date | None = None
    clear_due_date: bool = False
    estimate: int | None = Field(default=None, ge=0, le=100)
    is_archived: bool | None = None
    sprint_id: int | None = None  # null — убрать из спринта (в бэклог)


class SprintIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    goal: str = Field(default="", max_length=2000)
    start_date: date | None = None
    end_date: date | None = None


class SprintUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    goal: str | None = Field(default=None, max_length=2000)
    start_date: date | None = None
    end_date: date | None = None


class SprintStart(BaseModel):
    end_date: date


class SprintComplete(BaseModel):
    # куда деть незавершённые задачи: в бэклог, в существующий запланированный спринт или в новый
    move_to: Literal["backlog", "sprint", "new"] = "backlog"
    target_sprint_id: int | None = None
    new_sprint_name: str | None = Field(default=None, max_length=100)


class CardMove(BaseModel):
    column_id: int
    index: int = Field(ge=0)


class IdsIn(BaseModel):
    ids: list[int]


class ChecklistIn(BaseModel):
    text: str = Field(min_length=1, max_length=300)


class ChecklistUpdate(BaseModel):
    text: str | None = Field(default=None, min_length=1, max_length=300)
    done: bool | None = None


class CommentIn(BaseModel):
    text: str = Field(min_length=1, max_length=5000)


class PresenceIn(BaseModel):
    connection_id: int
    board_id: int | None = None
    card_id: int | None = None
    editing: Literal["description", "title"] | None = None
    seq: int = 0  # порядковый номер: сервер игнорирует устаревшие (пришедшие позже) обновления
