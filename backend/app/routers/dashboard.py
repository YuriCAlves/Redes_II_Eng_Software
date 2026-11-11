"""CP2 - endpoints de leitura do dashboard (qualquer perfil autenticado)."""
from fastapi import APIRouter, Depends

from .. import collectors
from ..deps import require_role

router = APIRouter(
    prefix="/api", tags=["dashboard"], dependencies=[Depends(require_role("leitor"))]
)


@router.get("/metrics")
def metrics():
    return collectors.collect_metrics()


@router.get("/services")
def services():
    return collectors.collect_services()


@router.get("/http-latency")
def http_latency():
    return collectors.http_latency()
