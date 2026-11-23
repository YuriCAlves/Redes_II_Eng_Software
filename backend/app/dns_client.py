"""Incremento 1 - DNS interno (BIND 9), secao 8.1 do enunciado.

Convenção adotada (documentada no README): o arquivo de zona já existente no
Debian (escrito uma única vez, à mão, ao instalar o BIND) contém um bloco
delimitado por marcadores, e só o CONTEÚDO DESSE BLOCO é gerenciado pelo
console. O cabeçalho (TTL, SOA, NS) nunca é tocado por aqui, exceto o número
de série da SOA, que fica num placeholder substituído a cada publicação.

Fluxo de uma alteração (igual ao desenho da seção 6 do enunciado):
 1. ler a zona atual via SSH;
 2. validar o novo conjunto de registros (nome, tipo, valor, duplicidade);
 3. renderizar a zona completa com o novo bloco e serial incrementado;
 4. gravar em arquivo temporário no Debian e validar com `named-checkzone`;
 5. só então mover por cima do arquivo real e recarregar (`rndc reload`);
 6. guardar a versão anterior (válida) em config_versions para permitir restauração.

Nada de concatenar texto do usuário em comando de shell: valores vão para o
arquivo de zona (dados), nunca para um argv remoto; os únicos comandos
remotos são fixos, com no máximo o caminho de um arquivo temporário nosso.
"""
import re
import threading
from datetime import datetime, timezone

from . import audit, config, ssh_client
from .db import kv_get, kv_set
from .errors import Conflict, InvalidInput, ServiceError

MARKER_START = "; ===== CONSOLE:INICIO (gerado pelo console, nao editar a mao) ====="
MARKER_END = "; ===== CONSOLE:FIM ====="
SERIAL_PLACEHOLDER = "__SERIAL__"

FORWARD_TYPES = ("A", "CNAME", "MX", "TXT")
REVERSE_TYPES = ("PTR",)

_NAME_RE = re.compile(r"^(@|[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*)$")
_FQDN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+\.$")
_IPV4_RE = re.compile(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$")
_PTR_OCTET_RE = re.compile(r"^\d{1,3}$")
_lock = threading.Lock()  # uma publicação de zona por vez (fwd e rev compartilham o travamento)


# ------------------------------------------------------------------ erros
class DnsConfigError(ServiceError):
    status, code = 503, "dns_config"
    message = "DNS não configurado no servidor do console (caminho de zona ausente)."


class DnsValidationError(ServiceError):
    status, code = 422, "dns_invalid_zone"
    message = "O BIND recusou a nova zona (named-checkzone); nada foi alterado."


class DnsQueryError(ServiceError):
    status, code = 502, "dns_query_error"
    message = "Não foi possível consultar o servidor DNS."


# ------------------------------------------------------------- validação
def validate_name(name) -> str:
    if not isinstance(name, str) or not _NAME_RE.fullmatch(name.lower()):
        raise InvalidInput("Nome inválido: use letras minúsculas, números, hífen e pontos, ou '@' para a raiz.")
    return name.lower()


def validate_ipv4(value) -> str:
    if not isinstance(value, str) or not (m := _IPV4_RE.fullmatch(value)):
        raise InvalidInput("Endereço IPv4 inválido.")
    if any(int(g) > 255 for g in m.groups()):
        raise InvalidInput("Endereço IPv4 inválido.")
    return value


def validate_fqdn(value) -> str:
    if not isinstance(value, str):
        raise InvalidInput("Nome de domínio inválido.")
    v = value.lower() if value.endswith(".") else value.lower() + "."
    if not _FQDN_RE.fullmatch(v):
        raise InvalidInput("Nome de domínio inválido (use um FQDN, ex.: mail.empresa.test.).")
    return v


def validate_txt(value) -> str:
    if not isinstance(value, str) or not value or len(value) > 250 or "\n" in value or '"' in value:
        raise InvalidInput("Valor TXT inválido (sem aspas nem quebras de linha; até 250 caracteres).")
    return value


def validate_priority(value) -> int:
    try:
        p = int(value)
    except (TypeError, ValueError):
        raise InvalidInput("Prioridade MX inválida.")
    if not (0 <= p <= 65535):
        raise InvalidInput("Prioridade MX deve estar entre 0 e 65535.")
    return p


def validate_forward_record(rec: dict) -> dict:
    name = validate_name(rec.get("name", ""))
    rtype = rec.get("type")
    if rtype not in FORWARD_TYPES:
        raise InvalidInput(f"Tipo inválido (use um de: {', '.join(FORWARD_TYPES)}).")
    out = {"name": name, "type": rtype}
    if rtype == "A":
        out["value"] = validate_ipv4(rec.get("value", ""))
    elif rtype == "CNAME":
        out["value"] = validate_fqdn(rec.get("value", ""))
    elif rtype == "MX":
        out["value"] = validate_fqdn(rec.get("value", ""))
        out["priority"] = validate_priority(rec.get("priority", 10))
    elif rtype == "TXT":
        out["value"] = validate_txt(rec.get("value", ""))
    return out


def validate_reverse_record(rec: dict) -> dict:
    octet = rec.get("name", "")
    if not isinstance(octet, str) or not _PTR_OCTET_RE.fullmatch(octet) or int(octet) > 255:
        raise InvalidInput("O nome do registro reverso deve ser o último octeto do IP (0-255).")
    return {"name": octet, "type": "PTR", "value": validate_fqdn(rec.get("value", ""))}


def check_duplicates_and_cname(records: list[dict]) -> None:
    seen = set()
    cname_names = set()
    other_names = set()
    for r in records:
        key = (r["name"], r["type"], r["value"], r.get("priority"))
        if key in seen:
            raise InvalidInput(f"Registro duplicado: {r['name']} {r['type']} {r['value']}.")
        seen.add(key)
        (cname_names if r["type"] == "CNAME" else other_names).add(r["name"])
    conflict = cname_names & other_names
    if conflict:
        raise InvalidInput(f"'{next(iter(conflict))}' tem CNAME e outro tipo de registro ao mesmo tempo (não é permitido).")


# --------------------------------------------------------- zona (texto)
def _zone_path(zone: str) -> str:
    paths = {"fwd": config.DNS_ZONE_FWD_PATH, "rev": config.DNS_ZONE_REV_PATH}
    path = paths.get(zone)
    if not path:
        raise DnsConfigError()
    return path


def _zone_name(zone: str) -> str:
    return {"fwd": config.DNS_ZONE_FWD_NAME, "rev": config.DNS_ZONE_REV_NAME}[zone]


def split_managed_block(text: str) -> tuple[str, list[str], str]:
    try:
        before, rest = text.split(MARKER_START, 1)
        middle, after = rest.split(MARKER_END, 1)
    except ValueError:
        raise DnsConfigError(
            "A zona no Debian não tem os marcadores do console. Veja o README (seção DNS) "
            "para preparar o arquivo uma única vez."
        )
    lines = [ln for ln in middle.strip("\n").splitlines() if ln.strip() and not ln.strip().startswith(";")]
    return before + MARKER_START + "\n", lines, "\n" + MARKER_END + after


def render_record_line(r: dict) -> str:
    if r["type"] == "MX":
        return f"{r['name']:<24}IN  MX  {r['priority']:<4}{r['value']}"
    if r["type"] == "TXT":
        return f'{r["name"]:<24}IN  TXT "{r["value"]}"'
    return f"{r['name']:<24}IN  {r['type']:<6}{r['value']}"


def parse_record_line(line: str, zone: str) -> dict | None:
    parts = line.split(None, 4)
    # name IN TYPE [priority] value
    if len(parts) < 4 or parts[1] != "IN":
        return None
    name, _, rtype = parts[0], parts[1], parts[2]
    if zone == "fwd" and rtype == "MX" and len(parts) >= 5:
        return {"name": name, "type": "MX", "priority": int(parts[3]), "value": parts[4]}
    if len(parts) >= 4:
        value = parts[3] if len(parts) == 4 else " ".join(parts[3:])
        if rtype == "TXT":
            value = value.strip().strip('"')
        return {"name": name, "type": rtype, "value": value}
    return None


def _next_serial(zone: str) -> str:
    key = f"dns_serial_{zone}"
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    current = kv_get(key)
    if current and current.startswith(today):
        n = int(current[-2:]) + 1
    else:
        n = 1
    serial = f"{today}{n:02d}"
    kv_set(key, serial)
    return serial


def read_zone(zone: str) -> dict:
    """Lê a zona do Debian e devolve o cabeçalho (oculto) e os registros gerenciados."""
    client = ssh_client.connect()
    try:
        rc, out, err = ssh_client.run(client, ["sudo", "-n", "cat", _zone_path(zone)])
        if rc != 0:
            raise ssh_client.SshCommandError("Não foi possível ler o arquivo de zona.")
    finally:
        client.close()
    _, lines, _ = split_managed_block(out)
    records = [r for ln in lines if (r := parse_record_line(ln, zone))]
    return {"zone": zone, "zone_name": _zone_name(zone), "records": records, "raw_lines": len(lines)}


def _apply(zone: str, records: list[dict], user: dict, note: str) -> dict:
    with _lock:
        client = ssh_client.connect()
        try:
            rc, current_text, err = ssh_client.run(client, ["sudo", "-n", "cat", _zone_path(zone)])
            if rc != 0:
                raise ssh_client.SshCommandError("Não foi possível ler o arquivo de zona atual.")
            before, _, after = split_managed_block(current_text)
            serial = _next_serial(zone)
            body = "\n".join(render_record_line(r) for r in records)
            new_text = before + body + after
            new_text = new_text.replace(SERIAL_PLACEHOLDER, serial, 1) if SERIAL_PLACEHOLDER in new_text else new_text

            tmp_path = f"/tmp/console-dns-{zone}-{serial}.zone"
            sftp = client.open_sftp()
            try:
                with sftp.open(tmp_path, "w") as f:
                    f.write(new_text)
            finally:
                sftp.close()

            rc, out, err = ssh_client.run(
                client, ["named-checkzone", _zone_name(zone), tmp_path], timeout=15
            )
            if rc != 0:
                ssh_client.run(client, ["rm", "-f", tmp_path])
                raise DnsValidationError(f"named-checkzone recusou a zona (registro inválido).")

            # guarda a versão ANTERIOR (ainda válida) antes de sobrescrever
            audit.save_config_version("dns_" + zone, user["username"], current_text, note)

            rc, _, err = ssh_client.run(
                client, ["sudo", "-n", "cp", tmp_path, _zone_path(zone)], timeout=15
            )
            ssh_client.run(client, ["rm", "-f", tmp_path])
            if rc != 0:
                raise ssh_client.SshCommandError("O Debian recusou substituir o arquivo de zona (sudoers).")

            rc, _, err = ssh_client.run(
                client, ["sudo", "-n", "rndc", "reload", _zone_name(zone)], timeout=15
            )
            if rc != 0:
                raise ssh_client.SshCommandError("Zona gravada, mas o reload (rndc) falhou.")
        finally:
            client.close()
    return {"zone": zone, "zone_name": _zone_name(zone), "serial": serial, "records": len(records)}


def create_record(zone: str, rec: dict, user: dict) -> dict:
    validator = validate_forward_record if zone == "fwd" else validate_reverse_record
    new_rec = validator(rec)
    current = read_zone(zone)["records"]
    if any(r["name"] == new_rec["name"] and r["type"] == new_rec["type"] and r["value"] == new_rec["value"] for r in current):
        raise Conflict("Esse registro já existe.")
    if zone == "fwd":
        check_duplicates_and_cname(current + [new_rec])
    result = _apply(zone, current + [new_rec], user, f"criar {new_rec['type']} {new_rec['name']}")
    audit.record(user=user, resource="dns", action="record.create", detail=f"{zone}:{new_rec['name']} {new_rec['type']}")
    return result


def update_records(zone: str, records: list[dict], user: dict) -> dict:
    """Substitui a lista inteira de registros gerenciados (edição em lote, já validada)."""
    validator = validate_forward_record if zone == "fwd" else validate_reverse_record
    new_recs = [validator(r) for r in records]
    if zone == "fwd":
        check_duplicates_and_cname(new_recs)
    result = _apply(zone, new_recs, user, "editar registros")
    audit.record(user=user, resource="dns", action="record.update", detail=f"{zone}: {len(new_recs)} registros")
    return result


def delete_record(zone: str, name: str, rtype: str, value: str, user: dict) -> dict:
    current = read_zone(zone)["records"]
    remaining = [r for r in current if not (r["name"] == name and r["type"] == rtype and r["value"] == value)]
    if len(remaining) == len(current):
        raise Conflict("Registro não encontrado (já pode ter sido removido).")
    result = _apply(zone, remaining, user, f"remover {rtype} {name}")
    audit.record(user=user, resource="dns", action="record.delete", detail=f"{zone}:{name} {rtype}")
    return result


def restore_last_version(zone: str, user: dict) -> dict:
    version = audit.last_config_version("dns_" + zone)
    if not version:
        raise Conflict("Não há versão anterior registrada para restaurar.")
    _, lines, _ = split_managed_block(version["content"])
    records = [r for ln in lines if (r := parse_record_line(ln, zone))]
    result = _apply(zone, records, user, f"restaurar versão #{version['id']}")
    audit.record(user=user, resource="dns", action="record.restore", detail=f"{zone}: versão #{version['id']}")
    return result


# ------------------------------------------------------------------ consulta
def query(name: str, qtype: str = "A") -> dict:
    import time as _time

    import dns.exception
    import dns.rdatatype
    import dns.resolver

    if qtype not in (*FORWARD_TYPES, "PTR", "NS", "SOA"):
        raise InvalidInput("Tipo de consulta inválido.")
    resolver = dns.resolver.Resolver(configure=False)
    resolver.nameservers = [config.SERVICE_HOST]
    resolver.port = 53
    resolver.lifetime = config.DNS_QUERY_TIMEOUT
    started = _time.monotonic()
    try:
        answer = resolver.resolve(name, qtype)
        elapsed_ms = round((_time.monotonic() - started) * 1000, 1)
        return {
            "name": name,
            "type": qtype,
            "status": "NOERROR",
            "answers": [str(r) for r in answer],
            "server": config.SERVICE_HOST,
            "elapsed_ms": elapsed_ms,
        }
    except dns.resolver.NXDOMAIN:
        elapsed_ms = round((_time.monotonic() - started) * 1000, 1)
        return {"name": name, "type": qtype, "status": "NXDOMAIN", "answers": [], "server": config.SERVICE_HOST, "elapsed_ms": elapsed_ms}
    except dns.resolver.NoAnswer:
        elapsed_ms = round((_time.monotonic() - started) * 1000, 1)
        return {"name": name, "type": qtype, "status": "NOANSWER", "answers": [], "server": config.SERVICE_HOST, "elapsed_ms": elapsed_ms}
    except dns.exception.Timeout:
        raise DnsQueryError("Tempo esgotado ao consultar o servidor DNS (verifique se o BIND está ativo).")
    except OSError as exc:
        raise DnsQueryError() from exc
