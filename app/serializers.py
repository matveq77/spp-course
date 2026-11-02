"""Преобразование ORM-объектов в JSON-словари для API."""
from .models import (
    Activity, Attachment, Board, Card, Column, Comment, Label, Notification, Project, Team, User,
)


def user_brief(u: User | None) -> dict | None:
    if u is None:
        return None
    return {"id": u.id, "username": u.username, "full_name": u.full_name, "color": u.color}


def user_full(u: User) -> dict:
    return {
        **user_brief(u), "email": u.email, "system_role": u.system_role,
        "is_active": u.is_active, "created_at": u.created_at, "totp_enabled": u.totp_enabled,
    }


def team_out(t: Team, my_role: str | None = None) -> dict:
    return {
        "id": t.id, "name": t.name, "description": t.description, "created_at": t.created_at,
        "my_role": my_role, "members_count": len(t.members), "projects_count": len(t.projects),
    }


def project_out(p: Project, my_role: str | None = None) -> dict:
    return {
        "id": p.id, "team_id": p.team_id, "team_name": p.team.name, "name": p.name, "key": p.key,
        "description": p.description, "visibility": p.visibility, "is_archived": p.is_archived,
        "created_at": p.created_at, "my_role": my_role,
    }


def label_out(l: Label) -> dict:
    return {"id": l.id, "name": l.name, "color": l.color}


def board_brief(b: Board) -> dict:
    return {
        "id": b.id, "project_id": b.project_id, "name": b.name, "description": b.description,
        "color": b.color, "is_archived": b.is_archived, "position": b.position,
    }


def card_brief(c: Card, project_key: str) -> dict:
    total = len(c.checklist)
    done = sum(1 for i in c.checklist if i.done)
    return {
        "id": c.id, "key": f"{project_key}-{c.number}", "board_id": c.board_id,
        "column_id": c.column_id, "title": c.title, "priority": c.priority,
        "due_date": c.due_date, "estimate": c.estimate, "is_archived": c.is_archived, "sprint_id": c.sprint_id,
        "position": c.position,
        "has_description": bool(c.description.strip()),
        "assignees": [user_brief(a.user) for a in c.assignees],
        "labels": [label_out(cl.label) for cl in c.labels],
        "checklist": {"total": total, "done": done},
        "comments_count": len(c.comments),
        "attachments_count": len(c.attachments),
        "updated_at": c.updated_at,
    }


def attachment_out(a: Attachment) -> dict:
    return {
        "id": a.id, "card_id": a.card_id, "filename": a.filename, "content_type": a.content_type,
        "size": a.size, "is_image": a.is_image, "uploader": user_brief(a.uploader), "created_at": a.created_at,
    }


def comment_out(c: Comment) -> dict:
    return {
        "id": c.id, "card_id": c.card_id, "text": c.text, "author": user_brief(c.author),
        "created_at": c.created_at, "edited_at": c.edited_at,
    }


def card_full(c: Card, project_key: str, creator: User | None) -> dict:
    return {
        **card_brief(c, project_key),
        "description": c.description,
        "created_at": c.created_at,
        "created_by": user_brief(creator),
        "column_name": c.column.name,
        "checklist_items": [{"id": i.id, "text": i.text, "done": i.done} for i in c.checklist],
        "comments": [comment_out(cm) for cm in c.comments],
        "attachments": [attachment_out(a) for a in c.attachments],
    }


def column_out(col: Column, project_key: str, include_archived=False) -> dict:
    cards = [c for c in col.cards if include_archived or not c.is_archived]
    return {
        "id": col.id, "name": col.name, "position": col.position, "wip_limit": col.wip_limit,
        "is_done": col.is_done, "cards": [card_brief(c, project_key) for c in cards],
    }


def activity_out(a: Activity) -> dict:
    return {
        "id": a.id, "project_id": a.project_id, "board_id": a.board_id, "card_id": a.card_id,
        "user": user_brief(a.user), "action": a.action, "message": a.message,
        "created_at": a.created_at,
    }


def notification_out(n: Notification) -> dict:
    return {"id": n.id, "text": n.text, "link": n.link, "is_read": n.is_read, "created_at": n.created_at}
