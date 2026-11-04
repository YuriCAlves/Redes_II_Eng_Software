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
