"""Configuração via variáveis de ambiente (nenhum segredo no código)."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Caminho do banco SQLite
DB_PATH = Path(os.environ.get("CONSOLE_DB_PATH", BASE_DIR / "console.db"))

# Duração da sessão (segundos)
SESSION_TTL = int(os.environ.get("CONSOLE_SESSION_TTL", "3600"))

# Nome do cookie de sessão
COOKIE_NAME = "console_session"

# Cookie "Secure" só faz sentido com HTTPS; na demo (HTTP) deixe 0
COOKIE_SECURE = os.environ.get("CONSOLE_COOKIE_SECURE", "0") == "1"

# Pasta com o build do React (npm run build)
FRONTEND_DIST = Path(
    os.environ.get("CONSOLE_FRONTEND_DIST", BASE_DIR.parent / "frontend" / "dist")
)

# Perfis, do menor para o maior privilégio
ROLES = ("leitor", "operador", "admin")
