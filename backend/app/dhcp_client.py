"""Incremento 2 - DHCP (Kea DHCPv4), secao 8.2 do enunciado.

Mesma estratégia de segurança do DNS: o console nunca concatena texto do
usuário num comando remoto. O que entra é dado (um JSON de configuração
escrito via SFTP), nunca argumento de shell. Fluxo de uma alteração de
reserva:

 1. ler a configuração atual (JSON) via SSH;
 2. validar a nova lista de reservas (MAC, IP dentro da sub-rede, sem
    conflito com infraestrutura fixa nem duplicidade);
 3. gravar o JSON completo em arquivo temporário e validar com `kea-dhcp4 -t`;
 4. só então sobrescrever o arquivo real e recarregar o serviço (systemd);
 5. guardar a versão anterior (válida) para permitir restauração.

Concessões (leases) são só leitura: o console lê o arquivo de concessões do
Kea (CSV, backend "memfile", já citado na seção 12.2 do enunciado) e nunca
escreve nele — revogar uma concessão ativa não é uma operação deste MVP,
como o próprio enunciado adverte ("excluir uma reserva não revoga
imediatamente uma concessão ativa").
"""
import csv
import io
import ipaddress
import json
import re
import threading

from . import audit, config, ssh_client
from .errors import Conflict, InvalidInput, ServiceError

_MAC_RE = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$")
_HOSTNAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
_lock = threading.Lock()

LEASE_STATE = {0: "ativa", 1: "recusada (declined)", 2: "expirada/reciclada"}


class DhcpConfigError(ServiceError):
    status, code = 503, "dhcp_config"
    message = "DHCP não configurado no servidor do console."


class DhcpValidationError(ServiceError):
    status, code = 422, "dhcp_invalid_config"
    message = "O Kea recusou a nova configuração (kea-dhcp4 -t); nada foi alterado."


# ------------------------------------------------------------- validação
def validate_mac(value) -> str:
    if not isinstance(value, str) or not _MAC_RE.fullmatch(value.lower()):
        raise InvalidInput("Endereço MAC inválido (use o formato aa:bb:cc:dd:ee:ff).")
    return value.lower()


def validate_hostname(value) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str) or not _HOSTNAME_RE.fullmatch(value.lower()) or len(value) > 63:
        raise InvalidInput("Hostname da reserva inválido.")
    return value.lower()


def validate_ip_in_subnet(value: str, subnet_cidr: str) -> str:
    try:
        ip = ipaddress.IPv4Address(value)
        net = ipaddress.IPv4Network(subnet_cidr, strict=False)
    except (ValueError, ipaddress.AddressValueError):
        raise InvalidInput("Endereço IPv4 inválido.")
    if ip not in net:
        raise InvalidInput(f"O endereço deve pertencer à sub-rede {subnet_cidr}.")
    if str(ip) in config.DHCP_RESERVED_INFRA:
        raise InvalidInput("Esse endereço é reservado à infraestrutura (Debian/Ubuntu) e não pode ser realocado.")
    return str(ip)


def validate_reservation(rec: dict, subnet_cidr: str) -> dict:
    out = {
        "hw-address": validate_mac(rec.get("hw-address", "")),
        "ip-address": validate_ip_in_subnet(rec.get("ip-address", ""), subnet_cidr),
    }
    hostname = validate_hostname(rec.get("hostname"))
    if hostname:
        out["hostname"] = hostname
    return out


def check_reservation_conflicts(reservations: list[dict]) -> None:
    macs, ips = set(), set()
    for r in reservations:
        if r["hw-address"] in macs:
            raise InvalidInput(f"MAC duplicado: {r['hw-address']}.")
        if r["ip-address"] in ips:
            raise InvalidInput(f"IP duplicado: {r['ip-address']}.")
        macs.add(r["hw-address"])
        ips.add(r["ip-address"])


# --------------------------------------------------------------- config
def _find_subnet(cfg: dict) -> dict:
    subnets = cfg.get("Dhcp4", {}).get("subnet4", [])
    for s in subnets:
        if s.get("subnet") == config.DHCP_SUBNET:
            return s
    if subnets:
        return subnets[0]
    raise DhcpConfigError("Nenhuma sub-rede encontrada em kea-dhcp4.conf.")


def read_config() -> dict:
    client = ssh_client.connect()
    try:
        rc, out, err = ssh_client.run(client, ["sudo", "-n", "/usr/bin/cat", config.KEA_CONFIG_PATH])
        if rc != 0:
            raise ssh_client.SshCommandError("Não foi possível ler a configuração do Kea.")
    finally:
        client.close()
    try:
        cfg = json.loads(out)
    except json.JSONDecodeError:
        raise DhcpConfigError("kea-dhcp4.conf não é um JSON válido.")
    subnet = _find_subnet(cfg)
    return {
        "subnet": subnet.get("subnet"),
        "pools": [p.get("pool") for p in subnet.get("pools", [])],
        "valid_lifetime": cfg.get("Dhcp4", {}).get("valid-lifetime"),
        "reservations": subnet.get("reservations", []),
        "_raw": out,
    }


def _apply(reservations: list[dict], user: dict, note: str) -> dict:
    with _lock:
        client = ssh_client.connect()
        try:
            rc, current_text, err = ssh_client.run(client, ["sudo", "-n", "/usr/bin/cat", config.KEA_CONFIG_PATH])
            if rc != 0:
                raise ssh_client.SshCommandError("Não foi possível ler a configuração atual do Kea.")
            cfg = json.loads(current_text)
            subnet = _find_subnet(cfg)
            subnet["reservations"] = reservations
            new_text = json.dumps(cfg, indent=4, ensure_ascii=False) + "\n"

            tmp_path = f"{config.STAGE_DIR}/kea-dhcp4.json"  # nome fixo: sudoers sem curinga
            sftp = client.open_sftp()
            try:
                with sftp.open(tmp_path, "w") as f:
                    f.write(new_text)
            finally:
                sftp.close()

            # O AppArmor do Kea só o deixa ler /etc/kea/**, e /etc/kea é 750 _kea:_kea:
            # a validação precisa rodar como _kea sobre uma cópia candidata DENTRO de /etc/kea
            # (o kea-dhcp4 rodando como root confinado nem consegue abrir a pasta).
            rc, _, err = ssh_client.run(
                client, ["sudo", "-n", "/usr/bin/cp", tmp_path, config.KEA_CANDIDATE_PATH], timeout=15
            )
            if rc != 0:
                ssh_client.run(client, ["rm", "-f", tmp_path])
                raise ssh_client.SshCommandError("O Debian recusou preparar a configuração candidata (sudoers).")
            rc, out, err = ssh_client.run(
                client,
                ["sudo", "-n", "-u", config.KEA_USER, config.KEA_DHCP4_BIN, "-t", config.KEA_CANDIDATE_PATH],
                timeout=15,
            )
            if rc != 0:
                ssh_client.run(client, ["rm", "-f", tmp_path])
                raise DhcpValidationError("kea-dhcp4 -t recusou a configuração (reserva inválida ou conflito).")

            audit.save_config_version("dhcp", user["username"], current_text, note)

            rc, _, err = ssh_client.run(client, ["sudo", "-n", "/usr/bin/cp", tmp_path, config.KEA_CONFIG_PATH], timeout=15)
            ssh_client.run(client, ["rm", "-f", tmp_path])
            if rc != 0:
                raise ssh_client.SshCommandError("O Debian recusou substituir kea-dhcp4.conf (sudoers).")

            rc, _, err = ssh_client.run(
                client, ["sudo", "-n", "/usr/bin/systemctl", "reload-or-restart", config.KEA_SYSTEMD_UNIT], timeout=20
            )
            if rc != 0:
                raise ssh_client.SshCommandError("Configuração gravada, mas o Kea não recarregou (systemctl).")
        finally:
            client.close()
    return {"subnet": subnet.get("subnet"), "reservations": len(reservations)}


def create_reservation(rec: dict, user: dict) -> dict:
    current = read_config()
    new_rec = validate_reservation(rec, current["subnet"])
    all_recs = current["reservations"] + [new_rec]
    check_reservation_conflicts(all_recs)
    result = _apply(all_recs, user, f"criar reserva {new_rec['ip-address']}")
    audit.record(user=user, resource="dhcp", action="reservation.create", detail=new_rec["ip-address"])
    return result


def delete_reservation(ip_address: str, user: dict) -> dict:
    current = read_config()
    remaining = [r for r in current["reservations"] if r["ip-address"] != ip_address]
    if len(remaining) == len(current["reservations"]):
        raise Conflict("Reserva não encontrada (já pode ter sido removida).")
    result = _apply(remaining, user, f"remover reserva {ip_address}")
    audit.record(user=user, resource="dhcp", action="reservation.delete", detail=ip_address)
    return result


def restore_last_version(user: dict) -> dict:
    version = audit.last_config_version("dhcp")
    if not version:
        raise Conflict("Não há versão anterior registrada para restaurar.")
    cfg = json.loads(version["content"])
    reservations = _find_subnet(cfg).get("reservations", [])
    result = _apply(reservations, user, f"restaurar versão #{version['id']}")
    audit.record(user=user, resource="dhcp", action="reservation.restore", detail=f"versão #{version['id']}")
    return result


# --------------------------------------------------------------- leases
def parse_lease_csv(text: str) -> list[dict]:
    """Converte o CSV de concessões do Kea (backend memfile) em registros legíveis."""
    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for row in reader:
        try:
            state = int(row.get("state", "0") or 0)
        except ValueError:
            state = 0
        rows.append(
            {
                "address": row.get("address"),
                "hwaddr": row.get("hwaddr"),
                "hostname": row.get("hostname") or None,
                "expire": row.get("expire"),
                "valid_lifetime": row.get("valid_lifetime"),
                "state": LEASE_STATE.get(state, "desconhecido"),
            }
        )
    rows.sort(key=lambda r: r["address"] or "")
    return rows


def read_leases() -> dict:
    client = ssh_client.connect()
    try:
        rc, out, err = ssh_client.run(client, ["sudo", "-n", "/usr/bin/cat", config.KEA_LEASE_CSV])
        if rc != 0:
            raise ssh_client.SshCommandError("Não foi possível ler o arquivo de concessões do Kea.")
    finally:
        client.close()
    return {"leases": parse_lease_csv(out)}
