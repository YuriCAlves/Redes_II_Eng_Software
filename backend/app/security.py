"""Hash de senha (Argon2id) e sessões no servidor."""
import hashlib
import secrets
import time

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError

from . import config
from .db import get_db

_ph = PasswordHasher()

# Hash falso para igualar o tempo de resposta quando o usuário não existe
_DUMMY_HASH = _ph.hash("dummy-password-not-used")


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _ph.verify(password_hash or _DUMMY_HASH, password) and bool(password_hash)
    except (VerifyMismatchError, InvalidHashError):
        return False


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    with get_db() as db:
        db.execute("DELETE FROM sessions WHERE expires_at < ?", (int(time.time()),))
        db.execute(
            "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?,?,?)",
            (_digest(token), user_id, int(time.time()) + config.SESSION_TTL),
        )
    return token


def get_user_by_token(token: str | None):
    if not token:
        return None
    with get_db() as db:
        return db.execute(
            """SELECT u.id, u.username, u.role
               FROM sessions s JOIN users u ON u.id = s.user_id
               WHERE s.token_hash = ? AND s.expires_at >= ? AND u.active = 1""",
            (_digest(token), int(time.time())),
        ).fetchone()


def delete_session(token: str | None) -> None:
    if token:
        with get_db() as db:
            db.execute("DELETE FROM sessions WHERE token_hash = ?", (_digest(token),))
