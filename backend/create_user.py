"""Cria ou atualiza um usuário do console.

Uso:
    python create_user.py <usuario> <perfil> [--password SENHA]

Se --password for omitido, a senha é pedida de forma oculta.
Perfis: leitor | operador | admin
"""
import argparse
import getpass
import sys

from app import config
from app.db import get_db, init_db
from app.security import hash_password


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("username")
    p.add_argument("role", choices=config.ROLES)
    p.add_argument("--password")
    args = p.parse_args()

    password = args.password or getpass.getpass("Senha: ")
    if len(password) < 8:
        print("A senha deve ter pelo menos 8 caracteres.", file=sys.stderr)
        return 1

    init_db()
    with get_db() as db:
        db.execute(
            """INSERT INTO users (username, password_hash, role) VALUES (?,?,?)
               ON CONFLICT(username) DO UPDATE SET
                 password_hash = excluded.password_hash, role = excluded.role, active = 1""",
            (args.username, hash_password(password), args.role),
        )
    print(f"Usuário '{args.username}' ({args.role}) salvo.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
