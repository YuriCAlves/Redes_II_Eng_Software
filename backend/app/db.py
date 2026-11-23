import sqlite3
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('leitor','operador','admin')),
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at INTEGER NOT NULL
);

-- Auditoria persistente (Parte 2, secao 7.3). Sem UPDATE/DELETE pela aplicacao:
-- so insert_audit() grava, e so por codigo do backend (nunca a partir de input do usuario).
CREATE TABLE IF NOT EXISTS audit_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             TEXT NOT NULL DEFAULT (datetime('now')),
    username       TEXT NOT NULL,
    role           TEXT NOT NULL,
    resource       TEXT NOT NULL,   -- ex.: 'dns', 'dhcp', 'auth', 'labvm'
    action         TEXT NOT NULL,   -- ex.: 'record.create', 'login', 'reserve.delete'
    result         TEXT NOT NULL CHECK (result IN ('ok', 'falha', 'negado')),
    correlation_id TEXT NOT NULL,
    detail         TEXT             -- texto curto, sanitizado; nunca senha/token/chave
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log (ts);
CREATE INDEX IF NOT EXISTS idx_audit_username ON audit_log (username);

-- Versionamento de configuracoes (DNS/DHCP): guarda o conteudo valido anterior
-- para permitir restauracao quando uma alteracao for aplicada e precisar reverter.
CREATE TABLE IF NOT EXISTS config_versions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    module     TEXT NOT NULL,      -- 'dns' | 'dhcp'
    ts         TEXT NOT NULL DEFAULT (datetime('now')),
    username   TEXT NOT NULL,
    content    TEXT NOT NULL,      -- conteudo completo do arquivo de configuracao/zona
    note       TEXT
);
CREATE INDEX IF NOT EXISTS idx_config_versions_module ON config_versions (module, id);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def get_db():
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_db() as conn:
        conn.executescript(SCHEMA)


def kv_get(key: str) -> str | None:
    with get_db() as db:
        row = db.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def kv_set(key: str, value: str) -> None:
    with get_db() as db:
        db.execute(
            "INSERT INTO kv (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def kv_delete(key: str) -> None:
    with get_db() as db:
        db.execute("DELETE FROM kv WHERE key = ?", (key,))
