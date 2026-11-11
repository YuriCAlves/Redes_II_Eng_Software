"""CP6 - Logs técnicos (somente leitura, todos os perfis), lidos no Debian via SSH."""
from fastapi import APIRouter, Depends

from .. import ssh_client
from ..deps import raise_service_error, require_role
from ..errors import ServiceError

router = APIRouter(prefix="/api", tags=["logs"])


@router.get("/logs", dependencies=[Depends(require_role("leitor"))])
def logs(service: str = "ssh", lines: int = 20):
    try:
        return ssh_client.read_logs(service, lines)
    except ServiceError as exc:
        raise_service_error(exc)
