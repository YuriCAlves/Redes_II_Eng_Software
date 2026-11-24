"""RADIUS (incremento 4): validação, renderização, aplicação com rollback, logs e permissões.

O Debian é simulado (FakeRadiusSsh). A renderização também foi conferida contra um
FreeRADIUS 3.2 real durante o desenvolvimento (freeradius -XC + radclient).
"""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import audit, config, db, radius_client as rc, radius_hash
from app.errors import Conflict, InvalidInput
from app.security import hash_password

ADMIN = {"username": "admin1", "role": "admin"}
INITIAL = json.loads((Path(__file__).resolve().parents[2] / "deploy/freeradius/console-n2.json").read_text())


class FakeSftpFile:
    def __init__(self, store, path):
        self.store, self.path = store, path

    def write(self, data):
        self.store[self.path] = data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeSftp:
    def __init__(self, store):
        self.store = store

    def open(self, path, mode):
        return FakeSftpFile(self.store, path)

    def close(self):
        pass


class FakeRadiusSsh:
    def __init__(self, monkeypatch, state=None, check_ok=True, radclient_out=""):
        self.files = {config.RADIUS_STATE_PATH: json.dumps(state or INITIAL),
                      config.RADIUS_CLIENTS_PATH: "", config.RADIUS_USERS_PATH: ""}
        self.stage = {}
        self.check_ok = check_ok
        self.radclient_out = radclient_out
        self.commands, self.inputs = [], []
        self.auth_log, self.acct = "", ""
        monkeypatch.setattr(rc.ssh_client, "connect", lambda: self)
        monkeypatch.setattr(rc.ssh_client, "run", self.run)

    def open_sftp(self):
        return FakeSftp(self.stage)

    def close(self):
        pass

    def run(self, client, argv, timeout=15, input=None):
        self.commands.append(argv)
        self.inputs.append(input)
        if argv[:3] == ["sudo", "-n", "/usr/bin/cat"]:
            return 0, self.files[argv[3]], ""
        if argv[:3] == ["sudo", "-n", "/usr/bin/cp"]:
            self.files[argv[4]] = self.stage[argv[3]]
            return 0, "", ""
        if argv[:5] == ["sudo", "-n", "-u", config.RADIUS_SERVICE_USER, config.RADIUS_BIN]:
            if self.check_ok:
                return 0, "...\nConfiguration appears to be OK\n", ""
            return 1, ("x\n" * 50) + "/etc/freeradius/3.0/console-n2/users[3]: Parse error (check) for entry x\n", ""
        if argv[:4] == ["sudo", "-n", "/usr/bin/systemctl", "restart"]:
            return 0, "", ""
        if argv[:3] == ["sudo", "-n", config.RADIUS_READ_HELPER]:
            return 0, self.auth_log if argv[3] == "auth" else self.acct, ""
        if argv[0] == config.RADCLIENT_BIN:
            return 0, self.radclient_out, ""
        if argv[0] == "rm":
            return 0, "", ""
        raise AssertionError(f"comando inesperado: {argv}")

    def state(self):
        return json.loads(self.files[config.RADIUS_STATE_PATH])


@pytest.fixture
def tmpdb(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "r.db")
    db.init_db()


# ------------------------------------------------------------------ hashes
def test_sha512_crypt_matches_glibc_vector():
    # vetor gerado com `openssl passwd -6 -salt abcdefgh senha` (mesmo algoritmo da glibc)
    assert radius_hash.sha512_crypt("senha", "abcdefgh") == (
        "$6$abcdefgh$EoHSae/BSqeou8BlT.4KEm7chI9y28.qpTvSWLhLAT6gsJPg/RIo8g2yAwHy21n33pbq6JMTpC4VRXraWFInF1"
    )
    assert radius_hash.sha512_crypt("x", "s", rounds=12000).startswith("$6$rounds=12000$s$")


def test_ssha2_roundtrip_and_never_plaintext():
    attr, value = radius_hash.make("Senha-forte-1")
    assert attr == "SSHA2-256-Password" and "Senha" not in value
    assert radius_hash.verify_ssha2_256("Senha-forte-1", value)
    assert not radius_hash.verify_ssha2_256("outra", value)


# ------------------------------------------------------------------ validação
def test_client_validation():
    ok = rc.validate_client({"name": "ubuntu", "ipaddr": "192.168.100.20"}, [])
    assert ok["ipaddr"] == "192.168.100.20" and rc.SECRET_RE.match(ok["secret"])
    with pytest.raises(InvalidInput):
        rc.validate_client({"name": "ubuntu", "ipaddr": "10.0.2.15"}, [])  # fora da rede do laboratório
    with pytest.raises(InvalidInput):
        rc.validate_client({"name": "Ubuntu }", "ipaddr": "192.168.100.20"}, [])
    with pytest.raises(InvalidInput):
        rc.validate_client({"name": "a", "ipaddr": "192.168.100.20", "secret": "curto"}, [])
    with pytest.raises(Conflict):
        rc.validate_client({"name": "outro", "ipaddr": "192.168.100.20"}, [ok])


def test_policy_and_user_validation():
    assert rc.validate_policy({"name": "aula", "session_timeout": 1800})["idle_timeout"] == 0
    for bad in ({"name": "x", "session_timeout": 10}, {"name": "x", "session_timeout": 600, "reply_message": 'oi"'},
                {"name": "x", "session_timeout": 600, "reply_message": "%{exec:id}"}):
        with pytest.raises(InvalidInput):
            rc.validate_policy(bad)
    assert rc.validate_username("Maria") == "maria"  # normaliza para minúsculas
    for name in ("carga00001", "a b", "", "x\"y"):
        with pytest.raises(InvalidInput):
            rc.validate_username(name)
    with pytest.raises(InvalidInput):
        rc.validate_password("curta")


# ------------------------------------------------------------------ renderização
def test_render_users_and_clients():
    state = json.loads(json.dumps(INITIAL))
    state["clients"].append({"name": "ubuntu", "ipaddr": "192.168.100.20", "secret": "s" * 20, "description": ""})
    state["users"] += [
        rc._new_user("maria", "Senha-forte-1", "padrao", "ssha2-256", 5000),
        rc._new_user("joao", "Senha-forte-2", "visitante", "sha512-crypt", 5000),
        {**rc._new_user("ana", "Senha-forte-3", "padrao", "ssha2-256", 5000), "enabled": False},
        rc._new_user("bia", "Senha-forte-4", "suspenso", "ssha2-256", 5000),
    ]
    users = rc.render_users(state)
    assert '"maria"\tSSHA2-256-Password := "' in users and "Session-Timeout := 3600,\n\tIdle-Timeout := 600" in users
    assert '"joao"\tCrypt-Password := "$6$' in users and "Session-Timeout := 900" in users
    assert '"ana"\tAuth-Type := Reject' in users and '"bia"\tAuth-Type := Reject' in users
    assert "Senha-forte" not in users
    clients = rc.render_clients(state)
    assert "client ubuntu {\n\tipaddr = 192.168.100.20\n\tsecret = " + "s" * 20 in clients
    assert "require_message_authenticator = no" in clients  # padrão: pam_radius antigo não envia
    state["clients"][0]["require_ma"] = True
    assert "require_message_authenticator = yes" in rc.render_clients(state)


def test_check_state_rejects_tampered_file():
    state = json.loads(json.dumps(INITIAL))
    state["users"].append({**rc._new_user("maria", "Senha-forte-1", "padrao", "ssha2-256", 5000), "hash": 'x" Auth-Type := Accept'})
    with pytest.raises(rc.RadiusConfigError):
        rc.check_state(state)


# ------------------------------------------------------------------ aplicação
def test_create_client_and_user_applies_and_versions(tmpdb, monkeypatch):
    ssh = FakeRadiusSsh(monkeypatch)
    out = rc.create_client({"name": "ubuntu", "ipaddr": "192.168.100.20"}, ADMIN)
    assert out["clients"] == [{"name": "ubuntu", "ipaddr": "192.168.100.20", "description": "", "require_ma": False}]
    assert "secret" not in json.dumps(out)
    assert "client ubuntu" in ssh.files[config.RADIUS_CLIENTS_PATH]
    rc.create_user({"username": "maria", "password": "Senha-forte-1", "policy": "visitante"}, ADMIN)
    assert '"maria"' in ssh.files[config.RADIUS_USERS_PATH]
    assert [u["username"] for u in ssh.state()["users"]] == ["maria"]
    assert ["sudo", "-n", "/usr/bin/systemctl", "restart", "freeradius"] in ssh.commands
    version = audit.last_config_version("radius")
    assert version and json.loads(version["content"])["users"] == []  # versão ANTERIOR
    # arquivos de passagem (com segredos) removidos
    assert ["rm", "-f", f"{config.STAGE_DIR}/radius-users"] in ssh.commands


def test_rejected_config_is_rolled_back_without_restart(tmpdb, monkeypatch):
    ssh = FakeRadiusSsh(monkeypatch, check_ok=False)
    with pytest.raises(rc.RadiusValidationError, match="Parse error"):
        rc.create_user({"username": "maria", "password": "Senha-forte-1"}, ADMIN)
    assert ssh.state()["users"] == []  # a versão válida voltou
    assert '"maria"' not in ssh.files[config.RADIUS_USERS_PATH]
    assert not any(c[:4] == ["sudo", "-n", "/usr/bin/systemctl", "restart"] for c in ssh.commands)
    assert audit.last_config_version("radius") is None
    assert [e for e in audit.query(resource="radius") if e["result"] == "falha"]


def test_block_policy_and_restore(tmpdb, monkeypatch):
    ssh = FakeRadiusSsh(monkeypatch)
    rc.create_user({"username": "maria", "password": "Senha-forte-1"}, ADMIN)
    rc.update_user({"username": "maria", "enabled": False}, ADMIN)
    assert '"maria"\tAuth-Type := Reject' in ssh.files[config.RADIUS_USERS_PATH]
    rc.restore_last_version(ADMIN)
    assert ssh.state()["users"][0]["enabled"] is True
    with pytest.raises(Conflict):
        rc.delete_policy("padrao", ADMIN)  # em uso


def test_load_users_are_hidden_and_replaced(tmpdb, monkeypatch):
    ssh = FakeRadiusSsh(monkeypatch)
    out = rc.create_load_users({"count": 50, "scheme": "sha512-crypt"}, ADMIN)
    assert out["load_count"] == 50 and len(out["load_password"]) >= 12
    assert out["users"] == [] and out["load_users"] == {"count": 50, "schemes": ["sha512-crypt"]}
    rc.create_load_users({"count": 10}, ADMIN)  # nova base substitui a anterior
    assert len(ssh.state()["users"]) == 10
    rc.delete_load_users(ADMIN)
    assert ssh.state()["users"] == []


def test_self_test_keeps_password_out_of_argv(tmpdb, monkeypatch):
    monkeypatch.setattr(config, "RADIUS_TEST_SECRET", "segredo-local-123456")
    ssh = FakeRadiusSsh(monkeypatch, radclient_out="Packet summary:\n\tAccepted      : 1\n\tRejected      : 0\n")
    assert rc.self_test({"username": "maria", "password": 'Se"nha-1\\x'}, ADMIN)["result"] == "aceita"
    argv = next(c for c in ssh.commands if c[0] == config.RADCLIENT_BIN)
    assert "segredo-local-123456" not in " ".join(argv) and "Se" not in " ".join(argv)
    sent = ssh.inputs[ssh.commands.index(argv)]
    assert 'User-Password = "Se\\"nha-1\\\\x"' in sent
    assert ssh.stage[f"{config.STAGE_DIR}/radius-test.secret"].strip() == "segredo-local-123456"
    assert "Se\"nha" not in json.dumps(audit.query(resource="radius"))


# ------------------------------------------------------------------ logs
AUTH_LOG = """Tue Nov 24 16:38:09 2026 : Auth: (1) Login incorrect (pap: SSHA2-256 digest does not match "known good" digest): [maria] (from client ubuntu port 0 cli 192.168.100.20)
Tue Nov 24 16:38:10 2026 : Auth: (2) Login OK: [joao] (from client ubuntu port 22 cli 192.168.100.20)
Tue Nov 24 16:38:10 2026 : Auth: (3) Login incorrect (No Auth-Type found: rejecting the user via Post-Auth-Type = Reject): [ninguem] (from client ubuntu port 0)
Tue Nov 24 16:38:47 2026 : Info: Dropping packet without response because of error: Received packet from 192.168.100.20 with invalid Message-Authenticator!  (Shared secret is incorrect.) (from client ubuntu)
Tue Nov 24 16:39:00 2026 : Error: Ignoring request to auth address * port 1812 bound to server default from unknown client 192.168.100.77 port 40000 proto udp
"""

DETAIL = """Tue Nov 24 16:40:00 2026
\tAcct-Status-Type = Start
\tUser-Name = "joao"
\tAcct-Session-Id = "S1"
\tNAS-IP-Address = 192.168.100.20
\tCalling-Station-Id = "192.168.100.30"

Tue Nov 24 16:41:00 2026
\tAcct-Status-Type = Stop
\tUser-Name = "joao"
\tAcct-Session-Id = "S1"
\tNAS-IP-Address = 192.168.100.20
\tAcct-Session-Time = 60
\tAcct-Input-Octets = 1000
\tAcct-Output-Octets = 2000
\tAcct-Terminate-Cause = User-Request

Tue Nov 24 16:42:00 2026
\tAcct-Status-Type = Start
\tUser-Name = "maria"
\tAcct-Session-Id = "S2"
\tNAS-IP-Address = 192.168.100.20
"""


def test_parse_auth_log():
    events = rc.parse_auth_log(AUTH_LOG)
    results = [e["result"] for e in events][::-1]
    assert results == ["negada", "aceita", "usuario_desconhecido", "segredo_incorreto", "nas_desconhecido"]
    assert events[-1]["user"] == "maria" and events[-1]["calling_station"] == "192.168.100.20"
    assert events[0]["client"] == "192.168.100.77"


def test_sessions_from_detail(tmpdb, monkeypatch):
    ssh = FakeRadiusSsh(monkeypatch)
    ssh.acct = DETAIL
    out = rc.sessions()
    assert out["open"] == 1
    s1 = next(s for s in out["sessions"] if s["session_id"] == "S1")
    assert s1["state"] == "encerrada" and s1["duration_s"] == 60 and s1["output_octets"] == 2000
    assert s1["terminate_cause"] == "User-Request"


# ------------------------------------------------------------------ sudoers e API
def test_every_sudo_command_matches_sudoers_file(tmpdb, monkeypatch):
    from sudoers_rules import allowed_commands, sudo_commands

    ssh = FakeRadiusSsh(monkeypatch)
    rc.create_user({"username": "maria", "password": "Senha-forte-1"}, ADMIN)
    rc.auth_events()
    rc.sessions()
    allowed = allowed_commands()
    issued = sudo_commands(ssh.commands)
    assert issued and all(c in allowed for c in issued), set(issued) - allowed


H = {"X-Requested-With": "console"}


def test_api_permissions(tmpdb, monkeypatch):
    from app.main import app

    FakeRadiusSsh(monkeypatch)
    with db.get_db() as conn:
        for name, role in (("ana", "admin"), ("otavio", "operador"), ("lia", "leitor")):
            conn.execute("INSERT INTO users (username, password_hash, role) VALUES (?,?,?)",
                         (name, hash_password("senha-12345"), role))
    client = TestClient(app)

    def login(u):
        client.cookies.clear()
        assert client.post("/api/auth/login", json={"username": u, "password": "senha-12345"}, headers=H).status_code == 200

    login("lia")
    assert client.get("/api/radius/config").status_code == 200
    assert client.post("/api/radius/user", json={"username": "x1", "password": "Senha-forte-1"}, headers=H).status_code == 403
    assert client.get("/api/radius/user").status_code == 403
    login("otavio")
    assert client.post("/api/radius/client", json={"name": "u", "ipaddr": "192.168.100.20"}, headers=H).status_code == 403
    login("ana")
    r = client.post("/api/radius/client", json={"name": "ubuntu", "ipaddr": "192.168.100.20"}, headers=H)
    assert r.status_code == 200 and "secret" not in r.text
    secret = client.get("/api/radius/client/ubuntu/secret").json()["secret"]
    assert rc.SECRET_RE.match(secret)
    assert any(e["action"] == "client.secret.view" for e in audit.query(username="ana"))
    assert secret not in json.dumps(audit.query())
    bad = client.post("/api/radius/client", json={"name": "x", "ipaddr": "8.8.8.8"}, headers=H)
    assert bad.status_code == 400


def test_restore_replaces_unreadable_state(tmpdb, monkeypatch):
    ssh = FakeRadiusSsh(monkeypatch)
    rc.create_user({"username": "maria", "password": "Senha-forte-1"}, ADMIN)
    ssh.files[config.RADIUS_STATE_PATH] = '{"clients": "quebrado"}'  # editado à mão
    with pytest.raises(rc.RadiusConfigError):
        rc.create_user({"username": "joao", "password": "Senha-forte-2"}, ADMIN)
    rc.restore_last_version(ADMIN)
    assert ssh.state()["users"] == []  # volta à versão anterior registrada
