import os
import sqlite3
import tempfile

import pytest
from fastapi.testclient import TestClient

# Banco temporário ANTES de importar o app
_tmp = tempfile.mkdtemp()
os.environ["CONSOLE_DB_PATH"] = os.path.join(_tmp, "test.db")

from app.main import app  # noqa: E402
from app.db import init_db, get_db  # noqa: E402
from app.security import hash_password  # noqa: E402
from app import config  # noqa: E402

H = {"X-Requested-With": "console"}


@pytest.fixture(scope="module")
def client():
    init_db()
    with get_db() as db:
        for name, role in (("ana", "admin"), ("otavio", "operador"), ("lia", "leitor")):
            db.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (?,?,?)",
                (name, hash_password("senha-12345"), role),
            )
    with TestClient(app) as c:
        yield c


def login(client, user, password="senha-12345"):
    client.cookies.clear()
    return client.post("/api/auth/login", json={"username": user, "password": password}, headers=H)


def test_password_is_argon2_hashed(client):
    con = sqlite3.connect(config.DB_PATH)
    (h,) = con.execute("SELECT password_hash FROM users WHERE username='ana'").fetchone()
    assert h.startswith("$argon2")
    assert "senha-12345" not in h


def test_login_invalid(client):
    assert login(client, "lia", "errada").status_code == 401
    assert login(client, "inexistente").status_code == 401


def test_unauthenticated_gets_401(client):
    client.cookies.clear()
    assert client.get("/api/metrics").status_code == 401
    assert client.get("/api/files/upload").status_code == 401


def test_login_and_me(client):
    r = login(client, "lia")
    assert r.status_code == 200 and r.json()["role"] == "leitor"
    assert client.get("/api/auth/me").json()["username"] == "lia"


def test_logout_invalidates_session(client):
    login(client, "lia")
    assert client.post("/api/auth/logout", headers=H).status_code == 200
    assert client.get("/api/auth/me").status_code == 401


# ---- Critério de aceitação do CP1 ----
@pytest.mark.parametrize(
    "path",
    ["/api/files/upload", "/api/email/send", "/api/ssh/hostname/change", "/api/ssh/hostname/restore"],
)
def test_leitor_write_routes_return_403(client, path):
    login(client, "lia")
    assert client.get(path).status_code == 403  # digitando a URL
    assert client.post(path, headers=H).status_code == 403


def test_leitor_can_read(client):
    login(client, "lia")
    for path in ("/api/metrics", "/api/services", "/api/files", "/api/email/inbox", "/api/logs"):
        assert client.get(path).status_code == 200


def test_operador_permissions(client):
    login(client, "otavio")
    assert client.get("/api/files/upload").status_code == 405  # autorizado, mas método errado
    assert client.post("/api/files/upload", headers=H).status_code == 501
    assert client.post("/api/ssh/hostname/change", headers=H).status_code == 403  # só admin


def test_admin_permissions(client):
    login(client, "ana")
    assert client.post("/api/ssh/hostname/change", headers=H).status_code == 501


def test_csrf_header_required_on_post(client):
    login(client, "otavio")
    assert client.post("/api/files/upload").status_code == 403
