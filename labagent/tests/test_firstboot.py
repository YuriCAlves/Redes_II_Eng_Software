"""Testa o script de 1ª inicialização do convidado com comandos do sistema falsos.

Nada é alterado no sistema real: /etc, /var e o VBoxControl ficam numa pasta
temporária (CONSOLE_N2_ROOT) e hostnamectl, chpasswd, systemctl... são stubs
que só registram a chamada.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "guest" / "console-n2-firstboot"
OP = "a" * 32

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="precisa de bash")


def make_env(tmp_path, props):
    root = tmp_path / "root"
    (root / "etc/ssh").mkdir(parents=True)
    (root / "var/lib/dbus").mkdir(parents=True)
    (root / "etc/hosts").write_text("127.0.0.1\tlocalhost\n127.0.1.1\tbase\n")
    (root / "etc/ssh/ssh_host_ed25519_key").write_text("chave-da-base")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    store = tmp_path / "props"
    store.mkdir()
    for key, value in props.items():
        (store / key.strip("/").replace("/", "_")).write_text(value)
    (bin_dir / "VBoxControl").write_text(f"""#!/bin/bash
# VBoxControl --nologo guestproperty get|set NAME [VALUE]
f="{store}/$(echo "$4" | sed 's#^/##; s#/#_#g')"
if [ "$3" = get ]; then [ -f "$f" ] && echo "Value: $(cat "$f")" || echo "No value set!"; fi
if [ "$3" = set ]; then printf '%s' "$5" > "$f"; fi
""")
    for name in ("hostnamectl", "systemd-machine-id-setup", "ssh-keygen", "chpasswd", "passwd", "systemctl", "logger"):
        body = 'echo "$0 $*" >> "%s"\n' % log
        if name == "chpasswd":
            body = 'echo "chpasswd $(cat)" >> "%s"\n' % log
        if name == "systemctl":
            body += 'exit 0\n'
        (bin_dir / name).write_text("#!/bin/bash\n" + body)
    for f in bin_dir.iterdir():
        f.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "CONSOLE_N2_ROOT": str(root),
           "CONSOLE_N2_VBOXCONTROL": str(bin_dir / "VBoxControl")}
    return root, store, log, env


def run(env):
    return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=30)


def test_personalizes_new_instance_and_signals_ready(tmp_path):
    root, store, log, env = make_env(tmp_path, {
        "/ConsoleN2/InstanceId": OP,
        "/ConsoleN2/Hostname": "lab-ana-aaaaaaaa",
        "/ConsoleN2/GuestPassword": "Senha_Propria-123",
    })
    r = run(env)
    assert r.returncode == 0, r.stderr
    calls = log.read_text()
    assert "hostnamectl set-hostname lab-ana-aaaaaaaa" in calls
    assert "chpasswd aluno:Senha_Propria-123" in calls
    assert "ssh-keygen -A" in calls and "systemd-machine-id-setup" in calls
    assert not (root / "etc/ssh/ssh_host_ed25519_key").exists()  # chave da base removida
    assert "127.0.1.1\tlab-ana-aaaaaaaa" in (root / "etc/hosts").read_text()
    assert (root / "var/lib/console-n2/instance-id").read_text().strip() == OP
    assert (store / "ConsoleN2_Ready").read_text() == OP


def test_later_boot_only_signals_ready(tmp_path):
    root, store, log, env = make_env(tmp_path, {"/ConsoleN2/InstanceId": OP, "/ConsoleN2/Hostname": "lab-x"})
    (root / "var/lib/console-n2").mkdir(parents=True)
    (root / "var/lib/console-n2/instance-id").write_text(OP + "\n")
    assert run(env).returncode == 0
    assert "hostnamectl" not in (log.read_text() if log.exists() else "")
    assert (store / "ConsoleN2_Ready").read_text() == OP


def test_rejects_bad_hostname(tmp_path):
    _, store, log, env = make_env(tmp_path, {"/ConsoleN2/InstanceId": OP, "/ConsoleN2/Hostname": "x; rm -rf /"})
    assert run(env).returncode == 1
    assert not (store / "ConsoleN2_Ready").exists()
    assert "hostnamectl" not in (log.read_text() if log.exists() else "")
