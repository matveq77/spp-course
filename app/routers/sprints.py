"""Спринты проекта: планирование, старт, завершение с переносом задач, burndown."""
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import Principal, get_principal
from ..models import Board, Card, Column, Project, Sprint, utcnow
from ..permissions import P
from ..schemas import SprintComplete, SprintIn, SprintStart, SprintUpdate
from ..serializers import card_brief
from ..services import log_activity, queue_event
from ..sprints import active_sprint, burndown, record_snapshot, sprint_cards, sprint_totals

router = APIRouter(prefix="/api", tags=["sprints"])


def _project(db: Session, project_id: int) -> Project:
    p = db.get(Project, project_id)
    if not p:
        raise HTTPException(404, "Проект не найден")
    return p


def _sprint(db: Session, sprint_id: int) -> Sprint:
    s = db.get(Sprint, sprint_id)
    if not s:
        raise HTTPException(404, "Спринт не найден")
    return s


def sprint_out(db: Session, s: Sprint) -> dict:
    totals = sprint_totals(db, s.id)
    return {
        "id": s.id, "project_id": s.project_id, "name": s.name, "goal": s.goal, "status": s.status,
        "start_date": s.start_date, "end_date": s.end_date, "completed_at": s.completed_at,
        "completed_cards": s.completed_cards, "completed_points": s.completed_points,
        "carried_cards": s.carried_cards, **totals,
        "done_cards": totals["total_cards"] - totals["remaining_cards"],
        "done_points": totals["total_points"] - totals["remaining_points"],
    }


def _changed(db: Session, project_id: int, actor: int, boards: set[int] = frozenset()):
    queue_event(db, "sprint.changed", {}, project_id=project_id, actor_id=actor)
    for b in boards:
        queue_event(db, "board.changed", {"board_id": b, "card_id": None}, project_id=project_id, actor_id=actor)


def _check_dates(start: date | None, end: date | None):
    if start and end and end < start:
        raise HTTPException(422, "Дата окончания раньше даты начала")


def _next_name(db: Session, project_id: int) -> str:
    n = len(db.scalars(select(Sprint.id).where(Sprint.project_id == project_id)).all()) + 1
    return f"Спринт {n}"


@router.get("/projects/{project_id}/sprints")
def list_sprints(project_id: int, me: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    me.require_project(project_id, P.PROJECT_READ)
    p = _project(db, project_id)
    order = {"active": 0, "planned": 1, "completed": 2}
    sprints = sorted(p.sprints, key=lambda s: (order[s.status], -s.id if s.status == "completed" else s.id))
    return [sprint_out(db, s) for s in sprints]


@router.post("/projects/{project_id}/sprints")
def create_sprint(project_id: int, body: SprintIn, me: Principal = Depends(get_principal),
                  db: Session = Depends(get_db)):
    me.require_project(project_id, P.BOARD_MANAGE)
    _project(db, project_id)
    _check_dates(body.start_date, body.end_date)
    s = Sprint(project_id=project_id, name=body.name, goal=body.goal,
               start_date=body.start_date, end_date=body.end_date)
    db.add(s)
    db.flush()
    log_activity(db, project_id=project_id, user_id=me.id, action="sprint.created",
                 message=f"запланировал(а) «{s.name}»")
    _changed(db, project_id, me.id)
    db.commit()
    return sprint_out(db, s)


@router.get("/sprints/{sprint_id}")
def sprint_detail(sprint_id: int, me: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    s = _sprint(db, sprint_id)
    me.require_project(s.project_id, P.PROJECT_READ)
    key = db.get(Project, s.project_id).key
    cards = []
    for c, col in sprint_cards(db, s.id):
        cards.append({**card_brief(c, key), "column_name": col.name, "done": col.is_done,
                      "board_name": db.get(Board, c.board_id).name})
    cards.sort(key=lambda c: (c["done"], {"critical": 0, "high": 1, "medium": 2, "low": 3}[c["priority"]]))
    return {**sprint_out(db, s), "cards": cards, "burndown": burndown(s)}


@router.patch("/sprints/{sprint_id}")
def update_sprint(sprint_id: int, body: SprintUpdate, me: Principal = Depends(get_principal),
                  db: Session = Depends(get_db)):
    s = _sprint(db, sprint_id)
    me.require_project(s.project_id, P.BOARD_MANAGE)
    if s.status == "completed":
        raise HTTPException(409, "Завершённый спринт нельзя изменить")
    data = body.model_dump(exclude_unset=True)
    if s.status == "active" and "start_date" in data and data["start_date"] != s.start_date:
        raise HTTPException(409, "Дату начала активного спринта менять нельзя")
    _check_dates(data.get("start_date", s.start_date), data.get("end_date", s.end_date))
    if s.status == "active" and data.get("end_date", s.end_date) is None:
        raise HTTPException(422, "У активного спринта должна быть дата окончания")
    for k, v in data.items():
        setattr(s, k, v)
    _changed(db, s.project_id, me.id)
    db.commit()
    return sprint_out(db, s)


@router.delete("/sprints/{sprint_id}")
def delete_sprint(sprint_id: int, me: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    s = _sprint(db, sprint_id)
    me.require_project(s.project_id, P.BOARD_MANAGE)
    if s.status == "active":
        raise HTTPException(409, "Активный спринт нельзя удалить — сначала завершите его")
    boards = set()
    for c in db.scalars(select(Card).where(Card.sprint_id == s.id)):
        c.sprint_id = None
        boards.add(c.board_id)
    log_activity(db, project_id=s.project_id, user_id=me.id, action="sprint.deleted",
                 message=f"удалил(а) «{s.name}», задачи вернулись в бэклог")
    _changed(db, s.project_id, me.id, boards)
    db.delete(s)
    db.commit()
    return {"ok": True}


@router.post("/sprints/{sprint_id}/start")
def start_sprint(sprint_id: int, body: SprintStart, me: Principal = Depends(get_principal),
                 db: Session = Depends(get_db)):
    s = _sprint(db, sprint_id)
    me.require_project(s.project_id, P.BOARD_MANAGE)
    if s.status != "planned":
        raise HTTPException(409, "Запустить можно только запланированный спринт")
    current = active_sprint(db, s.project_id)
    if current:
        raise HTTPException(409, f"В проекте уже идёт «{current.name}». Сначала завершите его")
    today = date.today()
    if body.end_date <= today:
        raise HTTPException(422, "Дата окончания должна быть позже сегодняшней")
    s.status, s.start_date, s.end_date = "active", today, body.end_date
    db.flush()
    record_snapshot(db, s, today)  # стартовая точка burndown
    totals = sprint_totals(db, s.id)
    log_activity(db, project_id=s.project_id, user_id=me.id, action="sprint.started",
                 message=f"начал(а) «{s.name}»: {totals['total_cards']} задач, {totals['total_points']} SP, "
                         f"до {s.end_date.strftime('%d.%m.%Y')}")
    _changed(db, s.project_id, me.id)
    db.commit()
    return sprint_out(db, s)


@router.post("/sprints/{sprint_id}/complete")
def complete_sprint(sprint_id: int, body: SprintComplete, me: Principal = Depends(get_principal),
                    db: Session = Depends(get_db)):
    s = _sprint(db, sprint_id)
    me.require_project(s.project_id, P.BOARD_MANAGE)
    if s.status != "active":
        raise HTTPException(409, "Завершить можно только активный спринт")

    target: Sprint | None = None
    if body.move_to == "sprint":
        target = db.get(Sprint, body.target_sprint_id) if body.target_sprint_id else None
        if not target or target.project_id != s.project_id or target.status != "planned":
            raise HTTPException(422, "Выберите запланированный спринт этого проекта")
    elif body.move_to == "new":
        target = Sprint(project_id=s.project_id, name=(body.new_sprint_name or "").strip() or _next_name(db, s.project_id))
        db.add(target)
        db.flush()

    record_snapshot(db, s)  # финальная точка до переноса задач
    rows = sprint_cards(db, s.id)
    done = [(c, col) for c, col in rows if col.is_done]
    unfinished = [c for c, col in rows if not col.is_done]
    boards = {c.board_id for c, _ in rows}
    for c in unfinished:
        c.sprint_id = target.id if target else None

    s.status = "completed"
    s.completed_at = utcnow()
    s.completed_cards = len(done)
    s.completed_points = sum(c.estimate or 0 for c, _ in done)
    s.carried_cards = len(unfinished)
    where = f"в «{target.name}»" if target else "в бэклог"
    log_activity(db, project_id=s.project_id, user_id=me.id, action="sprint.completed",
                 message=f"завершил(а) «{s.name}»: выполнено {len(done)} задач ({s.completed_points} SP), "
                         f"{len(unfinished)} перенесено {where}")
    _changed(db, s.project_id, me.id, boards)
    db.commit()
    return {**sprint_out(db, s), "target_sprint_id": target.id if target else None}


@router.get("/projects/{project_id}/backlog")
def backlog(project_id: int, me: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Незавершённые задачи проекта, не входящие ни в один спринт."""
    me.require_project(project_id, P.PROJECT_READ)
    p = _project(db, project_id)
    rows = db.execute(
        select(Card, Column, Board).join(Column, Column.id == Card.column_id).join(Board, Board.id == Card.board_id)
        .where(Board.project_id == project_id, Board.is_archived.is_(False), Card.is_archived.is_(False),
               Card.sprint_id.is_(None), Column.is_done.is_(False))
        .order_by(Card.id)
    ).all()
    prio = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    items = [{**card_brief(c, p.key), "column_name": col.name, "board_name": b.name} for c, col, b in rows]
    return sorted(items, key=lambda c: (prio[c["priority"]], c["id"]))
