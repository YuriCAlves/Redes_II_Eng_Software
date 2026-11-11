"""CP3 - Arquivos via FTP real (vsftpd). Leitura: todos os perfis; envio: operador/admin."""
import mimetypes
import urllib.parse

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse

from .. import config, ftp_client
from ..deps import post_only_guard, raise_service_error, require_role
from ..errors import PayloadTooLarge, ServiceError
from ..collectors import now_iso

router = APIRouter(prefix="/api", tags=["files"])

post_only_guard(router, "/files/upload", "operador")


@router.get("/files", dependencies=[Depends(require_role("leitor"))])
def list_files():
    try:
        ftp = ftp_client.connect()
    except ServiceError as exc:
        raise_service_error(exc)
    try:
        return {"files": ftp_client.list_files(ftp), "timestamp": now_iso()}
    except ServiceError as exc:
        raise_service_error(exc)
    finally:
        ftp_client.close(ftp)


@router.post("/files/upload")
def upload(
    request: Request,
    file: UploadFile = File(...),
    overwrite: bool = Form(False),
    user: dict = Depends(require_role("operador")),
):
    try:
        name = ftp_client.validate_filename(file.filename)

        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > ftp_client.max_bytes() + 1024 * 1024:
            raise PayloadTooLarge(f"Arquivo maior que o limite de {config.MAX_UPLOAD_MB} MB.")

        sha_original, size = ftp_client.hash_and_measure(file.file)  # antes de enviar

        ftp = ftp_client.connect()
        try:
            replaced = ftp_client.upload(ftp, name, file.file, overwrite)
            sha_ftp, _ = ftp_client.sha256_remote(ftp, name)  # recuperado via FTP
        finally:
            ftp_client.close(ftp)
    except ServiceError as exc:
        raise_service_error(exc)

    url = ftp_client.public_url(name)
    sha_http, http_error = ftp_client.sha256_http(url, ftp_client.max_bytes())
    return {
        "name": name,
        "size": size,
        "replaced": replaced,
        "url": url,
        "sha256_original": sha_original,
        "sha256_ftp": sha_ftp,
        "sha256_http": sha_http,
        "http_error": http_error,
        "match_ftp": sha_ftp == sha_original,
        "match_http": None if sha_http is None else sha_http == sha_original,
        "timestamp": now_iso(),
    }


@router.get("/files/download", dependencies=[Depends(require_role("leitor"))])
def download(name: str):
    try:
        size, stream = ftp_client.open_download(name)
    except ServiceError as exc:
        raise_service_error(exc)
    quoted = urllib.parse.quote(name)
    ascii_name = name.encode("ascii", "replace").decode().replace("?", "_")
    return StreamingResponse(
        stream,
        media_type=mimetypes.guess_type(name)[0] or "application/octet-stream",
        headers={
            "Content-Length": str(size),
            "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quoted}",
            "X-Content-Type-Options": "nosniff",
        },
    )
