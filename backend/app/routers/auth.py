from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .. import audit, config
from ..db import get_db
from ..deps import current_user
from ..security import create_session, delete_session, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


@router.post("/login")
def login(body: LoginBody, request: Request, response: Response):
    if request.headers.get("x-requested-with") != "console":
        raise HTTPException(status_code=403, detail="Requisição inválida (CSRF)")

    with get_db() as db:
        user = db.execute(
            "SELECT id, username, password_hash, role FROM users WHERE username = ? AND active = 1",
            (body.username,),
        ).fetchone()

    if not verify_password(user["password_hash"] if user else None, body.password):
        # Mensagem genérica: não revela se o usuário existe
        audit.record(
            user={"username": body.username, "role": "-"},
            resource="auth",
            action="login",
            result="negado",
            detail="credenciais inválidas",
        )
        raise HTTPException(status_code=401, detail="Usuário ou senha inválidos")

    token = create_session(user["id"])
    audit.record(user=dict(user), resource="auth", action="login", result="ok")
    response.set_cookie(
        config.COOKIE_NAME,
        token,
        max_age=config.SESSION_TTL,
        httponly=True,
        samesite="lax",
        secure=config.COOKIE_SECURE,
        path="/",
    )
    return {"username": user["username"], "role": user["role"]}


@router.post("/logout")
def logout(request: Request, response: Response, user: dict = Depends(current_user)):
    delete_session(request.cookies.get(config.COOKIE_NAME))
    response.delete_cookie(config.COOKIE_NAME, path="/")
    audit.record(user=user, resource="auth", action="logout", result="ok")
    return {"ok": True}


@router.get("/me")
def me(user: dict = Depends(current_user)):
    return {"username": user["username"], "role": user["role"]}
