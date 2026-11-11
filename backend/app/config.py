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
