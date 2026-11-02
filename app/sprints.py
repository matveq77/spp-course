"""
Спринты: подсчёт остатка работы и снимки для burndown-диаграммы.

Снимок за «сегодня» пересчитывается автоматически перед каждым commit, в котором поменялись
карточки или колонки проекта с активным спринтом (см. хуки в services.py). Поэтому диаграмма
строится по реальной истории, без фоновых задач: если в какой-то день ничего не менялось,
остаток в этот день равен остатку предыдущего дня.
"""
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Card, Column, Sprint, SprintDay


def sprint_cards(db: Session, sprint_id: int) -> list[tuple[Card, Column]]:
    return list(db.execute(
        select(Card, Column).join(Column, Column.id == Card.column_id)
        .where(Card.sprint_id == sprint_id, Card.is_archived.is_(False))
    ).all())


def sprint_totals(db: Session, sprint_id: int) -> dict:
    rows = sprint_cards(db, sprint_id)
    open_rows = [(c, col) for c, col in rows if not col.is_done]
    return {
        "total_cards": len(rows),
        "remaining_cards": len(open_rows),
        "total_points": sum(c.estimate or 0 for c, _ in rows),
        "remaining_points": sum(c.estimate or 0 for c, _ in open_rows),
    }


def record_snapshot(db: Session, sprint: Sprint, day: date | None = None):
    day = day or date.today()
    totals = sprint_totals(db, sprint.id)
    snap = db.scalar(select(SprintDay).where(SprintDay.sprint_id == sprint.id, SprintDay.day == day))
    if not snap:
        snap = SprintDay(sprint_id=sprint.id, day=day)
        db.add(snap)
    for k, v in totals.items():
        setattr(snap, k, v)


def active_sprint(db: Session, project_id: int) -> Sprint | None:
    return db.scalar(select(Sprint).where(Sprint.project_id == project_id, Sprint.status == "active"))


def burndown(sprint: Sprint, today: date | None = None) -> dict:
    """Ряды для графика: идеальная линия, фактический остаток (очки и карточки), объём спринта."""
    if not sprint.start_date:
        return {"days": []}
    today = today or date.today()
    end = sprint.end_date or sprint.start_date
    last_real = today
    if sprint.status == "completed" and sprint.completed_at:
        last_real = min(today, sprint.completed_at.date())
    span_end = max(end, last_real if sprint.status == "completed" else end)
    snaps = {s.day: s for s in sprint.days}
    first = sprint.days[0] if sprint.days else None
    start_points = first.total_points if first else 0
    start_cards = first.total_cards if first else 0
    total_days = max(1, (end - sprint.start_date).days)

    days, prev = [], first
    d = sprint.start_date
    while d <= span_end:
        if d in snaps:
            prev = snaps[d]
        real = d <= last_real and prev is not None
        k = min(1.0, (d - sprint.start_date).days / total_days)
        days.append({
            "date": d.isoformat(),
            "ideal_points": round(start_points * (1 - k), 2),
            "ideal_cards": round(start_cards * (1 - k), 2),
            "remaining_points": prev.remaining_points if real else None,
            "remaining_cards": prev.remaining_cards if real else None,
            "total_points": prev.total_points if real else None,
            "total_cards": prev.total_cards if real else None,
        })
        d += timedelta(days=1)
    return {"days": days, "start_points": start_points, "start_cards": start_cards}
