"""
CP4 — Módulo E-mail (SMTP + IMAP)
FastAPI router: envio via smtplib, leitura/listagem via imaplib, cópia em Enviados
"""

import os
import imaplib
import smtplib
import email as email_lib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import decode_header
from email.utils import formatdate
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, EmailStr

from app.auth import require_role

router = APIRouter(prefix="/api/email", tags=["email"])

# ── Configuração ────────────────────────────────────────────────────────────
SMTP_HOST = os.getenv("SMTP_HOST", "192.168.100.10")
SMTP_PORT = int(os.getenv("SMTP_PORT", 25))
IMAP_HOST = os.getenv("IMAP_HOST", "192.168.100.10")
IMAP_PORT = int(os.getenv("IMAP_PORT", 143))

# Mapeamento conta→senha Linux (configurado via variáveis de ambiente)
ACCOUNT_PASSWORDS = {
    "aluno":   os.getenv("IMAP_PASS_ALUNO",   ""),
    "suporte": os.getenv("IMAP_PASS_SUPORTE", ""),
}
VALID_ACCOUNTS = {"aluno@empresa.test", "suporte@empresa.test"}
SENT_FOLDER    = "Sent"


# ── Helpers ─────────────────────────────────────────────────────────────────

def _username(email_addr: str) -> str:
    """Extrai o usuário Linux da conta (sem domínio)."""
    return email_addr.split("@")[0]


def _imap_connect(account_email: str) -> imaplib.IMAP4:
    user = _username(account_email)
    pwd  = ACCOUNT_PASSWORDS.get(user, "")
    try:
        imap = imaplib.IMAP4(IMAP_HOST, IMAP_PORT)
        imap.login(user, pwd)
        return imap
    except imaplib.IMAP4.error as e:
        raise HTTPException(401, f"Falha na autenticação IMAP: {e}")
    except OSError as e:
        raise HTTPException(503, f"Não foi possível conectar ao servidor IMAP: {e}")


def _decode_header_value(value: str) -> str:
    """Decodifica cabeçalhos MIME (ex.: assunto em UTF-8)."""
    parts = decode_header(value or "")
    result = []
    for part, charset in parts:
        if isinstance(part, bytes):
            result.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            result.append(part)
    return "".join(result)


def _parse_message(raw: bytes) -> dict:
    """Analisa uma mensagem bruta e extrai campos principais."""
    msg = email_lib.message_from_bytes(raw)
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            cd = str(part.get("Content-Disposition", ""))
            if ct == "text/plain" and "attachment" not in cd:
                body = part.get_payload(decode=True).decode(
                    part.get_content_charset() or "utf-8", errors="replace"
                )
                break
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            body = payload.decode(
                msg.get_content_charset() or "utf-8", errors="replace"
            )
    return {
        "from":    _decode_header_value(msg.get("From", "")),
        "to":      _decode_header_value(msg.get("To", "")),
        "subject": _decode_header_value(msg.get("Subject", "(sem assunto)")),
        "date":    msg.get("Date", ""),
        "body":    body,
    }


def _list_folder(imap: imaplib.IMAP4, folder: str) -> list[dict]:
    """Lista mensagens de uma pasta; retorna lista de dicts sem o corpo."""
    status, data = imap.select(folder, readonly=True)
    if status != "OK":
        # Pasta pode não existir; tenta encontrar pelo LIST
        _, folders = imap.list()
        sent_names = []
        for f in (folders or []):
            decoded = f.decode() if isinstance(f, bytes) else f
            if any(k in decoded for k in ("Sent", "SENT", "Enviados")):
                sent_names.append(decoded.split('"')[-1].strip())
        if sent_names:
            status, data = imap.select(sent_names[0], readonly=True)
        if status != "OK":
            return []

    _, msg_ids = imap.search(None, "ALL")
    ids = msg_ids[0].split() if msg_ids[0] else []

    messages = []
    # Busca apenas os cabeçalhos (sem corpo) dos últimos 50
    for uid in reversed(ids[-50:]):
        _, raw = imap.fetch(uid, "(BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE)])")
        if not raw or not raw[0]:
            continue
        hdr_bytes = raw[0][1] if isinstance(raw[0], tuple) else b""
        msg = email_lib.message_from_bytes(hdr_bytes)
        messages.append({
            "uid": uid.decode(),
            "from":    _decode_header_value(msg.get("From", "")),
            "to":      _decode_header_value(msg.get("To", "")),
            "subject": _decode_header_value(msg.get("Subject", "(sem assunto)")),
            "date":    msg.get("Date", ""),
        })
    return messages


# ── Schemas ──────────────────────────────────────────────────────────────────

class SendRequest(BaseModel):
    sender:  str          # ex.: "aluno@empresa.test"
    to:      str          # destinatário
    subject: str
    body:    str


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/send")
def send_email(
    req: SendRequest,
    _user=Depends(require_role(["admin", "operador"])),
):
    """
    Envia via SMTP e, após confirmação, salva cópia em Enviados via IMAP.
    Retorna status_smtp e status_imap separados.
    """
    if req.sender not in VALID_ACCOUNTS:
        raise HTTPException(400, "Conta remetente inválida.")

    # Monta a mensagem
    msg = MIMEMultipart()
    msg["From"]    = req.sender
    msg["To"]      = req.to
    msg["Subject"] = req.subject
    msg["Date"]    = formatdate(localtime=True)
    msg.attach(MIMEText(req.body, "plain", "utf-8"))
    raw_msg = msg.as_bytes()

    # 1) Envia via SMTP
    smtp_status = "ok"
    smtp_error  = None
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as smtp:
            smtp.sendmail(req.sender, [req.to], raw_msg)
    except smtplib.SMTPException as e:
        smtp_status = "error"
        smtp_error  = str(e)
    except OSError as e:
        smtp_status = "error"
        smtp_error  = f"Não foi possível conectar ao servidor SMTP: {e}"

    if smtp_status == "error":
        return {
            "smtp_status": smtp_status,
            "smtp_error":  smtp_error,
            "imap_status": "skipped",
            "imap_error":  "SMTP falhou; cópia não salva.",
        }

    # 2) Salva cópia em Enviados via IMAP (operação independente)
    imap_status = "ok"
    imap_error  = None
    try:
        imap = _imap_connect(req.sender)
        # Garante que a pasta existe
        imap.create(SENT_FOLDER)          # ignora erro se já existir
        imap.append(
            SENT_FOLDER,
            "\\Seen",
            imaplib.Time2Internaldate(None),
            raw_msg,
        )
        imap.logout()
    except HTTPException as e:
        imap_status = "error"
        imap_error  = e.detail
    except imaplib.IMAP4.error as e:
        imap_status = "error"
        imap_error  = f"Erro ao salvar nos Enviados: {e}"

    return {
        "smtp_status": smtp_status,
        "smtp_error":  smtp_error,
        "imap_status": imap_status,
        "imap_error":  imap_error,
    }


@router.get("/inbox/{account}")
def get_inbox(
    account: str,
    _user=Depends(require_role(["admin", "operador", "leitor"])),
):
    """Lista mensagens da INBOX sem baixar os corpos."""
    if account not in VALID_ACCOUNTS:
        raise HTTPException(400, "Conta inválida.")
    imap = _imap_connect(account)
    messages = _list_folder(imap, "INBOX")
    imap.logout()
    return {"account": account, "messages": messages}


@router.get("/message/{account}/{uid}")
def read_message(
    account: str,
    uid: str,
    _user=Depends(require_role(["admin", "operador", "leitor"])),
):
    """Baixa e retorna cabeçalhos + corpo de uma mensagem pelo UID."""
    if account not in VALID_ACCOUNTS:
        raise HTTPException(400, "Conta inválida.")
    if not uid.isdigit():
        raise HTTPException(400, "UID inválido.")

    imap = _imap_connect(account)
    try:
        imap.select("INBOX", readonly=True)
        _, raw = imap.fetch(uid.encode(), "(RFC822)")
        if not raw or not raw[0]:
            raise HTTPException(404, "Mensagem não encontrada.")
        raw_bytes = raw[0][1] if isinstance(raw[0], tuple) else b""
        return _parse_message(raw_bytes)
    finally:
        imap.logout()


@router.get("/sent/{account}")
def get_sent(
    account: str,
    _user=Depends(require_role(["admin", "operador", "leitor"])),
):
    """Lista mensagens da pasta Enviados."""
    if account not in VALID_ACCOUNTS:
        raise HTTPException(400, "Conta inválida.")
    imap = _imap_connect(account)
    messages = _list_folder(imap, SENT_FOLDER)
    imap.logout()
    return {"account": account, "messages": messages}


@router.get("/sent/message/{account}/{uid}")
def read_sent_message(
    account: str,
    uid: str,
    _user=Depends(require_role(["admin", "operador", "leitor"])),
):
    """Lê uma mensagem da pasta Enviados pelo UID."""
    if account not in VALID_ACCOUNTS:
        raise HTTPException(400, "Conta inválida.")
    if not uid.isdigit():
        raise HTTPException(400, "UID inválido.")

    imap = _imap_connect(account)
    try:
        # Seleciona Sent (ou nome real encontrado)
        status, _ = imap.select(SENT_FOLDER, readonly=True)
        if status != "OK":
            raise HTTPException(404, "Pasta Enviados não encontrada.")
        _, raw = imap.fetch(uid.encode(), "(RFC822)")
        if not raw or not raw[0]:
            raise HTTPException(404, "Mensagem não encontrada.")
        raw_bytes = raw[0][1] if isinstance(raw[0], tuple) else b""
        return _parse_message(raw_bytes)
    finally:
        imap.logout()
