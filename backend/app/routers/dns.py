"""Incremento 1 - DNS. Consulta: todos os perfis. Criar/editar/remover/restaurar: só admin
(seção 7 do enunciado: "Alterar configurações de serviços" é exclusivo do administrador,
mesmo padrão já usado em ssh.py para trocar o hostname)."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from .. import dns_client as dc
from ..deps import post_only_guard, raise_service_error, require_role
from ..errors import ServiceError

router = APIRouter(prefix="/api/dns", tags=["dns"])

post_only_guard(router, "/record", "admin")
post_only_guard(router, "/record/delete", "admin")
post_only_guard(router, "/{zone}/restore", "admin")


class RecordBody(BaseModel):
    zone: str = Field(pattern="^(fwd|rev)$")
    name: str
    type: str
    value: str
    priority: int | None = None


class DeleteBody(BaseModel):
    zone: str = Field(pattern="^(fwd|rev)$")
    name: str
    type: str
    value: str


@router.get("/{zone}", dependencies=[Depends(require_role("leitor"))])
def get_zone(zone: str):
    if zone not in ("fwd", "rev"):
        raise_service_error(dc.InvalidInput("Zona inválida."))
    try:
        return dc.read_zone(zone)
    except ServiceError as exc:
        raise_service_error(exc)


@router.post("/record")
def create_record(body: RecordBody, user: dict = Depends(require_role("admin"))):
    try:
        return dc.create_record(body.zone, body.model_dump(exclude={"zone"}), user)
    except ServiceError as exc:
        raise_service_error(exc)


@router.post("/record/delete")
def delete_record(body: DeleteBody, user: dict = Depends(require_role("admin"))):
    try:
        return dc.delete_record(body.zone, body.name, body.type, body.value, user)
    except ServiceError as exc:
        raise_service_error(exc)


@router.post("/{zone}/restore")
def restore(zone: str, user: dict = Depends(require_role("admin"))):
    if zone not in ("fwd", "rev"):
        raise_service_error(dc.InvalidInput("Zona inválida."))
    try:
        return dc.restore_last_version(zone, user)
    except ServiceError as exc:
        raise_service_error(exc)


@router.get("/query/run", dependencies=[Depends(require_role("leitor"))])
def run_query(name: str, type: str = "A"):
    try:
        return dc.query(name, type)
    except ServiceError as exc:
        raise_service_error(exc)
