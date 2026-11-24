"""Rotas do laboratório virtual (incremento 3).

Perfis: leitor só vê catálogo e capacidade; operador e admin pedem e operam
as PRÓPRIAS instâncias (cota por perfil); admin também vê, desliga e exclui
as dos outros, mas nunca recebe os dados de acesso de outro usuário.
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from .. import lab
from ..deps import post_only_guard, raise_service_error, require_role
from ..errors import ServiceError

router = APIRouter(prefix="/api/lab", tags=["lab"])

for _path in ("/instances/{instance_id}/start", "/instances/{instance_id}/stop", "/instances/{instance_id}/delete"):
    post_only_guard(router, _path, "operador")


class RequestBody(BaseModel):
    image_id: str = Field(max_length=64)
    profile_id: str = Field(max_length=32)
    op_id: str = Field(min_length=32, max_length=36)


def _run(fn, *args):
    try:
        return fn(*args)
    except ServiceError as exc:
        raise_service_error(exc)


@router.get("/catalog")
def get_catalog(user: dict = Depends(require_role("leitor"))):
    cat = lab.catalog()
    profiles = [{**p, "allowed": user["role"] in p["roles"]} for p in cat["profiles"]]
    return {"images": cat["images"], "profiles": profiles, "quota": lab.quota_for(user)}


@router.get("/capacity", dependencies=[Depends(require_role("leitor"))])
def get_capacity():
    return lab.capacity()


@router.get("/instances")
def list_instances(user: dict = Depends(require_role("operador"))):
    return lab.list_instances(user)


@router.post("/instances")
def request_instance(body: RequestBody, user: dict = Depends(require_role("operador"))):
    instance, created = _run(lab.request_instance, user, body.image_id, body.profile_id, body.op_id)
    return {"instance": instance, "created": created}


@router.get("/instances/{instance_id}")
def get_instance(instance_id: int, user: dict = Depends(require_role("operador"))):
    return _run(lab.get_instance, user, instance_id)


@router.post("/instances/{instance_id}/start")
def start(instance_id: int, user: dict = Depends(require_role("operador"))):
    return _run(lab.request_start, user, instance_id)


@router.post("/instances/{instance_id}/stop")
def stop(instance_id: int, user: dict = Depends(require_role("operador"))):
    return _run(lab.request_stop, user, instance_id)


@router.post("/instances/{instance_id}/delete")
def delete(instance_id: int, user: dict = Depends(require_role("operador"))):
    return _run(lab.request_delete, user, instance_id)


@router.get("/instances/{instance_id}/access")
def access(instance_id: int, user: dict = Depends(require_role("operador"))):
    return _run(lab.access, user, instance_id)
