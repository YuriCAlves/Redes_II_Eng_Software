"""Auditoria (secao 7.3): leitor nao ve; operador ve so as proprias acoes; admin ve tudo."""
from fastapi import APIRouter, Depends

from .. import audit
from ..deps import require_role

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("", dependencies=[])
def list_audit(resource: str | None = None, limit: int = 100, user: dict = Depends(require_role("operador"))):
    own_only = user["role"] != "admin"
    return audit.query(username=user["username"] if own_only else None, resource=resource, limit=limit)
