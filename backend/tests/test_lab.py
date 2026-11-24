"""Laboratório virtual: admissão, cota, capacidade, idempotência, fila e recuperação.

O agente do host é substituído por um falso em memória (FakeAgent), que imita
as respostas de labagent/agent.py, inclusive indisponibilidade e timeout.
"""
import threading
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import audit, config, db, lab, labvm_client
from app.errors import Conflict, Forbidden
from app.security import hash_password

ANA = {"username": "ana", "role": "admin"}
OTAVIO = {"username": "otavio", "role": "operador"}
LIA = {"username": "lia", "role": "leitor"}
IMG = "debian12-xfce-v1"


def op(n: int) -> str:
    return f"{n:032x}"


class FakeAgent:
    def __init__(self):
        self.vms = {}  # uuid -> dict
        self.calls = []
        self.down = False
        self.clone_timeout_once = False
        self.clone_error = None
        self.seq = 0
        self.auto_ready = True

    def _guard(self):
        if self.down:
            raise labvm_client.AgentUnavailable()

    def _vm(self, uuid, op_id=None):
        vm = self.vms.get(uuid)
        if vm is None:
            raise labvm_client.AgentError("VM não encontrada.", 404, "not_found")
        if op_id is not None and vm["op_id"] != op_id:
            raise labvm_client.AgentError("VM de outra operação.", 409, "op_mismatch")
        return vm

    def view(self, vm):
        return {k: v for k, v in vm.items() if k != "secret"}

    # mesmas assinaturas de labvm_client
    def inventory(self):
        self._guard()
        return {"vbox_version": "7.0.20", "host": {"cpu_percent": 10.0, "ram_total_mb": 16000,
                                                   "ram_available_mb": 9000, "disk_free_gb": 120}}

    def find_by_op(self, op_id):
        self._guard()
        self.calls.append(("find", op_id))
        return next((self.view(v) for v in self.vms.values() if v["op_id"] == op_id), None)

    def clone(self, op_id, name, image, owner):
        self._guard()
        self.calls.append(("clone", name))
        if self.clone_error:
            raise labvm_client.AgentError(self.clone_error, 502, "vbox")
        self.seq += 1
        uuid = f"33333333-3333-3333-3333-{self.seq:012d}"
        self.vms[uuid] = {"uuid": uuid, "name": name, "op_id": op_id, "state": "poweroff",
                          "macs": [f"08:00:27:00:00:{self.seq:02x}"], "guest_ready": False,
                          "vrde_listening": False, "ip": None, "measured": None}
        if self.clone_timeout_once:  # a VM foi criada, mas a resposta se perdeu
            self.clone_timeout_once = False
            raise labvm_client.AgentTimeout()
        return self.view(self.vms[uuid])

    def configure(self, uuid, body):
        self._guard()
        vm = self._vm(uuid, body["op_id"])
        self.calls.append(("configure", uuid, body["cpus"], body["memory_mb"]))
        assert len(body["vrde_password_sha256"]) == 64  # só o hash da senha RDP sai do Debian
        vm["configured"] = body
        return self.view(vm)

    def status(self, uuid):
        self._guard()
        vm = self.vms.get(uuid)
        return None if vm is None else self.view(vm)

    def start(self, uuid, op_id):
        self._guard()
        vm = self._vm(uuid, op_id)
        self.calls.append(("start", uuid))
        vm.update(state="running", vrde_listening=True, measured={"cpu_percent": 12.5, "ram_used_mb": 1500.0})
        if self.auto_ready:
            vm.update(guest_ready=True, ip="192.168.100.101")
        return self.view(vm)

    def stop(self, uuid, op_id, force=False):
        self._guard()
        vm = self._vm(uuid, op_id)
        self.calls.append(("stop", uuid, force))
        if force or vm.get("acpi_works", True):
            vm.update(state="poweroff", guest_ready=False, vrde_listening=False)
        return self.view(vm)

    def delete(self, uuid, op_id):
        self._guard()
        self._vm(uuid, op_id)
        self.calls.append(("delete", uuid))
        del self.vms[uuid]
        return {"uuid": uuid, "deleted": True}


@pytest.fixture
def fake(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "lab.db")
    monkeypatch.setattr(config, "LAB_MAX_VCPUS", 2)
    monkeypatch.setattr(config, "LAB_MAX_MEMORY_MB", 4096)
    monkeypatch.setattr(config, "LAB_MAX_INSTANCES", 3)
    monkeypatch.setattr(config, "LAB_QUOTA", {"operador": 1, "admin": 2})
    monkeypatch.setattr(lab, "_ip_from_dhcp", lambda mac: None)
    db.init_db()
    agent = FakeAgent()
    for name in ("inventory", "find_by_op", "clone", "configure", "status", "start", "stop", "delete"):
        monkeypatch.setattr(labvm_client, name, getattr(agent, name))
    lab._inventory_cache.update(at=0.0, data=None, error=None)
    return agent


def state(instance_id):
    with db.get_db() as conn:
        return conn.execute("SELECT * FROM lab_instances WHERE id = ?", (instance_id,)).fetchone()


def provision(user=OTAVIO, n=1, profile="p1"):
    inst, created = lab.request_instance(user, IMG, profile, op(n))
    assert created
    for _ in range(4):
        lab.process_once()
    return inst["id"]


# ------------------------------------------------------------------ admissão
def test_request_reserves_and_is_idempotent(fake):
    inst, created = lab.request_instance(OTAVIO, IMG, "p1", op(1))
    assert created and inst["state"] == "solicitada" and inst["holds_reservation"]
    assert "vrde_password" not in inst and "guest_password" not in inst
    again, created2 = lab.request_instance(OTAVIO, IMG, "p1", op(1))
    assert not created2 and again["id"] == inst["id"]
    assert lab.capacity()["assigned"]["reserved_cpus"] == 1
    with pytest.raises(Conflict):  # o mesmo op_id não pode virar instância de outro usuário
        lab.request_instance(ANA, IMG, "p1", op(1))


def test_quota_per_user(fake):
    lab.request_instance(OTAVIO, IMG, "p1", op(1))
    with pytest.raises(Conflict, match="Cota"):
        lab.request_instance(OTAVIO, IMG, "p1", op(2))
    denied = [e for e in audit.query(username="otavio") if e["result"] == "negado"]
    assert denied and "Cota" in denied[0]["detail"]


def test_profile_authorization(fake):
    with pytest.raises(Forbidden):
        lab.request_instance(OTAVIO, IMG, "p2", op(1))  # p2 só para admin
    with pytest.raises(Exception):
        lab.request_instance(OTAVIO, "windows-11", "p1", op(1))


def test_capacity_refused_when_full(fake):
    lab.request_instance(ANA, IMG, "p2", op(1))  # 2 vCPU / 4 GiB = tudo
    with pytest.raises(Conflict, match="Capacidade"):
        lab.request_instance(OTAVIO, IMG, "p1", op(2))


def test_concurrent_requests_never_exceed_capacity(fake, monkeypatch):
    monkeypatch.setattr(config, "LAB_MAX_INSTANCES", 50)
    users = [{"username": f"aluno{i}", "role": "operador"} for i in range(8)]
    results, barrier = [], threading.Barrier(len(users))

    def worker(i, user):
        barrier.wait()
        try:
            results.append(lab.request_instance(user, IMG, "p1", op(100 + i))[1])
        except Conflict:
            results.append("recusado")

    threads = [threading.Thread(target=worker, args=(i, u)) for i, u in enumerate(users)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results.count(True) == 2 and results.count("recusado") == 6  # 2 vCPU de capacidade
    assert lab.capacity()["assigned"]["reserved_cpus"] == 2


def test_concurrent_duplicate_op_creates_one(fake):
    out, barrier = [], threading.Barrier(5)

    def worker():
        barrier.wait()
        out.append(lab.request_instance(OTAVIO, IMG, "p1", op(7))[0]["id"])

    threads = [threading.Thread(target=worker) for _ in range(5)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(set(out)) == 1
    with db.get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM lab_instances").fetchone()[0] == 1


# ------------------------------------------------------------------ provisionamento
def test_full_flow_until_ready_and_access(fake):
    iid = provision()
    row = state(iid)
    assert row["state"] == "disponivel" and row["ready_at"] and row["ip"] == "192.168.100.101"
    assert [c[0] for c in fake.calls].count("clone") == 1
    assert ("configure", row["vbox_uuid"], 1, 2048) in fake.calls
    acc = lab.access(OTAVIO, iid)
    assert acc["port"] == 5001 and acc["rdp_password"] == row["vrde_password"]
    detail = lab.get_instance(OTAVIO, iid)
    assert [t["to_state"] for t in detail["transitions"]][:4] == ["solicitada", "em_criacao", "iniciando", "disponivel"]
    assert detail["seconds_to_ready"] is not None
    trail = [e for e in audit.query(resource="lab") if e["correlation_id"] == row["correlation_id"]]
    assert {"lab.request", "lab.disponivel", "lab.access"} <= {e["action"] for e in trail}


def test_ready_requires_guest_signal(fake):
    fake.auto_ready = False  # VM ligada e RDP respondendo, mas convidado ainda não personalizou
    inst, _ = lab.request_instance(OTAVIO, IMG, "p1", op(1))
    for _ in range(4):
        lab.process_once()
    assert state(inst["id"])["state"] == "iniciando"
    with pytest.raises(Conflict):
        lab.access(OTAVIO, inst["id"])  # dados de conexão só após a prontidão
    vm = next(iter(fake.vms.values()))
    vm.update(guest_ready=True, ip="192.168.100.102")
    lab.process_once()
    assert state(inst["id"])["state"] == "disponivel"


def test_other_user_cannot_see_or_access(fake):
    iid = provision()
    other = {"username": "bruno", "role": "operador"}
    for fn in (lab.get_instance, lab.access, lab.request_stop, lab.request_delete):
        with pytest.raises(Forbidden):
            fn(other, iid)
    with pytest.raises(Forbidden):
        lab.access(ANA, iid)  # nem o admin recebe a senha de outro usuário
    lab.get_instance(ANA, iid)  # mas pode ver e administrar
    assert [e for e in audit.query(username="bruno") if e["result"] == "negado"]


def test_clone_timeout_checks_host_before_retrying(fake):
    fake.clone_timeout_once = True
    inst, _ = lab.request_instance(OTAVIO, IMG, "p1", op(1))
    lab.process_once()
    row = state(inst["id"])
    assert row["state"] == "em_criacao" and "não confirmou" in row["error"]
    for _ in range(3):
        lab.process_once()
    assert state(inst["id"])["state"] == "disponivel"
    assert [c[0] for c in fake.calls].count("clone") == 1  # sem clone duplicado
    assert len(fake.vms) == 1


def test_agent_down_then_back(fake):
    fake.down = True
    inst, _ = lab.request_instance(OTAVIO, IMG, "p1", op(1))
    lab.process_once()
    lab.process_once()
    row = state(inst["id"])
    assert row["state"] == "em_criacao" and "indisponível" in row["error"]
    fake.down = False  # agente reiniciado: a tarefa continua de onde parou
    for _ in range(3):
        lab.process_once()
    assert state(inst["id"])["state"] == "disponivel"


def test_clone_error_fails_and_releases(fake):
    fake.clone_error = "disco cheio"
    inst, _ = lab.request_instance(OTAVIO, IMG, "p1", op(1))
    lab.process_once()
    row = state(inst["id"])
    assert row["state"] == "falha" and "disco cheio" in row["error"] and not row["cleanup_pending"]
    assert lab.capacity()["assigned"]["reserved_cpus"] == 0
    assert lab.quota_for(OTAVIO)["used"] == 0  # falha sem nada no host não ocupa a cota


def test_ready_timeout_removes_partial_clone(fake, monkeypatch):
    fake.auto_ready = False
    inst, _ = lab.request_instance(OTAVIO, IMG, "p1", op(1))
    for _ in range(3):
        lab.process_once()
    assert len(fake.vms) == 1
    later = datetime.now(timezone.utc) + timedelta(seconds=config.LAB_READY_TIMEOUT + 5)
    monkeypatch.setattr(lab, "_now", lambda: later)
    lab.process_once()
    row = state(inst["id"])
    assert row["state"] == "falha" and row["cleanup_pending"] == 0 and row["vbox_uuid"] is None
    assert fake.vms == {}  # só o clone desta operação foi removido


def test_backend_restart_reconciles(fake):
    inst, _ = lab.request_instance(OTAVIO, IMG, "p1", op(1))
    lab.process_once()  # clonou e configurou: está em iniciando
    assert state(inst["id"])["state"] == "iniciando"
    # "reinício": nada em memória; só o banco e o host. A próxima volta continua.
    lab.process_once()
    lab.process_once()
    assert state(inst["id"])["state"] == "disponivel"
    assert [c[0] for c in fake.calls].count("clone") == 1


# ------------------------------------------------------------------ ciclo de vida
def test_stop_releases_reservation_and_restart(fake):
    iid = provision()
    lab.request_stop(OTAVIO, iid)
    lab.process_once()  # envia ACPI
    lab.process_once()  # confere: desligada
    row = state(iid)
    assert row["state"] == "desligada" and row["vbox_uuid"]
    assert lab.capacity()["assigned"]["reserved_cpus"] == 0
    first_ready = row["ready_at"]
    lab.request_start(OTAVIO, iid)
    for _ in range(3):
        lab.process_once()
    row = state(iid)
    assert row["state"] == "disponivel" and row["ready_at"] == first_ready
    assert [c[0] for c in fake.calls].count("clone") == 1


def test_start_rechecks_capacity(fake):
    iid = provision()
    lab.request_stop(OTAVIO, iid)
    lab.process_once()
    lab.process_once()
    lab.request_instance(ANA, IMG, "p2", op(9))  # ocupou toda a capacidade
    with pytest.raises(Conflict, match="Capacidade"):
        lab.request_start(OTAVIO, iid)


def test_stop_forces_after_acpi_timeout(fake, monkeypatch):
    iid = provision()
    next(iter(fake.vms.values()))["acpi_works"] = False
    lab.request_stop(OTAVIO, iid)
    lab.process_once()
    assert state(iid)["state"] == "disponivel"
    later = datetime.now(timezone.utc) + timedelta(seconds=config.LAB_STOP_TIMEOUT + 5)
    monkeypatch.setattr(lab, "_now", lambda: later)
    lab.process_once()
    lab.process_once()
    assert state(iid)["state"] == "desligada"
    assert ("stop", state(iid)["vbox_uuid"], True) in fake.calls


def test_shutdown_inside_guest_is_detected(fake):
    iid = provision()
    next(iter(fake.vms.values()))["state"] = "poweroff"
    lab.process_once()
    row = state(iid)
    assert row["state"] == "desligada"
    assert "fora do console" in lab.get_instance(OTAVIO, iid)["transitions"][-1]["detail"]


def test_delete_removes_vm_and_secrets(fake):
    iid = provision()
    uuid = state(iid)["vbox_uuid"]
    lab.request_delete(OTAVIO, iid)
    lab.process_once()
    row = state(iid)
    assert row["state"] == "excluida" and row["vrde_password"] is None and row["vrde_port"] is None
    assert ("delete", uuid) in fake.calls and fake.vms == {}
    assert lab.quota_for(OTAVIO)["used"] == 0
    assert lab.list_instances(OTAVIO) == []


# ------------------------------------------------------------------ API e perfis
@pytest.fixture
def api(fake):
    from app.main import app

    with db.get_db() as conn:
        for name, role in (("ana", "admin"), ("otavio", "operador"), ("lia", "leitor"), ("bruno", "operador")):
            conn.execute("INSERT INTO users (username, password_hash, role) VALUES (?,?,?)",
                         (name, hash_password("senha-12345"), role))
    return TestClient(app)


H = {"X-Requested-With": "console"}


def login(client, user):
    client.cookies.clear()
    assert client.post("/api/auth/login", json={"username": user, "password": "senha-12345"}, headers=H).status_code == 200


def test_api_roles_and_ownership(api, fake):
    login(api, "lia")
    assert api.get("/api/lab/catalog").status_code == 200
    assert api.get("/api/lab/capacity").status_code == 200
    body = {"image_id": IMG, "profile_id": "p1", "op_id": op(1)}
    assert api.post("/api/lab/instances", json=body, headers=H).status_code == 403
    assert api.get("/api/lab/instances").status_code == 403

    login(api, "otavio")
    r = api.post("/api/lab/instances", json=body, headers=H)
    assert r.status_code == 200 and r.json()["created"]
    iid = r.json()["instance"]["id"]
    assert api.post("/api/lab/instances", json=body, headers=H).json()["created"] is False
    assert api.post("/api/lab/instances", json={**body, "op_id": op(2)}, headers=H).status_code == 409
    assert api.get(f"/api/lab/instances/{iid}/start").status_code == 405

    login(api, "bruno")
    assert api.get(f"/api/lab/instances/{iid}").status_code == 403
    assert api.post(f"/api/lab/instances/{iid}/delete", headers=H).status_code == 403
    assert "vrde_password" not in str(api.get("/api/lab/instances").json())

    login(api, "lia")
    assert api.get(f"/api/lab/instances/{iid}/start").status_code == 403
