"""E-mail: envio por SMTP (smtplib) e leitura/gravação por IMAP (imaplib).

O envio e a cópia em "Enviados" são operações SEPARADAS, com status separados:
se o SMTP aceitou mas o IMAP falhou, o chamador sabe que NÃO deve reenviar.
"""
import email
import email.policy
import email.utils
import imaplib
import os
import re
import smtplib
import time
from email.message import EmailMessage
from html.parser import HTMLParser

from . import config
from .errors import InvalidInput, NotFound, ServiceError


# ----------------------------------------------------------------- erros
class MailConfigError(ServiceError):
    status, code = 503, "mail_config"
    message = "Senha da conta de e-mail não configurada no servidor do console."


class ImapConnectionError(ServiceError):
    status, code = 502, "imap_connection"
    message = "Falha de conexão com o servidor IMAP."


class ImapAuthError(ServiceError):
    status, code = 502, "imap_auth"
    message = "O servidor IMAP recusou o login da conta."


class ImapError(ServiceError):
    status, code = 502, "imap_error"
    message = "Erro ao consultar o servidor IMAP."


# ------------------------------------------------------------ validação
ADDR_RE = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,190}$")
UID_RE = re.compile(r"^\d{1,12}$")
FOLDERS = ("inbox", "sent")
MAX_SUBJECT, MAX_BODY, MAX_RECIPIENTS = 200, 100_000, 10


def accounts() -> list[str]:
    return list(config.MAIL_ACCOUNTS)


def validate_account(address: str) -> str:
    if address not in config.MAIL_ACCOUNTS:  # lista fixa: nunca aceita conta arbitrária
        raise InvalidInput("Conta de e-mail inválida.")
    return address


def validate_folder(folder: str) -> str:
    if folder not in FOLDERS:
        raise InvalidInput("Pasta inválida.")
    return folder


def validate_uid(uid: str) -> str:
    if not UID_RE.fullmatch(uid or ""):
        raise InvalidInput("Identificador de mensagem inválido.")
    return uid


def parse_recipients(text: str) -> list[str]:
    parts = [p.strip() for p in re.split(r"[,;]", text or "") if p.strip()]
    if not parts:
        raise InvalidInput("Informe ao menos um destinatário.")
    if len(parts) > MAX_RECIPIENTS:
        raise InvalidInput(f"No máximo {MAX_RECIPIENTS} destinatários.")
    for p in parts:
        if not ADDR_RE.fullmatch(p):
            raise InvalidInput(f"Endereço de e-mail inválido: {p[:60]}")
    return parts


def build_message(sender: str, recipients: list[str], subject: str, body: str) -> EmailMessage:
    if len(subject) > MAX_SUBJECT or len(body) > MAX_BODY:
        raise InvalidInput("Assunto ou corpo acima do limite permitido.")
    if "\r" in subject or "\n" in subject:  # barra injeção de cabeçalhos
        raise InvalidInput("O assunto não pode ter quebras de linha.")
    msg = EmailMessage()
    try:
        msg["From"] = sender
        msg["To"] = ", ".join(recipients)
        msg["Subject"] = subject
        msg["Date"] = email.utils.formatdate(localtime=True)
        msg["Message-ID"] = email.utils.make_msgid(domain=sender.split("@", 1)[1])
        msg.set_content(body)
    except ValueError as exc:  # cabeçalho recusado pelo módulo email
        raise InvalidInput("Dados do e-mail inválidos.") from exc
    return msg


# ------------------------------------------------------------------ SMTP
def send_smtp(msg: EmailMessage, sender: str, recipients: list[str]) -> dict:
    try:
        with smtplib.SMTP(config.MAIL_HOST, config.SMTP_PORT, timeout=config.MAIL_TIMEOUT) as s:
            s.send_message(msg, from_addr=sender, to_addrs=recipients)
        return {"ok": True}
    except smtplib.SMTPRecipientsRefused:
        return {"ok": False, "error": "Destinatário recusado pelo servidor SMTP."}
    except smtplib.SMTPResponseException as exc:
        return {"ok": False, "error": f"O servidor SMTP recusou a mensagem (código {exc.smtp_code})."}
    except (smtplib.SMTPException, OSError):
        return {"ok": False, "error": "Falha de conexão com o servidor SMTP."}


# ------------------------------------------------------------------ IMAP
def _q(name: str) -> str:
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


def imap_connect(address: str) -> imaplib.IMAP4:
    user, env_name = config.MAIL_ACCOUNTS[validate_account(address)]
    password = os.environ.get(env_name)
    if not password:
        raise MailConfigError()
    try:
        imap = imaplib.IMAP4(config.MAIL_HOST, config.IMAP_PORT, timeout=config.MAIL_TIMEOUT)
    except (OSError, imaplib.IMAP4.error) as exc:
        raise ImapConnectionError() from exc
    try:
        imap.login(user, password)  # usuário sem domínio + senha Linux
    except imaplib.IMAP4.error as exc:
        _logout(imap)
        raise ImapAuthError() from exc
    except OSError as exc:
        raise ImapConnectionError() from exc
    return imap


def _logout(imap) -> None:
    try:
        imap.logout()
    except Exception:  # noqa: BLE001
        pass


_SENT_NAMES = ("sent", "enviados", "sent items", "sent messages", "itens enviados")
_LIST_RE = re.compile(r'^\((?P<flags>[^)]*)\)\s+(?:"[^"]*"|NIL)\s+(?P<name>.+)$')


def pick_sent_folder(list_lines: list) -> str:
    """Escolhe a pasta de enviados a partir da resposta do LIST (flag \\Sent ou nome conhecido)."""
    by_name = None
    for raw in list_lines:
        if not raw:
            continue
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        m = _LIST_RE.match(line.strip())
        if not m:
            continue
        name = m.group("name").strip()
        if name.startswith('"') and name.endswith('"'):
            name = name[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        if "\\sent" in m.group("flags").lower():
            return name
        if by_name is None and name.lower() in _SENT_NAMES:
            by_name = name
    return by_name or "Sent"


def _sent_folder(imap) -> str:
    typ, data = imap.list()
    return pick_sent_folder(data) if typ == "OK" else "Sent"


def append_sent(address: str, msg: EmailMessage) -> dict:
    """Guarda cópia da mensagem enviada na pasta Enviados do remetente (IMAP APPEND)."""
    try:
        imap = imap_connect(address)
    except ServiceError as exc:
        return {"ok": False, "error": exc.message}
    try:
        folder = _sent_folder(imap)
        typ, _ = imap.append(_q(folder), r"\Seen", imaplib.Time2Internaldate(time.time()), msg.as_bytes())
        if typ != "OK":
            return {"ok": False, "error": "O servidor IMAP não aceitou a cópia em Enviados."}
        return {"ok": True, "folder": folder}
    except (imaplib.IMAP4.error, OSError):
        return {"ok": False, "error": "Falha ao salvar a cópia na pasta Enviados."}
    finally:
        _logout(imap)


def _select(imap, folder: str) -> bool:
    mailbox = "INBOX" if folder == "inbox" else _sent_folder(imap)
    typ, _ = imap.select(_q(mailbox), readonly=True)
    return typ == "OK"


def _header(msg, name: str) -> str:
    try:
        return str(msg.get(name, "") or "")
    except Exception:  # noqa: BLE001 - cabeçalho malformado não derruba a listagem
        return ""


def parse_fetch_headers(data: list) -> list[dict]:
    """Interpreta a resposta de UID FETCH (UID BODY.PEEK[HEADER.FIELDS (...)])."""
    out = []
    for i, item in enumerate(data):
        if not isinstance(item, tuple):
            continue
        trailing = data[i + 1] if i + 1 < len(data) and isinstance(data[i + 1], bytes) else b""
        m = re.search(rb"UID (\d+)", item[0] + b" " + trailing)
        if not m:
            continue
        msg = email.message_from_bytes(item[1], policy=email.policy.default)
        out.append(
            {
                "uid": int(m.group(1)),
                "from": _header(msg, "From"),
                "to": _header(msg, "To"),
                "subject": _header(msg, "Subject") or "(sem assunto)",
                "date": _header(msg, "Date"),
            }
        )
    out.sort(key=lambda r: r["uid"], reverse=True)  # mais novas primeiro
    return out


def list_messages(address: str, folder: str, limit: int = 50) -> list[dict]:
    validate_folder(folder)
    limit = max(1, min(int(limit), 100))
    imap = imap_connect(address)
    try:
        if not _select(imap, folder):
            return []  # pasta ainda não existe (nenhum envio feito)
        typ, data = imap.uid("SEARCH", None, "ALL")
        if typ != "OK":
            raise ImapError()
        uids = (data[0] or b"").split()[-limit:]
        if not uids:
            return []
        typ, data = imap.uid(
            "FETCH",
            b",".join(uids).decode(),
            "(UID BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE)])",
        )
        if typ != "OK":
            raise ImapError()
        return parse_fetch_headers(data)
    except (imaplib.IMAP4.error, OSError) as exc:
        raise ImapError() from exc
    finally:
        _logout(imap)


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in ("br", "p", "div", "li", "tr"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    p = _TextExtractor()
    p.feed(html)
    return re.sub(r"\n{3,}", "\n\n", "".join(p.parts)).strip()


def parse_full_message(raw: bytes) -> dict:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    body_part = msg.get_body(preferencelist=("plain", "html"))
    text = ""
    if body_part is not None:
        text = body_part.get_content()
        if body_part.get_content_type() == "text/html":
            text = html_to_text(text)
    return {
        "from": _header(msg, "From"),
        "to": _header(msg, "To"),
        "subject": _header(msg, "Subject") or "(sem assunto)",
        "date": _header(msg, "Date"),
        "body": text[:200_000],
        "has_attachments": any(p.get_content_disposition() == "attachment" for p in msg.walk()),
    }


def read_message(address: str, folder: str, uid: str) -> dict:
    validate_folder(folder)
    validate_uid(uid)
    imap = imap_connect(address)
    try:
        if not _select(imap, folder):
            raise NotFound("Pasta não encontrada.")
        typ, data = imap.uid("FETCH", uid, "(BODY.PEEK[])")
        if typ != "OK":
            raise ImapError()
        raw = next((i[1] for i in data if isinstance(i, tuple)), None)
        if raw is None:
            raise NotFound("Mensagem não encontrada.")
        return {"uid": int(uid), **parse_full_message(raw)}
    except (imaplib.IMAP4.error, OSError) as exc:
        raise ImapError() from exc
    finally:
        _logout(imap)
