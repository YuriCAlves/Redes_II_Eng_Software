"""Testes do agente com um VirtualBox falso em memória (nenhum VBoxManage real)."""
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agent  # noqa: E402
import vbox as vbox_mod  # noqa: E402
from settings import Image, Settings  # noqa: E402

TOKEN = "t" * 32
AUTH = {"Authorization": f"Bearer {TOKEN}"}
BASE_UUID = "11111111-1111-1111-1111-111111111111"
OTHER_UUID = "22222222-2222-2222-2222-222222222222"
OP = "a" * 32
OP2 = "b" * 32


class FakeVBox:
    """Imita os métodos de vbox.VBox guardando o estado em dicionários."""

    def __init__(self):
        self.vms = {
            BASE_UUID: {"name": "BASE-LINUX-v1", "VMState": "poweroff", "cpus": "1", "memory": "2048"},
            OTHER_UUID: {"name": "Debian", "VMState": "running", "cpus": "2", "memory": "2048"},
        }
        self.extra = {}
        self.props = {}
        self.calls = []
        self.vrde_lib = "VBoxAuthSimple"
        self.seq = 0

    def _resolve(self, vm):
        if vm in self.vms:
            return vm
        for uuid, info in self.vms.items():
            if info["name"] == vm:
                return uuid
        raise vbox_mod.VBoxError("VM não encontrada")

    def version(self):
        return "7.0.20r163906"

    def list_vms(self, running=False):
        return [{"name": v["name"], "uuid": u} for u, v in self.vms.items()
                if not running or v["VMState"] == "running"]

    def info(self, vm):
        uuid = self._resolve(vm)
        return {**self.vms[uuid], "UUID": uuid}

    def get_extradata(self, vm, key):
        return self.extra.get((self._resolve(vm), key))

    def set_extradata(self, vm, key, value):
        self.extra[(self._resolve(vm), key)] = value

    def get_guestproperty(self, vm, prop):
        return self.props.get((self._resolve(vm), prop))

    def set_guestproperty(self, vm, prop, value, flags=None):
        self.props[(self._resolve(vm), prop)] = value

    def unset_guestproperty(self, vm, prop):
        self.props.pop((self._resolve(vm), prop), None)

    def system_properties(self):
        return {"Default machine folder": "", "VRDE auth library": self.vrde_lib}

    def extpacks(self):
        return "Pack no. 0:   Oracle VirtualBox Extension Pack"

    def metrics(self, vm):
        return {"cpu_percent": 3.0, "ram_used_mb": 900.0}

    def clone_linked(self, base, snapshot, name, timeout):
        self.calls.append(("clone", base, snapshot, name))
        self.seq += 1
        uuid = f"33333333-3333-3333-3333-{self.seq:012d}"
        self.vms[uuid] = {"name": name, "VMState": "poweroff", "cpus": "1", "memory": "2048",
                          "macaddress1": f"0800270000{self.seq:02d}", "nic1": "intnet"}

    def modify(self, vm, *, cpus, memory_mb, vrde_port, vrde_address):
        uuid = self._resolve(vm)
        self.vms[uuid].update(cpus=str(cpus), memory=str(memory_mb), vrdeport=str(vrde_port))

    def metrics_setup(self, vm):
        pass

    def start_headless(self, vm):
        self.calls.append(("start", vm))
        self.vms[self._resolve(vm)]["VMState"] = "running"

    def acpi_shutdown(self, vm):
        self.calls.append(("acpi", vm))
        self.vms[self._resolve(vm)]["VMState"] = "poweroff"

    def poweroff(self, vm):
        self.calls.append(("poweroff", vm))
        self.vms[self._resolve(vm)]["VMState"] = "poweroff"

    def unregister_delete(self, vm):
        self.calls.append(("delete", vm))
        del self.vms[self._resolve(vm)]


@pytest.fixture
def env(monkeypatch):
    fake = FakeVBox()
    cfg = Settings(
        token=TOKEN,
        allowed_sources=["testclient"],
        images={"debian12-xfce-v1": Image(base_vm="BASE-LINUX-v1", snapshot="v1", version="1")},
    )
    app = agent.create_app(cfg, fake)
    return TestClient(app), fake


def clone(client, op=OP, name="LAB-ana-aaaaaaaa"):
    return client.post("/v1/instances", headers=AUTH,
                       json={"op_id": op, "name": name, "image": "debian12-xfce-v1", "owner": "ana"})


CONF = {"op_id": OP, "cpus": 1, "memory_mb": 2048, "vrde_port": 5001, "vrde_user": "ana",
        "vrde_password_sha256": "c" * 64, "hostname": "lab-ana-aaaaaaaa", "guest_password": "senhaGuest-123"}


# ------------------------------------------------------------------ autenticação
def test_requires_token(env):
    client, _ = env
    assert client.get("/v1/inventory").status_code == 401
    assert client.get("/v1/inventory", headers={"Authorization": "Bearer errado"}).status_code == 401
    assert client.get("/v1/inventory", headers=AUTH).status_code == 200


def test_rejects_other_origin(monkeypatch):
    cfg = Settings(token=TOKEN, allowed_sources=["192.168.56.10"])
    client = TestClient(agent.create_app(cfg, FakeVBox()))
    assert client.get("/v1/health", headers=AUTH).status_code == 403


# ------------------------------------------------------------------ clonar
def test_clone_marks_vm_and_is_idempotent(env):
    client, fake = env
    r1 = clone(client)
    assert r1.status_code == 200, r1.text
    uuid = r1.json()["uuid"]
    assert fake.extra[(uuid, vbox_mod.MANAGED_KEY)] == "1"
    assert fake.extra[(uuid, vbox_mod.OP_KEY)] == OP
    assert r1.json()["image"] == "debian12-xfce-v1@1"
    r2 = clone(client)  # repetição: mesma VM, nenhum clone novo
    assert r2.json()["uuid"] == uuid
    assert [c for c in fake.calls if c[0] == "clone"] == [("clone", "BASE-LINUX-v1", "v1", "LAB-ana-aaaaaaaa")]


def test_clone_rejects_unknown_image_and_bad_name(env):
    client, _ = env
    bad = client.post("/v1/instances", headers=AUTH,
                      json={"op_id": OP, "name": "LAB-x", "image": "windows", "owner": "ana"})
    assert bad.status_code == 400
    for name in ("BASE-LINUX-v1", "LAB-x; rm -rf /", "lab-minusculo", "../LAB-x"):
        assert clone(client, name=name).status_code == 422


def test_clone_name_taken_by_other_op(env):
    client, _ = env
    clone(client)
    assert clone(client, op=OP2).status_code == 409


# ------------------------------------------------------------------ proteção
def test_never_touches_base_or_unmanaged_vms(env):
    client, fake = env
    for uuid in (BASE_UUID, OTHER_UUID):
        for path in ("start", "stop", "delete"):
            r = client.post(f"/v1/instances/{uuid}/{path}", headers=AUTH, json={"op_id": OP})
            assert r.status_code == 403, (uuid, path, r.text)
    assert BASE_UUID in fake.vms and OTHER_UUID in fake.vms
    assert not [c for c in fake.calls if c[0] in ("start", "poweroff", "delete", "acpi")]


def test_delete_requires_matching_op(env):
    client, fake = env
    uuid = clone(client).json()["uuid"]
    assert client.post(f"/v1/instances/{uuid}/delete", headers=AUTH, json={"op_id": OP2}).status_code == 409
    assert uuid in fake.vms
    assert client.post(f"/v1/instances/{uuid}/delete", headers=AUTH, json={"op_id": OP}).status_code == 200
    assert uuid not in fake.vms


def test_invalid_uuid_is_rejected(env):
    client, _ = env
    assert client.get("/v1/instances/not-a-uuid", headers=AUTH).status_code == 400


# ------------------------------------------------------------------ configurar e iniciar
def test_configure_sets_resources_vrde_and_personalization(env):
    client, fake = env
    uuid = clone(client).json()["uuid"]
    r = client.post(f"/v1/instances/{uuid}/configure", headers=AUTH, json=CONF)
    assert r.status_code == 200, r.text
    assert r.json()["cpus"] == 1 and r.json()["vrde_port"] == 5001
    assert fake.extra[(uuid, "VBoxAuthSimple/users/ana")] == "c" * 64
    assert fake.props[(uuid, vbox_mod.PROP_HOSTNAME)] == "lab-ana-aaaaaaaa"
    assert fake.props[(uuid, vbox_mod.PROP_INSTANCE)] == OP


def test_configure_limits_and_vrde_library(env):
    client, fake = env
    uuid = clone(client).json()["uuid"]
    assert client.post(f"/v1/instances/{uuid}/configure", headers=AUTH,
                       json={**CONF, "cpus": 4}).status_code == 400
    assert client.post(f"/v1/instances/{uuid}/configure", headers=AUTH,
                       json={**CONF, "vrde_port": 6000}).status_code == 400
    fake.vrde_lib = "VBoxAuth"
    assert client.post(f"/v1/instances/{uuid}/configure", headers=AUTH, json=CONF).status_code == 412


def test_ready_only_after_guest_signals_and_password_is_cleared(env, monkeypatch):
    client, fake = env
    uuid = clone(client).json()["uuid"]
    client.post(f"/v1/instances/{uuid}/configure", headers=AUTH, json=CONF)
    monkeypatch.setattr(agent.socket, "create_connection", lambda *a, **k: _Conn())
    r = client.post(f"/v1/instances/{uuid}/start", headers=AUTH, json={"op_id": OP})
    assert r.json()["state"] == "running" and r.json()["guest_ready"] is False  # ligada != pronta
    fake.props[(uuid, vbox_mod.PROP_READY)] = OP  # o script do convidado sinaliza
    fake.props[(uuid, vbox_mod.PROP_IP)] = "192.168.100.101"
    s = client.get(f"/v1/instances/{uuid}", headers=AUTH).json()
    assert s["guest_ready"] and s["vrde_listening"] and s["ip"] == "192.168.100.101"
    assert s["macs"] == ["08:00:27:00:00:01"]
    assert (uuid, vbox_mod.PROP_GUEST_PASSWORD) not in fake.props
    assert s["measured"] == {"cpu_percent": 3.0, "ram_used_mb": 900.0}


def test_start_clears_stale_ready_flag(env):
    client, fake = env
    uuid = clone(client).json()["uuid"]
    fake.props[(uuid, vbox_mod.PROP_READY)] = OP  # sobrou de um boot anterior
    client.post(f"/v1/instances/{uuid}/start", headers=AUTH, json={"op_id": OP})
    assert (uuid, vbox_mod.PROP_READY) not in fake.props


def test_by_op_finds_vm_after_lost_response(env):
    client, _ = env
    uuid = clone(client).json()["uuid"]
    assert client.get(f"/v1/instances/by-op/{OP}", headers=AUTH).json()["uuid"] == uuid
    assert client.get(f"/v1/instances/by-op/{OP2}", headers=AUTH).status_code == 404


def test_stop_acpi_and_force(env):
    client, fake = env
    uuid = clone(client).json()["uuid"]
    client.post(f"/v1/instances/{uuid}/start", headers=AUTH, json={"op_id": OP})
    client.post(f"/v1/instances/{uuid}/stop", headers=AUTH, json={"op_id": OP})
    fake.vms[uuid]["VMState"] = "running"
    client.post(f"/v1/instances/{uuid}/stop", headers=AUTH, json={"op_id": OP, "force": True})
    assert [c[0] for c in fake.calls if c[0] in ("acpi", "poweroff")] == ["acpi", "poweroff"]


class _Conn:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# ------------------------------------------------------------------ vbox.py: argv sem shell
def test_vbox_builds_argument_lists(monkeypatch):
    seen = []

    def fake_run(argv, **kwargs):
        assert kwargs.get("shell") in (None, False)
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(vbox_mod.subprocess, "run", fake_run)
    vb = vbox_mod.VBox("VBoxManage")
    vb.clone_linked("BASE-LINUX-v1", "v1", "LAB-ana-aaaaaaaa", 900)
    vb.modify("LAB-ana-aaaaaaaa", cpus=1, memory_mb=2048, vrde_port=5001, vrde_address="192.168.56.1")
    assert seen[0] == ["VBoxManage", "clonevm", "BASE-LINUX-v1", "--snapshot", "v1", "--options", "link",
                       "--name", "LAB-ana-aaaaaaaa", "--register"]
    assert "--vrdeauthtype" in seen[1] and seen[1][seen[1].index("--vrdeauthtype") + 1] == "external"


def test_vbox_timeout_and_error(monkeypatch):
    def timeout(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 1)

    monkeypatch.setattr(vbox_mod.subprocess, "run", timeout)
    with pytest.raises(vbox_mod.VBoxTimeout):
        vbox_mod.VBox("VBoxManage").version()

    monkeypatch.setattr(vbox_mod.subprocess, "run",
                        lambda argv, **k: subprocess.CompletedProcess(argv, 1, "", "VBOX_E_OBJECT_NOT_FOUND"))
    with pytest.raises(vbox_mod.VBoxError):
        vbox_mod.VBox("VBoxManage").version()


def test_vbox_parsers(monkeypatch):
    outputs = {
        "list": '"BASE-LINUX-v1" {11111111-1111-1111-1111-111111111111}\n"LAB-ana-aaaaaaaa" {AAAAAAAA-1111-1111-1111-111111111111}\n',
        "showvminfo": 'name="LAB-ana-aaaaaaaa"\nVMState="running"\ncpus=1\nvrdeport=5001\n',
        "getextradata": "Value: 1\n",
        "metrics": ("Object          Metric               Values\n"
                    "--------------- -------------------- -----------\n"
                    "LAB-ana         CPU/Load/User        2.50%\n"
                    "LAB-ana         CPU/Load/Kernel      1.00%\n"
                    "LAB-ana         RAM/Usage/Used       1048576 kB\n"),
    }
    monkeypatch.setattr(vbox_mod.subprocess, "run",
                        lambda argv, **k: subprocess.CompletedProcess(argv, 0, outputs[argv[1]], ""))
    vb = vbox_mod.VBox("VBoxManage")
    assert vb.list_vms()[1] == {"name": "LAB-ana-aaaaaaaa", "uuid": "aaaaaaaa-1111-1111-1111-111111111111"}
    assert vb.info("x")["VMState"] == "running" and vb.info("x")["vrdeport"] == "5001"
    assert vb.get_extradata("x", "k") == "1"
    assert vb.metrics("x") == {"cpu_percent": 3.5, "ram_used_mb": 1024.0}
