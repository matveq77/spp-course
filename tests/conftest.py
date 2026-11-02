import os
import sys
import tempfile
from pathlib import Path

import pytest

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_tmp) / 'test.db'}"
os.environ["UPLOAD_DIR"] = str(Path(_tmp) / "uploads")
os.environ["MAX_UPLOAD_MB"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.security import reset_perm_cache  # noqa: E402


@pytest.fixture()
def client():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    reset_perm_cache()
    with TestClient(app) as c:
        yield c


class Api:
    """Маленькая обёртка: клиент от имени пользователя с авто-refresh токена."""

    def __init__(self, client: TestClient, username: str, password: str = "secret123"):
        self.c = client
        r = client.post("/api/auth/register", json={
            "username": username, "email": f"{username}@test.io", "full_name": username.title(),
            "password": password,
        })
        assert r.status_code == 200, r.text
        self._set(r.json())

    def _set(self, data):
        self.access, self.refresh_token, self.user = data["access_token"], data["refresh_token"], data["user"]

    def refresh(self):
        r = self.c.post("/api/auth/refresh", json={"refresh_token": self.refresh_token})
        assert r.status_code == 200, r.text
        self._set(r.json())

    def req(self, method, url, auto_refresh=True, **kw):
        r = self.c.request(method, url, headers={"Authorization": f"Bearer {self.access}"}, **kw)
        if auto_refresh and r.status_code == 401 and r.json().get("detail") == "token_outdated":
            self.refresh()
            r = self.c.request(method, url, headers={"Authorization": f"Bearer {self.access}"}, **kw)
        return r

    def get(self, url, **kw):
        return self.req("GET", url, **kw)

    def post(self, url, **kw):
        return self.req("POST", url, **kw)

    def patch(self, url, **kw):
        return self.req("PATCH", url, **kw)

    def put(self, url, **kw):
        return self.req("PUT", url, **kw)

    def delete(self, url, **kw):
        return self.req("DELETE", url, **kw)


@pytest.fixture()
def make_user(client):
    return lambda name: Api(client, name)
