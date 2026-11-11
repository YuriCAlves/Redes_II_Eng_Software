"""CP4 - E-mail. Leitura: todos os perfis; envio: operador/admin."""
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from .. import mail_client as mc
from ..deps import post_only_guard, raise_service_error, require_role
from ..errors import ServiceError

router = APIRouter(prefix="/api/email", tags=["email"])

post_only_guard(router, "/send", "operador")

_read = Depends(require_role("leitor"))


class SendBody(BaseModel):
    account: str = Field(max_length=100)
    to: str = Field(max_length=2000)
    subject: str = Field(default="", max_length=mc.MAX_SUBJECT)
    body: str = Field(default="", max_length=mc.MAX_BODY)


@router.get("/accounts", dependencies=[_read])
def accounts():
    return {"accounts": mc.accounts()}


@router.get("/messages", dependencies=[_read])
def messages(account: str, folder: str = "inbox", limit: int = Query(50, ge=1, le=100)):
    try:
        mc.validate_account(account)
        return {"messages": mc.list_messages(account, folder, limit), "folder": folder}
    except ServiceError as exc:
        raise_service_error(exc)


@router.get("/message", dependencies=[_read])
def message(account: str, uid: str, folder: str = "inbox"):
    try:
        mc.validate_account(account)
        return mc.read_message(account, folder, uid)
    except ServiceError as exc:
        raise_service_error(exc)


@router.post("/send")
def send(body: SendBody, user: dict = Depends(require_role("operador"))):
    """Duas etapas independentes, com status separados na resposta."""
    try:
        sender = mc.validate_account(body.account)
        recipients = mc.parse_recipients(body.to)
        msg = mc.build_message(sender, recipients, body.subject, body.body)
    except ServiceError as exc:
        raise_service_error(exc)

    smtp = mc.send_smtp(msg, sender, recipients)
    if not smtp["ok"]:  # nada foi enviado: não há o que salvar
        return {"smtp": smtp, "imap": {"ok": None, "skipped": True}}
    # SMTP aceitou: a partir daqui NUNCA reenviar, mesmo que o IMAP falhe
    return {"smtp": smtp, "imap": mc.append_sent(sender, msg), "message_id": msg["Message-ID"]}
