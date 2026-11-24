"""Configuração do agente: labagent/agent.json (fora do Git) + variáveis de ambiente.

O token compartilhado com o backend vem de LABAGENT_TOKEN ou do campo "token"
do agent.json; nunca fica no código nem no repositório.
"""
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_VBOX = r"C:\Program Files\Oracle\VirtualBox\VBoxManage.exe" if os.name == "nt" else "VBoxManage"


@dataclass
class Image:
    base_vm: str
    snapshot: str
    version: str


@dataclass
class Settings:
    token: str
    listen_host: str = "192.168.56.1"
    listen_port: int = 8765
    allowed_sources: list[str] = field(default_factory=lambda: ["192.168.56.10"])
    vboxmanage: str = DEFAULT_VBOX
    vrde_address: str = "192.168.56.1"
    vrde_ports: tuple[int, int] = (5001, 5010)
    max_cpus: int = 2
    max_memory_mb: int = 4096
    clone_timeout: int = 900
    images: dict[str, Image] = field(default_factory=dict)

    @property
    def base_vms(self) -> set[str]:
        return {img.base_vm for img in self.images.values()}


def load(path: Path | None = None) -> Settings:
    path = path or Path(os.environ.get("LABAGENT_CONFIG", HERE / "agent.json"))
    raw = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    token = os.environ.get("LABAGENT_TOKEN") or raw.get("token", "")
    if len(token) < 24:
        raise SystemExit("Defina um token de pelo menos 24 caracteres (LABAGENT_TOKEN ou agent.json).")
    images = {
        key: Image(base_vm=v["base_vm"], snapshot=v["snapshot"], version=str(v["version"]))
        for key, v in raw.get("images", {}).items()
    }
    ports = raw.get("vrde_ports", [5001, 5010])
    return Settings(
        token=token,
        listen_host=raw.get("listen_host", "192.168.56.1"),
        listen_port=int(raw.get("listen_port", 8765)),
        allowed_sources=list(raw.get("allowed_sources", ["192.168.56.10"])),
        vboxmanage=raw.get("vboxmanage", DEFAULT_VBOX),
        vrde_address=raw.get("vrde_address", "192.168.56.1"),
        vrde_ports=(int(ports[0]), int(ports[1])),
        max_cpus=int(raw.get("max_cpus", 2)),
        max_memory_mb=int(raw.get("max_memory_mb", 4096)),
        clone_timeout=int(raw.get("clone_timeout", 900)),
        images=images,
    )
