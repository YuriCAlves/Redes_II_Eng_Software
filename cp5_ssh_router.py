"""
CP5 — Módulo SSH
FastAPI router: informações do servidor + alteração e restauração de hostname via paramiko
"""

import os
import re
import paramiko
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from app.auth import require_role
from app.database import get_db          # SQLAlchemy session ou similar
from app.models import HostnameHistory   # modelo simples: id, original, changed_at

router = APIRouter(prefix="/api/ssh", tags=["ssh"])

# ── Configuração ────────────────────────────────────────────────────────────
SSH_HOST        = os.getenv("SSH_HOST",     "192.168.100.10")
SSH_PORT        = int(os.getenv("SSH_PORT", 22))
SSH_USER        = os.getenv("SSH_USER",     "yuri")
SSH_KEY_PATH    = os.getenv("SSH_KEY_PATH", "")   # caminho absoluto definido no .env
SSH_KNOWN_HOSTS = os.getenv("SSH_KNOWN_HOSTS", "/app/known_hosts")  # arquivo no backend

HOSTNAME_REGEX = re.compile(r"^[a-z0-9][a-z0-9\-]{0,61}[a-z0-9]$")

# ── Helper: conexão segura ───────────────────────────────────────────────────

def _ssh_client() -> paramiko.SSHClient:
    """
    Abre cliente SSH com verificação de host key a partir de known_hosts.
    Lança HTTPException em caso de falha de conexão ou autenticação.
    Nunca expõe detalhes de credencial na mensagem de erro.
    """
    if not SSH_KEY_PATH or not os.path.isfile(SSH_KEY_PATH):
        raise HTTPException(500, "Chave SSH não configurada no servidor.")
    if not os.path.isfile(SSH_KNOWN_HOSTS):
        raise HTTPException(500, "Arquivo known_hosts não encontrado no servidor.")

    client = paramiko.SSHClient()
    client.load_host_keys(SSH_KNOWN_HOSTS)
    client.set_missing_host_key_policy(paramiko.RejectPolicy())  # rejeita hosts desconhecidos

    try:
        client.connect(
            SSH_HOST,
            port=SSH_PORT,
            username=SSH_USER,
            key_filename=SSH_KEY_PATH,
            timeout=10,
            look_for_keys=False,
            allow_agent=False,
        )
    except paramiko.AuthenticationException:
        raise HTTPException(503, "Não foi possível autenticar no servidor remoto.")
    except paramiko.SSHException as e:
        raise HTTPException(503, f"Erro SSH: {e}")
    except OSError:
        raise HTTPException(503, "Servidor remoto inacessível.")

    return client


def _run(client: paramiko.SSHClient, command: str) -> tuple[str, str]:
    """Executa um comando e retorna (stdout, stderr). Nunca injeta entrada do usuário diretamente."""
    _, stdout, stderr = client.exec_command(command, timeout=15)
    return stdout.read().decode().strip(), stderr.read().decode().strip()


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/info")
def ssh_info(_user=Depends(require_role(["admin", "operador", "leitor"]))):
    """
    Retorna: hostname, usuário logado, uptime legível, versão do kernel.
    Trata falha como "estado desconhecido", nunca como "serviço parado".
    """
    try:
        client = _ssh_client()
    except HTTPException as e:
        return {
            "status": "unreachable",
            "error":  e.detail,
            "hostname": None, "user": None, "uptime": None, "kernel": None,
        }

    try:
        hostname, _ = _run(client, "hostnamectl --static")
        user,     _ = _run(client, "whoami")
        uptime,   _ = _run(client, "uptime -p")
        kernel,   _ = _run(client, "uname -r")
    finally:
        client.close()

    return {
        "status":   "ok",
        "hostname": hostname,
        "user":     user,
        "uptime":   uptime,
        "kernel":   kernel,
    }


class ChangeHostnameRequest(BaseModel):
    new_hostname: str


@router.post("/hostname/change")
def change_hostname(
    req: ChangeHostnameRequest,
    db=Depends(get_db),
    _user=Depends(require_role(["admin", "operador"])),
):
    """
    Valida, salva o nome original no banco e aplica o novo hostname via SSH.
    Retorna o valor anterior e o novo.
    """
    new = req.new_hostname.strip().lower()

    # Validação rigorosa — nunca chega ao shell sem passar por aqui
    if not HOSTNAME_REGEX.match(new):
        raise HTTPException(
            400,
            "Hostname inválido. Use apenas letras minúsculas, números e hífens (2–63 chars).",
        )

    client = _ssh_client()
    try:
        current, err = _run(client, "hostnamectl --static")
        if err:
            raise HTTPException(502, "Não foi possível ler o hostname atual.")

        # Salva o original no banco (apenas se ainda não houver registro)
        existing = db.query(HostnameHistory).first()
        if not existing:
            db.add(HostnameHistory(original=current))
            db.commit()

        # Aplica: o nome é passado como argumento posicional — sem interpolação de shell
        cmd = ["sudo", "hostnamectl", "set-hostname", new]
        _, stderr = _run(client, " ".join(cmd))
        if stderr:
            raise HTTPException(502, f"Falha ao alterar hostname: {stderr}")
    finally:
        client.close()

    return {"previous": current, "new": new}


@router.post("/hostname/restore")
def restore_hostname(
    db=Depends(get_db),
    _user=Depends(require_role(["admin", "operador"])),
):
    """
    Lê o hostname original salvo no banco e o restaura via SSH.
    """
    record = db.query(HostnameHistory).first()
    if not record:
        raise HTTPException(404, "Nenhum hostname original registrado. Faça uma alteração primeiro.")

    original = record.original

    client = _ssh_client()
    try:
        cmd = ["sudo", "hostnamectl", "set-hostname", original]
        _, stderr = _run(client, " ".join(cmd))
        if stderr:
            raise HTTPException(502, f"Falha ao restaurar hostname: {stderr}")
        current, _ = _run(client, "hostnamectl --static")
    finally:
        client.close()

    return {"restored": original, "current": current}
