"""CP5 - SSH. Info: todos os perfis; alterar/restaurar hostname: somente admin."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from .. import ssh_client
from ..deps import post_only_guard, raise_service_error, require_role
from ..errors import ServiceError

router = APIRouter(prefix="/api/ssh", tags=["ssh"])

post_only_guard(router, "/hostname/change", "admin")
post_only_guard(router, "/hostname/restore", "admin")


class HostnameBody(BaseModel):
    hostname: str = Field(max_length=64)


@router.get("/info", dependencies=[Depends(require_role("leitor"))])
def info():
    try:
        return ssh_client.get_info()
    except ServiceError as exc:
        raise_service_error(exc)


@router.post("/hostname/change")
def change(body: HostnameBody, user: dict = Depends(require_role("admin"))):
    try:
        return ssh_client.change_hostname(body.hostname)
    except ServiceError as exc:
        raise_service_error(exc)


@router.post("/hostname/restore")
def restore(user: dict = Depends(require_role("admin"))):
    try:
        return ssh_client.restore_hostname()
    except ServiceError as exc:
        raise_service_error(exc)
