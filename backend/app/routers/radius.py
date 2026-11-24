"""Rotas do RADIUS (incremento 4). Consultas para leitor+; alterações só para admin
(mesma regra de DNS/DHCP: alterar configuração de serviço, seção 7)."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from .. import radius_client as rc
from ..deps import post_only_guard, raise_service_error, require_role
from ..errors import ServiceError

router = APIRouter(prefix="/api/radius", tags=["radius"])

for _path in ("/client", "/client/delete", "/policy", "/policy/delete", "/user", "/user/update", "/user/delete",
              "/load", "/load/delete", "/restore"):
    post_only_guard(router, _path, "admin")
post_only_guard(router, "/test", "operador")


class ClientBody(BaseModel):
    name: str = Field(max_length=32)
    ipaddr: str = Field(max_length=15)
    secret: str | None = Field(default=None, max_length=64)
    description: str | None = Field(default=None, max_length=80)
    require_ma: bool = False


class NameBody(BaseModel):
    name: str = Field(max_length=32)


class PolicyBody(BaseModel):
    name: str = Field(max_length=24)
    session_timeout: int = 3600
    idle_timeout: int = 0
    blocked: bool = False
    reply_message: str | None = Field(default="", max_length=80)


class UserBody(BaseModel):
    username: str = Field(max_length=32)
    password: str = Field(max_length=128)
    policy: str = Field(default="padrao", max_length=24)
    scheme: str = Field(default="ssha2-256", max_length=16)
    rounds: int = 5000


class UserUpdateBody(BaseModel):
    username: str = Field(max_length=32)
    enabled: bool | None = None
    policy: str | None = Field(default=None, max_length=24)
    password: str | None = Field(default=None, max_length=128)
    scheme: str | None = Field(default=None, max_length=16)
    rounds: int | None = None


class UsernameBody(BaseModel):
    username: str = Field(max_length=32)


class LoadBody(BaseModel):
    count: int
    scheme: str = Field(default="ssha2-256", max_length=16)
    rounds: int = 5000


class TestBody(BaseModel):
    username: str = Field(max_length=32)
    password: str = Field(max_length=128)


def _run(fn, *args):
    try:
        return fn(*args)
    except ServiceError as exc:
        raise_service_error(exc)


@router.get("/config", dependencies=[Depends(require_role("leitor"))])
def get_config():
    return _run(lambda: rc.public_state(rc.read_state()))


@router.get("/events", dependencies=[Depends(require_role("leitor"))])
def get_events():
    return _run(rc.auth_events)


@router.get("/sessions", dependencies=[Depends(require_role("leitor"))])
def get_sessions():
    return _run(rc.sessions)


@router.get("/client/{name}/secret")
def get_client_secret(name: str, user: dict = Depends(require_role("admin"))):
    return _run(rc.client_secret, name, user)


@router.post("/client")
def create_client(body: ClientBody, user: dict = Depends(require_role("admin"))):
    return _run(rc.create_client, body.model_dump(), user)


@router.post("/client/delete")
def delete_client(body: NameBody, user: dict = Depends(require_role("admin"))):
    return _run(rc.delete_client, body.name, user)


@router.post("/policy")
def save_policy(body: PolicyBody, user: dict = Depends(require_role("admin"))):
    return _run(rc.save_policy, body.model_dump(), user)


@router.post("/policy/delete")
def delete_policy(body: NameBody, user: dict = Depends(require_role("admin"))):
    return _run(rc.delete_policy, body.name, user)


@router.post("/user")
def create_user(body: UserBody, user: dict = Depends(require_role("admin"))):
    return _run(rc.create_user, body.model_dump(), user)


@router.post("/user/update")
def update_user(body: UserUpdateBody, user: dict = Depends(require_role("admin"))):
    return _run(rc.update_user, body.model_dump(), user)


@router.post("/user/delete")
def delete_user(body: UsernameBody, user: dict = Depends(require_role("admin"))):
    return _run(rc.delete_user, body.username, user)


@router.post("/load")
def create_load(body: LoadBody, user: dict = Depends(require_role("admin"))):
    return _run(rc.create_load_users, body.model_dump(), user)


@router.post("/load/delete")
def delete_load(user: dict = Depends(require_role("admin"))):
    return _run(rc.delete_load_users, user)


@router.post("/restore")
def restore(user: dict = Depends(require_role("admin"))):
    return _run(rc.restore_last_version, user)


@router.post("/test")
def self_test(body: TestBody, user: dict = Depends(require_role("operador"))):
    return _run(rc.self_test, body.model_dump(), user)
