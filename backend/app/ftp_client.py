"""Cliente FTP (ftplib, modo passivo) para o diretório publicado pelo Apache.

Regras de segurança:
 - nomes de arquivo validados por lista de caracteres permitidos (nada de "/", "..", "\\");
 - o arquivo é sempre gravado no diretório fixo FTP_DIR (cwd) usando só o nome base;
 - nada de shell, nada de SFTP; os comandos FTP recebem apenas nomes já validados.
"""
import ftplib
import hashlib
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from . import config
from .errors import Conflict, InvalidInput, NotFound, PayloadTooLarge, ServiceError

CHUNK = 64 * 1024
# Letras (inclui acentos), dígitos, "_", ".", "-" e espaço; começa com letra/dígito/_
NAME_RE = re.compile(r"^\w[\w.\- ]{0,99}$")


# ----------------------------------------------------------------- erros
class FtpConfigError(ServiceError):
    status, code = 503, "ftp_config"
    message = "Credenciais FTP não configuradas no servidor do console."


class FtpConnectionError(ServiceError):
    status, code = 502, "ftp_connection"
    message = "Falha de conexão com o servidor FTP."


class FtpAuthError(ServiceError):
    status, code = 502, "ftp_auth"
    message = "O servidor FTP recusou o login."


class FtpTransferError(ServiceError):
    status, code = 502, "ftp_transfer"
    message = "Falha de transferência FTP."


# ------------------------------------------------------------ validação
def validate_filename(name: str | None) -> str:
    if not name or name != name.strip() or not NAME_RE.fullmatch(name):
        raise InvalidInput(
            "Nome de arquivo inválido. Use letras, números, espaço, ponto, hífen e sublinhado."
        )
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in config.ALLOWED_EXT:
        raise InvalidInput(
            "Extensão não permitida. Permitidas: " + ", ".join(config.ALLOWED_EXT) + "."
        )
    return name


def max_bytes() -> int:
    return config.MAX_UPLOAD_MB * 1024 * 1024


def hash_and_measure(fileobj) -> tuple[str, int]:
    """SHA-256 e tamanho do arquivo recebido; aborta se passar do limite."""
    h, size = hashlib.sha256(), 0
    while chunk := fileobj.read(CHUNK):
        size += len(chunk)
        if size > max_bytes():
            raise PayloadTooLarge(f"Arquivo maior que o limite de {config.MAX_UPLOAD_MB} MB.")
        h.update(chunk)
    fileobj.seek(0)
    if size == 0:
        raise InvalidInput("O arquivo está vazio.")
    return h.hexdigest(), size


def public_url(name: str) -> str:
    return config.FILES_PUBLIC_URL.rstrip("/") + "/" + urllib.parse.quote(name)


# -------------------------------------------------------------- conexão
def connect() -> ftplib.FTP:
    password = config.ftp_password()
    if not password:
        raise FtpConfigError()
    ftp = ftplib.FTP(encoding="utf-8")
    try:
        ftp.connect(config.FTP_HOST, config.FTP_PORT, timeout=config.FTP_TIMEOUT)
    except (OSError, EOFError, ftplib.Error) as exc:
        raise FtpConnectionError() from exc
    try:
        ftp.login(config.FTP_USER, password)
        ftp.set_pasv(True)
        ftp.voidcmd("TYPE I")
    except ftplib.error_perm as exc:
        _quit(ftp)
        raise FtpAuthError() from exc
    except (OSError, EOFError, ftplib.Error) as exc:
        _quit(ftp)
        raise FtpConnectionError() from exc
    try:
        ftp.cwd(config.FTP_DIR)
    except ftplib.Error as exc:
        _quit(ftp)
        raise FtpTransferError("Diretório remoto indisponível no servidor FTP.") from exc
    except (OSError, EOFError) as exc:
        _quit(ftp)
        raise FtpConnectionError() from exc
    return ftp


def _quit(ftp: ftplib.FTP) -> None:
    try:
        ftp.quit()
    except Exception:  # noqa: BLE001 - encerrar sem mascarar o erro original
        try:
            ftp.close()
        except Exception:  # noqa: BLE001
            pass


close = _quit

_TRANSFER_ERRORS = (OSError, EOFError, ftplib.Error)


# ---------------------------------------------------------------- listagem
def parse_mdtm(response: str) -> str | None:
    """'213 20261107103000[.123]' -> ISO 8601 UTC."""
    m = re.search(r"\b(\d{14})", response)
    if not m:
        return None
    dt = datetime.strptime(m.group(1), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    return dt.isoformat(timespec="seconds")


def list_files(ftp: ftplib.FTP) -> list[dict]:
    """Nome, tamanho e data de cada arquivo (vsftpd não tem MLSD: NLST + SIZE + MDTM)."""
    try:
        names = ftp.nlst()
    except ftplib.error_perm as exc:
        if str(exc).startswith("550"):  # diretório vazio em alguns servidores
            return []
        raise FtpTransferError("Não foi possível listar o diretório remoto.") from exc
    except _TRANSFER_ERRORS as exc:
        raise FtpTransferError("Não foi possível listar o diretório remoto.") from exc

    files = []
    for raw in names:
        name = raw.rsplit("/", 1)[-1]
        if not NAME_RE.fullmatch(name):  # ignora ocultos/estranhos; nunca repassa ao FTP
            continue
        try:
            size = ftp.size(name)
        except ftplib.error_perm:  # diretório ou inacessível
            continue
        except _TRANSFER_ERRORS as exc:
            raise FtpTransferError("Não foi possível listar o diretório remoto.") from exc
        if size is None:
            continue
        try:
            modified = parse_mdtm(ftp.voidcmd("MDTM " + name))
        except ftplib.error_perm:
            modified = None
        except _TRANSFER_ERRORS as exc:
            raise FtpTransferError("Não foi possível listar o diretório remoto.") from exc
        files.append({"name": name, "size": size, "modified": modified, "url": public_url(name)})
    files.sort(key=lambda f: f["modified"] or "", reverse=True)
    return files


def remote_size(ftp: ftplib.FTP, name: str) -> int | None:
    try:
        return ftp.size(name)
    except ftplib.error_perm:
        return None
    except _TRANSFER_ERRORS as exc:
        raise FtpTransferError() from exc


# ------------------------------------------------------------------ upload
def upload(ftp: ftplib.FTP, name: str, fileobj, overwrite: bool) -> bool:
    """Envia por FTP. Retorna True se substituiu um arquivo existente.

    Grava num nome temporário e renomeia no final: o Apache nunca serve um arquivo pela metade.
    """
    exists = remote_size(ftp, name) is not None
    if exists and not overwrite:
        raise Conflict("Já existe um arquivo com esse nome. Marque 'substituir' para sobrescrever.")
    tmp = f".upload-{secrets.token_hex(6)}.part"
    try:
        ftp.storbinary("STOR " + tmp, fileobj, blocksize=CHUNK)
        ftp.rename(tmp, name)
    except _TRANSFER_ERRORS as exc:
        try:
            ftp.delete(tmp)
        except Exception:  # noqa: BLE001
            pass
        raise FtpTransferError("A transferência FTP falhou durante o envio.") from exc
    return exists


def sha256_remote(ftp: ftplib.FTP, name: str) -> tuple[str, int]:
    """Baixa o arquivo recém-enviado e calcula o SHA-256 do que o servidor guardou."""
    h, size = hashlib.sha256(), 0

    def sink(block: bytes) -> None:
        nonlocal size
        size += len(block)
        h.update(block)

    try:
        ftp.retrbinary("RETR " + name, sink, blocksize=CHUNK)
    except _TRANSFER_ERRORS as exc:
        raise FtpTransferError("A transferência FTP falhou ao recuperar o arquivo.") from exc
    return h.hexdigest(), size


# ---------------------------------------------------------------- download
def open_download(name: str):
    """Valida o arquivo e devolve (tamanho, gerador de blocos). O gerador fecha o FTP."""
    validate_filename(name)
    ftp = connect()
    try:
        size = remote_size(ftp, name)
        if size is None:
            raise NotFound("Arquivo não encontrado no servidor FTP.")
    except BaseException:
        close(ftp)
        raise

    def stream():
        conn = None
        try:
            conn = ftp.transfercmd("RETR " + name)
            while block := conn.recv(CHUNK):
                yield block
            conn.close()
            conn = None
            ftp.voidresp()
        except _TRANSFER_ERRORS:
            pass  # o cliente verá o download truncado (Content-Length não confere)
        finally:
            if conn is not None:
                try:
                    conn.close()
                except OSError:
                    pass
            close(ftp)

    return size, stream()


# ---------------------------------------------------------- hash via HTTP
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def sha256_http(url: str, limit: int) -> tuple[str | None, str | None]:
    """SHA-256 do arquivo como o Apache o publica. Retorna (hash, erro)."""
    try:
        with _opener.open(url, timeout=config.FTP_TIMEOUT) as resp:
            h, size = hashlib.sha256(), 0
            while block := resp.read(CHUNK):
                size += len(block)
                if size > limit:
                    return None, "arquivo publicado maior que o limite"
                h.update(block)
            return h.hexdigest(), None
    except urllib.error.HTTPError as exc:
        return None, f"o Apache respondeu HTTP {exc.code}"
    except (urllib.error.URLError, OSError, ValueError):
        return None, "não foi possível consultar o site (verifique /etc/hosts)"
