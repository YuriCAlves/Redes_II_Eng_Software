"""Chamadas ao VBoxManage com argumentos separados (nunca uma string de shell).

Todas as funções recebem valores já validados por `agent.py` (UUID, nome,
número) e montam uma LISTA de argumentos para `subprocess.run`, sem
`shell=True`. Assim nada que venha do backend vira comando de shell.
"""
import os
import re
import subprocess

MANAGED_KEY = "ConsoleN2/Managed"
OP_KEY = "ConsoleN2/OpId"
OWNER_KEY = "ConsoleN2/Owner"
IMAGE_KEY = "ConsoleN2/Image"

PROP_INSTANCE = "/ConsoleN2/InstanceId"
PROP_HOSTNAME = "/ConsoleN2/Hostname"
PROP_GUEST_PASSWORD = "/ConsoleN2/GuestPassword"
PROP_READY = "/ConsoleN2/Ready"
PROP_IP = "/VirtualBox/GuestInfo/Net/0/V4/IP"

_LIST_RE = re.compile(r'^"(?P<name>.*)" \{(?P<uuid>[0-9a-fA-F-]{36})\}$')


class VBoxError(Exception):
    """VBoxManage terminou com erro (resultado confirmado, não timeout)."""

    def __init__(self, message: str, stderr: str = ""):
        super().__init__(message)
        self.stderr = stderr[-400:]


class VBoxTimeout(Exception):
    """VBoxManage não terminou no prazo: o resultado é desconhecido."""


class VBox:
    def __init__(self, exe: str):
        self.exe = exe

    # ---------------------------------------------------------- execução
    def run(self, args: list[str], timeout: int = 60) -> str:
        kwargs = {}
        if os.name == "nt":  # não abrir uma janela de console por chamada
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            proc = subprocess.run(
                [self.exe, *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
                **kwargs,
            )
        except subprocess.TimeoutExpired as exc:
            raise VBoxTimeout(f"VBoxManage {args[0]} excedeu {timeout}s") from exc
        except FileNotFoundError as exc:
            raise VBoxError("VBoxManage não encontrado no caminho configurado.") from exc
        if proc.returncode != 0:
            raise VBoxError(f"VBoxManage {args[0]} falhou (código {proc.returncode}).", proc.stderr)
        return proc.stdout

    # ---------------------------------------------------------- leitura
    def version(self) -> str:
        return self.run(["--version"]).strip()

    def list_vms(self, running: bool = False) -> list[dict]:
        out = self.run(["list", "runningvms" if running else "vms"])
        vms = []
        for line in out.splitlines():
            m = _LIST_RE.match(line.strip())
            if m:
                vms.append({"name": m.group("name"), "uuid": m.group("uuid").lower()})
        return vms

    def info(self, vm: str) -> dict:
        """showvminfo --machinereadable como dicionário (chave -> valor sem aspas)."""
        out = self.run(["showvminfo", vm, "--machinereadable"])
        data = {}
        for line in out.splitlines():
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            data[key.strip().strip('"')] = value.strip().strip('"')
        return data

    def get_extradata(self, vm: str, key: str) -> str | None:
        out = self.run(["getextradata", vm, key]).strip()
        if out.startswith("Value:"):
            return out[len("Value:"):].strip()
        return None

    def get_guestproperty(self, vm: str, prop: str) -> str | None:
        out = self.run(["guestproperty", "get", vm, prop]).strip()
        if out.startswith("Value:"):
            return out[len("Value:"):].strip()
        return None

    def system_properties(self) -> dict:
        out = self.run(["list", "systemproperties"])
        props = {}
        for line in out.splitlines():
            key, sep, value = line.partition(":")
            if sep:
                props[key.strip()] = value.strip()
        return props

    def extpacks(self) -> str:
        return self.run(["list", "extpacks"])

    def metrics(self, vm: str) -> dict:
        """CPU (%) e memória (kB) do processo da VM medidos pelo VirtualBox."""
        out = self.run(["metrics", "query", vm, "CPU/Load/User,CPU/Load/Kernel,RAM/Usage/Used"])
        values = {}
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 3 and "/" in parts[1]:
                values[parts[1]] = parts[2].split(",")[-1]
        def pct(key):
            v = values.get(key, "").rstrip("%")
            try:
                return float(v)
            except ValueError:
                return None
        cpu_user, cpu_kernel = pct("CPU/Load/User"), pct("CPU/Load/Kernel")
        ram_kb = values.get("RAM/Usage/Used", "")
        try:
            ram_mb = round(int(ram_kb) / 1024, 1)
        except ValueError:
            ram_mb = None
        cpu = None if cpu_user is None else round(cpu_user + (cpu_kernel or 0.0), 1)
        return {"cpu_percent": cpu, "ram_used_mb": ram_mb}

    # ---------------------------------------------------------- escrita
    def clone_linked(self, base: str, snapshot: str, name: str, timeout: int) -> None:
        # Sem "--options KeepAllMACs": o VirtualBox gera MACs novos para o clone.
        self.run(
            ["clonevm", base, "--snapshot", snapshot, "--options", "link", "--name", name, "--register"],
            timeout=timeout,
        )

    def set_extradata(self, vm: str, key: str, value: str | None) -> None:
        args = ["setextradata", vm, key]
        if value is not None:
            args.append(value)
        self.run(args)

    def modify(self, vm: str, *, cpus: int, memory_mb: int, vrde_port: int, vrde_address: str) -> None:
        self.run(
            [
                "modifyvm", vm,
                "--cpus", str(cpus),
                "--memory", str(memory_mb),
                "--vrde", "on",
                "--vrdeport", str(vrde_port),
                "--vrdeaddress", vrde_address,
                "--vrdeauthtype", "external",
            ]
        )

    def set_guestproperty(self, vm: str, prop: str, value: str, flags: str | None = None) -> None:
        args = ["guestproperty", "set", vm, prop, value]
        if flags:
            args += ["--flags", flags]
        self.run(args)

    def unset_guestproperty(self, vm: str, prop: str) -> None:
        self.run(["guestproperty", "unset", vm, prop])

    def metrics_setup(self, vm: str) -> None:
        self.run(["metrics", "setup", "--period", "5", "--samples", "1", vm])

    def start_headless(self, vm: str) -> None:
        self.run(["startvm", vm, "--type", "headless"], timeout=120)

    def acpi_shutdown(self, vm: str) -> None:
        self.run(["controlvm", vm, "acpipowerbutton"])

    def poweroff(self, vm: str) -> None:
        self.run(["controlvm", vm, "poweroff"], timeout=60)

    def unregister_delete(self, vm: str) -> None:
        self.run(["unregistervm", vm, "--delete"], timeout=300)
