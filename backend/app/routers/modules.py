"""Rotas dos módulos (Visão Geral, Serviços, Arquivos, E-mail, SSH, Logs).

No Checkpoint 1 são apenas ESQUELETOS que provam o controle de acesso:
 - rotas de leitura  -> qualquer perfil autenticado (leitor, operador, admin)
 - rotas de escrita  -> operador/admin (SSH hostname: somente admin)

As rotas de escrita aceitam GET e POST de propósito: assim, um leitor que digite
a URL no navegador (GET) recebe 403 ANTES de qualquer outra verificação.
Para quem tem permissão, GET devolve 405 e POST devolve 501 (ainda não implementado).
"""
from fastapi import APIRouter, Depends, HTTPException, Request

from ..deps import require_role

router = APIRouter(prefix="/api", tags=["modules"])

read_access = Depends(require_role("leitor"))
write_access = Depends(require_role("operador"))
admin_access = Depends(require_role("admin"))


def _todo(name: str):
    return {"modulo": name, "status": "esqueleto - implementado nos próximos checkpoints"}


def _write_stub(request: Request, name: str):
    if request.method != "POST":
        raise HTTPException(status_code=405, detail="Use POST")
    raise HTTPException(status_code=501, detail=f"{name}: ainda não implementado")


# ---------- Leitura (todos os perfis) ----------
@router.get("/metrics", dependencies=[read_access])
def metrics():
    return _todo("metrics (CP2)")


@router.get("/services", dependencies=[read_access])
def services():
    return _todo("services (CP2)")


@router.get("/files", dependencies=[read_access])
def files_list():
    return _todo("files list (CP3)")


@router.get("/email/inbox", dependencies=[read_access])
def email_inbox():
    return _todo("email inbox (CP4)")


@router.get("/ssh/info", dependencies=[read_access])
def ssh_info():
    return _todo("ssh info (CP5)")


@router.get("/logs", dependencies=[read_access])
def logs():
    return _todo("logs (CP6)")


# ---------- Escrita (operador ou admin) ----------
@router.api_route("/files/upload", methods=["GET", "POST"], dependencies=[write_access])
def files_upload(request: Request):
    _write_stub(request, "upload FTP (CP3)")


@router.api_route("/email/send", methods=["GET", "POST"], dependencies=[write_access])
def email_send(request: Request):
    _write_stub(request, "envio de e-mail (CP4)")


# ---------- Escrita sensível (somente admin) ----------
@router.api_route("/ssh/hostname/change", methods=["GET", "POST"], dependencies=[admin_access])
def hostname_change(request: Request):
    _write_stub(request, "alterar hostname (CP5)")


@router.api_route("/ssh/hostname/restore", methods=["GET", "POST"], dependencies=[admin_access])
def hostname_restore(request: Request):
    _write_stub(request, "restaurar hostname (CP5)")
