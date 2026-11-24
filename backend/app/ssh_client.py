"""SSH (paramiko) com chave privada guardada só no backend e known_hosts obrigatório.

 - a chave vem de CONSOLE_SSH_KEY_PATH (nunca no repositório, nunca no navegador);
 - host key desconhecida ou diferente => conexão RECUSADA (RejectPolicy);
 - só roda comandos predefinidos; argumentos variáveis passam por validação rígida
   e por shlex.join (nada de concatenar texto do usuário).
"""
import base64
import hashlib
import logging
import os
import re
import shlex
import threading

from . import config
from .collectors import now_iso
from .db import kv_delete, kv_get, kv_set
from .errors import Conflict, InvalidInput, ServiceError

log = logging.getLogger("console.ssh")

# Regex do planejamento: 2 a 63 caracteres, minúsculas/dígitos/hífen, sem hífen nas pontas
HOSTNAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,61}[a-z0-9]$")
# Hostname ORIGINAL (lido do servidor) pode ter maiúsculas/pontos; ainda assim é validado
_ORIGINAL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$")
ORIGINAL_KEY = "original_hostname"
_lock = threading.Lock()  # uma alteração de hostname por vez


# ----------------------------------------------------------------- erros
class SshConfigError(ServiceError):
    status, code = 503, "ssh_config"
    message = "SSH não configurado no servidor do console (chave ou known_hosts ausentes)."


class SshUnreachable(ServiceError):
    status, code = 502, "unknown_state"
    message = "Estado desconhecido: sem conexão SSH com o Debian."


class SshHostKeyError(ServiceError):
    status, code = 502, "ssh_hostkey"
    message = "A identidade (host key) do servidor não confere com o known_hosts. Conexão recusada."


class SshAuthError(ServiceError):
    status, code = 502, "ssh_auth"
    message = "O servidor SSH recusou a chave do console."


class SshCommandError(ServiceError):
    status, code = 502, "ssh_command"
    message = "O comando remoto falhou."


# ------------------------------------------------------------ validação
def validate_hostname(name) -> str:
    if not isinstance(name, str) or not HOSTNAME_RE.fullmatch(name):
        raise InvalidInput(
            "Hostname inválido: use 2 a 63 caracteres (a-z, 0-9 e hífen), "
            "sem hífen no início ou no fim."
        )
    return name


# ----------------------------------------------------------- conexão
def _load_key():
    import paramiko

    path = config.SSH_KEY_PATH
    if not path or not os.path.isfile(path):
        raise SshConfigError()
    try:
        return paramiko.Ed25519Key.from_private_key_file(path, password=config.ssh_key_passphrase())
    except (paramiko.SSHException, ValueError) as exc:
        log.warning("chave SSH ilegível: %s", type(exc).__name__)
        raise SshConfigError("Chave SSH inválida (ou senha da chave não informada).") from exc


def connect():
    if not os.path.isfile(config.SSH_KNOWN_HOSTS):
        raise SshConfigError("known_hosts não encontrado. Veja o README (registrar a host key).")
    key = _load_key()
    import paramiko

    client = paramiko.SSHClient()
    client.load_host_keys(config.SSH_KNOWN_HOSTS)
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    t = config.SSH_TIMEOUT
    try:
        client.connect(
            config.SSH_HOST,
            port=config.SSH_PORT,
            username=config.SSH_USER,
            pkey=key,
            timeout=t,
            banner_timeout=t,
            auth_timeout=t,
            allow_agent=False,
            look_for_keys=False,
        )
    except paramiko.BadHostKeyException as exc:
        raise SshHostKeyError() from exc
    except paramiko.AuthenticationException as exc:
        raise SshAuthError() from exc
    except paramiko.SSHException as exc:
        if "known_hosts" in str(exc):  # RejectPolicy: host key ausente no known_hosts
            raise SshHostKeyError() from exc
        raise SshUnreachable() from exc
    except (OSError, EOFError) as exc:
        raise SshUnreachable() from exc
    return client


def run(client, argv: list[str], timeout: int = 15, input: str | None = None) -> tuple[int, str, str]:
    """Executa um comando montado a partir de uma LISTA de argumentos.

    `input` vai pela entrada padrão (nunca pela linha de comando, onde apareceria no `ps`).
    """
    import paramiko

    try:
        _in, out, err = client.exec_command(shlex.join(argv), timeout=timeout)
        if input is not None:
            _in.write(input)
            _in.channel.shutdown_write()
        stdout = out.read(1_000_000).decode("utf-8", "replace")
        stderr = err.read(100_000).decode("utf-8", "replace")
        return out.channel.recv_exit_status(), stdout, stderr
    except (paramiko.SSHException, OSError, EOFError) as exc:
        raise SshUnreachable() from exc


def fingerprint(client) -> str | None:
    transport = client.get_transport()
    key = transport.get_remote_server_key() if transport else None
    if key is None:
        return None
    digest = hashlib.sha256(key.asbytes()).digest()
    return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")


def _static_hostname(client) -> str:
    rc, out, _ = run(client, ["hostnamectl", "--static"])
    name = out.strip()
    if rc != 0 or not name:
        raise SshCommandError("Não foi possível ler o hostname atual.")
    return name


# ------------------------------------------------------------------ CP5
def parse_os_release(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("PRETTY_NAME="):
            return line.split("=", 1)[1].strip().strip('"')
    return ""


def get_info() -> dict:
    client = connect()
    try:
        whoami = run(client, ["whoami"])[1].strip()
        hostname = _static_hostname(client)
        uptime = run(client, ["uptime", "-p"])[1].strip()
        kernel = run(client, ["uname", "-r"])[1].strip()
        os_name = parse_os_release(run(client, ["cat", "/etc/os-release"])[1])
        return {
            "user": whoami,
            "hostname": hostname,
            "uptime": uptime,
            "kernel": kernel,
            "os": os_name,
            "host": config.SSH_HOST,
            "fingerprint": fingerprint(client),
            "original_hostname": kv_get(ORIGINAL_KEY),
            "timestamp": now_iso(),
        }
    finally:
        client.close()


def change_hostname(new_name: str) -> dict:
    validate_hostname(new_name)
    with _lock:
        client = connect()
        saved_now = False
        try:
            previous = _static_hostname(client)
            if kv_get(ORIGINAL_KEY) is None:  # registra o original ANTES de alterar
                kv_set(ORIGINAL_KEY, previous)
                saved_now = True
            rc, _, err = run(client, ["sudo", "-n", "hostnamectl", "set-hostname", new_name])
            if rc != 0:
                log.warning("set-hostname falhou (rc=%s): %s", rc, err.strip()[:200])
                if saved_now:
                    kv_delete(ORIGINAL_KEY)
                raise SshCommandError(
                    "O Debian recusou a alteração do hostname (verifique a regra do sudo)."
                )
            current = _static_hostname(client)
        finally:
            client.close()
    return {"previous": previous, "current": current, "original": kv_get(ORIGINAL_KEY)}


def restore_hostname() -> dict:
    with _lock:
        original = kv_get(ORIGINAL_KEY)
        if not original:
            raise Conflict("Não há hostname original registrado para restaurar.")
        if not _ORIGINAL_RE.fullmatch(original):
            raise SshCommandError("O hostname original registrado é inválido; restauração recusada.")
        client = connect()
        try:
            before = _static_hostname(client)
            rc, _, err = run(client, ["sudo", "-n", "hostnamectl", "set-hostname", original])
            if rc != 0:
                log.warning("restore falhou (rc=%s): %s", rc, err.strip()[:200])
                raise SshCommandError(
                    "O Debian recusou a restauração do hostname (verifique a regra do sudo)."
                )
            current = _static_hostname(client)
        finally:
            client.close()
        kv_delete(ORIGINAL_KEY)
    return {"previous": before, "current": current, "original": None}


# ------------------------------------------------------------------ CP6
def log_command(service: str, lines: int) -> list[str]:
    """Comando fixo para ler logs; `service` e `lines` só aceitam valores da lista branca."""
    if service not in config.SERVICES:
        raise InvalidInput("Serviço inválido.")
    if lines not in config.LOG_LINES:
        raise InvalidInput("Quantidade de linhas inválida (use 20, 50 ou 100).")
    if service == "apache2":
        return ["tail", "-n", str(lines), "/var/log/apache2/access.log"]
    return ["journalctl", "-u", service, "-n", str(lines), "--no-pager"]


def read_logs(service: str, lines: int) -> dict:
    argv = log_command(service, lines)
    client = connect()  # falha de conexão => "estado desconhecido", nunca "serviço parado"
    try:
        rc, out, err = run(client, argv, timeout=20)
    finally:
        client.close()
    if rc != 0:
        log.warning("leitura de logs falhou (%s rc=%s): %s", service, rc, err.strip()[:200])
        raise SshCommandError("Não foi possível ler os logs (permissão ou arquivo ausente no Debian).")
    rows = [ln[:2000] for ln in out.splitlines()][-lines:]
    return {"service": service, "lines": rows, "requested": lines, "timestamp": now_iso()}
