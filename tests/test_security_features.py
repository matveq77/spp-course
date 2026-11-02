import asyncio

from app import config, totp
from app.events import Broker


def code_now(secret, shift=0):
    return totp.code_at(secret, totp.current_step() + shift)


def login(client, username, password="secret123"):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def enable_2fa(user):
    setup = user.post("/api/me/2fa/setup").json()
    assert setup["qr"].startswith("data:image/svg+xml;base64,")
    assert setup["otpauth_uri"].startswith("otpauth://totp/")
    r = user.post("/api/me/2fa/enable", json={"code": code_now(setup["secret"])})
    assert r.status_code == 200, r.text
    return setup["secret"], r.json()["backup_codes"]


# ===================== 2FA =====================

def test_totp_matches_rfc6238_vector():
    # тестовый вектор RFC 6238 (SHA1, секрет "12345678901234567890", T=59 -> 94287082, берём 6 последних цифр)
    import base64
    secret = base64.b32encode(b"12345678901234567890").decode()
    assert totp.code_at(secret, 59 // 30) == "287082"


def test_2fa_login_flow(make_user, client):
    make_user("root")
    bob = make_user("bob")
    secret, backup = enable_2fa(bob)
    assert len(backup) == config.BACKUP_CODES_COUNT

    r = login(client, "bob")
    assert r.status_code == 200 and r.json()["two_factor_required"] is True
    assert "access_token" not in r.json()
    challenge = r.json()["challenge_token"]

    assert client.post("/api/auth/login/2fa", json={"challenge_token": challenge, "code": "000000"}).status_code == 400
    # код текущего шага уже использован при включении -> повтор не принимается
    assert client.post("/api/auth/login/2fa", json={"challenge_token": challenge,
                                                    "code": code_now(secret)}).status_code == 400
    r = client.post("/api/auth/login/2fa", json={"challenge_token": challenge, "code": code_now(secret, 1)})
    assert r.status_code == 200, r.text
    assert "access_token" in r.json()


def test_backup_code_is_single_use(make_user, client):
    make_user("root")
    bob = make_user("bob")
    _, backup = enable_2fa(bob)
    for expected in (200, 400):
        ch = login(client, "bob").json()["challenge_token"]
        r = client.post("/api/auth/login/2fa", json={"challenge_token": ch, "code": backup[0].upper()})
        assert r.status_code == expected
    assert bob.get("/api/me/2fa").json()["backup_codes_left"] == config.BACKUP_CODES_COUNT - 1


def test_2fa_attempts_limited(make_user, client):
    make_user("root")
    bob = make_user("bob")
    enable_2fa(bob)
    ch = login(client, "bob").json()["challenge_token"]
    codes = [client.post("/api/auth/login/2fa", json={"challenge_token": ch, "code": "111111"}).status_code
             for _ in range(config.TWO_FACTOR_MAX_ATTEMPTS + 1)]
    assert codes[-1] == 429


def test_challenge_token_is_not_access_token(make_user, client):
    make_user("root")
    bob = make_user("bob")
    enable_2fa(bob)
    ch = login(client, "bob").json()["challenge_token"]
    assert client.get("/api/me", headers={"Authorization": f"Bearer {ch}"}).status_code == 401


def test_disable_2fa_and_admin_reset(make_user, client):
    admin = make_user("root")
    bob = make_user("bob")
    secret, backup = enable_2fa(bob)
    assert bob.post("/api/me/2fa/disable", json={"password": "wrong", "code": backup[0]}).status_code == 400
    assert bob.post("/api/me/2fa/disable", json={"password": "secret123", "code": backup[0]}).status_code == 200
    assert "access_token" in login(client, "bob").json()

    enable_2fa(bob)
    admin.patch(f"/api/admin/users/{bob.user['id']}", json={"reset_2fa": True})
    assert "access_token" in login(client, "bob").json()


# ===================== СЕССИИ =====================

def test_sessions_list_and_revoke(make_user, client):
    make_user("root")
    bob = make_user("bob")                       # сессия 1 (регистрация) — текущая
    other = login(client, "bob").json()          # сессия 2 (другое «устройство»)
    sessions = bob.get("/api/me/sessions").json()
    assert len(sessions) == 2
    assert sum(s["current"] for s in sessions) == 1 and sessions[0]["current"]

    target = next(s for s in sessions if not s["current"])
    assert bob.delete(f"/api/me/sessions/{target['id']}").status_code == 200
    # у отозванной сессии не работает ни refresh, ни старый access
    assert client.post("/api/auth/refresh", json={"refresh_token": other["refresh_token"]}).status_code == 401
    r = client.get("/api/me", headers={"Authorization": f"Bearer {other['access_token']}"})
    assert r.status_code == 401
    # текущая сессия продолжает работать (через тихий refresh)
    assert bob.get("/api/me").status_code == 200
    assert len(bob.get("/api/me/sessions").json()) == 1


def test_refresh_keeps_session_and_revoke_others(make_user, client):
    make_user("root")
    bob = make_user("bob")
    sid = bob.get("/api/auth/token-info").json()["session_id"]
    bob.refresh()
    assert bob.get("/api/auth/token-info").json()["session_id"] == sid
    login(client, "bob"), login(client, "bob")
    assert len(bob.get("/api/me/sessions").json()) == 3
    assert bob.post("/api/me/sessions/revoke-others").json()["revoked"] == 2
    assert [s["current"] for s in bob.get("/api/me/sessions").json()] == [True]


def test_password_change_ends_other_sessions(make_user, client):
    make_user("root")
    bob = make_user("bob")
    other = login(client, "bob").json()
    bob.post("/api/me/password", json={"old_password": "secret123", "new_password": "newpass123"})
    assert client.post("/api/auth/refresh", json={"refresh_token": other["refresh_token"]}).status_code == 401
    assert bob.get("/api/me").status_code == 200


# ===================== ВЛОЖЕНИЯ =====================

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100


def _project_with_card(alice, bob, carol=None):
    team = alice.post("/api/teams", json={"name": "T"}).json()
    alice.post(f"/api/teams/{team['id']}/members", json={"username": bob.user["username"], "role": "member"})
    if carol:
        alice.post(f"/api/teams/{team['id']}/members", json={"username": carol.user["username"], "role": "member"})
    p = alice.post(f"/api/teams/{team['id']}/projects", json={"name": "P", "key": "P"}).json()
    alice.post(f"/api/projects/{p['id']}/members", json={"username": bob.user["username"], "role": "member"})
    d = alice.get(f"/api/projects/{p['id']}").json()
    board = alice.get(f"/api/boards/{d['boards'][0]['id']}").json()
    card = alice.post("/api/cards", json={"column_id": board["columns"][0]["id"], "title": "c"}).json()
    return p, card


def test_attachments_upload_download_permissions(make_user):
    make_user("root")
    alice, bob, carol, eve = make_user("alice"), make_user("bob"), make_user("carol"), make_user("eve")
    _, card = _project_with_card(alice, bob, carol)  # carol — viewer, eve — посторонний

    r = bob.post(f"/api/cards/{card['id']}/attachments", files={"file": ("shot.png", PNG, "image/png")})
    assert r.status_code == 200, r.text
    att = r.json()
    assert att["is_image"] and att["size"] == len(PNG)

    # подделка: html с расширением .png не становится картинкой
    fake = bob.post(f"/api/cards/{card['id']}/attachments",
                    files={"file": ("x.png", b"<script>alert(1)</script>", "image/png")}).json()
    assert fake["is_image"] is False
    d = carol.get(f"/api/attachments/{fake['id']}")
    assert d.headers["content-type"] == "application/octet-stream"
    assert d.headers["content-disposition"].startswith("attachment")

    assert carol.get(f"/api/attachments/{att['id']}").content == PNG       # viewer может скачать
    assert eve.get(f"/api/attachments/{att['id']}").status_code == 403    # посторонний — нет
    assert carol.post(f"/api/cards/{card['id']}/attachments",
                      files={"file": ("a.txt", b"hi", "text/plain")}).status_code == 403
    assert alice.get(f"/api/cards/{card['id']}").json()["attachments_count"] == 2


def test_attachment_size_limit_and_delete_rules(make_user):
    make_user("root")
    alice, bob, carol = make_user("alice"), make_user("bob"), make_user("carol")
    p, card = _project_with_card(alice, bob, carol)
    alice.post(f"/api/projects/{p['id']}/members", json={"username": "carol", "role": "member"})

    big = b"x" * (config.MAX_UPLOAD_MB * 1024 * 1024 + 10)
    assert bob.post(f"/api/cards/{card['id']}/attachments", files={"file": ("big.bin", big)}).status_code == 413
    assert list(config.UPLOAD_DIR.glob("*")) == [] or all(f.stat().st_size < len(big) for f in config.UPLOAD_DIR.glob("*"))

    att = bob.post(f"/api/cards/{card['id']}/attachments", files={"file": ("a.txt", b"hello")}).json()
    files_before = set(config.UPLOAD_DIR.glob("*"))
    assert carol.delete(f"/api/attachments/{att['id']}").status_code == 403  # чужое, нет card:delete
    assert alice.delete(f"/api/attachments/{att['id']}").status_code == 200  # менеджер может
    assert len(set(config.UPLOAD_DIR.glob("*"))) == len(files_before) - 1   # файл удалён с диска


def test_card_delete_removes_files(make_user):
    make_user("root")
    alice, bob = make_user("alice"), make_user("bob")
    _, card = _project_with_card(alice, bob)
    before = len(list(config.UPLOAD_DIR.glob("*")))
    alice.post(f"/api/cards/{card['id']}/attachments", files={"file": ("a.txt", b"1")})
    alice.post(f"/api/cards/{card['id']}/attachments", files={"file": ("b.txt", b"2")})
    assert len(list(config.UPLOAD_DIR.glob("*"))) == before + 2
    alice.delete(f"/api/cards/{card['id']}")
    assert len(list(config.UPLOAD_DIR.glob("*"))) == before


# ===================== ПРИСУТСТВИЕ =====================

def test_presence_card_editing_and_seq():
    async def run():
        b = Broker()
        s1 = b.subscribe(user_id=1, name="A", is_admin=False, project_ids={1}, team_ids=set())
        s2 = b.subscribe(user_id=1, name="A", is_admin=False, project_ids={1}, team_ids=set())  # вторая вкладка
        s3 = b.subscribe(user_id=2, name="B", is_admin=False, project_ids={1}, team_ids=set())
        b.set_presence(s1.id, 1, 10, 1, card_id=None, seq=1)
        b.set_presence(s2.id, 1, 10, 1, card_id=5, editing="description", seq=1)
        b.set_presence(s3.id, 2, 10, 1, card_id=5, seq=2)
        b.set_presence(s3.id, 2, 10, 1, card_id=None, seq=1)  # запоздавший запрос игнорируется
        v = {x["id"]: x for x in b.viewers(10)}
        assert v[1]["card_id"] == 5 and v[1]["editing"] == "description"
        assert v[2]["card_id"] == 5 and v[2]["editing"] is None
        assert not b.set_presence(s3.id, 1, 10, 1)  # чужое соединение не подменить
        b.unsubscribe(s2.id)
        assert {x["id"]: x for x in b.viewers(10)}[1]["card_id"] is None
    asyncio.run(run())
