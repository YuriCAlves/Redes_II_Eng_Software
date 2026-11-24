"""Configuração via variáveis de ambiente (nenhum segredo no código).

Se existir `backend/.env` (fora do Git), ele é lido ao iniciar; variáveis já
definidas no ambiente têm prioridade.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(BASE_DIR / ".env")


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


# ---------- Núcleo (CP1) ----------
DB_PATH = Path(os.environ.get("CONSOLE_DB_PATH", BASE_DIR / "console.db"))
SESSION_TTL = _int("CONSOLE_SESSION_TTL", 3600)
COOKIE_NAME = "console_session"
COOKIE_SECURE = os.environ.get("CONSOLE_COOKIE_SECURE", "0") == "1"
FRONTEND_DIST = Path(
    os.environ.get("CONSOLE_FRONTEND_DIST", BASE_DIR.parent / "frontend" / "dist")
)
ROLES = ("leitor", "operador", "admin")  # do menor para o maior privilégio

# ---------- Dashboard (CP2) ----------
SERVICES = ("apache2", "vsftpd", "postfix", "dovecot", "ssh")
SERVICE_HOST = os.environ.get("CONSOLE_SERVICE_HOST", "192.168.100.10")
SERVICE_PORTS = {"apache2": 80, "vsftpd": 21, "postfix": 25, "dovecot": 143, "ssh": 22}
DISK_PATHS = ("/", os.environ.get("CONSOLE_FILES_DIR", "/srv/portal/arquivos"))
SITE_URL = os.environ.get("CONSOLE_SITE_URL", "http://site.empresa.test/")

# ---------- FTP (CP3) ----------
FTP_HOST = os.environ.get("CONSOLE_FTP_HOST", SERVICE_HOST)
FTP_PORT = _int("CONSOLE_FTP_PORT", 21)
FTP_USER = os.environ.get("CONSOLE_FTP_USER", "ftpuser")
FTP_DIR = os.environ.get("CONSOLE_FTP_DIR", "/arquivos")
FTP_TIMEOUT = _int("CONSOLE_FTP_TIMEOUT", 10)
MAX_UPLOAD_MB = _int("CONSOLE_MAX_UPLOAD_MB", 10)
ALLOWED_EXT = tuple(
    e.strip().lower()
    for e in os.environ.get("CONSOLE_ALLOWED_EXT", "pdf,txt,png,jpg,jpeg").split(",")
    if e.strip()
)
FILES_PUBLIC_URL = os.environ.get("CONSOLE_FILES_PUBLIC_URL", "http://site.empresa.test/arquivos/")


def ftp_password() -> str | None:
    return os.environ.get("CONSOLE_FTP_PASSWORD") or None


# ---------- E-mail (CP4) ----------
MAIL_HOST = os.environ.get("CONSOLE_MAIL_HOST", SERVICE_HOST)
SMTP_PORT = _int("CONSOLE_SMTP_PORT", 25)
IMAP_PORT = _int("CONSOLE_IMAP_PORT", 143)
MAIL_TIMEOUT = _int("CONSOLE_MAIL_TIMEOUT", 10)
# endereço -> (usuário IMAP sem domínio, variável de ambiente com a senha)
MAIL_ACCOUNTS = {
    "aluno@empresa.test": ("aluno", "CONSOLE_MAIL_PASS_ALUNO"),
    "suporte@empresa.test": ("suporte", "CONSOLE_MAIL_PASS_SUPORTE"),
}

# ---------- SSH (CP5/CP6) ----------
SSH_HOST = os.environ.get("CONSOLE_SSH_HOST", SERVICE_HOST)
SSH_PORT = _int("CONSOLE_SSH_PORT", 22)
SSH_USER = os.environ.get("CONSOLE_SSH_USER", "yuri")
SSH_KEY_PATH = os.environ.get("CONSOLE_SSH_KEY_PATH", "")  # sem valor padrão: vem do ambiente
SSH_KNOWN_HOSTS = os.environ.get("CONSOLE_SSH_KNOWN_HOSTS", str(BASE_DIR / "known_hosts"))
SSH_TIMEOUT = _int("CONSOLE_SSH_TIMEOUT", 8)
LOG_LINES = (20, 50, 100)


def ssh_key_passphrase() -> str | None:
    return os.environ.get("CONSOLE_SSH_KEY_PASSPHRASE") or None


# ---------- DNS - BIND 9 (incremento 1) ----------
DNS_ZONE_FWD_PATH = os.environ.get("CONSOLE_DNS_ZONE_FWD_PATH", "/etc/bind/db.empresa.test")
DNS_ZONE_REV_PATH = os.environ.get("CONSOLE_DNS_ZONE_REV_PATH", "/etc/bind/db.192.168.100")
DNS_ZONE_FWD_NAME = os.environ.get("CONSOLE_DNS_ZONE_FWD_NAME", "empresa.test")
DNS_ZONE_REV_NAME = os.environ.get("CONSOLE_DNS_ZONE_REV_NAME", "100.168.192.in-addr.arpa")
DNS_QUERY_TIMEOUT = _int("CONSOLE_DNS_QUERY_TIMEOUT", 5)

# ---------- DHCP - Kea (incremento 2) ----------
# Kea expõe um Control Agent HTTP local (kea-ctrl-agent) por padrão em 127.0.0.1:8000;
# o backend fala com ele só pelo túnel SSH (nunca expõe essa porta na rede).
KEA_CTRL_SOCKET = os.environ.get("CONSOLE_KEA_CTRL_SOCKET", "/run/kea/kea-dhcp4-ctrl.sock")
KEA_CONFIG_PATH = os.environ.get("CONSOLE_KEA_CONFIG_PATH", "/etc/kea/kea-dhcp4.conf")
KEA_LEASE_CSV = os.environ.get("CONSOLE_KEA_LEASE_CSV", "/var/lib/kea/kea-leases4.csv")
KEA_SYSTEMD_UNIT = os.environ.get("CONSOLE_KEA_SYSTEMD_UNIT", "kea-dhcp4-server")
DHCP_SUBNET = os.environ.get("CONSOLE_DHCP_SUBNET", "192.168.100.0/24")

# Pasta privada (dono yuri, modo 700) onde o console grava a configuração nova
# para validar antes de aplicar. Os nomes dentro dela são FIXOS, para que as
# regras do sudoers não precisem de curinga (deploy/sudoers/console-n2-servicos).
STAGE_DIR = os.environ.get("CONSOLE_STAGE_DIR", "/var/lib/console-n2")
# Caminhos absolutos: o PATH de uma sessão SSH sem login no Debian não inclui /usr/sbin.
NAMED_CHECKZONE_BIN = os.environ.get("CONSOLE_NAMED_CHECKZONE_BIN", "/usr/bin/named-checkzone")
KEA_DHCP4_BIN = os.environ.get("CONSOLE_KEA_DHCP4_BIN", "/usr/sbin/kea-dhcp4")
# Validação do Kea: roda como o usuário do serviço sobre uma cópia dentro de /etc/kea
# (único caminho de configuração que o perfil AppArmor do kea-dhcp4 permite ler).
KEA_USER = os.environ.get("CONSOLE_KEA_USER", "_kea")
KEA_CANDIDATE_PATH = os.environ.get("CONSOLE_KEA_CANDIDATE_PATH", "/etc/kea/kea-dhcp4.candidate.json")
DHCP_RESERVED_INFRA = ("192.168.100.10", "192.168.100.20")  # Debian e Ubuntu: nunca reservar/alocar

# ---------- Laboratório virtual (incremento 3) ----------
# O agente roda no host físico (Windows) e é alcançado pela rede host-only.
LAB_AGENT_URL = os.environ.get("CONSOLE_LAB_AGENT_URL", "")          # ex.: http://192.168.56.1:8765
LAB_AGENT_TOKEN = os.environ.get("CONSOLE_LAB_AGENT_TOKEN", "")      # mesmo token do agent.json
LAB_AGENT_TIMEOUT = _int("CONSOLE_LAB_AGENT_TIMEOUT", 30)
LAB_CLONE_TIMEOUT = _int("CONSOLE_LAB_CLONE_TIMEOUT", 900)
# Capacidade reservável (medir o host e deixar margem para host, Debian e Ubuntu).
LAB_MAX_VCPUS = _int("CONSOLE_LAB_MAX_VCPUS", 2)
LAB_MAX_MEMORY_MB = _int("CONSOLE_LAB_MAX_MEMORY_MB", 4096)
LAB_MAX_INSTANCES = _int("CONSOLE_LAB_MAX_INSTANCES", 2)            # limite de disco: VMs existentes
LAB_QUOTA = {"operador": _int("CONSOLE_LAB_QUOTA_OPERADOR", 1), "admin": _int("CONSOLE_LAB_QUOTA_ADMIN", 2)}
LAB_VRDE_HOST = os.environ.get("CONSOLE_LAB_VRDE_HOST", "192.168.56.1")
LAB_VRDE_PORTS = (_int("CONSOLE_LAB_VRDE_PORT_MIN", 5001), _int("CONSOLE_LAB_VRDE_PORT_MAX", 5010))
LAB_READY_TIMEOUT = _int("CONSOLE_LAB_READY_TIMEOUT", 600)          # s até o desktop ficar pronto
LAB_STOP_TIMEOUT = _int("CONSOLE_LAB_STOP_TIMEOUT", 90)             # s de ACPI antes de forçar
LAB_POLL_INTERVAL = _int("CONSOLE_LAB_POLL_INTERVAL", 5)
LAB_WORKER = os.environ.get("CONSOLE_LAB_WORKER", "1") == "1"        # desliga a fila (testes)
LAB_CATALOG_PATH = Path(os.environ.get("CONSOLE_LAB_CATALOG", BASE_DIR / "app" / "lab_catalog.json"))
