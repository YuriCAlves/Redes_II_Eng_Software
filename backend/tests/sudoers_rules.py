"""Lê deploy/sudoers/console-n2-servicos e devolve os comandos exatos permitidos.

Os testes de DNS/DHCP conferem que TODO comando com sudo que o console manda
existe, letra por letra, nesse arquivo (o sudoers não tem curinga, então
qualquer diferença de caminho ou argumento faria o Debian recusar o comando).
"""
from pathlib import Path

SUDOERS = Path(__file__).resolve().parents[2] / "deploy" / "sudoers" / "console-n2-servicos"


def allowed_commands() -> set[str]:
    text = SUDOERS.read_text(encoding="utf-8")
    logical = text.replace("\\\n", " ")
    cmds: set[str] = set()
    for line in logical.splitlines():
        line = line.strip()
        if not line.startswith("Cmnd_Alias"):
            continue
        _, body = line.split("=", 1)
        cmds.update(" ".join(c.split()) for c in body.split(","))
    assert cmds and not any("*" in c for c in cmds)
    return cmds


def sudo_commands(commands: list[list[str]]) -> list[str]:
    return [" ".join(argv[2:]) for argv in commands if argv[:2] == ["sudo", "-n"]]
