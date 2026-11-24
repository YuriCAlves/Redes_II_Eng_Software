"""Cliente HTTP do agente do laboratório (roda no host físico).

Distingue três resultados, como pede a seção 10 ("em timeout, distinguir erro
confirmado de execução remota sem confirmação"):
 - AgentUnavailable: não conectou (agente parado, host desligado, rede);
 - AgentTimeout: conectou, mas a resposta não veio no prazo -> o efeito é
   DESCONHECIDO; quem chama deve consultar o host antes de repetir;
 - AgentError: o agente respondeu com erro (efeito confirmado: não aconteceu).
Só a biblioteca padrão (urllib): nenhuma dependência nova no backend.
"""
import json
import socket
import urllib.error
import urllib.request

from . import config
from .errors import ServiceError


class AgentUnavailable(ServiceError):
    status = 503
    code = "agent_unavailable"
    message = "Agente do laboratório indisponível."


class AgentTimeout(ServiceError):
    status = 504
    code = "agent_timeout"
    message = "O agente não respondeu a tempo (resultado ainda não confirmado)."


class AgentError(ServiceError):
    status = 502
    code = "agent_error"
    message = "O agente recusou a operação."

    def __init__(self, message: str | None = None, http_status: int = 502, agent_code: str = ""):
        super().__init__(message)
        self.http_status = http_status
        self.agent_code = agent_code


def _request(method: str, path: str, body: dict | None = None, timeout: int | None = None) -> dict:
    if not config.LAB_AGENT_URL:
        raise AgentUnavailable("Agente do laboratório não configurado (CONSOLE_LAB_AGENT_URL).")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        config.LAB_AGENT_URL.rstrip("/") + path,
        data=data,
        method=method,
        headers={"Authorization": f"Bearer {config.LAB_AGENT_TOKEN}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout or config.LAB_AGENT_TIMEOUT) as resp:
            return json.loads(resp.read(2_000_000) or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read(20_000)).get("detail", {})
        except (ValueError, AttributeError):
            detail = {}
        if not isinstance(detail, dict):
            detail = {"message": str(detail)}
        if exc.code == 504:
            raise AgentTimeout(detail.get("message")) from exc
        raise AgentError(detail.get("message") or f"Agente respondeu {exc.code}.", exc.code, detail.get("code", "")) from exc
    except (TimeoutError, socket.timeout) as exc:
        raise AgentTimeout() from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            raise AgentTimeout() from exc
        raise AgentUnavailable() from exc
    except (ConnectionError, OSError) as exc:
        raise AgentUnavailable() from exc


def inventory() -> dict:
    return _request("GET", "/v1/inventory")


def find_by_op(op_id: str) -> dict | None:
    try:
        return _request("GET", f"/v1/instances/by-op/{op_id}")
    except AgentError as exc:
        if exc.http_status == 404:
            return None
        raise


def clone(op_id: str, name: str, image: str, owner: str) -> dict:
    return _request("POST", "/v1/instances", {"op_id": op_id, "name": name, "image": image, "owner": owner},
                    timeout=config.LAB_CLONE_TIMEOUT)


def configure(uuid: str, body: dict) -> dict:
    return _request("POST", f"/v1/instances/{uuid}/configure", body)


def status(uuid: str) -> dict | None:
    try:
        return _request("GET", f"/v1/instances/{uuid}")
    except AgentError as exc:
        if exc.http_status == 404:
            return None
        raise


def start(uuid: str, op_id: str) -> dict:
    return _request("POST", f"/v1/instances/{uuid}/start", {"op_id": op_id}, timeout=150)


def stop(uuid: str, op_id: str, force: bool = False) -> dict:
    return _request("POST", f"/v1/instances/{uuid}/stop", {"op_id": op_id, "force": force}, timeout=90)


def delete(uuid: str, op_id: str) -> dict:
    return _request("POST", f"/v1/instances/{uuid}/delete", {"op_id": op_id}, timeout=330)
