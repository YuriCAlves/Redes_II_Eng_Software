"""
CP3 — Módulo FTP/Arquivos
FastAPI router: upload, listagem, download e verificação SHA-256 via ftplib
"""

from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from fastapi.responses import StreamingResponse
from ftplib import FTP, error_perm, error_temp, all_errors
from datetime import datetime
import hashlib
import io
import os

from app.auth import require_role  # injeta usuário autenticado; operador ou admin para escrita

router = APIRouter(prefix="/api/files", tags=["files"])

# ── Configuração ────────────────────────────────────────────────────────────
FTP_HOST = os.getenv("FTP_HOST", "192.168.100.10")
FTP_PORT = int(os.getenv("FTP_PORT", 21))
FTP_USER = os.getenv("FTP_USER", "ftpuser")
FTP_PASS = os.getenv("FTP_PASS", "")          # defina no .env
FTP_DIR  = "/arquivos"                         # diretório remoto fixo
HTTP_BASE = os.getenv("HTTP_BASE", "http://site.empresa.test/arquivos")

ALLOWED_EXTENSIONS = {"pdf", "txt", "png", "jpg", "jpeg"}
MAX_SIZE_BYTES = int(os.getenv("MAX_UPLOAD_MB", 10)) * 1024 * 1024


# ── Helpers ─────────────────────────────────────────────────────────────────

def _safe_filename(name: str) -> str:
    """Remove trechos perigosos e garante nome apenas dentro de FTP_DIR."""
    # Rejeita qualquer tentativa de path traversal
    base = os.path.basename(name)
    if not base or base.startswith("."):
        raise HTTPException(400, "Nome de arquivo inválido.")
    return base


def _extension_ok(filename: str) -> bool:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return ext in ALLOWED_EXTENSIONS


def _ftp_connect() -> FTP:
    """Abre conexão FTP passiva; lança HTTPException em caso de falha."""
    try:
        ftp = FTP()
        ftp.connect(FTP_HOST, FTP_PORT, timeout=10)
        ftp.login(FTP_USER, FTP_PASS)
        ftp.set_pasv(True)
        ftp.cwd(FTP_DIR)
        return ftp
    except ConnectionRefusedError:
        raise HTTPException(503, "Não foi possível conectar ao servidor FTP.")
    except error_perm as e:
        raise HTTPException(502, f"Erro de autenticação FTP: {e}")
    except all_errors as e:
        raise HTTPException(502, f"Erro FTP: {e}")


def _sha256_of_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _retrieve_bytes(ftp: FTP, filename: str) -> bytes:
    """Baixa um arquivo do FTP e devolve como bytes."""
    buf = io.BytesIO()
    ftp.retrbinary(f"RETR {filename}", buf.write)
    return buf.getvalue()


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    _user=Depends(require_role(["admin", "operador"])),
):
    """
    Valida extensão e tamanho, envia via FTP e retorna SHA-256 original + recuperado.
    """
    filename = _safe_filename(file.filename)

    if not _extension_ok(filename):
        raise HTTPException(400, f"Extensão não permitida. Use: {', '.join(ALLOWED_EXTENSIONS)}")

    content = await file.read()

    if len(content) > MAX_SIZE_BYTES:
        raise HTTPException(413, f"Arquivo excede o limite de {MAX_SIZE_BYTES // (1024*1024)} MB.")

    sha_original = _sha256_of_bytes(content)

    # Envia via FTP
    ftp = _ftp_connect()
    try:
        ftp.storbinary(f"STOR {filename}", io.BytesIO(content))
    except all_errors as e:
        ftp.quit()
        raise HTTPException(502, f"Falha na transferência FTP: {e}")

    # Recupera imediatamente para verificação
    try:
        recovered = _retrieve_bytes(ftp, filename)
        sha_recovered = _sha256_of_bytes(recovered)
    except all_errors as e:
        ftp.quit()
        raise HTTPException(502, f"Arquivo enviado, mas falha na verificação: {e}")
    finally:
        try:
            ftp.quit()
        except Exception:
            pass

    return {
        "filename": filename,
        "size": len(content),
        "sha256_original": sha_original,
        "sha256_recovered": sha_recovered,
        "integrity_ok": sha_original == sha_recovered,
        "http_url": f"{HTTP_BASE}/{filename}",
    }


@router.get("/list")
def list_files(_user=Depends(require_role(["admin", "operador", "leitor"]))):
    """Lista arquivos no diretório FTP com nome, tamanho e data."""
    ftp = _ftp_connect()
    files = []
    try:
        raw = []
        ftp.retrlines("LIST", raw.append)
        for line in raw:
            # Formato típico: "-rw-r--r-- 1 user group 12345 Nov 05 14:32 arquivo.pdf"
            parts = line.split(None, 8)
            if len(parts) < 9 or parts[0].startswith("d"):
                continue  # ignora diretórios
            name = parts[8]
            size_bytes = int(parts[4])
            date_str = f"{parts[5]} {parts[6]} {parts[7]}"
            files.append({
                "name": name,
                "size_bytes": size_bytes,
                "size_human": _human_size(size_bytes),
                "date": date_str,
                "http_url": f"{HTTP_BASE}/{name}",
            })
    except all_errors as e:
        raise HTTPException(502, f"Erro ao listar arquivos FTP: {e}")
    finally:
        try:
            ftp.quit()
        except Exception:
            pass
    return {"files": files, "count": len(files)}


@router.get("/download/{filename}")
def download_file(
    filename: str,
    _user=Depends(require_role(["admin", "operador", "leitor"])),
):
    """Baixa um arquivo via FTP e o serve como streaming response."""
    safe = _safe_filename(filename)
    ftp = _ftp_connect()
    try:
        data = _retrieve_bytes(ftp, safe)
    except error_perm:
        raise HTTPException(404, "Arquivo não encontrado no servidor FTP.")
    except all_errors as e:
        raise HTTPException(502, f"Falha ao baixar arquivo: {e}")
    finally:
        try:
            ftp.quit()
        except Exception:
            pass

    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{safe}"'},
    )


# ── Utilitário ───────────────────────────────────────────────────────────────

def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"
