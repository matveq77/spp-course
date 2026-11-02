from datetime import date, datetime, timedelta

from app.models import Sprint, SprintDay
from app.sprints import burndown

TODAY = date.today()


def setup(alice, bob=None, viewer=None):
    team = alice.post("/api/teams", json={"name": "T"}).json()
    for u, _ in [(bob, "member"), (viewer, "viewer")]:
        if u:
            alice.post(f"/api/teams/{team['id']}/members", json={"username": u.user["username"], "role": "member"})
    p = alice.post(f"/api/teams/{team['id']}/projects", json={"name": "P", "key": "P"}).json()
    if bob:
        alice.post(f"/api/projects/{p['id']}/members", json={"username": bob.user["username"], "role": "member"})
    d = alice.get(f"/api/projects/{p['id']}").json()
    board = alice.get(f"/api/boards/{d['boards'][0]['id']}").json()
    cols = {c["name"]: c["id"] for c in board["columns"]}
    cards = [alice.post("/api/cards", json={"column_id": cols["К выполнению"], "title": f"t{i}", "estimate": est}).json()
             for i, est in enumerate([3, 5, 8])]
    return p, cols, cards


def last_point(detail):
    return [d for d in detail["burndown"]["days"] if d["remaining_points"] is not None][-1]


def test_sprint_lifecycle_and_burndown(make_user):
    make_user("root")
    alice = make_user("alice")
    p, cols, cards = setup(alice)
    s = alice.post(f"/api/projects/{p['id']}/sprints", json={"name": "Спринт 1"}).json()
    assert s["status"] == "planned"
    for c in cards:
        assert alice.patch(f"/api/cards/{c['id']}", json={"sprint_id": s["id"]}).status_code == 200

    r = alice.post(f"/api/sprints/{s['id']}/start", json={"end_date": (TODAY + timedelta(days=7)).isoformat()})
    assert r.status_code == 200, r.text
    d = alice.get(f"/api/sprints/{s['id']}").json()
    assert d["total_points"] == 16 and d["remaining_points"] == 16
    assert d["burndown"]["start_points"] == 16 and len(d["burndown"]["days"]) == 8
    assert d["burndown"]["days"][0]["ideal_points"] == 16 and d["burndown"]["days"][-1]["ideal_points"] == 0

    # задача завершена -> снимок за сегодня обновился автоматически
    alice.post(f"/api/cards/{cards[2]['id']}/move", json={"column_id": cols["Готово"], "index": 0})
    assert last_point(alice.get(f"/api/sprints/{s['id']}").json())["remaining_points"] == 8
    # добавили задачу в середине спринта -> растёт объём
    extra = alice.post("/api/cards", json={"column_id": cols["К выполнению"], "title": "x", "estimate": 2}).json()
    alice.patch(f"/api/cards/{extra['id']}", json={"sprint_id": s["id"]})
    pt = last_point(alice.get(f"/api/sprints/{s['id']}").json())
    assert pt["remaining_points"] == 10 and pt["total_points"] == 18
    # смена оценки тоже учитывается
    alice.patch(f"/api/cards/{extra['id']}", json={"estimate": 4})
    assert last_point(alice.get(f"/api/sprints/{s['id']}").json())["remaining_points"] == 12

    # завершение с переносом незакрытых в новый спринт
    r = alice.post(f"/api/sprints/{s['id']}/complete", json={"move_to": "new", "new_sprint_name": "Спринт 2"}).json()
    assert r["status"] == "completed"
    assert r["completed_cards"] == 1 and r["completed_points"] == 8 and r["carried_cards"] == 3
    nxt = alice.get(f"/api/sprints/{r['target_sprint_id']}").json()
    assert nxt["name"] == "Спринт 2" and nxt["status"] == "planned" and nxt["total_cards"] == 3
    done = alice.get(f"/api/sprints/{s['id']}").json()
    assert [c["id"] for c in done["cards"]] == [cards[2]["id"]]          # выполненная осталась в спринте
    assert last_point(done)["remaining_points"] == 12                    # история burndown сохранилась
    # в завершённый спринт задачи добавлять нельзя
    assert alice.patch(f"/api/cards/{extra['id']}", json={"sprint_id": s["id"]}).status_code == 409


def test_one_active_sprint_and_move_to_backlog(make_user):
    make_user("root")
    alice = make_user("alice")
    p, cols, cards = setup(alice)
    end = (TODAY + timedelta(days=5)).isoformat()
    s1 = alice.post(f"/api/projects/{p['id']}/sprints", json={"name": "A"}).json()
    s2 = alice.post(f"/api/projects/{p['id']}/sprints", json={"name": "B"}).json()
    alice.patch(f"/api/cards/{cards[0]['id']}", json={"sprint_id": s1["id"]})
    assert alice.post(f"/api/sprints/{s1['id']}/start", json={"end_date": end}).status_code == 200
    assert alice.post(f"/api/sprints/{s2['id']}/start", json={"end_date": end}).status_code == 409
    assert alice.delete(f"/api/sprints/{s1['id']}").status_code == 409
    assert alice.post(f"/api/sprints/{s1['id']}/start", json={"end_date": end}).status_code == 409
    assert alice.post(f"/api/sprints/{s1['id']}/start", json={"end_date": TODAY.isoformat()}).status_code == 409

    backlog_before = {c["id"] for c in alice.get(f"/api/projects/{p['id']}/backlog").json()}
    assert cards[0]["id"] not in backlog_before
    alice.post(f"/api/sprints/{s1['id']}/complete", json={"move_to": "backlog"})
    assert cards[0]["id"] in {c["id"] for c in alice.get(f"/api/projects/{p['id']}/backlog").json()}
    # теперь можно запустить второй (но не с датой окончания «сегодня»)
    assert alice.post(f"/api/sprints/{s2['id']}/start", json={"end_date": TODAY.isoformat()}).status_code == 422
    assert alice.post(f"/api/sprints/{s2['id']}/start", json={"end_date": end}).status_code == 200


def test_complete_into_existing_sprint_and_delete_planned(make_user):
    make_user("root")
    alice = make_user("alice")
    p, cols, cards = setup(alice)
    s1 = alice.post(f"/api/projects/{p['id']}/sprints", json={"name": "A"}).json()
    s2 = alice.post(f"/api/projects/{p['id']}/sprints", json={"name": "B"}).json()
    for c in cards:
        alice.patch(f"/api/cards/{c['id']}", json={"sprint_id": s1["id"]})
    alice.post(f"/api/sprints/{s1['id']}/start", json={"end_date": (TODAY + timedelta(days=3)).isoformat()})
    r = alice.post(f"/api/sprints/{s1['id']}/complete", json={"move_to": "sprint", "target_sprint_id": s1["id"]})
    assert r.status_code == 422  # нельзя перенести в сам себя / не в запланированный
    r = alice.post(f"/api/sprints/{s1['id']}/complete", json={"move_to": "sprint", "target_sprint_id": s2["id"]})
    assert r.json()["carried_cards"] == 3
    assert alice.get(f"/api/sprints/{s2['id']}").json()["total_cards"] == 3
    assert alice.delete(f"/api/sprints/{s2['id']}").status_code == 200
    assert len(alice.get(f"/api/projects/{p['id']}/backlog").json()) == 3


def test_sprint_permissions(make_user):
    make_user("root")
    alice, bob, olga = make_user("alice"), make_user("bob"), make_user("olga")
    p, cols, cards = setup(alice, bob, olga)
    # участник не управляет спринтами, но может планировать задачи
    assert bob.post(f"/api/projects/{p['id']}/sprints", json={"name": "X"}).status_code == 403
    s = alice.post(f"/api/projects/{p['id']}/sprints", json={"name": "S"}).json()
    assert bob.patch(f"/api/cards/{cards[0]['id']}", json={"sprint_id": s["id"]}).status_code == 200
    assert bob.post(f"/api/sprints/{s['id']}/start", json={"end_date": (TODAY + timedelta(days=3)).isoformat()}).status_code == 403
    # наблюдатель только смотрит
    assert olga.get(f"/api/sprints/{s['id']}").status_code == 200
    assert olga.patch(f"/api/cards/{cards[1]['id']}", json={"sprint_id": s["id"]}).status_code == 403
    # чужой спринт к карточке не привязать
    other_team = bob.post("/api/teams", json={"name": "Other"}).json()
    q = bob.post(f"/api/teams/{other_team['id']}/projects", json={"name": "Q", "key": "Q"}).json()
    foreign = bob.post(f"/api/projects/{q['id']}/sprints", json={"name": "F"})
    assert foreign.status_code == 200
    assert alice.patch(f"/api/cards/{cards[0]['id']}", json={"sprint_id": foreign.json()["id"]}).status_code == 422


def test_burndown_carries_values_over_quiet_days():
    start = TODAY - timedelta(days=3)
    s = Sprint(id=1, project_id=1, name="S", status="active", start_date=start, end_date=TODAY + timedelta(days=3))
    s.days = [
        SprintDay(day=start, total_points=12, remaining_points=12, total_cards=4, remaining_cards=4),
        SprintDay(day=start + timedelta(days=2), total_points=12, remaining_points=6, total_cards=4, remaining_cards=2),
    ]
    days = burndown(s)["days"]
    assert len(days) == 7
    assert [d["remaining_points"] for d in days] == [12, 12, 6, 6, None, None, None]  # день без изменений = вчера
    assert days[0]["ideal_points"] == 12 and days[-1]["ideal_points"] == 0 and days[3]["ideal_points"] == 6

    s.status, s.completed_at = "completed", datetime.combine(start + timedelta(days=2), datetime.min.time())
    days = burndown(s)["days"]
    assert [d["remaining_points"] for d in days][:4] == [12, 12, 6, None]  # после завершения — пусто
