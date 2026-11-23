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
    assert client.get("/api/services").status_code == 401
    assert client.get("/api/http-latency").status_code == 401
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
    [
        "/api/files/upload",
        "/api/email/send",
        "/api/ssh/hostname/change",
        "/api/ssh/hostname/restore",
        "/api/dns/record",
        "/api/dns/record/delete",
        "/api/dns/fwd/restore",
        "/api/dhcp/reservation",
        "/api/dhcp/reservation/delete",
        "/api/dhcp/restore",
    ],
)
def test_leitor_write_routes_return_403(client, path):
    login(client, "lia")
    assert client.get(path).status_code == 403  # digitando a URL
    assert client.post(path, headers=H).status_code == 403


def test_leitor_can_read(client):
    login(client, "lia")
    for path in ("/api/metrics", "/api/services", "/api/http-latency"):
        assert client.get(path).status_code == 200, path
    # Rotas que dependem de serviços externos: o leitor É autorizado. A resposta pode ser
    # 502/503 se o serviço não existir neste ambiente de teste, mas nunca 401/403.
    for path in (
        "/api/files",
        "/api/email/accounts",
        "/api/email/messages?account=aluno@empresa.test",
        "/api/ssh/info",
        "/api/logs?service=ssh&lines=20",
        "/api/dns/fwd",
        "/api/dns/rev",
        "/api/dns/query/run?name=site.empresa.test&type=A",
        "/api/dhcp/config",
        "/api/dhcp/leases",
    ):
        assert client.get(path).status_code not in (401, 403), path
    assert client.get("/api/audit").status_code == 403  # leitor não consulta auditoria


def test_operador_permissions(client):
    login(client, "otavio")
    assert client.get("/api/files/upload").status_code == 405  # autorizado, mas método errado
    # autorizado: sem arquivo no corpo a resposta é 422 (validação), nunca 401/403
    assert client.post("/api/files/upload", headers=H).status_code not in (401, 403)
    assert client.post("/api/ssh/hostname/change", headers=H).status_code == 403  # só admin
    # DNS/DHCP: "alterar configurações de serviços" é exclusivo do admin (seção 7)
    assert client.post("/api/dns/record", headers=H).status_code == 403
    assert client.post("/api/dhcp/reservation", headers=H).status_code == 403
    assert client.get("/api/audit").status_code == 200  # operador vê só as próprias ações


def test_admin_permissions(client):
    login(client, "ana")
    assert client.post("/api/ssh/hostname/change", headers=H).status_code not in (401, 403)
    assert client.post("/api/dns/record", headers=H).status_code not in (401, 403)
    assert client.post("/api/dhcp/reservation", headers=H).status_code not in (401, 403)
    assert client.get("/api/audit").status_code == 200  # admin vê tudo


def test_csrf_header_required_on_post(client):
    login(client, "otavio")
    assert client.post("/api/files/upload").status_code == 403


def test_leitor_cannot_use_any_write_post_with_valid_body(client):
    login(client, "lia")
    assert client.post("/api/files/upload", files={"file": ("a.pdf", b"x")}, headers=H).status_code == 403
    body = {"account": "aluno@empresa.test", "to": "suporte@empresa.test", "subject": "a", "body": "b"}
    assert client.post("/api/email/send", json=body, headers=H).status_code == 403
    assert client.post("/api/ssh/hostname/change", json={"hostname": "novo"}, headers=H).status_code == 403
    assert client.post("/api/ssh/hostname/restore", headers=H).status_code == 403


def test_upload_rejects_bad_extension_and_name_before_ftp(client):
    login(client, "otavio")
    r = client.post("/api/files/upload", files={"file": ("virus.exe", b"abc")}, headers=H)
    assert r.status_code == 400 and r.json()["detail"]["code"] == "invalid_input"
    r = client.post("/api/files/upload", files={"file": ("../../etc/passwd.txt", b"abc")}, headers=H)
    assert r.status_code == 400


def test_email_send_rejects_unknown_account(client):
    login(client, "otavio")
    body = {"account": "root@empresa.test", "to": "suporte@empresa.test", "subject": "a", "body": "b"}
    assert client.post("/api/email/send", json=body, headers=H).status_code == 400


def test_admin_hostname_validation(client):
    login(client, "ana")
    r = client.post("/api/ssh/hostname/change", json={"hostname": "Invalido; reboot"}, headers=H)
    assert r.status_code == 400


def test_logs_reject_unknown_service_and_lines(client):
    login(client, "lia")
    assert client.get("/api/logs?service=mysql&lines=20").status_code == 400
    assert client.get("/api/logs?service=ssh&lines=7").status_code == 400
