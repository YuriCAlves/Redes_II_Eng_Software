from app.db import get_db

nova_tabela = """
CREATE TABLE IF NOT EXISTS hostname_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    original TEXT NOT NULL,
    saved_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

with get_db() as conn:
    conn.executescript(nova_tabela)

print(">> Tabela hostname_history criada com sucesso no banco!")
