"""Testes de lógica do módulo DNS (incremento 1) com SSH simulado."""
import pytest

from app import audit, config, db, dns_client as dc
from app.errors import Conflict, InvalidInput


# ------------------------------------------------------------- validação
@pytest.mark.parametrize("name", ["@", "site", "mail-01", "a.b.c", "x" * 63])
def test_valid_names(name):
    assert dc.validate_name(name) == name


@pytest.mark.parametrize("name", ["", "-a", "a-", "a_b", "a b", "a;id", None, "a..b"])
def test_invalid_names(name):
    with pytest.raises(InvalidInput):
        dc.validate_name(name)


@pytest.mark.parametrize("ip", ["192.168.100.10", "0.0.0.0", "255.255.255.255"])
def test_valid_ipv4(ip):
    assert dc.validate_ipv4(ip) == ip


@pytest.mark.parametrize("ip", ["256.1.1.1", "1.2.3", "1.2.3.4.5", "a.b.c.d", ""])
def test_invalid_ipv4(ip):
    with pytest.raises(InvalidInput):
        dc.validate_ipv4(ip)


def test_fqdn_normalizes_trailing_dot():
    assert dc.validate_fqdn("mail.empresa.test") == "mail.empresa.test."
    assert dc.validate_fqdn("mail.empresa.test.") == "mail.empresa.test."


@pytest.mark.parametrize("bad", ["", "a..b.", "-a.b.", "a_b."])
def test_invalid_fqdn(bad):
    with pytest.raises(InvalidInput):
        dc.validate_fqdn(bad)


def test_validate_forward_record_mx_needs_priority():
    rec = dc.validate_forward_record({"name": "@", "type": "MX", "value": "mail.empresa.test", "priority": "10"})
    assert rec == {"name": "@", "type": "MX", "value": "mail.empresa.test.", "priority": 10}


def test_validate_reverse_record():
    rec = dc.validate_reverse_record({"name": "10", "value": "server.empresa.test"})
    assert rec == {"name": "10", "type": "PTR", "value": "server.empresa.test."}
    with pytest.raises(InvalidInput):
        dc.validate_reverse_record({"name": "300", "value": "x.empresa.test."})


def test_duplicate_and_cname_conflict():
    with pytest.raises(InvalidInput):
        dc.check_duplicates_and_cname(
            [{"name": "site", "type": "A", "value": "1.1.1.1", "priority": None}] * 2
        )
    with pytest.raises(InvalidInput):
        dc.check_duplicates_and_cname(
            [
                {"name": "x", "type": "A", "value": "1.1.1.1", "priority": None},
                {"name": "x", "type": "CNAME", "value": "y.test.", "priority": None},
            ]
        )


# --------------------------------------------------------- zona (texto)
ZONE_TEXT = """$TTL 3600
@   IN  SOA ns.empresa.test. admin.empresa.test. (
        2026112301 ; serial
        3600 ; refresh
        900  ; retry
        604800 ; expire
        3600 ) ; minimum
    IN  NS  ns.empresa.test.
; ===== CONSOLE:INICIO (gerado pelo console, nao editar a mao) =====
site                    IN  A   192.168.100.10
mail                    IN  A   192.168.100.10
; ===== CONSOLE:FIM =====
"""


def test_split_managed_block_roundtrip():
    before, lines, after = dc.split_managed_block(ZONE_TEXT)
    assert lines == ["site                    IN  A   192.168.100.10", "mail                    IN  A   192.168.100.10"]
    assert before.endswith(dc.MARKER_START + "\n")
    assert after.startswith("\n" + dc.MARKER_END)
    assert before + "\n".join(lines) + after == ZONE_TEXT


def test_split_managed_block_missing_markers_is_config_error():
    with pytest.raises(dc.DnsConfigError):
        dc.split_managed_block("$TTL 3600\n@ IN SOA ...\n")


def test_render_and_parse_record_line_roundtrip():
    rec = {"name": "site", "type": "A", "value": "192.168.100.10"}
    line = dc.render_record_line(rec)
    assert dc.parse_record_line(line, "fwd") == rec


def test_render_and_parse_mx():
    rec = {"name": "@", "type": "MX", "priority": 10, "value": "mail.empresa.test."}
    line = dc.render_record_line(rec)
    assert dc.parse_record_line(line, "fwd") == rec


@pytest.fixture
def tmpdb(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    db.init_db()


def test_next_serial_increments_same_day(tmpdb, monkeypatch):
    import app.dns_client as dc_mod
    from datetime import datetime, timezone

    fixed = datetime(2026, 11, 23, tzinfo=timezone.utc)

    class FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed

    monkeypatch.setattr(dc_mod, "datetime", FakeDatetime)
    s1 = dc._next_serial("fwd")
    s2 = dc._next_serial("fwd")
    assert s1 == "2026112301" and s2 == "2026112302"


def test_next_serial_is_always_greater_than_file(tmpdb, monkeypatch):
    # Serial escrito à mão hoje (2026112301): a 1ª publicação do console não
    # pode repetir o mesmo número.
    import app.dns_client as dc_mod
    from datetime import datetime, timezone

    class FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 23, tzinfo=timezone.utc)

    monkeypatch.setattr(dc_mod, "datetime", FakeDatetime)
    assert dc._next_serial("fwd", 2026112301) == "2026112302"
    assert dc._next_serial("fwd", 2030010105) == "2030010106"


def test_bump_serial_multiline_and_inline():
    assert "2026112399 ; serial" in dc._bump_serial(ZONE_TEXT, "2026112399")
    inline = "@ IN SOA ns.empresa.test. admin.empresa.test. ( 2026112301 3600 900 604800 3600 )\n"
    assert "( 2026112399 3600" in dc._bump_serial(inline, "2026112399")


def test_bump_serial_rejects_placeholder_text():
    # O BIND recusa texto no lugar do serial; o console também não deve aceitar.
    with pytest.raises(dc.DnsConfigError):
        dc._bump_serial("@ IN SOA ns. admin. (\n  __SERIAL__ ; serial\n", "2026112301")


def test_serial_keeps_increasing_across_publications(tmpdb, monkeypatch):
    # Regressão: antes, só a 1ª publicação trocava o serial.
    text = ZONE_TEXT
    for _ in range(3):
        current = int(dc._find_serial(text).group(2))
        text = dc._bump_serial(text, dc._next_serial("fwd", current))
        assert int(dc._find_serial(text).group(2)) > current


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


class FakeDnsSsh:
    """Simula cat/checkzone/cp/rndc reload para apply(); começa com ZONE_TEXT."""

    def __init__(self, monkeypatch, checkzone_ok=True, cp_ok=True, reload_ok=True):
        self.files = {config.DNS_ZONE_FWD_PATH: ZONE_TEXT}
        self.sftp_store = {}
        self.checkzone_ok, self.cp_ok, self.reload_ok = checkzone_ok, cp_ok, reload_ok
        self.commands = []
        monkeypatch.setattr(dc.ssh_client, "connect", lambda: self)
        monkeypatch.setattr(dc.ssh_client, "run", self.run)

    def open_sftp(self):
        return FakeSftp(self.sftp_store)

    def close(self):
        pass

    def run(self, client, argv, timeout=15):
        self.commands.append(argv)
        if argv[:3] == ["sudo", "-n", "/usr/bin/cat"]:
            return 0, self.files[argv[3]], ""
        if argv[0] == config.NAMED_CHECKZONE_BIN:
            return (0, "OK", "") if self.checkzone_ok else (1, "", "zona invalida")
        if argv[:3] == ["sudo", "-n", "/usr/bin/cp"]:
            if self.cp_ok:
                self.files[argv[4]] = self.sftp_store[argv[3]]
            return (0, "", "") if self.cp_ok else (1, "", "sudoers")
        if argv[:3] == ["sudo", "-n", "/usr/sbin/rndc"]:
            return (0, "", "") if self.reload_ok else (1, "", "rndc falhou")
        if argv[0] == "rm":
            return 0, "", ""
        raise AssertionError(f"comando inesperado: {argv}")


USER = {"username": "admin1", "role": "admin"}


def test_create_record_applies_and_versions(tmpdb, monkeypatch):
    ssh = FakeDnsSsh(monkeypatch)
    result = dc.create_record("fwd", {"name": "ftp", "type": "A", "value": "192.168.100.10"}, USER)
    assert result["zone"] == "fwd" and result["records"] == 3
    zone = dc.read_zone("fwd")
    assert {"name": "ftp", "type": "A", "value": "192.168.100.10"} in zone["records"]
    version = audit.last_config_version("dns_fwd")
    assert version is not None and "ftp" not in version["content"]  # versão ANTERIOR não tem o novo registro


def test_create_record_rejects_duplicate(tmpdb, monkeypatch):
    FakeDnsSsh(monkeypatch)
    with pytest.raises(Conflict):
        dc.create_record("fwd", {"name": "site", "type": "A", "value": "192.168.100.10"}, USER)


def test_create_record_invalid_zone_is_rejected_by_checkzone(tmpdb, monkeypatch):
    FakeDnsSsh(monkeypatch, checkzone_ok=False)
    with pytest.raises(dc.DnsValidationError):
        dc.create_record("fwd", {"name": "x", "type": "A", "value": "1.2.3.4"}, USER)


def test_delete_record_not_found(tmpdb, monkeypatch):
    FakeDnsSsh(monkeypatch)
    with pytest.raises(Conflict):
        dc.delete_record("fwd", "nao-existe", "A", "1.2.3.4", USER)


def test_restore_without_version_is_conflict(tmpdb, monkeypatch):
    FakeDnsSsh(monkeypatch)
    with pytest.raises(Conflict):
        dc.restore_last_version("fwd", USER)


def test_restore_reapplies_previous_version(tmpdb, monkeypatch):
    FakeDnsSsh(monkeypatch)
    dc.create_record("fwd", {"name": "ftp", "type": "A", "value": "192.168.100.10"}, USER)
    dc.restore_last_version("fwd", USER)
    zone = dc.read_zone("fwd")
    assert {"name": "ftp", "type": "A", "value": "192.168.100.10"} not in zone["records"]


def test_every_sudo_command_matches_sudoers_file(tmpdb, monkeypatch):
    from sudoers_rules import allowed_commands, sudo_commands

    ssh = FakeDnsSsh(monkeypatch)
    dc.create_record("fwd", {"name": "ftp", "type": "A", "value": "192.168.100.10"}, USER)
    allowed = allowed_commands()
    issued = sudo_commands(ssh.commands)
    assert issued and all(c in allowed for c in issued), set(issued) - allowed
    # a zona reversa usa o mesmo fluxo, com os próprios caminhos fixos
    assert f"root /usr/bin/cp {dc._stage_path('rev')} {config.DNS_ZONE_REV_PATH}" in allowed
    assert f"root /usr/sbin/rndc reload {config.DNS_ZONE_REV_NAME}" in allowed
