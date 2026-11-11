"""Coletores de métricas do Debian (sem dependências externas).

Lê /proc diretamente, chama `systemctl show` com lista fixa de argumentos
(nunca via shell e nunca com entrada do usuário) e mede a latência HTTP.
Este módulo não importa FastAPI, para poder ser testado isoladamente.
"""
import os
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from . import config


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- CPU
def parse_cpu_line(line: str) -> tuple[int, int]:
    """Primeira linha de /proc/stat -> (total, idle)."""
    parts = [int(x) for x in line.split()[1:]]
    idle = parts[3] + (parts[4] if len(parts) > 4 else 0)  # idle + iowait
    total = sum(parts[:8])  # user nice system idle iowait irq softirq steal
    return total, idle


def _read_cpu() -> tuple[int, int]:
    with open("/proc/stat") as f:
        return parse_cpu_line(f.readline())


class CpuSampler:
    """CPU % calculado pela diferença entre duas leituras de /proc/stat.

    Vários clientes consultando ao mesmo tempo não distorcem o valor: se a
    última leitura tem menos de 1 s, devolve o valor já calculado.
    """

    MIN_INTERVAL = 1.0

    def __init__(self, reader=_read_cpu, clock=time.monotonic):
        self._read, self._clock = reader, clock
        self._lock = threading.Lock()
        self._prev = None  # (instante, total, idle)
        self._last = 0.0

    def percent(self) -> float:
        with self._lock:
            if self._prev is None:
                total, idle = self._read()
                self._prev = (self._clock(), total, idle)
                time.sleep(0.25)
            now = self._clock()
            if now - self._prev[0] < self.MIN_INTERVAL and self._last:
                return self._last
            total, idle = self._read()
            d_total, d_idle = total - self._prev[1], idle - self._prev[2]
            self._prev = (now, total, idle)
            if d_total > 0:
                self._last = round(100.0 * (d_total - d_idle) / d_total, 1)
            return self._last


cpu_sampler = CpuSampler()


# ------------------------------------------------------------- Memória
def parse_meminfo(text: str) -> dict:
    values = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        if rest:
            values[key] = int(rest.split()[0])  # kB
    total = values["MemTotal"] // 1024
    available = values.get("MemAvailable", values.get("MemFree", 0)) // 1024
    used = total - available
    return {
        "total_mb": total,
        "used_mb": used,
        "available_mb": available,
        "percent": round(100.0 * used / total, 1) if total else 0.0,
    }


def memory() -> dict:
    with open("/proc/meminfo") as f:
        return parse_meminfo(f.read())


# --------------------------------------------------------------- Disco
def disk_usage(path: str) -> dict:
    try:
        st = os.statvfs(path)
    except OSError:
        return {"path": path, "error": "indisponível"}
    gb = 1024**3
    total = st.f_blocks * st.f_frsize
    used = (st.f_blocks - st.f_bfree) * st.f_frsize
    avail = st.f_bavail * st.f_frsize
    percent = round(100.0 * used / (used + avail), 1) if (used + avail) else 0.0
    return {
        "path": path,
        "total_gb": round(total / gb, 1),
        "used_gb": round(used / gb, 1),
        "percent": percent,
    }


# -------------------------------------------------------------- Uptime
def format_uptime(seconds: float) -> str:
    s = int(seconds)
    days, s = divmod(s, 86400)
    hours, s = divmod(s, 3600)
    minutes = s // 60
    parts = []
    if days:
        parts.append(f"{days}d")
    if days or hours:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}min")
    return " ".join(parts)


def uptime_seconds() -> float:
    with open("/proc/uptime") as f:
        return float(f.read().split()[0])


def collect_metrics() -> dict:
    up = uptime_seconds()
    return {
        "timestamp": now_iso(),
        "cpu_percent": cpu_sampler.percent(),
        "memory": memory(),
        "disks": [disk_usage(p) for p in config.DISK_PATHS],
        "uptime_seconds": int(up),
        "uptime_human": format_uptime(up),
    }


# ------------------------------------------------------------ Serviços
def parse_show(output: str) -> dict:
    props = {}
    for line in output.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            props[key] = value.strip()
    return props


def classify(props: dict | None) -> str:
    """ativo / inativo / falha / desconhecido (nunca chuta: na dúvida, desconhecido)."""
    if not props or props.get("LoadState") == "not-found":
        return "desconhecido"
    return {"active": "ativo", "inactive": "inativo", "failed": "falha"}.get(
        props.get("ActiveState", ""), "desconhecido"
    )


def _systemctl_show(name: str) -> dict | None:
    try:
        r = subprocess.run(
            ["systemctl", "show", name, "--property=LoadState,ActiveState,SubState"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return parse_show(r.stdout) if r.returncode == 0 else None


def probe_port(host: str, port: int, timeout: float = 1.0) -> str:
    """aberta / fechada (recusada) / desconhecido (timeout ou erro de rede).

    Falha de conexão que não seja recusa explícita NÃO significa serviço parado.
    """
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return "aberta"
    except ConnectionRefusedError:
        return "fechada"
    except OSError:
        return "desconhecido"


def _service_state(name: str) -> dict:
    props = _systemctl_show(name)
    port = config.SERVICE_PORTS.get(name)
    return {
        "name": name,
        "status": classify(props),
        "active_state": (props or {}).get("ActiveState"),
        "sub_state": (props or {}).get("SubState"),
        "port": port,
        "port_state": probe_port(config.SERVICE_HOST, port) if port else None,
    }


def collect_services() -> dict:
    with ThreadPoolExecutor(max_workers=len(config.SERVICES)) as pool:
        services = list(pool.map(_service_state, config.SERVICES))
    return {"timestamp": now_iso(), "services": services}


# ------------------------------------------------------ Latência HTTP
# Sem proxy: a medição deve ir direto ao Apache, mesmo com HTTP_PROXY no ambiente.
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http_latency(url: str | None = None, timeout: float = 3.0) -> dict:
    url = url or config.SITE_URL
    ts = now_iso()
    start = time.perf_counter()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "console-n2/1.0"})
        with _opener.open(req, timeout=timeout) as resp:
            resp.read(1024)
            code = resp.status
    except urllib.error.HTTPError as e:  # o site respondeu (ex.: 404): há latência
        code = e.code
    except (urllib.error.URLError, OSError, ValueError):
        return {"ok": False, "url": url, "status_code": None, "duration_ms": None,
                "timestamp": ts, "error": "sem resposta do site"}
    ms = round((time.perf_counter() - start) * 1000, 1)
    return {"ok": True, "url": url, "status_code": code, "duration_ms": ms, "timestamp": ts}
