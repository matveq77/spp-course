import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import Principal, get_principal
from ..events import broker
from ..models import (
    Board, Card, CardAssignee, CardLabel, ChecklistItem, Column, Comment, Label, Project, Sprint, User, utcnow,
)
from ..permissions import P, PROJECT_ROLES
from ..schemas import (
    BoardUpdate, CardIn, CardMove, CardUpdate, ChecklistIn, ChecklistUpdate, ColumnIn, ColumnUpdate,
    CommentIn, IdsIn, MoveIn,
)
from ..serializers import (
    board_brief, card_brief, card_full, column_out, comment_out, label_out, project_out,
)
from ..services import display_name, log_activity, notify, queue_event
from .projects import my_project_role, project_members

router = APIRouter(prefix="/api", tags=["boards"])

PRIORITY_RU = {"low": "низкий", "medium": "средний", "high": "высокий", "critical": "критический"}


# ---------- вспомогательное ----------

def _board(db: Session, board_id: int) -> Board:
    b = db.get(Board, board_id)
    if not b:
        raise HTTPException(404, "Доска не найдена")
    return b


def _column(db: Session, column_id: int) -> Column:
    c = db.get(Column, column_id)
    if not c:
        raise HTTPException(404, "Колонка не найдена")
    return c


def _card(db: Session, card_id: int) -> tuple[Card, Project]:
    c = db.get(Card, card_id)
    if not c:
        raise HTTPException(404, "Карточка не найдена")
    return c, c.column.board.project


def _changed(db: Session, board: Board, actor: int, card_id: int | None = None, **extra):
    queue_event(db, "board.changed", {"board_id": board.id, "card_id": card_id, **extra},
                project_id=board.project_id, actor_id=actor)


def _reorder(items: list, item, index: int):
    """Переставляет item на позицию index и перенумеровывает позиции 0..n."""
    items = [i for i in items if i.id != item.id]
    index = max(0, min(index, len(items)))
    items.insert(index, item)
    for pos, it in enumerate(items):
        it.position = pos


def _project_user_ids(db: Session, project_id: int, me: Principal) -> set[int]:
    return {m["id"] for m in project_members(project_id, me, db)}


def _card_ref(card: Card, project: Project) -> str:
    return f"{project.key}-{card.number} «{card.title}»"


# ---------- доска ----------

@router.get("/boards/{board_id}")
def board_detail(board_id: int, me: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    b = _board(db, board_id)
    me.require_project(b.project_id, P.PROJECT_READ)
    p = b.project
    role = my_project_role(me, p.id)
    return {
        **board_brief(b),
        "project": project_out(p, role),
        "my_perms": PROJECT_ROLES.get(role, []) if role else [],
        "columns": [column_out(c, p.key) for c in b.columns],
        "labels": [label_out(l) for l in p.labels],
        "members": project_members(p.id, me, db),
        "boards": [board_brief(x) for x in sorted(p.boards, key=lambda x: x.position) if not x.is_archived],
        "viewers": broker.viewers(board_id),
        "sprints": [{"id": sp.id, "name": sp.name, "status": sp.status} for sp in p.sprints if sp.status != "completed"],
    }


@router.patch("/boards/{board_id}")
def update_board(board_id: int, body: BoardUpdate, me: Principal = Depends(get_principal),
                 db: Session = Depends(get_db)):
    b = _board(db, board_id)
    me.require_project(b.project_id, P.BOARD_MANAGE)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(b, k, v)
    _changed(db, b, me.id)
    queue_event(db, "project.updated", {"project_id": b.project_id}, project_id=b.project_id, actor_id=me.id)
    db.commit()
    return board_brief(b)


@router.post("/boards/{board_id}/move")
def move_board(board_id: int, body: MoveIn, me: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    b = _board(db, board_id)
    me.require_project(b.project_id, P.BOARD_MANAGE)
    _reorder(sorted(b.project.boards, key=lambda x: x.position), b, body.index)
    queue_event(db, "project.updated", {"project_id": b.project_id}, project_id=b.project_id, actor_id=me.id)
    db.commit()
    return {"ok": True}


@router.delete("/boards/{board_id}")
def delete_board(board_id: int, me: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    b = _board(db, board_id)
    me.require_project(b.project_id, P.BOARD_MANAGE)
    log_activity(db, project_id=b.project_id, user_id=me.id, action="board.deleted",
                 message=f"удалил(а) доску «{b.name}»")
    queue_event(db, "board.deleted", {"board_id": b.id}, project_id=b.project_id, actor_id=me.id)
    queue_event(db, "project.updated", {"project_id": b.project_id}, project_id=b.project_id, actor_id=me.id)
    db.delete(b)
    db.commit()
    return {"ok": True}


@router.get("/boards/{board_id}/archived")
def archived_cards(board_id: int, me: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    b = _board(db, board_id)
    me.require_project(b.project_id, P.PROJECT_READ)
    cards = db.scalars(select(Card).where(Card.board_id == board_id, Card.is_archived.is_(True))
                       .order_by(Card.updated_at.desc()))
    return [{**card_brief(c, b.project.key), "column_name": c.column.name} for c in cards]


# ---------- колонки ----------

@router.post("/boards/{board_id}/columns")
def create_column(board_id: int, body: ColumnIn, me: Principal = Depends(get_principal),
                  db: Session = Depends(get_db)):
    b = _board(db, board_id)
    me.require_project(b.project_id, P.BOARD_MANAGE)
    pos = len(b.columns)
    col = Column(board_id=b.id, name=body.name, wip_limit=body.wip_limit, is_done=body.is_done, position=pos)
    db.add(col)
    log_activity(db, project_id=b.project_id, user_id=me.id, action="column.created",
                 message=f"добавил(а) колонку «{col.name}» на доску «{b.name}»", board_id=b.id)
    _changed(db, b, me.id)
    db.commit()
    return {"id": col.id}


@router.patch("/columns/{column_id}")
def update_column(column_id: int, body: ColumnUpdate, me: Principal = Depends(get_principal),
                  db: Session = Depends(get_db)):
    col = _column(db, column_id)
    me.require_project(col.board.project_id, P.BOARD_MANAGE)
    data = body.model_dump(exclude_unset=True)
    if "wip_limit" in data:
        col.wip_limit = data.pop("wip_limit") or None
    for k, v in data.items():
        setattr(col, k, v)
    _changed(db, col.board, me.id)
    db.commit()
    return {"ok": True}


@router.post("/columns/{column_id}/move")
def move_column(column_id: int, body: MoveIn, me: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    col = _column(db, column_id)
    me.require_project(col.board.project_id, P.BOARD_MANAGE)
    _reorder(list(col.board.columns), col, body.index)
    _changed(db, col.board, me.id)
    db.commit()
    return {"ok": True}


@router.delete("/columns/{column_id}")
def delete_column(column_id: int, me: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    col = _column(db, column_id)
    b = col.board
    me.require_project(b.project_id, P.BOARD_MANAGE)
    if any(not c.is_archived for c in col.cards):
        raise HTTPException(409, "В колонке есть карточки — сначала переместите или удалите их")
    log_activity(db, project_id=b.project_id, user_id=me.id, action="column.deleted",
                 message=f"удалил(а) колонку «{col.name}»", board_id=b.id)
    _changed(db, b, me.id)
    db.delete(col)
    db.commit()
    return {"ok": True}


# ---------- карточки ----------

def _active_count(db: Session, column_id: int) -> int:
    return db.scalar(select(func.count(Card.id)).where(Card.column_id == column_id, Card.is_archived.is_(False)))


def _check_wip(db: Session, col: Column):
    if col.wip_limit and _active_count(db, col.id) >= col.wip_limit:
        raise HTTPException(409, f"Достигнут WIP-лимит колонки «{col.name}» ({col.wip_limit})")


def _set_assignees(db: Session, card: Card, project: Project, ids: list[int], me: Principal) -> list[int]:
    allowed = _project_user_ids(db, project.id, me)
    bad = set(ids) - allowed
    if bad:
        raise HTTPException(422, f"Пользователи {sorted(bad)} не имеют доступа к проекту")
    current = {a.user_id for a in card.assignees}
    new = set(ids)
    for a in list(card.assignees):
        if a.user_id not in new:
            card.assignees.remove(a)
    for uid in new - current:
        card.assignees.append(CardAssignee(user_id=uid))
    return sorted(new - current)


def _set_labels(db: Session, card: Card, project: Project, ids: list[int]):
    valid = {l.id for l in project.labels}
    if set(ids) - valid:
        raise HTTPException(422, "Метки должны принадлежать проекту")
    current = {cl.label_id for cl in card.labels}
    for cl in list(card.labels):
        if cl.label_id not in ids:
            card.labels.remove(cl)
    for lid in set(ids) - current:
        card.labels.append(CardLabel(label_id=lid))


def _card_response(db: Session, card: Card, project: Project) -> dict:
    db.refresh(card)
    creator = db.get(User, card.created_by) if card.created_by else None
    return {**card_full(card, project.key, creator), "project_id": project.id}


@router.post("/cards")
def create_card(body: CardIn, me: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    col = _column(db, body.column_id)
    board = col.board
    project = board.project
    me.require_project(project.id, P.CARD_CREATE)
    if body.assignee_ids:
        me.require_project(project.id, P.CARD_ASSIGN)
    _check_wip(db, col)
    project.card_seq += 1
    card = Card(
        board_id=board.id, column_id=col.id, number=project.card_seq, title=body.title,
        description=body.description, priority=body.priority, due_date=body.due_date,
        estimate=body.estimate, created_by=me.id, position=_active_count(db, col.id),
    )
    db.add(card)
    db.flush()
    added = _set_assignees(db, card, project, body.assignee_ids, me)
    _set_labels(db, card, project, body.label_ids)
    log_activity(db, project_id=project.id, user_id=me.id, action="card.created",
                 message=f"создал(а) карточку {_card_ref(card, project)} в «{col.name}»",
                 board_id=board.id, card_id=card.id)
    notify(db, added, f"{me.name} назначил(а) вас на {_card_ref(card, project)}",
           {"board_id": board.id, "card_id": card.id}, exclude=me.id)
    _changed(db, board, me.id, card.id)
    db.commit()
    return _card_response(db, card, project)


@router.get("/cards/{card_id}")
def get_card(card_id: int, me: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    card, project = _card(db, card_id)
    me.require_project(project.id, P.PROJECT_READ)
    return _card_response(db, card, project)


@router.patch("/cards/{card_id}")
def update_card(card_id: int, body: CardUpdate, me: Principal = Depends(get_principal),
                db: Session = Depends(get_db)):
    card, project = _card(db, card_id)
    me.require_project(project.id, P.CARD_UPDATE)
    data = body.model_dump(exclude_unset=True)
    changes = []
    if data.pop("clear_due_date", False):
        card.due_date = None
        changes.append("убрал(а) срок")
    if "is_archived" in data and data["is_archived"] != card.is_archived:
        if not data["is_archived"]:
            _check_wip(db, card.column)
        changes.append("архивировал(а)" if data["is_archived"] else "восстановил(а) из архива")
    if "priority" in data and data["priority"] != card.priority:
        changes.append(f"сменил(а) приоритет на «{PRIORITY_RU[data['priority']]}»")
    if "due_date" in data and data["due_date"] != card.due_date and data["due_date"]:
        changes.append(f"установил(а) срок {data['due_date'].strftime('%d.%m.%Y')}")
    if "title" in data and data["title"] != card.title:
        changes.append("переименовал(а)")
    if "description" in data and data["description"] != card.description:
        changes.append("обновил(а) описание")
    if "sprint_id" in data and data["sprint_id"] != card.sprint_id:
        if data["sprint_id"] is None:
            changes.append("убрал(а) из спринта в бэклог")
        else:
            sprint = db.get(Sprint, data["sprint_id"])
            if not sprint or sprint.project_id != project.id:
                raise HTTPException(422, "Спринт не найден в этом проекте")
            if sprint.status == "completed":
                raise HTTPException(409, "Нельзя добавить задачу в завершённый спринт")
            changes.append(f"добавил(а) в спринт «{sprint.name}»")
        queue_event(db, "sprint.changed", {}, project_id=project.id, actor_id=me.id)
    for k, v in data.items():
        setattr(card, k, v)
    card.updated_at = utcnow()
    if changes:
        log_activity(db, project_id=project.id, user_id=me.id, action="card.updated",
                     message=f"{', '.join(changes)}: {_card_ref(card, project)}",
                     board_id=card.board_id, card_id=card.id)
    _changed(db, card.column.board, me.id, card.id)
    db.commit()
    return _card_response(db, card, project)


@router.post("/cards/{card_id}/move")
def move_card(card_id: int, body: CardMove, me: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    card, project = _card(db, card_id)
    me.require_project(project.id, P.CARD_MOVE)
    target = _column(db, body.column_id)
    if target.board.project_id != project.id:
        raise HTTPException(422, "Нельзя переместить карточку в другой проект")
    source = card.column
    old_board = source.board
    if target.id != source.id:
        _check_wip(db, target)
    siblings = [c for c in target.cards if not c.is_archived and c.id != card.id]
    card.column_id = target.id
    card.board_id = target.board_id
    card.updated_at = utcnow()
    _reorder(siblings, card, body.index)
    if target.id != source.id:
        msg = f"переместил(а) {_card_ref(card, project)}: «{source.name}» → «{target.name}»"
        if old_board.id != target.board_id:
            msg += f" (доска «{target.board.name}»)"
        log_activity(db, project_id=project.id, user_id=me.id, action="card.moved", message=msg,
                     board_id=target.board_id, card_id=card.id)
        if target.is_done:
            notify(db, [c.created_by for c in [card] if c.created_by] + [a.user_id for a in card.assignees],
                   f"{me.name} завершил(а) {_card_ref(card, project)}",
                   {"board_id": target.board_id, "card_id": card.id}, exclude=me.id)
    _changed(db, target.board, me.id, card.id)
    if old_board.id != target.board_id:
        _changed(db, old_board, me.id, card.id)
    db.commit()
    return {"ok": True}


@router.delete("/cards/{card_id}")
def delete_card(card_id: int, me: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    card, project = _card(db, card_id)
    me.require_project(project.id, P.CARD_DELETE)
    board = card.column.board
    log_activity(db, project_id=project.id, user_id=me.id, action="card.deleted",
                 message=f"удалил(а) карточку {_card_ref(card, project)}", board_id=board.id)
    _changed(db, board, me.id, card.id, deleted=True)
    db.delete(card)
    db.commit()
    return {"ok": True}


@router.put("/cards/{card_id}/assignees")
def set_assignees(card_id: int, body: IdsIn, me: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    card, project = _card(db, card_id)
    me.require_project(project.id, P.CARD_ASSIGN)
    added = _set_assignees(db, card, project, body.ids, me)
    if added:
        names = ", ".join(display_name(db.get(User, uid)) for uid in added)
        log_activity(db, project_id=project.id, user_id=me.id, action="card.assigned",
                     message=f"назначил(а) {names} на {_card_ref(card, project)}",
                     board_id=card.board_id, card_id=card.id)
        notify(db, added, f"{me.name} назначил(а) вас на {_card_ref(card, project)}",
               {"board_id": card.board_id, "card_id": card.id}, exclude=me.id)
    _changed(db, card.column.board, me.id, card.id)
    db.commit()
    return _card_response(db, card, project)


@router.put("/cards/{card_id}/labels")
def set_labels(card_id: int, body: IdsIn, me: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    card, project = _card(db, card_id)
    me.require_project(project.id, P.CARD_UPDATE)
    _set_labels(db, card, project, body.ids)
    _changed(db, card.column.board, me.id, card.id)
    db.commit()
    return _card_response(db, card, project)


# ---------- чек-лист ----------

@router.post("/cards/{card_id}/checklist")
def add_check_item(card_id: int, body: ChecklistIn, me: Principal = Depends(get_principal),
                   db: Session = Depends(get_db)):
    card, project = _card(db, card_id)
    me.require_project(project.id, P.CARD_UPDATE)
    card.checklist.append(ChecklistItem(text=body.text, position=len(card.checklist)))
    _changed(db, card.column.board, me.id, card.id)
    db.commit()
    return _card_response(db, card, project)


@router.patch("/checklist/{item_id}")
def update_check_item(item_id: int, body: ChecklistUpdate, me: Principal = Depends(get_principal),
                      db: Session = Depends(get_db)):
    item = db.get(ChecklistItem, item_id)
    if not item:
        raise HTTPException(404, "Пункт не найден")
    card, project = _card(db, item.card_id)
    me.require_project(project.id, P.CARD_UPDATE)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(item, k, v)
    _changed(db, card.column.board, me.id, card.id)
    db.commit()
    return _card_response(db, card, project)


@router.delete("/checklist/{item_id}")
def delete_check_item(item_id: int, me: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    item = db.get(ChecklistItem, item_id)
    if not item:
        raise HTTPException(404, "Пункт не найден")
    card, project = _card(db, item.card_id)
    me.require_project(project.id, P.CARD_UPDATE)
    db.delete(item)
    _changed(db, card.column.board, me.id, card.id)
    db.commit()
    return _card_response(db, card, project)


# ---------- комментарии ----------

MENTION_RE = re.compile(r"@([a-zA-Z0-9_.-]+)")


@router.post("/cards/{card_id}/comments")
def add_comment(card_id: int, body: CommentIn, me: Principal = Depends(get_principal),
                db: Session = Depends(get_db)):
    card, project = _card(db, card_id)
    me.require_project(project.id, P.COMMENT_CREATE)
    comment = Comment(card_id=card.id, author_id=me.id, text=body.text)
    db.add(comment)
    db.flush()
    link = {"board_id": card.board_id, "card_id": card.id}
    # упоминания @username
    mentioned = set()
    allowed = _project_user_ids(db, project.id, me)
    for uname in set(MENTION_RE.findall(body.text)):
        u = db.scalar(select(User).where(func.lower(User.username) == uname.lower()))
        if u and u.id in allowed:
            mentioned.add(u.id)
    notify(db, mentioned, f"{me.name} упомянул(а) вас в {_card_ref(card, project)}", link, exclude=me.id)
    watchers = {a.user_id for a in card.assignees} - mentioned
    notify(db, watchers, f"{me.name} прокомментировал(а) {_card_ref(card, project)}", link, exclude=me.id)
    log_activity(db, project_id=project.id, user_id=me.id, action="comment.created",
                 message=f"прокомментировал(а) {_card_ref(card, project)}", board_id=card.board_id, card_id=card.id)
    _changed(db, card.column.board, me.id, card.id)
    db.commit()
    db.refresh(comment)
    return comment_out(comment)


def _comment(db: Session, comment_id: int) -> tuple[Comment, Card, Project]:
    c = db.get(Comment, comment_id)
    if not c:
        raise HTTPException(404, "Комментарий не найден")
    card, project = _card(db, c.card_id)
    return c, card, project


@router.patch("/comments/{comment_id}")
def edit_comment(comment_id: int, body: CommentIn, me: Principal = Depends(get_principal),
                 db: Session = Depends(get_db)):
    c, card, project = _comment(db, comment_id)
    me.require_project(project.id, P.PROJECT_READ)
    if c.author_id != me.id:
        raise HTTPException(403, "Редактировать можно только свои комментарии")
    c.text = body.text
    c.edited_at = utcnow()
    _changed(db, card.column.board, me.id, card.id)
    db.commit()
    return comment_out(c)


@router.delete("/comments/{comment_id}")
def delete_comment(comment_id: int, me: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    c, card, project = _comment(db, comment_id)
    if c.author_id != me.id:
        me.require_project(project.id, P.COMMENT_MODERATE)
    _changed(db, card.column.board, me.id, card.id)
    db.delete(c)
    db.commit()
    return {"ok": True}
