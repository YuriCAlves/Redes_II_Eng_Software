"""Lê deploy/sudoers/console-n2-servicos e devolve os comandos exatos permitidos.

Os testes de DNS/DHCP conferem que TODO comando com sudo que o console manda
existe, letra por letra, nesse arquivo, com o usuário-alvo certo (root ou
_kea). O sudoers não tem curinga, então qualquer diferença de caminho,
argumento ou usuário faria o Debian recusar o comando.
"""
import re
from pathlib import Path

SUDOERS = Path(__file__).resolve().parents[2] / "deploy" / "sudoers" / "console-n2-servicos"


def allowed_commands() -> set[str]:
    """Conjunto de "<runas> <comando>", ex.: "root /usr/bin/cat /etc/kea/kea-dhcp4.conf"."""
    logical = SUDOERS.read_text(encoding="utf-8").replace("\\\n", " ")
    aliases: dict[str, list[str]] = {}
    grants: list[tuple[str, list[str]]] = []
    for line in logical.splitlines():
        line = line.strip()
        if line.startswith("Cmnd_Alias"):
            name, body = line[len("Cmnd_Alias"):].split("=", 1)
            aliases[name.strip()] = [" ".join(c.split()) for c in body.split(",")]
        elif m := re.match(r"^yuri\s+ALL=\((\w+)\)\s+NOPASSWD:\s*(.+)$", line):
            grants.append((m.group(1), [a.strip() for a in m.group(2).split(",")]))
    cmds = {f"{runas} {c}" for runas, names in grants for n in names for c in aliases[n]}
    assert cmds and not any("*" in c for c in cmds)
    return cmds


def sudo_commands(commands: list[list[str]]) -> list[str]:
    out = []
    for argv in commands:
        if argv[:2] != ["sudo", "-n"]:
            continue
        rest = argv[2:]
        runas = "root"
        if rest[:1] == ["-u"]:
            runas, rest = rest[1], rest[2:]
        out.append(f"{runas} {' '.join(rest)}")
    return out
