"""Dependências de autenticação/autorização aplicadas NO BACKEND em cada rota."""
from fastapi import Depends, HTTPException, Request

from . import config
from .security import get_user_by_token

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def current_user(request: Request):
    user = get_user_by_token(request.cookies.get(config.COOKIE_NAME))
    if user is None:
        raise HTTPException(status_code=401, detail="Não autenticado")

    # Proteção CSRF simples: métodos que alteram estado exigem este header,
    # que um formulário/site de terceiros não consegue enviar sem CORS.
    if request.method not in _SAFE_METHODS:
        if request.headers.get("x-requested-with") != "console":
            raise HTTPException(status_code=403, detail="Requisição inválida (CSRF)")
    return dict(user)


def require_role(minimum: str):
    """Exige perfil >= `minimum` (leitor < operador < admin)."""
    needed = config.ROLES.index(minimum)

    def checker(user: dict = Depends(current_user)):
        if config.ROLES.index(user["role"]) < needed:
            raise HTTPException(status_code=403, detail="Permissão insuficiente")
        return user

    return checker


def raise_service_error(exc) -> None:
    """Converte um ServiceError em resposta HTTP com {code, message}."""
    raise HTTPException(
        status_code=exc.status, detail={"code": exc.code, "message": exc.message}
    )


def post_only_guard(router, path: str, role: str) -> None:
    """Registra um GET em `path` que só devolve 405 DEPOIS de checar o perfil.

    Assim um leitor que digita a URL de uma rota de escrita recebe 403 (e não 405),
    como exige o critério de aceitação do CP1.
    """

    @router.get(path, include_in_schema=False, dependencies=[Depends(require_role(role))])
    def _post_only():
        raise HTTPException(status_code=405, detail="Use POST")
