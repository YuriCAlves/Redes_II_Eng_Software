"""Testes de lógica do módulo DHCP (incremento 2) com SSH simulado."""
import json

import pytest

from app import audit, config, db, dhcp_client as hc
from app.errors import Conflict, InvalidInput

USER = {"username": "admin1", "role": "admin"}

BASE_CONFIG = {
    "Dhcp4": {
        "valid-lifetime": 3600,
        "subnet4": [
            {
                "subnet": "192.168.100.0/24",
                "pools": [{"pool": "192.168.100.100 - 192.168.100.150"}],
                "reservations": [
                    {"hw-address": "aa:bb:cc:dd:ee:01", "ip-address": "192.168.100.30", "hostname": "ubuntu-cliente"}
                ],
            }
        ],
    }
}


# ------------------------------------------------------------- validação
@pytest.mark.parametrize("mac", ["aa:bb:cc:dd:ee:ff", "00:11:22:33:44:55"])
def test_valid_mac(mac):
    assert hc.validate_mac(mac) == mac


@pytest.mark.parametrize("mac", ["", "aabbccddeeff", "aa:bb:cc:dd:ee", "zz:bb:cc:dd:ee:ff", None])
def test_invalid_mac(mac):
    with pytest.raises(InvalidInput):
        hc.validate_mac(mac)


def test_ip_must_be_in_subnet_and_not_infra():
    assert hc.validate_ip_in_subnet("192.168.100.50", "192.168.100.0/24") == "192.168.100.50"
    with pytest.raises(InvalidInput):
        hc.validate_ip_in_subnet("192.168.200.5", "192.168.100.0/24")
    with pytest.raises(InvalidInput):
        hc.validate_ip_in_subnet("192.168.100.10", "192.168.100.0/24")  # Debian


def test_check_reservation_conflicts():
    with pytest.raises(InvalidInput):
        hc.check_reservation_conflicts(
            [
                {"hw-address": "aa:bb:cc:dd:ee:01", "ip-address": "192.168.100.30"},
                {"hw-address": "aa:bb:cc:dd:ee:01", "ip-address": "192.168.100.31"},
            ]
        )
    with pytest.raises(InvalidInput):
        hc.check_reservation_conflicts(
            [
                {"hw-address": "aa:bb:cc:dd:ee:01", "ip-address": "192.168.100.30"},
                {"hw-address": "aa:bb:cc:dd:ee:02", "ip-address": "192.168.100.30"},
            ]
        )


def test_parse_lease_csv():
    csv_text = (
        "address,hwaddr,client_id,valid_lifetime,expire,subnet_id,fqdn_fwd,fqdn_rev,hostname,state\n"
        "192.168.100.30,aa:bb:cc:dd:ee:01,,3600,1795000000,1,0,0,ubuntu-cliente,0\n"
    )
    rows = hc.parse_lease_csv(csv_text)
    assert rows == [
        {
            "address": "192.168.100.30",
            "hwaddr": "aa:bb:cc:dd:ee:01",
            "hostname": "ubuntu-cliente",
            "expire": "1795000000",
            "valid_lifetime": "3600",
            "state": "ativa",
        }
    ]


@pytest.fixture
def tmpdb(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    db.init_db()


class FakeSftpFile:
    def __init__(self, store, path):
        self.store, self.path = store, path

    def write(self, data):
        self.store[self.path] = data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


class FakeSftp:
    def __init__(self, store):
        self.store = store

    def open(self, path, mode):
        return FakeSftpFile(self.store, path)

    def close(self):
        pass


class FakeDhcpSsh:
    def __init__(self, monkeypatch, checktest_ok=True, cp_ok=True, reload_ok=True):
        self.config_text = json.dumps(BASE_CONFIG)
        self.sftp_store = {}
        self.checktest_ok, self.cp_ok, self.reload_ok = checktest_ok, cp_ok, reload_ok
        self.commands = []
        monkeypatch.setattr(hc.ssh_client, "connect", lambda: self)
        monkeypatch.setattr(hc.ssh_client, "run", self.run)

    def open_sftp(self):
        return FakeSftp(self.sftp_store)

    def close(self):
        pass

    def run(self, client, argv, timeout=15):
        self.commands.append(argv)
        if argv[:3] == ["sudo", "-n", "/usr/bin/cat"] and argv[3] == config.KEA_CONFIG_PATH:
            return 0, self.config_text, ""
        if argv[:5] == ["sudo", "-n", "-u", config.KEA_USER, config.KEA_DHCP4_BIN] and argv[5] == "-t":
            return (0, "configuration seems sane", "") if self.checktest_ok else (1, "", "erro de sintaxe")
        if argv[:3] == ["sudo", "-n", "/usr/bin/cp"]:
            if argv[4] == config.KEA_CANDIDATE_PATH:
                return 0, "", ""
            if self.cp_ok:
                self.config_text = self.sftp_store[argv[3]]
            return (0, "", "") if self.cp_ok else (1, "", "sudoers")
        if argv[:3] == ["sudo", "-n", "/usr/bin/systemctl"]:
            return (0, "", "") if self.reload_ok else (1, "", "falha ao recarregar")
        if argv[0] == "rm":
            return 0, "", ""
        raise AssertionError(f"comando inesperado: {argv}")


def test_read_config_extracts_subnet(tmpdb, monkeypatch):
    FakeDhcpSsh(monkeypatch)
    cfg = hc.read_config()
    assert cfg["subnet"] == "192.168.100.0/24"
    assert cfg["pools"] == ["192.168.100.100 - 192.168.100.150"]
    assert len(cfg["reservations"]) == 1


def test_create_reservation_applies_and_versions(tmpdb, monkeypatch):
    FakeDhcpSsh(monkeypatch)
    result = hc.create_reservation(
        {"hw-address": "aa:bb:cc:dd:ee:02", "ip-address": "192.168.100.31", "hostname": "cliente2"}, USER
    )
    assert result["reservations"] == 2
    version = audit.last_config_version("dhcp")
    assert version is not None and "192.168.100.31" not in version["content"]


def test_create_reservation_rejects_duplicate_mac(tmpdb, monkeypatch):
    FakeDhcpSsh(monkeypatch)
    with pytest.raises(InvalidInput):
        hc.create_reservation({"hw-address": "aa:bb:cc:dd:ee:01", "ip-address": "192.168.100.32"}, USER)


def test_create_reservation_rejects_invalid_config(tmpdb, monkeypatch):
    FakeDhcpSsh(monkeypatch, checktest_ok=False)
    with pytest.raises(hc.DhcpValidationError):
        hc.create_reservation({"hw-address": "aa:bb:cc:dd:ee:03", "ip-address": "192.168.100.33"}, USER)


def test_delete_reservation_not_found(tmpdb, monkeypatch):
    FakeDhcpSsh(monkeypatch)
    with pytest.raises(Conflict):
        hc.delete_reservation("192.168.100.99", USER)


def test_restore_without_version_is_conflict(tmpdb, monkeypatch):
    FakeDhcpSsh(monkeypatch)
    with pytest.raises(Conflict):
        hc.restore_last_version(USER)


def test_restore_reapplies_previous_version(tmpdb, monkeypatch):
    FakeDhcpSsh(monkeypatch)
    hc.create_reservation({"hw-address": "aa:bb:cc:dd:ee:02", "ip-address": "192.168.100.31"}, USER)
    hc.restore_last_version(USER)
    cfg = hc.read_config()
    assert len(cfg["reservations"]) == 1


def test_every_sudo_command_matches_sudoers_file(tmpdb, monkeypatch):
    from sudoers_rules import allowed_commands, sudo_commands

    ssh = FakeDhcpSsh(monkeypatch)
    hc.create_reservation({"hw-address": "aa:bb:cc:dd:ee:02", "ip-address": "192.168.100.31"}, USER)
    allowed = allowed_commands()
    issued = sudo_commands(ssh.commands)
    assert issued and all(c in allowed for c in issued), set(issued) - allowed
    assert f"root /usr/bin/cat {config.KEA_LEASE_CSV}" in allowed
