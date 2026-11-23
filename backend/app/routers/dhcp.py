"""Incremento 2 - DHCP. Concessões/config: leitura para todos. Reservas: só admin (seção 7)."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from .. import dhcp_client as hc
from ..deps import post_only_guard, raise_service_error, require_role
from ..errors import ServiceError

router = APIRouter(prefix="/api/dhcp", tags=["dhcp"])

post_only_guard(router, "/reservation", "admin")
post_only_guard(router, "/reservation/delete", "admin")
post_only_guard(router, "/restore", "admin")


class ReservationBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    hw_address: str = Field(alias="hw-address")
    ip_address: str = Field(alias="ip-address")
    hostname: str | None = None


class DeleteBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    ip_address: str = Field(alias="ip-address")


@router.get("/config", dependencies=[Depends(require_role("leitor"))])
def get_config():
    try:
        cfg = hc.read_config()
        cfg.pop("_raw", None)
        return cfg
    except ServiceError as exc:
        raise_service_error(exc)


@router.get("/leases", dependencies=[Depends(require_role("leitor"))])
def get_leases():
    try:
        return hc.read_leases()
    except ServiceError as exc:
        raise_service_error(exc)


@router.post("/reservation")
def create_reservation(body: ReservationBody, user: dict = Depends(require_role("admin"))):
    try:
        return hc.create_reservation(body.model_dump(by_alias=True, exclude_none=True), user)
    except ServiceError as exc:
        raise_service_error(exc)


@router.post("/reservation/delete")
def delete_reservation(body: DeleteBody, user: dict = Depends(require_role("admin"))):
    try:
        return hc.delete_reservation(body.ip_address, user)
    except ServiceError as exc:
        raise_service_error(exc)


@router.post("/restore")
def restore(user: dict = Depends(require_role("admin"))):
    try:
        return hc.restore_last_version(user)
    except ServiceError as exc:
        raise_service_error(exc)
