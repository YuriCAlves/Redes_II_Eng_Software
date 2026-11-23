"""Auditoria persistente (secao 7.3 do enunciado).

Regras:
 - toda acao relevante (login, logout, consulta recusada, alteracao, operacao de
   servico) e registrada com usuario, perfil, recurso, acao, resultado, instante
   e um identificador de correlacao;
 - nunca grava senha, token, chave privada ou corpo de mensagem: quem chama
   `record()` manda só um `detail` curto já sanitizado;
 - a tabela so recebe INSERT (ver schema em db.py); nao existe update/delete
   aqui de proposito, para que o registro nao seja editavel pelo painel.
"""
import uuid

from .db import get_db


def new_correlation_id() -> str:
    return uuid.uuid4().hex[:12]


def record(
    *,
    user: dict | None,
    resource: str,
    action: str,
    result: str = "ok",
    detail: str | None = None,
    correlation_id: str | None = None,
) -> str:
    """Grava um evento de auditoria. Devolve o correlation_id usado (gera um se faltar)."""
    if result not in ("ok", "falha", "negado"):
        result = "falha"
    cid = correlation_id or new_correlation_id()
    username = user["username"] if user else "anonimo"
    role = user["role"] if user else "-"
    detail = (detail or "")[:500]  # nunca segredos; o chamador já deve sanitizar
    with get_db() as db:
        db.execute(
            "INSERT INTO audit_log (username, role, resource, action, result, correlation_id, detail) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (username, role, resource, action, result, cid, detail),
        )
    return cid


def query(*, username: str | None = None, resource: str | None = None, limit: int = 200) -> list[dict]:
    """Lista eventos, mais recentes primeiro. `username` filtra para 'próprias ações' (operador)."""
    limit = max(1, min(limit, 500))
    clauses, params = [], []
    if username:
        clauses.append("username = ?")
        params.append(username)
    if resource:
        clauses.append("resource = ?")
        params.append(resource)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with get_db() as db:
        rows = db.execute(
            f"SELECT id, ts, username, role, resource, action, result, correlation_id, detail "
            f"FROM audit_log {where} ORDER BY id DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def save_config_version(module: str, username: str, content: str, note: str = "") -> int:
    """Guarda a última configuração válida de um módulo, para permitir restauração."""
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO config_versions (module, username, content, note) VALUES (?, ?, ?, ?)",
            (module, username, content, note),
        )
        return cur.lastrowid


def last_config_version(module: str) -> dict | None:
    with get_db() as db:
        row = db.execute(
            "SELECT id, ts, username, content, note FROM config_versions "
            "WHERE module = ? ORDER BY id DESC LIMIT 1",
            (module,),
        ).fetchone()
    return dict(row) if row else None
