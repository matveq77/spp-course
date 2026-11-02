"""
Realtime через Server-Sent Events (SSE).

Браузер открывает EventSource('/api/events?token=<JWT>') — одно долгоживущее HTTP-соединение,
по которому сервер пушит события. Обновлять страницу не нужно, WebSocket не нужен.
EventSource сам переподключается при обрыве.
"""
import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from .. import config
from ..db import get_db
from ..deps import Principal, get_principal, principal_from_token
from ..events import broker
from ..models import Board, Card
from ..permissions import P
from ..schemas import PresenceIn

router = APIRouter(prefix="/api", tags=["realtime"])


@router.get("/events")
async def events(request: Request, token: str, db: Session = Depends(get_db)):
    # EventSource не умеет ставить заголовки, поэтому JWT передаётся в query-параметре
    me = principal_from_token(token, db)
    db.close()
    sub = broker.subscribe(
        user_id=me.id, name=me.name, is_admin=me.is_admin,
        project_ids=me.project_ids(), team_ids=me.team_ids(),
    )

    async def stream():
        try:
            hello = {"type": "hello", "data": {"connection_id": sub.id, "user_id": me.id}}
            yield f"retry: 3000\ndata: {json.dumps(hello)}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(sub.queue.get(), timeout=config.SSE_KEEPALIVE_SEC)
                    yield f"data: {msg}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            broker.unsubscribe(sub.id)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive",
    })


@router.post("/presence")
def presence(body: PresenceIn, me: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Сообщает, какую доску/карточку сейчас смотрит пользователь и что редактирует."""
    project_id = None
    if body.board_id is not None:
        board = db.get(Board, body.board_id)
        if not board:
            raise HTTPException(404, "Доска не найдена")
        me.require_project(board.project_id, P.PROJECT_READ)
        project_id = board.project_id
        if body.card_id is not None:
            card = db.get(Card, body.card_id)
            if not card or card.board_id != board.id:
                raise HTTPException(404, "Карточка не найдена на этой доске")
    ok = broker.set_presence(body.connection_id, me.id, body.board_id, project_id,
                             card_id=body.card_id, editing=body.editing, seq=body.seq)
    return {"ok": ok}
