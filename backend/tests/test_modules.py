"""Testes de lógica dos módulos CP3-CP6 com servidores falsos (sem FastAPI/paramiko)."""
import ftplib
import hashlib
import io

import pytest

from app import config, db, ftp_client as fc, mail_client as mc, ssh_client as sc
from app.errors import Conflict, InvalidInput, PayloadTooLarge


# =============================================================== CP3: FTP
class FakeFTP:
    def __init__(self, files=None):
        self.files = {k: (v, "20261107120000") for k, v in (files or {}).items()}
        self.dirs = {"subpasta"}

    def nlst(self):
        return list(self.files) + list(self.dirs) + [".oculto"]

    def size(self, name):
        if name in self.files:
            return len(self.files[name][0])
        raise ftplib.error_perm("550 Could not get file size.")

    def voidcmd(self, cmd):
        assert cmd.startswith("MDTM ")
        return "213 " + self.files[cmd[5:]][1]

    def storbinary(self, cmd, fileobj, blocksize=8192):
        assert cmd.startswith("STOR ")
        self.files[cmd[5:]] = (fileobj.read(), "20261107130000")

    def rename(self, src, dst):
        self.files[dst] = self.files.pop(src)

    def delete(self, name):
        self.files.pop(name, None)

    def retrbinary(self, cmd, callback, blocksize=8192):
        callback(self.files[cmd[5:]][0])


@pytest.mark.parametrize(
    "name",
    ["relatório final.pdf", "foto-1.JPG", "notas.txt", "a_b.png", "x.jpeg"],
)
def test_valid_filenames(name):
    assert fc.validate_filename(name) == name


@pytest.mark.parametrize(
    "name",
    ["", None, "../x.pdf", "a/b.pdf", "a\\b.pdf", "..", ".oculto.pdf", "virus.exe", "semextensao",
     "x.pdf ", " x.pdf", "a;rm.pdf" , "nome\nquebra.pdf", "x.pdf.", "a" * 101 + ".pdf"],
)
def test_invalid_filenames(name):
    with pytest.raises(InvalidInput):
        fc.validate_filename(name)


def test_hash_and_measure_matches_sha256():
    data = b"conteudo do pdf" * 1000
    digest, size = fc.hash_and_measure(io.BytesIO(data))
    assert digest == hashlib.sha256(data).hexdigest() and size == len(data)


def test_hash_and_measure_limits(monkeypatch):
    monkeypatch.setattr(config, "MAX_UPLOAD_MB", 1)
    with pytest.raises(PayloadTooLarge):
        fc.hash_and_measure(io.BytesIO(b"x" * (1024 * 1024 + 1)))
    with pytest.raises(InvalidInput):
        fc.hash_and_measure(io.BytesIO(b""))


def test_upload_roundtrip_hashes_match():
    ftp, data = FakeFTP(), b"%PDF-1.4 teste"
    f = io.BytesIO(data)
    original, _ = fc.hash_and_measure(f)  # hash ANTES do envio; também rebobina o arquivo
    assert fc.upload(ftp, "doc.pdf", f, overwrite=False) is False
    recovered, size = fc.sha256_remote(ftp, "doc.pdf")
    assert recovered == original and size == len(data)
    assert not any(n.startswith(".upload-") for n in ftp.files)  # temporário foi renomeado


def test_upload_refuses_overwrite_unless_asked():
    ftp = FakeFTP({"doc.pdf": b"antigo"})
    with pytest.raises(Conflict):
        fc.upload(ftp, "doc.pdf", io.BytesIO(b"novo"), overwrite=False)
    assert ftp.files["doc.pdf"][0] == b"antigo"
    assert fc.upload(ftp, "doc.pdf", io.BytesIO(b"novo"), overwrite=True) is True
    assert ftp.files["doc.pdf"][0] == b"novo"


def test_upload_failure_cleans_temp_and_is_transfer_error():
    class Broken(FakeFTP):
        def rename(self, src, dst):
            raise ftplib.error_perm("550 rename falhou")

    ftp = Broken()
    with pytest.raises(fc.FtpTransferError):
        fc.upload(ftp, "a.txt", io.BytesIO(b"x"), overwrite=False)
    assert ftp.files == {}


def test_list_files_skips_dirs_and_hidden_and_sorts():
    ftp = FakeFTP({"a.pdf": b"1234", "b.txt": b"12"})
    ftp.files["b.txt"] = (b"12", "20261108000000")
    rows = fc.list_files(ftp)
    assert [r["name"] for r in rows] == ["b.txt", "a.pdf"]  # mais recente primeiro
    assert rows[1]["size"] == 4 and rows[1]["modified"] == "2026-11-07T12:00:00+00:00"
    assert rows[0]["url"] == "http://site.empresa.test/arquivos/b.txt"


def test_list_files_empty_dir_550():
    class Empty(FakeFTP):
        def nlst(self):
            raise ftplib.error_perm("550 No files found")

    assert fc.list_files(Empty()) == []


def test_public_url_quotes_name():
    assert fc.public_url("relatório final.pdf").endswith("/relat%C3%B3rio%20final.pdf")


def test_connect_without_password_is_config_error(monkeypatch):
    monkeypatch.delenv("CONSOLE_FTP_PASSWORD", raising=False)
    with pytest.raises(fc.FtpConfigError):
        fc.connect()


def test_connect_refused_is_connection_error_not_transfer(monkeypatch):
    monkeypatch.setenv("CONSOLE_FTP_PASSWORD", "x")
    monkeypatch.setattr(config, "FTP_HOST", "127.0.0.1")
    monkeypatch.setattr(config, "FTP_PORT", 1)
    with pytest.raises(fc.FtpConnectionError):
        fc.connect()


# ============================================================ CP4: e-mail
def test_parse_recipients():
    assert mc.parse_recipients("a@empresa.test, b@empresa.test;") == ["a@empresa.test", "b@empresa.test"]
    for bad in ("", "sem-arroba", "a@b.c\nBcc: x@y.z", "x@y.z," + ",".join(f"u{i}@e.t" for i in range(11))):
        with pytest.raises(InvalidInput):
            mc.parse_recipients(bad)


def test_build_message_blocks_header_injection():
    with pytest.raises(InvalidInput):
        mc.build_message("aluno@empresa.test", ["suporte@empresa.test"], "oi\nBcc: x@y.z", "corpo")
    msg = mc.build_message("aluno@empresa.test", ["suporte@empresa.test"], "Olá", "corpo ç")
    assert msg["From"] == "aluno@empresa.test" and msg["Bcc"] is None
    assert msg["Message-ID"].endswith("@empresa.test>")


def test_validators():
    assert mc.validate_account("aluno@empresa.test")
    for bad in ("root@empresa.test", "", "aluno@outro.test"):
        with pytest.raises(InvalidInput):
            mc.validate_account(bad)
    assert mc.validate_folder("sent") == "sent"
    with pytest.raises(InvalidInput):
        mc.validate_folder("../../etc")
    for bad in ("abc", "1; DELETE", "", "1" * 13):
        with pytest.raises(InvalidInput):
            mc.validate_uid(bad)


def test_pick_sent_folder():
    special = [b'(\\HasNoChildren) "/" INBOX', b'(\\HasNoChildren \\Sent) "/" "Itens Enviados"']
    assert mc.pick_sent_folder(special) == "Itens Enviados"
    by_name = [b'(\\HasNoChildren) "." INBOX', b'(\\HasNoChildren) "." Sent']
    assert mc.pick_sent_folder(by_name) == "Sent"
    assert mc.pick_sent_folder([b'(\\HasNoChildren) "/" INBOX']) == "Sent"  # padrão


def test_parse_fetch_headers_newest_first_and_decodes():
    h1 = b"From: aluno@empresa.test\r\nTo: suporte@empresa.test\r\nSubject: =?UTF-8?Q?Rela=C3=A7=C3=A3o?=\r\nDate: Sat, 07 Nov 2026 10:00:00 -0300\r\n\r\n"
    h2 = b"From: x@empresa.test\r\nTo: y@empresa.test\r\nDate: Sat, 07 Nov 2026 11:00:00 -0300\r\n\r\n"
    data = [
        (b"1 (UID 4 BODY[HEADER.FIELDS (FROM TO SUBJECT DATE)] {99}", h1),
        b")",
        (b"2 (BODY[HEADER.FIELDS (FROM TO SUBJECT DATE)] {80}", h2),
        b" UID 9)",  # UID depois do literal também é aceito
    ]
    rows = mc.parse_fetch_headers(data)
    assert [r["uid"] for r in rows] == [9, 4]
    assert rows[1]["subject"] == "Relação" and rows[0]["subject"] == "(sem assunto)"


def test_parse_full_message_plain_html_and_attachment():
    from email.message import EmailMessage

    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = "a@empresa.test", "b@empresa.test", "Teste"
    m.set_content("texto simples")
    m.add_attachment(b"%PDF", maintype="application", subtype="pdf", filename="x.pdf")
    parsed = mc.parse_full_message(m.as_bytes())
    assert parsed["body"].strip() == "texto simples" and parsed["has_attachments"] is True

    h = EmailMessage()
    h["From"], h["To"], h["Subject"] = "a@empresa.test", "b@empresa.test", "Html"
    h.set_content("<p>Olá <b>mundo</b></p><script>alert(1)</script>", subtype="html")
    body = mc.parse_full_message(h.as_bytes())["body"]
    assert "Olá mundo" in body and "<" not in body and "alert" not in body


def test_smtp_failure_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(config, "MAIL_HOST", "127.0.0.1")
    monkeypatch.setattr(config, "SMTP_PORT", 1)
    msg = mc.build_message("aluno@empresa.test", ["suporte@empresa.test"], "a", "b")
    r = mc.send_smtp(msg, "aluno@empresa.test", ["suporte@empresa.test"])
    assert r["ok"] is False and "SMTP" in r["error"]


def test_imap_without_password_is_config_error(monkeypatch):
    monkeypatch.delenv("CONSOLE_MAIL_PASS_ALUNO", raising=False)
    with pytest.raises(mc.MailConfigError):
        mc.imap_connect("aluno@empresa.test")


# ============================================================== CP5: SSH
@pytest.mark.parametrize("name", ["debian-teste", "ab", "srv01", "a" * 63])
def test_hostname_valid(name):
    assert sc.validate_hostname(name) == name


@pytest.mark.parametrize(
    "name", ["", "a", "-ab", "ab-", "Debian", "a_b", "a.b", "a b", "a;reboot", "a$(id)", "a" * 64, "ab\n", None, 5]
)
def test_hostname_invalid(name):
    with pytest.raises(InvalidInput):
        sc.validate_hostname(name)


def test_parse_os_release():
    assert sc.parse_os_release('NAME="Debian GNU/Linux"\nPRETTY_NAME="Debian GNU/Linux 13 (trixie)"\n') == "Debian GNU/Linux 13 (trixie)"


class FakeSsh:
    """Substitui connect()/run(): simula o hostname do Debian e registra os comandos."""

    def __init__(self, monkeypatch, hostname="debian-servidor", sudo_ok=True):
        self.hostname, self.sudo_ok, self.commands = hostname, sudo_ok, []
        monkeypatch.setattr(sc, "connect", lambda: self)
        monkeypatch.setattr(sc, "run", self.run)

    def close(self):
        pass

    def run(self, client, argv, timeout=15):
        self.commands.append(argv)
        if argv == ["hostnamectl", "--static"]:
            return 0, self.hostname + "\n", ""
        if argv[:4] == ["sudo", "-n", "hostnamectl", "set-hostname"]:
            if not self.sudo_ok:
                return 1, "", "sudo: a password is required"
            self.hostname = argv[4]
            return 0, "", ""
        raise AssertionError(f"comando inesperado: {argv}")


@pytest.fixture
def tmpdb(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    db.init_db()


def test_change_then_restore_returns_to_original(tmpdb, monkeypatch):
    ssh = FakeSsh(monkeypatch)
    r = sc.change_hostname("servidor-novo")
    assert (r["previous"], r["current"], r["original"]) == ("debian-servidor", "servidor-novo", "debian-servidor")
    # segunda alteração NÃO sobrescreve o original registrado
    r = sc.change_hostname("outro-nome")
    assert r["previous"] == "servidor-novo" and r["original"] == "debian-servidor"
    r = sc.restore_hostname()
    assert (r["previous"], r["current"]) == ("outro-nome", "debian-servidor")
    assert db.kv_get(sc.ORIGINAL_KEY) is None
    with pytest.raises(Conflict):
        sc.restore_hostname()
    assert ssh.hostname == "debian-servidor"


def test_change_failure_does_not_leave_original_saved(tmpdb, monkeypatch):
    FakeSsh(monkeypatch, sudo_ok=False)
    with pytest.raises(sc.SshCommandError):
        sc.change_hostname("novo-nome")
    assert db.kv_get(sc.ORIGINAL_KEY) is None  # botão Restaurar não deve ficar ativo


def test_change_rejects_invalid_before_connecting(tmpdb, monkeypatch):
    ssh = FakeSsh(monkeypatch)
    with pytest.raises(InvalidInput):
        sc.change_hostname("x; rm -rf /")
    assert ssh.commands == []  # nem abriu comando remoto


def test_connect_without_key_or_known_hosts_is_config_error(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SSH_KNOWN_HOSTS", str(tmp_path / "nao-existe"))
    with pytest.raises(sc.SshConfigError):
        sc.connect()


# =============================================================== CP6: logs
def test_log_command_whitelist():
    assert sc.log_command("apache2", 50) == ["tail", "-n", "50", "/var/log/apache2/access.log"]
    assert sc.log_command("vsftpd", 20) == ["journalctl", "-u", "vsftpd", "-n", "20", "--no-pager"]
    for svc, n in (("mysql", 20), ("vsftpd; id", 20), ("ssh", 7), ("ssh", "20")):
        with pytest.raises(InvalidInput):
            sc.log_command(svc, n)


def test_read_logs_ok_and_command_failure(monkeypatch):
    fake = type("F", (), {"close": lambda self: None})()
    monkeypatch.setattr(sc, "connect", lambda: fake)
    monkeypatch.setattr(sc, "run", lambda c, a, timeout=15: (0, "l1\nl2\n" + "x" * 5000 + "\n", ""))
    r = sc.read_logs("vsftpd", 20)
    assert r["lines"][:2] == ["l1", "l2"] and len(r["lines"][2]) == 2000 and r["timestamp"]
    monkeypatch.setattr(sc, "run", lambda c, a, timeout=15: (1, "", "Permission denied /var/log/x"))
    with pytest.raises(sc.SshCommandError) as e:
        sc.read_logs("apache2", 20)
    assert "Permission" not in e.value.message  # não vaza detalhes do servidor


def test_ssh_unreachable_means_unknown_state_not_stopped(monkeypatch):
    def boom():
        raise sc.SshUnreachable()

    monkeypatch.setattr(sc, "connect", boom)
    with pytest.raises(sc.SshUnreachable) as e:
        sc.read_logs("vsftpd", 50)
    assert e.value.code == "unknown_state" and "desconhecido" in e.value.message
