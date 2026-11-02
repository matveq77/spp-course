"""
In-memory брокер событий для Server-Sent Events.

Каждое открытое SSE-соединение = подписчик. События адресуются:
  * в проект (получают все, у кого проект есть в JWT),
  * в команду (участники команды),
  * конкретному пользователю (уведомления, "обнови токен"),
  * всем.
Фильтрация идёт по правам из того же JWT, с которым открыто соединение.
"""
import asyncio
import itertools
import json
import threading
from dataclasses import dataclass, field


@dataclass
class Subscriber:
    id: int
    user_id: int
    name: str
    is_admin: bool
    project_ids: set[int]
    team_ids: set[int]
    queue: asyncio.Queue
    board_id: int | None = None          # какую доску сейчас смотрит (presence)
    board_project_id: int | None = None
    card_id: int | None = None           # какую карточку открыл
    editing: str | None = None           # что редактирует в карточке ("description" | "title")
    presence_seq: int = -1


@dataclass
class Broker:
    loop: asyncio.AbstractEventLoop | None = None
    subs: dict[int, Subscriber] = field(default_factory=dict)
    _ids: itertools.count = field(default_factory=lambda: itertools.count(1))
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def subscribe(self, **kwargs) -> Subscriber:
        sub = Subscriber(id=next(self._ids), queue=asyncio.Queue(maxsize=1000), **kwargs)
        with self._lock:
            self.subs[sub.id] = sub
        return sub

    def unsubscribe(self, sub_id: int):
        with self._lock:
            sub = self.subs.pop(sub_id, None)
        if sub and sub.board_id:
            self._broadcast_presence(sub.board_id, sub.board_project_id)

    def _deliver(self, sub: Subscriber, msg: str):
        def put():
            try:
                sub.queue.put_nowait(msg)
            except asyncio.QueueFull:
                pass
        if self.loop is None:
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self.loop:
            put()
        else:
            self.loop.call_soon_threadsafe(put)

    def publish(self, type_: str, data: dict | None = None, *, project_id: int | None = None,
                team_id: int | None = None, user_ids=None, actor_id: int | None = None,
                broadcast: bool = False):
        event = {
            "type": type_, "data": data or {}, "project_id": project_id,
            "team_id": team_id, "actor_id": actor_id,
        }
        msg = json.dumps(event, default=str, ensure_ascii=False)
        user_ids = set(user_ids or [])
        with self._lock:
            targets = list(self.subs.values())
        for sub in targets:
            if broadcast:
                ok = True
            elif user_ids:
                ok = sub.user_id in user_ids
            elif project_id is not None:
                ok = sub.is_admin or project_id in sub.project_ids
            elif team_id is not None:
                ok = sub.is_admin or team_id in sub.team_ids
            else:
                ok = False
            if ok:
                self._deliver(sub, msg)

    # ---------- presence ----------

    def set_presence(self, sub_id: int, user_id: int, board_id: int | None, project_id: int | None,
                     card_id: int | None = None, editing: str | None = None, seq: int = 0):
        with self._lock:
            sub = self.subs.get(sub_id)
            if not sub or sub.user_id != user_id:
                return False
            if seq < sub.presence_seq:  # запоздавший запрос — более свежее состояние уже применено
                return True
            sub.presence_seq = seq
            old_board, old_project = sub.board_id, sub.board_project_id
            sub.board_id, sub.board_project_id = board_id, project_id
            sub.card_id = card_id if board_id else None
            sub.editing = editing if sub.card_id else None
        if old_board and old_board != board_id:
            self._broadcast_presence(old_board, old_project)
        if board_id:
            self._broadcast_presence(board_id, project_id)
        return True

    def viewers(self, board_id: int) -> list[dict]:
        """Кто смотрит доску; если у пользователя несколько вкладок — объединяем их состояние."""
        with self._lock:
            seen: dict[int, dict] = {}
            for s in self.subs.values():
                if s.board_id != board_id:
                    continue
                v = seen.setdefault(s.user_id, {"id": s.user_id, "name": s.name, "card_id": None, "editing": None})
                if s.card_id and (v["card_id"] is None or s.editing):
                    v["card_id"], v["editing"] = s.card_id, s.editing
        return list(seen.values())

    def _broadcast_presence(self, board_id: int, project_id: int | None):
        if project_id is None:
            return
        self.publish("presence", {"board_id": board_id, "users": self.viewers(board_id)},
                     project_id=project_id)


broker = Broker()
