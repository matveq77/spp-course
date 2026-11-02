import jwt

from app import config


def decode(token):
    return jwt.decode(token, config.JWT_SECRET, algorithms=[config.JWT_ALGORITHM])


def setup_project(owner, template="basic"):
    team = owner.post("/api/teams", json={"name": "Dev"}).json()
    project = owner.post(f"/api/teams/{team['id']}/projects",
                         json={"name": "Web", "key": "web", "template": template}).json()
    detail = owner.get(f"/api/projects/{project['id']}").json()
    board = owner.get(f"/api/boards/{detail['boards'][0]['id']}").json()
    return team, project, board


def test_first_user_is_admin_and_token_has_claims(make_user):
    admin = make_user("admin")
    bob = make_user("bob")
    assert decode(admin.access)["sys"] == ["sys:admin"]
    assert decode(bob.access)["sys"] == []
    assert decode(bob.access)["projects"] == {}


def test_permissions_are_embedded_in_jwt(make_user):
    make_user("root")
    alice = make_user("alice")
    team, project, _ = setup_project(alice)
    alice.refresh()
    claims = decode(alice.access)
    assert claims["teams"][str(team["id"])]["role"] == "owner"
    p = claims["projects"][str(project["id"])]
    assert p["role"] == "manager"
    assert "card:delete" in p["perms"]
    assert project["key"] == "WEB"


def test_old_token_rejected_after_role_change(make_user):
    make_user("root")
    alice, bob = make_user("alice"), make_user("bob")
    team, project, board = setup_project(alice)
    alice.post(f"/api/teams/{team['id']}/members", json={"username": "bob", "role": "member"})

    # старый токен bob'а (без команды) больше не принимается
    r = bob.get(f"/api/projects/{project['id']}", auto_refresh=False)
    assert r.status_code == 401 and r.json()["detail"] == "token_outdated"
    bob.refresh()
    claims = decode(bob.access)
    assert claims["projects"][str(project["id"])]["role"] == "viewer"  # visibility=team -> viewer
    assert bob.get(f"/api/projects/{project['id']}").status_code == 200


def test_viewer_cannot_create_cards_member_can(make_user):
    make_user("root")
    alice, bob = make_user("alice"), make_user("bob")
    team, project, board = setup_project(alice)
    alice.post(f"/api/teams/{team['id']}/members", json={"username": "bob", "role": "member"})
    col = board["columns"][0]["id"]

    r = bob.post("/api/cards", json={"column_id": col, "title": "x"})
    assert r.status_code == 403

    alice.post(f"/api/projects/{project['id']}/members", json={"username": "bob", "role": "member"})
    r = bob.post("/api/cards", json={"column_id": col, "title": "Bob's task"})
    assert r.status_code == 200, r.text
    assert r.json()["key"] == "WEB-1"
    # member не может удалять карточки
    assert bob.delete(f"/api/cards/{r.json()['id']}").status_code == 403
    assert alice.delete(f"/api/cards/{r.json()['id']}").status_code == 200


def test_private_project_hidden_from_team_members(make_user):
    make_user("root")
    alice, bob = make_user("alice"), make_user("bob")
    team = alice.post("/api/teams", json={"name": "T"}).json()
    alice.post(f"/api/teams/{team['id']}/members", json={"username": "bob", "role": "member"})
    p = alice.post(f"/api/teams/{team['id']}/projects",
                   json={"name": "Secret", "key": "SEC", "visibility": "private"}).json()
    assert bob.get(f"/api/projects/{p['id']}").status_code == 403
    assert all(x["id"] != p["id"] for x in bob.get("/api/projects").json())


def test_card_move_and_wip_limit(make_user):
    make_user("root")
    alice = make_user("alice")
    _, _, board = setup_project(alice)
    todo, doing = board["columns"][0]["id"], board["columns"][1]["id"]  # "В работе" WIP=3
    ids = [alice.post("/api/cards", json={"column_id": todo, "title": f"t{i}"}).json()["id"] for i in range(5)]
    for i in range(3):
        assert alice.post(f"/api/cards/{ids[i]}/move", json={"column_id": doing, "index": 0}).status_code == 200
    r = alice.post(f"/api/cards/{ids[3]}/move", json={"column_id": doing, "index": 0})
    assert r.status_code == 409
    b = alice.get(f"/api/boards/{board['id']}").json()
    assert [c["id"] for c in b["columns"][1]["cards"]] == [ids[2], ids[1], ids[0]]
    # перестановка внутри колонки
    alice.post(f"/api/cards/{ids[0]}/move", json={"column_id": doing, "index": 0})
    b = alice.get(f"/api/boards/{board['id']}").json()
    assert b["columns"][1]["cards"][0]["id"] == ids[0]


def test_assign_comment_mention_notifications(make_user):
    make_user("root")
    alice, bob = make_user("alice"), make_user("bob")
    team, project, board = setup_project(alice)
    alice.post(f"/api/teams/{team['id']}/members", json={"username": "bob", "role": "member"})
    card = alice.post("/api/cards", json={"column_id": board["columns"][0]["id"], "title": "Fix",
                                          "assignee_ids": [bob.user["id"]]}).json()
    alice.post(f"/api/cards/{card['id']}/comments", json={"text": "@bob глянь пожалуйста"})
    n = bob.get("/api/me/notifications").json()
    texts = [i["text"] for i in n["items"]]
    assert any("назначил" in t for t in texts)
    assert any("упомянул" in t for t in texts)
    assert n["unread"] >= 2
    assert len(bob.get("/api/me/cards").json()) == 1


def test_last_owner_protection(make_user):
    make_user("root")
    alice = make_user("alice")
    team = alice.post("/api/teams", json={"name": "T"}).json()
    r = alice.patch(f"/api/teams/{team['id']}/members/{alice.user['id']}", json={"role": "member"})
    assert r.status_code == 400


def test_blocked_user_loses_access(make_user):
    admin, bob = make_user("root"), make_user("bob")
    assert bob.get("/api/me").status_code == 200
    admin.patch(f"/api/admin/users/{bob.user['id']}", json={"is_active": False})
    assert bob.get("/api/me", auto_refresh=False).status_code == 401
    r = bob.c.post("/api/auth/refresh", json={"refresh_token": bob.refresh_token})
    assert r.status_code == 401


def test_non_admin_cannot_use_admin_api(make_user):
    make_user("root")
    bob = make_user("bob")
    assert bob.get("/api/admin/users").status_code == 403


def test_forged_token_rejected(make_user, client):
    make_user("root")
    bob = make_user("bob")
    claims = decode(bob.access)
    claims["sys"] = ["sys:admin"]
    forged = jwt.encode(claims, "wrong-secret-wrong-secret-wrong-secret", algorithm="HS256")
    r = client.get("/api/admin/users", headers={"Authorization": f"Bearer {forged}"})
    assert r.status_code == 401
