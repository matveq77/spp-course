"""
Заполняет базу демо-данными. Работает через HTTP API приложения (TestClient),
поэтому заодно наполняется журнал активности и уведомления.

    python seed.py          # пересоздаёт kanban.db
"""
import random
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi.testclient import TestClient  # noqa: E402

from app.config import DATABASE_URL  # noqa: E402
from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402

random.seed(7)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
today = date.today()


class U:
    def __init__(self, c: TestClient, username, password, full_name):
        self.c = c
        r = c.post("/api/auth/register", json={
            "username": username, "email": f"{username}@kanban.local", "full_name": full_name, "password": password,
        })
        r.raise_for_status()
        self.id = r.json()["user"]["id"]
        self.username, self.password = username, password
        self.login()

    def login(self):
        r = self.c.post("/api/auth/login", json={"username": self.username, "password": self.password})
        r.raise_for_status()
        self.token = r.json()["access_token"]

    def __call__(self, method, url, **kw):
        r = self.c.request(method, url, headers={"Authorization": f"Bearer {self.token}"}, **kw)
        if r.status_code == 401:  # права поменялись -> перелогин
            self.login()
            r = self.c.request(method, url, headers={"Authorization": f"Bearer {self.token}"}, **kw)
        if r.status_code >= 400:
            raise RuntimeError(f"{method} {url}: {r.status_code} {r.text}")
        return r.json()


def main():
    if DATABASE_URL.startswith("sqlite"):
        Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    with TestClient(app) as c:
        admin = U(c, "admin", "admin123", "Администратор")  # первый пользователь -> системный админ
        anna = U(c, "anna", "password", "Анна Смирнова")
        ivan = U(c, "ivan", "password", "Иван Петров")
        olga = U(c, "olga", "password", "Ольга Кузнецова")
        petr = U(c, "petr", "password", "Пётр Волков")
        maria = U(c, "maria", "password", "Мария Соколова")

        # ---------- команда разработки ----------
        dev = anna("POST", "/api/teams", json={"name": "Команда разработки", "description": "Веб и мобильная разработка"})
        for u, role in [(petr, "admin"), (ivan, "member"), (olga, "member"), (maria, "member")]:
            anna("POST", f"/api/teams/{dev['id']}/members", json={"username": u.username, "role": role})

        web = anna("POST", f"/api/teams/{dev['id']}/projects", json={
            "name": "Веб-портал", "key": "WEB", "description": "Клиентский портал на FastAPI + Vue",
            "visibility": "team", "template": "scrum"})
        anna("POST", f"/api/projects/{web['id']}/members", json={"username": "ivan", "role": "member"})
        anna("POST", f"/api/projects/{web['id']}/members", json={"username": "maria", "role": "member"})
        # olga остаётся наблюдателем (наследует от команды)

        wp = anna("GET", f"/api/projects/{web['id']}")
        labels = {l["name"]: l["id"] for l in wp["labels"]}
        board = anna("GET", f"/api/boards/{wp['boards'][0]['id']}")
        cols = {c_["name"]: c_["id"] for c_ in board["columns"]}

        tasks = [
            ("Бэклог", "Экспорт задач в CSV", "low", None, [], ["Фича"], 3),
            ("Бэклог", "Тёмная тема интерфейса", "low", None, [], ["Улучшение"], 5),
            ("Бэклог", "Интеграция с почтой", "medium", 20, [], ["Фича"], 8),
            ("К выполнению", "Страница восстановления пароля", "high", 4, [ivan], ["Фича"], 3),
            ("К выполнению", "Описать API в README", "medium", 6, [maria], ["Документация"], 2),
            ("К выполнению", "Ошибка 500 при пустом поиске", "critical", -1, [ivan], ["Баг", "Срочно"], 1),
            ("В работе", "JWT с правами доступа", "high", 2, [anna, ivan], ["Фича"], 5),
            ("В работе", "Realtime-обновления через SSE", "high", 3, [ivan], ["Фича"], 5),
            ("На ревью", "Drag-and-drop карточек", "medium", 1, [maria], ["Фича"], 3),
            ("Тестирование", "Валидация форм регистрации", "medium", -2, [petr], ["Баг"], 2),
            ("Готово", "Модель данных и миграции", "high", -5, [anna], ["Фича"], 5),
            ("Готово", "Настройка CI", "low", -7, [petr], ["Улучшение"], 2),
        ]
        card_ids = []
        for col, title, prio, due, assignees, lbls, est in tasks:
            card = anna("POST", "/api/cards", json={
                "column_id": cols[col], "title": title, "priority": prio,
                "due_date": (today + timedelta(days=due)).isoformat() if due is not None else None,
                "assignee_ids": [u.id for u in assignees], "label_ids": [labels[x] for x in lbls],
                "estimate": est,
                "description": f"Задача «{title}».\n\nКритерии готовности:\n- реализовано\n- покрыто тестами\n- проверено на ревью",
            })
            card_ids.append(card["id"])

        jwt_card = card_ids[6]
        for t in ["Собрать роли в права", "Зашить права в payload", "Версия прав (pv) для отзыва", "Авто-refresh на клиенте"]:
            anna("POST", f"/api/cards/{jwt_card}/checklist", json={"text": t})
        items = anna("GET", f"/api/cards/{jwt_card}")["checklist_items"]
        for it in items[:2]:
            ivan("PATCH", f"/api/checklist/{it['id']}", json={"done": True})
        ivan("POST", f"/api/cards/{jwt_card}/comments", json={"text": "Права раскладываю по проектам, @anna глянь структуру payload."})
        anna("POST", f"/api/cards/{jwt_card}/comments", json={"text": "Выглядит хорошо 👍 Не забудь про инвалидацию при смене роли."})
        olga("POST", f"/api/cards/{card_ids[5]}/comments", json={"text": "Воспроизводится на проде, @ivan посмотри пожалуйста."})
        ivan("POST", f"/api/cards/{card_ids[8]}/move", json={"column_id": cols["Тестирование"], "index": 0})
        maria("POST", f"/api/cards/{card_ids[4]}/move", json={"column_id": cols["В работе"], "index": 0})

        # ---------- спринты ----------
        sp1 = anna("POST", f"/api/projects/{web['id']}/sprints", json={
            "name": "Спринт 1", "goal": "Авторизация с правами в JWT и realtime-доска"})
        sp2 = anna("POST", f"/api/projects/{web['id']}/sprints", json={
            "name": "Спринт 2", "goal": "Интеграции и полировка интерфейса"})
        in_sprint = [cid for cid, t in zip(card_ids, tasks) if t[0] != "Бэклог"]
        for cid in in_sprint:
            anna("PATCH", f"/api/cards/{cid}", json={"sprint_id": sp1["id"]})
        anna("PATCH", f"/api/cards/{card_ids[2]}", json={"sprint_id": sp2["id"]})
        anna("POST", f"/api/sprints/{sp1['id']}/start", json={"end_date": (today + timedelta(days=8)).isoformat()})

        # вторая доска проекта
        b2 = anna("POST", f"/api/projects/{web['id']}/boards", json={"name": "Баги", "color": "#ef4444"})
        b2d = anna("GET", f"/api/boards/{b2['id']}")
        for title in ["Не грузится аватар", "Съезжает вёрстка на мобильных", "Двойная отправка формы"]:
            ivan("POST", "/api/cards", json={"column_id": b2d["columns"][0]["id"], "title": title,
                                             "label_ids": [labels["Баг"]], "priority": random.choice(["medium", "high"])})

        # приватный проект
        mob = anna("POST", f"/api/teams/{dev['id']}/projects", json={
            "name": "Мобильное приложение", "key": "MOB", "visibility": "private", "template": "basic",
            "description": "Закрытый проект — видят только приглашённые"})
        anna("POST", f"/api/projects/{mob['id']}/members", json={"username": "maria", "role": "manager"})
        mp = anna("GET", f"/api/projects/{mob['id']}")
        mb = anna("GET", f"/api/boards/{mp['boards'][0]['id']}")
        for t in ["Экран входа", "Push-уведомления", "Офлайн-режим"]:
            maria("POST", "/api/cards", json={"column_id": mb["columns"][0]["id"], "title": t, "assignee_ids": [maria.id]})

        # ---------- команда маркетинга ----------
        mkt = olga("POST", "/api/teams", json={"name": "Маркетинг", "description": "Продвижение и контент"})
        olga("POST", f"/api/teams/{mkt['id']}/members", json={"username": "anna", "role": "member"})
        launch = olga("POST", f"/api/teams/{mkt['id']}/projects", json={
            "name": "Запуск продукта", "key": "MKT", "template": "basic"})
        olga("POST", f"/api/projects/{launch['id']}/members", json={"username": "anna", "role": "member"})
        lp = olga("GET", f"/api/projects/{launch['id']}")
        lb = olga("GET", f"/api/boards/{lp['boards'][0]['id']}")
        for t, due in [("Лендинг", 5), ("Пост в соцсетях", 2), ("Пресс-релиз", 9)]:
            olga("POST", "/api/cards", json={"column_id": lb["columns"][0]["id"], "title": t,
                                             "due_date": (today + timedelta(days=due)).isoformat(),
                                             "assignee_ids": [anna.id]})

    # Демо-история для burndown: спринт «начат» 6 дней назад, по дням постепенно закрывались задачи.
    # (В реальной работе эти снимки пишутся автоматически при каждом изменении карточек.)
    from app.db import SessionLocal
    from app.models import Sprint, SprintDay
    with SessionLocal() as db:
        sprint = db.query(Sprint).filter_by(name="Спринт 1", status="active").one()
        now = db.query(SprintDay).filter_by(sprint_id=sprint.id).one()
        sprint.start_date = today - timedelta(days=6)
        start_points, start_cards = now.total_points + 3, now.total_cards + 1  # одну задачу (3 SP) вынесли из спринта
        for i in range(6):
            k = i / 6
            db.add(SprintDay(
                sprint_id=sprint.id, day=sprint.start_date + timedelta(days=i),
                total_points=start_points if i < 3 else now.total_points,
                total_cards=start_cards if i < 3 else now.total_cards,
                remaining_points=round(start_points - (start_points - now.remaining_points) * k * 0.9),
                remaining_cards=round(start_cards - (start_cards - now.remaining_cards) * k * 0.9),
            ))
        db.commit()

    print("Готово! Аккаунты:")
    print("  admin / admin123  — системный администратор")
    print("  anna  / password  — владелец «Команды разработки», менеджер проектов")
    print("  petr  / password  — администратор команды (авто-менеджер её проектов)")
    print("  ivan  / password  — участник команды, member в WEB")
    print("  maria / password  — member в WEB, менеджер приватного MOB")
    print("  olga  / password  — наблюдатель в WEB, владелец «Маркетинга»")


if __name__ == "__main__":
    main()
