"""Agente do laboratório virtual (Incremento 3) — roda NO HOST FÍSICO (Windows).

Só ele fala com o VBoxManage. O backend do console (no Debian) chama esta API
pela rede host-only. Regras de segurança (seção 8.3.1, passo 4):

 - autenticação por token (cabeçalho Authorization: Bearer) + restrição de
   origem (só o IP do Debian na rede host-only);
 - operações delimitadas: inventário, clonar, configurar, iniciar, consultar,
   desligar e excluir. Não existe rota que receba comando de shell;
 - UUIDs, nomes, perfis e imagens são validados aqui também (não só no
   backend), e cada processo é chamado com argumentos separados (vbox.py);
 - só mexe em VMs marcadas como do projeto (extradata ConsoleN2/Managed=1),
   e nunca na imagem-base;
 - cada chamada ao VBoxManage tem timeout; um timeout é devolvido como
   "resultado desconhecido" (504) para o backend conferir o host antes de
   repetir.

Uso:  python -m uvicorn agent:app --host 192.168.56.1 --port 8765
(ou run_agent.ps1, que lê host/porta do agent.json).
"""
import hmac
import re
import shutil
import socket
import threading

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

import settings as settings_mod
from vbox import (
    IMAGE_KEY, MANAGED_KEY, OP_KEY, OWNER_KEY, PROP_GUEST_PASSWORD, PROP_HOSTNAME,
    PROP_INSTANCE, PROP_IP, PROP_READY, VBox, VBoxError, VBoxTimeout,
)

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
OP_RE = re.compile(r"^[0-9a-f]{32}$")
NAME_RE = re.compile(r"^LAB-[a-z0-9][a-z0-9-]{0,40}$")
OWNER_RE = re.compile(r"^[a-z0-9_.-]{1,32}$")
HOSTNAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SECRET_RE = re.compile(r"^[A-Za-z0-9_-]{12,64}$")

RUNNING_STATES = {"running", "starting", "stopping", "paused"}


class CloneBody(BaseModel):
    op_id: str = Field(pattern=OP_RE.pattern)
    name: str = Field(pattern=NAME_RE.pattern)
    image: str = Field(max_length=64)
    owner: str = Field(pattern=OWNER_RE.pattern)


class ConfigureBody(BaseModel):
    op_id: str = Field(pattern=OP_RE.pattern)
    cpus: int = Field(ge=1, le=16)
    memory_mb: int = Field(ge=512, le=65536)
    vrde_port: int = Field(ge=1024, le=65535)
    vrde_user: str = Field(pattern=OWNER_RE.pattern)
    vrde_password_sha256: str = Field(pattern=SHA256_RE.pattern)
    hostname: str = Field(pattern=HOSTNAME_RE.pattern)
    guest_password: str = Field(pattern=SECRET_RE.pattern)


class OpBody(BaseModel):
    op_id: str = Field(pattern=OP_RE.pattern)
    force: bool = False


def create_app(cfg: "settings_mod.Settings", vbox: VBox) -> FastAPI:
    app = FastAPI(title="Console N2 - agente do laboratório", docs_url=None, redoc_url=None)
    lock = threading.Lock()  # serializa operações que alteram o host

    # ------------------------------------------------------ autenticação
    def authorize(request: Request) -> None:
        source = request.client.host if request.client else ""
        if source not in cfg.allowed_sources:
            raise HTTPException(status_code=403, detail={"code": "origin", "message": "Origem não autorizada."})
        header = request.headers.get("authorization", "")
        token = header[7:] if header.lower().startswith("bearer ") else ""
        if not hmac.compare_digest(token.encode(), cfg.token.encode()):
            raise HTTPException(status_code=401, detail={"code": "token", "message": "Token inválido."})

    guard = [Depends(authorize)]

    # ------------------------------------------------------ utilitários
    def call(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except VBoxTimeout as exc:
            raise HTTPException(status_code=504, detail={"code": "timeout", "message": str(exc)}) from exc
        except VBoxError as exc:
            raise HTTPException(status_code=502, detail={"code": "vbox", "message": str(exc)}) from exc

    def check_uuid(uuid: str) -> str:
        uuid = uuid.lower()
        if not UUID_RE.match(uuid):
            raise HTTPException(status_code=400, detail={"code": "uuid", "message": "UUID inválido."})
        return uuid

    def managed_info(uuid: str, op_id: str | None = None) -> dict:
        """Info da VM, exigindo que seja do projeto (e da operação, se informada)."""
        uuid = check_uuid(uuid)
        if uuid not in {vm["uuid"] for vm in call(vbox.list_vms)}:
            raise HTTPException(status_code=404, detail={"code": "not_found", "message": "VM não encontrada."})
        info = call(vbox.info, uuid)
        if info.get("name") in cfg.base_vms or not NAME_RE.match(info.get("name", "")):
            raise HTTPException(status_code=403, detail={"code": "protected", "message": "VM fora do projeto."})
        if call(vbox.get_extradata, uuid, MANAGED_KEY) != "1":
            raise HTTPException(status_code=403, detail={"code": "unmanaged", "message": "VM não criada pelo console."})
        if op_id is not None and call(vbox.get_extradata, uuid, OP_KEY) != op_id:
            raise HTTPException(status_code=409, detail={"code": "op_mismatch", "message": "VM de outra operação."})
        return info

    def find_by_op(op_id: str) -> dict | None:
        for vm in call(vbox.list_vms):
            if not NAME_RE.match(vm["name"]):
                continue
            if call(vbox.get_extradata, vm["uuid"], OP_KEY) == op_id:
                return vm
        return None

    def macs(info: dict) -> list[str]:
        out = []
        for i in range(1, 9):
            mac = info.get(f"macaddress{i}")
            if mac and info.get(f"nic{i}", "none") != "none":
                out.append(":".join(mac[j:j + 2] for j in range(0, 12, 2)).lower())
        return out

    def auth_library(props: dict) -> str:
        """O nome da chave muda entre versões ("VRDE auth library", "Remote desktop auth library"...)."""
        return next((v for k, v in props.items() if "auth library" in k.lower()), "")

    def vrde_listening(port: int) -> bool:
        try:
            with socket.create_connection((cfg.vrde_address, port), timeout=1.5):
                return True
        except OSError:
            return False

    def describe(uuid: str, info: dict, *, with_metrics: bool = False) -> dict:
        state = info.get("VMState", "unknown")
        op_id = call(vbox.get_extradata, uuid, OP_KEY)
        ready_value = call(vbox.get_guestproperty, uuid, PROP_READY) if state == "running" else None
        ready = bool(op_id) and ready_value == op_id
        port = int(info["vrdeport"]) if info.get("vrdeport", "").isdigit() else None
        result = {
            "uuid": uuid,
            "name": info.get("name"),
            "state": state,
            "cpus": int(info.get("cpus", 0) or 0),
            "memory_mb": int(info.get("memory", 0) or 0),
            "macs": macs(info),
            "op_id": op_id,
            "owner": call(vbox.get_extradata, uuid, OWNER_KEY),
            "image": call(vbox.get_extradata, uuid, IMAGE_KEY),
            "vrde_port": port,
            "vrde_listening": bool(port) and state == "running" and vrde_listening(port),
            "guest_ready": ready,
            "ip": call(vbox.get_guestproperty, uuid, PROP_IP) if state == "running" else None,
        }
        if ready:
            # a senha do convidado já foi aplicada pelo script de 1ª inicialização:
            # não deixar o segredo guardado nas propriedades da VM
            try:
                vbox.unset_guestproperty(uuid, PROP_GUEST_PASSWORD)
            except (VBoxError, VBoxTimeout):
                pass
        if with_metrics and state == "running":
            try:
                result["measured"] = vbox.metrics(uuid)
            except (VBoxError, VBoxTimeout):
                result["measured"] = None
        return result

    # ------------------------------------------------------ rotas
    @app.get("/v1/health", dependencies=guard)
    def health():
        return {"status": "ok"}

    @app.get("/v1/inventory", dependencies=guard)
    def inventory():
        props = call(vbox.system_properties)
        folder = props.get("Default machine folder", "")
        try:
            import psutil  # opcional: medidas reais do host
            mem = psutil.virtual_memory()
            host = {
                "cpu_logical": psutil.cpu_count(logical=True),
                "cpu_physical": psutil.cpu_count(logical=False),
                "cpu_percent": psutil.cpu_percent(interval=0.5),
                "ram_total_mb": mem.total // 2**20,
                "ram_available_mb": mem.available // 2**20,
            }
        except ImportError:
            host = {"cpu_logical": None, "cpu_physical": None, "cpu_percent": None,
                    "ram_total_mb": None, "ram_available_mb": None}
        try:
            host["disk_free_gb"] = round(shutil.disk_usage(folder).free / 2**30, 1) if folder else None
        except OSError:
            host["disk_free_gb"] = None
        vms = call(vbox.list_vms)
        names = {vm["name"] for vm in vms}
        images = {}
        for key, img in cfg.images.items():
            images[key] = {"version": img.version, "base_vm": img.base_vm, "present": img.base_vm in names}
        running = {vm["uuid"] for vm in call(vbox.list_vms, True)}
        return {
            "vbox_version": call(vbox.version),
            "extension_pack": bool(re.search(r"Pack no\.\s*\d+:.*Extension Pack", call(vbox.extpacks))),
            "vrde_auth_library": auth_library(props),
            "host": host,
            "limits": {"max_cpus": cfg.max_cpus, "max_memory_mb": cfg.max_memory_mb},
            "images": images,
            "vms_total": len(vms),
            "vms_running": len(running),
            "lab_vms": [vm for vm in vms if NAME_RE.match(vm["name"])],
        }

    @app.get("/v1/instances/by-op/{op_id}", dependencies=guard)
    def by_op(op_id: str):
        if not OP_RE.match(op_id):
            raise HTTPException(status_code=400, detail={"code": "op", "message": "Operação inválida."})
        vm = find_by_op(op_id)
        if vm is None:
            raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Nenhuma VM dessa operação."})
        return describe(vm["uuid"], managed_info(vm["uuid"], op_id))

    @app.post("/v1/instances", dependencies=guard)
    def clone(body: CloneBody):
        img = cfg.images.get(body.image)
        if img is None:
            raise HTTPException(status_code=400, detail={"code": "image", "message": "Imagem fora do catálogo."})
        with lock:
            existing = find_by_op(body.op_id)
            if existing:  # pedido repetido: devolve a mesma VM, nunca clona de novo
                return describe(existing["uuid"], managed_info(existing["uuid"], body.op_id))
            if body.name in {vm["name"] for vm in call(vbox.list_vms)}:
                raise HTTPException(status_code=409, detail={"code": "name", "message": "Já existe VM com esse nome."})
            call(vbox.clone_linked, img.base_vm, img.snapshot, body.name, cfg.clone_timeout)
            info = call(vbox.info, body.name)
            uuid = info["UUID"].lower()
            call(vbox.set_extradata, uuid, MANAGED_KEY, "1")
            call(vbox.set_extradata, uuid, OP_KEY, body.op_id)
            call(vbox.set_extradata, uuid, OWNER_KEY, body.owner)
            call(vbox.set_extradata, uuid, IMAGE_KEY, f"{body.image}@{img.version}")
            return describe(uuid, call(vbox.info, uuid))

    @app.post("/v1/instances/{uuid}/configure", dependencies=guard)
    def configure(uuid: str, body: ConfigureBody):
        if body.cpus > cfg.max_cpus or body.memory_mb > cfg.max_memory_mb:
            raise HTTPException(status_code=400, detail={"code": "profile", "message": "Perfil acima do limite do host."})
        low, high = cfg.vrde_ports
        if not low <= body.vrde_port <= high:
            raise HTTPException(status_code=400, detail={"code": "port", "message": "Porta VRDE fora da faixa."})
        with lock:
            info = managed_info(uuid, body.op_id)
            uuid = uuid.lower()
            if info.get("VMState") != "poweroff":
                raise HTTPException(status_code=409, detail={"code": "state", "message": "Configure com a VM desligada."})
            if "VBoxAuthSimple" not in auth_library(call(vbox.system_properties)):
                raise HTTPException(
                    status_code=412,
                    detail={"code": "vrde_auth", "message": "Defina VBoxAuthSimple como biblioteca de autenticação VRDE."},
                )
            call(vbox.modify, uuid, cpus=body.cpus, memory_mb=body.memory_mb,
                 vrde_port=body.vrde_port, vrde_address=cfg.vrde_address)
            call(vbox.set_extradata, uuid, f"VBoxAuthSimple/users/{body.vrde_user}", body.vrde_password_sha256)
            call(vbox.set_guestproperty, uuid, PROP_INSTANCE, body.op_id, "RDONLYGUEST")
            call(vbox.set_guestproperty, uuid, PROP_HOSTNAME, body.hostname, "RDONLYGUEST")
            call(vbox.set_guestproperty, uuid, PROP_GUEST_PASSWORD, body.guest_password, "RDONLYGUEST")
            return describe(uuid, call(vbox.info, uuid))

    @app.get("/v1/instances/{uuid}", dependencies=guard)
    def status(uuid: str):
        info = managed_info(uuid)
        return describe(uuid.lower(), info, with_metrics=True)

    @app.post("/v1/instances/{uuid}/start", dependencies=guard)
    def start(uuid: str, body: OpBody):
        with lock:
            info = managed_info(uuid, body.op_id)
            uuid = uuid.lower()
            if info.get("VMState") not in RUNNING_STATES:
                try:  # prontidão vale só para este boot (pode não existir ainda)
                    vbox.unset_guestproperty(uuid, PROP_READY)
                except VBoxError:
                    pass
                call(vbox.start_headless, uuid)
                try:
                    vbox.metrics_setup(uuid)  # medição é informativa: nunca impede o início
                except (VBoxError, VBoxTimeout):
                    pass
            return describe(uuid, call(vbox.info, uuid))

    @app.post("/v1/instances/{uuid}/stop", dependencies=guard)
    def stop(uuid: str, body: OpBody):
        with lock:
            info = managed_info(uuid, body.op_id)
            uuid = uuid.lower()
            if info.get("VMState") in RUNNING_STATES:
                call(vbox.poweroff if body.force else vbox.acpi_shutdown, uuid)
            return describe(uuid, call(vbox.info, uuid))

    @app.post("/v1/instances/{uuid}/delete", dependencies=guard)
    def delete(uuid: str, body: OpBody):
        with lock:
            info = managed_info(uuid, body.op_id)  # exige VM do projeto E da mesma operação
            uuid = uuid.lower()
            if info.get("VMState") in RUNNING_STATES:
                call(vbox.poweroff, uuid)
            call(vbox.unregister_delete, uuid)
            return {"uuid": uuid, "deleted": True}

    return app


def _build_default_app() -> FastAPI:
    cfg = settings_mod.load()
    return create_app(cfg, VBox(cfg.vboxmanage))


# `python -m uvicorn agent:app` carrega a configuração real; os testes usam create_app().
def __getattr__(name):
    if name == "app":
        global app
        app = _build_default_app()
        return app
    raise AttributeError(name)
