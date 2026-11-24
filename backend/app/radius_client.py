"""Incremento 4 - serviço exclusivo: autenticação RADIUS (FreeRADIUS 3), seção 8.4.

O console gerencia, por inteiro, três arquivos em /etc/freeradius/3.0/console-n2/
(incluídos uma única vez na configuração pelo deploy/freeradius/instalar.sh):
 - console-n2.json : estado (clientes NAS, políticas, usuários) — fonte da verdade;
 - clients.conf    : clientes NAS gerados a partir do estado;
 - users           : usuários e políticas no formato do módulo `files`.

Mesmo padrão de DNS/DHCP (ADR-001..003): SSH de volta ao Debian com sudoers de
comandos exatos; nada do usuário vira argumento de shell; valores validados
com regex estritas antes de virar texto de configuração; versão anterior
guardada em config_versions; `freeradius -XC` valida antes de reiniciar.

Diferença necessária: o FreeRADIUS só valida a árvore de configuração no lugar
dela, então o candidato é instalado, validado e, se recusado, a versão
anterior volta NA MESMA OPERAÇÃO, antes de qualquer reinício — o serviço em
execução nunca carrega uma configuração inválida.

Segredos: senhas de usuário só como hash (SSHA2-256 ou SHA-512 crypt); o
segredo de um NAS só é mostrado a admin, com auditoria; nada disso vai para a
auditoria nem para listagens.
"""
import ipaddress
import json
import re
import secrets
import threading
from datetime import datetime

from . import audit, config, radius_hash, ssh_client
from .errors import Conflict, InvalidInput, NotFound, ServiceError

_lock = threading.RLock()  # leitura + alteração + aplicação em sequência (sem atualização perdida)

CLIENT_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
USERNAME_RE = re.compile(r"^[a-z][a-z0-9._-]{0,31}$")
POLICY_RE = re.compile(r"^[a-z][a-z0-9_-]{0,23}$")
SECRET_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
# texto que vira string entre aspas no arquivo users: sem aspas, barra, % (xlat) ou quebra de linha
MESSAGE_RE = re.compile(r"^[\w .,:;()!?/+-]{0,80}$", re.UNICODE)
DESCRIPTION_RE = MESSAGE_RE
LOAD_PREFIX = "carga"
STAGE_FILES = {
    "state": ("radius-state.json", config.RADIUS_STATE_PATH),
    "clients": ("radius-clients.conf", config.RADIUS_CLIENTS_PATH),
    "users": ("radius-users", config.RADIUS_USERS_PATH),
}


class RadiusConfigError(ServiceError):
    status, code = 502, "radius_config"
    message = "Configuração do RADIUS ilegível no Debian."


class RadiusValidationError(ServiceError):
    status, code = 400, "radius_rejected"
    message = "O FreeRADIUS recusou a configuração; a versão anterior foi mantida."


# ------------------------------------------------------------------ validação
def validate_client(body: dict, existing: list[dict]) -> dict:
    name = str(body.get("name", "")).strip().lower()
    if not CLIENT_NAME_RE.match(name):
        raise InvalidInput("Nome do cliente: letras minúsculas, dígitos, _ ou -, começando por letra.")
    try:
        ip = ipaddress.IPv4Address(str(body.get("ipaddr", "")).strip())
    except ValueError as exc:
        raise InvalidInput("Endereço IPv4 do NAS inválido.") from exc
    if ip not in ipaddress.IPv4Network(config.RADIUS_NAS_NETWORK):
        raise InvalidInput(f"O NAS precisa estar na rede do laboratório ({config.RADIUS_NAS_NETWORK}).")
    if any(c["name"] == name for c in existing):
        raise Conflict("Já existe um cliente com esse nome.")
    if any(c["ipaddr"] == str(ip) for c in existing):
        raise Conflict("Já existe um cliente com esse endereço.")
    secret = str(body.get("secret") or "").strip() or secrets.token_urlsafe(24)
    if not SECRET_RE.match(secret):
        raise InvalidInput("Segredo: 16 a 64 caracteres (letras, dígitos, _ ou -).")
    description = str(body.get("description") or "").strip()
    if not DESCRIPTION_RE.match(description):
        raise InvalidInput("Descrição com caracteres não permitidos.")
    return {"name": name, "ipaddr": str(ip), "secret": secret, "description": description,
            "require_ma": bool(body.get("require_ma", False))}


def validate_policy(body: dict) -> dict:
    name = str(body.get("name", "")).strip().lower()
    if not POLICY_RE.match(name):
        raise InvalidInput("Nome da política: letras minúsculas, dígitos, _ ou -.")
    try:
        session = int(body.get("session_timeout", 0))
        idle = int(body.get("idle_timeout", 0))
    except (TypeError, ValueError) as exc:
        raise InvalidInput("Tempos devem ser números inteiros (segundos).") from exc
    blocked = bool(body.get("blocked", False))
    if not blocked and not 60 <= session <= 86400:
        raise InvalidInput("Tempo de sessão entre 60 e 86400 segundos.")
    if not 0 <= idle <= 86400:
        raise InvalidInput("Tempo ocioso entre 0 e 86400 segundos.")
    message = str(body.get("reply_message") or "").strip()
    if not MESSAGE_RE.match(message):
        raise InvalidInput("Mensagem com caracteres não permitidos (sem aspas, % ou barra invertida).")
    return {"name": name, "session_timeout": 0 if blocked else session, "idle_timeout": 0 if blocked else idle,
            "blocked": blocked, "reply_message": message}


def validate_password(password) -> str:
    if not isinstance(password, str) or not 8 <= len(password) <= 128:
        raise InvalidInput("Senha com 8 a 128 caracteres.")
    if any(ord(ch) < 32 for ch in password):
        raise InvalidInput("Senha com caracteres de controle.")
    return password


def validate_username(username) -> str:
    username = str(username or "").strip().lower()
    if not USERNAME_RE.match(username) or username.startswith(LOAD_PREFIX):
        raise InvalidInput("Usuário: letras minúsculas, dígitos, ., _ ou - (prefixo 'carga' é reservado).")
    return username


def _scheme(body: dict) -> tuple[str, int]:
    scheme = body.get("scheme") or "ssha2-256"
    if scheme not in radius_hash.SCHEMES:
        raise InvalidInput("Esquema de senha inválido.")
    try:
        rounds = int(body.get("rounds") or 5000)
    except (TypeError, ValueError) as exc:
        raise InvalidInput("Rodadas inválidas.") from exc
    if not 1000 <= rounds <= 200000:
        raise InvalidInput("Rodadas do SHA-512 crypt entre 1000 e 200000.")
    return scheme, rounds


def _new_user(username: str, password: str, policy: str, scheme: str, rounds: int, load: bool = False) -> dict:
    attr, value = radius_hash.make(password, scheme, rounds)
    return {"username": username, "policy": policy, "enabled": True, "scheme": scheme,
            "hash_attr": attr, "hash": value, "load": load,
            "created": datetime.now().isoformat(timespec="seconds")}


# ------------------------------------------------------------------ estado e renderização
def check_state(state: dict) -> dict:
    """Confere o estado lido do Debian antes de usá-lo (o arquivo pode ter sido editado à mão)."""
    if not isinstance(state, dict) or not all(isinstance(state.get(k), list) for k in ("clients", "policies", "users")):
        raise RadiusConfigError("console-n2.json sem as listas clients/policies/users.")
    for c in state["clients"]:
        if not (CLIENT_NAME_RE.match(c.get("name", "")) and SECRET_RE.match(c.get("secret", ""))):
            raise RadiusConfigError("console-n2.json com cliente inválido.")
        ipaddress.IPv4Address(c.get("ipaddr", ""))
    names = set()
    for p in state["policies"]:
        if not POLICY_RE.match(p.get("name", "")) or not MESSAGE_RE.match(p.get("reply_message", "")):
            raise RadiusConfigError("console-n2.json com política inválida.")
        names.add(p["name"])
    for u in state["users"]:
        if not USERNAME_RE.match(u.get("username", "")) or u.get("policy") not in names:
            raise RadiusConfigError("console-n2.json com usuário inválido.")
        if u.get("hash_attr") not in ("SSHA2-256-Password", "Crypt-Password") or not re.fullmatch(r"[A-Za-z0-9./$=+]{20,200}", u.get("hash", "")):
            raise RadiusConfigError("console-n2.json com hash de senha inválido.")
    return state


def render_clients(state: dict) -> str:
    out = ["# Gerado pelo Console N2 - nao editar a mao (use a aba RADIUS)\n"]
    for c in state["clients"]:
        out.append(
            f"client {c['name']} {{\n"
            f"\tipaddr = {c['ipaddr']}\n"
            f"\tsecret = {c['secret']}\n"
            f"\tshortname = {c['name']}\n"
            f"\trequire_message_authenticator = {'yes' if c.get('require_ma') else 'no'}\n"
            f"\tnas_type = other\n"
            f"}}\n"
        )
    return "\n".join(out)


def render_users(state: dict) -> str:
    policies = {p["name"]: p for p in state["policies"]}
    out = ["# Gerado pelo Console N2 - nao editar a mao (use a aba RADIUS)\n"]
    for u in state["users"]:
        pol = policies[u["policy"]]
        if pol["blocked"] or not u["enabled"]:
            message = pol["reply_message"] if pol["blocked"] else "Usuario bloqueado pelo administrador"
            out.append(f'"{u["username"]}"\tAuth-Type := Reject\n\tReply-Message := "{message}"\n')
            continue
        reply = [f"Session-Timeout := {pol['session_timeout']}"]
        if pol["idle_timeout"]:
            reply.append(f"Idle-Timeout := {pol['idle_timeout']}")
        if pol["reply_message"]:
            reply.append(f'Reply-Message := "{pol["reply_message"]}"')
        out.append(f'"{u["username"]}"\t{u["hash_attr"]} := "{u["hash"]}"\n\t' + ",\n\t".join(reply) + "\n")
    return "\n".join(out)


def public_state(state: dict) -> dict:
    """Sem segredos de NAS e sem hashes de senha."""
    usage = {}
    for u in state["users"]:
        usage[u["policy"]] = usage.get(u["policy"], 0) + 1
    regular = [u for u in state["users"] if not u.get("load")]
    load = [u for u in state["users"] if u.get("load")]
    return {
        "clients": [{**{k: c[k] for k in ("name", "ipaddr", "description")}, "require_ma": bool(c.get("require_ma"))}
                    for c in state["clients"]],
        "policies": [{**p, "users": usage.get(p["name"], 0)} for p in state["policies"]],
        "users": [{k: u.get(k) for k in ("username", "policy", "enabled", "scheme", "created")} for u in regular],
        "load_users": {"count": len(load), "schemes": sorted({u["scheme"] for u in load})},
    }


# ------------------------------------------------------------------ leitura / aplicação
def _read_state(client) -> dict:
    rc, out, _ = ssh_client.run(client, ["sudo", "-n", "/usr/bin/cat", config.RADIUS_STATE_PATH])
    if rc != 0:
        raise RadiusConfigError("Não foi possível ler console-n2.json (rode deploy/freeradius/instalar.sh).")
    try:
        return check_state(json.loads(out))
    except (ValueError, TypeError) as exc:
        raise RadiusConfigError("console-n2.json não é um JSON válido.") from exc


def read_state() -> dict:
    client = ssh_client.connect()
    try:
        return _read_state(client)
    finally:
        client.close()


def _install(client, state: dict) -> None:
    texts = {
        "state": json.dumps(state, indent=2, ensure_ascii=False) + "\n",
        "clients": render_clients(state),
        "users": render_users(state),
    }
    sftp = client.open_sftp()
    try:
        for key, (stage_name, _) in STAGE_FILES.items():
            with sftp.open(f"{config.STAGE_DIR}/{stage_name}", "w") as f:
                f.write(texts[key])
    finally:
        sftp.close()
    for key in ("clients", "users", "state"):
        stage_name, target = STAGE_FILES[key]
        rc, _, _ = ssh_client.run(client, ["sudo", "-n", "/usr/bin/cp", f"{config.STAGE_DIR}/{stage_name}", target])
        if rc != 0:
            raise ssh_client.SshCommandError("O Debian recusou gravar a configuração do RADIUS (sudoers).")


def _check(client) -> tuple[bool, str]:
    rc, out, err = ssh_client.run(
        client, ["sudo", "-n", "-u", config.RADIUS_SERVICE_USER, config.RADIUS_BIN, "-XC"], timeout=60
    )
    text = out + err
    if rc == 0 and "appears to be OK" in text:
        return True, ""
    # o -X imprime a configuração inteira; o motivo real está nas últimas linhas
    tail = [ln.strip() for ln in text.splitlines()[-25:]]
    lines = [ln for ln in tail if re.search(r"Parse error|[Ee]rror|[Ff]ailed", ln) and "Loading module" not in ln]
    first = lines[0] if lines else "erro não identificado"
    first = re.sub(r"\$6\$\S+|[A-Za-z0-9+/]{40,}={0,2}", "<oculto>", first)  # nunca devolver hashes
    return False, first[:200]


def _cleanup(client) -> None:
    for stage_name, _ in STAGE_FILES.values():  # os arquivos de passagem têm segredos
        ssh_client.run(client, ["rm", "-f", f"{config.STAGE_DIR}/{stage_name}"])


def _apply(new_state: dict, user: dict, note: str, *, allow_broken_current: bool = False) -> dict:
    with _lock:
        client = ssh_client.connect()
        try:
            try:
                previous = _read_state(client)
            except RadiusConfigError:
                if not allow_broken_current:  # só a restauração pode substituir um estado ilegível
                    raise
                previous = None
            _install(client, new_state)
            ok, error = _check(client)
            if not ok:
                if previous is not None:
                    _install(client, previous)  # devolve a versão válida antes de qualquer reinício
                raise RadiusValidationError(f"O FreeRADIUS recusou a configuração ({error}); a versão anterior foi mantida.")
            if previous is not None:
                audit.save_config_version("radius", user["username"], json.dumps(previous, ensure_ascii=False), note)
            rc, _, _ = ssh_client.run(
                client, ["sudo", "-n", "/usr/bin/systemctl", "restart", config.RADIUS_SYSTEMD_UNIT], timeout=30
            )
            if rc != 0:
                raise ssh_client.SshCommandError("Configuração válida gravada, mas o FreeRADIUS não reiniciou.")
        finally:
            try:
                _cleanup(client)
            finally:
                client.close()
    return public_state(new_state)


def _mutate(user: dict, action: str, detail: str, fn) -> dict:
    """Lê o estado atual, aplica `fn` (que valida e altera uma cópia) e publica."""
    with _lock:
        state = read_state()
        new_state = json.loads(json.dumps(state))
        result = fn(new_state)
        if len(new_state["users"]) > config.RADIUS_MAX_USERS:
            raise InvalidInput(f"Limite de {config.RADIUS_MAX_USERS} usuários RADIUS.")
        try:
            public = _apply(new_state, user, detail)
        except ServiceError as exc:
            audit.record(user=user, resource="radius", action=action, result="falha", detail=f"{detail}: {exc.message}")
            raise
    audit.record(user=user, resource="radius", action=action, detail=detail)
    if isinstance(result, dict):
        public = {**public, **result}
    return public


# ------------------------------------------------------------------ operações
def create_client(body: dict, user: dict) -> dict:
    def fn(s):
        s["clients"].append(validate_client(body, s["clients"]))
    return _mutate(user, "client.create", f"cliente NAS {body.get('name')} ({body.get('ipaddr')})", fn)


def delete_client(name: str, user: dict) -> dict:
    def fn(s):
        if not any(c["name"] == name for c in s["clients"]):
            raise NotFound("Cliente não encontrado.")
        s["clients"] = [c for c in s["clients"] if c["name"] != name]
    return _mutate(user, "client.delete", f"cliente NAS {name}", fn)


def client_secret(name: str, user: dict) -> dict:
    state = read_state()
    found = next((c for c in state["clients"] if c["name"] == name), None)
    if found is None:
        raise NotFound("Cliente não encontrado.")
    audit.record(user=user, resource="radius", action="client.secret.view", detail=f"cliente NAS {name}")
    return {"name": name, "ipaddr": found["ipaddr"], "secret": found["secret"]}


def save_policy(body: dict, user: dict) -> dict:
    pol = validate_policy(body)

    def fn(s):
        s["policies"] = [p for p in s["policies"] if p["name"] != pol["name"]] + [pol]
        s["policies"].sort(key=lambda p: p["name"])
    return _mutate(user, "policy.save", f"política {pol['name']}", fn)


def delete_policy(name: str, user: dict) -> dict:
    def fn(s):
        if not any(p["name"] == name for p in s["policies"]):
            raise NotFound("Política não encontrada.")
        if any(u["policy"] == name for u in s["users"]):
            raise Conflict("Há usuários nessa política; mova-os antes de excluir.")
        s["policies"] = [p for p in s["policies"] if p["name"] != name]
    return _mutate(user, "policy.delete", f"política {name}", fn)


def create_user(body: dict, user: dict) -> dict:
    username = validate_username(body.get("username"))
    password = validate_password(body.get("password"))
    scheme, rounds = _scheme(body)
    policy = str(body.get("policy") or "padrao")

    def fn(s):
        if not any(p["name"] == policy for p in s["policies"]):
            raise InvalidInput("Política inexistente.")
        if any(u["username"] == username for u in s["users"]):
            raise Conflict("Usuário já existe.")
        s["users"].append(_new_user(username, password, policy, scheme, rounds))
    return _mutate(user, "user.create", f"usuário {username} (política {policy}, {scheme})", fn)


def _find_user(s: dict, username: str) -> dict:
    found = next((u for u in s["users"] if u["username"] == username and not u.get("load")), None)
    if found is None:
        raise NotFound("Usuário não encontrado.")
    return found


def update_user(body: dict, user: dict) -> dict:
    """Bloquear/desbloquear, trocar de política e/ou redefinir a senha."""
    username = str(body.get("username") or "")
    changes = []
    if body.get("enabled") is not None:
        changes.append("desbloqueado" if body["enabled"] else "bloqueado")
    if body.get("policy"):
        changes.append(f"política {body['policy']}")
    if body.get("password"):
        changes.append("senha redefinida")
    if not changes:
        raise InvalidInput("Nada para alterar.")

    def fn(s):
        u = _find_user(s, username)
        if body.get("enabled") is not None:
            u["enabled"] = bool(body["enabled"])
        if body.get("policy"):
            if not any(p["name"] == body["policy"] for p in s["policies"]):
                raise InvalidInput("Política inexistente.")
            u["policy"] = body["policy"]
        if body.get("password"):
            scheme, rounds = _scheme({"scheme": body.get("scheme") or u["scheme"], "rounds": body.get("rounds")})
            u["hash_attr"], u["hash"] = radius_hash.make(validate_password(body["password"]), scheme, rounds)
            u["scheme"] = scheme
    return _mutate(user, "user.update", f"usuário {username}: {', '.join(changes)}", fn)


def delete_user(username: str, user: dict) -> dict:
    def fn(s):
        _find_user(s, username)
        s["users"] = [u for u in s["users"] if u["username"] != username]
    return _mutate(user, "user.delete", f"usuário {username}", fn)


def create_load_users(body: dict, user: dict) -> dict:
    """Base de carga para o experimento: N usuários carga00001.. com a mesma senha aleatória."""
    try:
        count = int(body.get("count", 0))
    except (TypeError, ValueError) as exc:
        raise InvalidInput("Quantidade inválida.") from exc
    if not 1 <= count <= 5000:
        raise InvalidInput("Quantidade entre 1 e 5000.")
    scheme, rounds = _scheme(body)
    password = secrets.token_urlsafe(12)

    def fn(s):
        s["users"] = [u for u in s["users"] if not u.get("load")]
        base = _new_user("carga00001", password, "padrao", scheme, rounds, load=True)
        for i in range(1, count + 1):
            # mesmo hash para todos: o custo medido no servidor é o da VERIFICAÇÃO, que é por pedido
            s["users"].append({**base, "username": f"{LOAD_PREFIX}{i:05d}"})
        return {"load_password": password, "load_count": count}
    return _mutate(user, "load.create", f"{count} usuários de carga ({scheme}, {rounds} rodadas)", fn)


def delete_load_users(user: dict) -> dict:
    def fn(s):
        s["users"] = [u for u in s["users"] if not u.get("load")]
    return _mutate(user, "load.delete", "usuários de carga removidos", fn)


def restore_last_version(user: dict) -> dict:
    version = audit.last_config_version("radius")
    if not version:
        raise Conflict("Não há versão anterior registrada para restaurar.")
    state = check_state(json.loads(version["content"]))
    public = _apply(state, user, f"restaurar versão #{version['id']}", allow_broken_current=True)
    audit.record(user=user, resource="radius", action="restore", detail=f"versão #{version['id']}")
    return public


# ------------------------------------------------------------------ teste pelo console
def self_test(body: dict, user: dict) -> dict:
    """Envia um Access-Request real ao FreeRADIUS do Debian (cliente localhost).

    Senha e segredo vão pela entrada padrão e por um arquivo temporário: nunca
    aparecem na linha de comando (`ps`) nem na auditoria.
    """
    username = str(body.get("username") or "").strip().lower()
    if not USERNAME_RE.match(username):
        raise InvalidInput("Usuário inválido.")
    password = validate_password(body.get("password"))
    if not config.RADIUS_TEST_SECRET:
        raise ServiceError("Defina CONSOLE_RADIUS_TEST_SECRET (segredo do client localhost).")
    secret_path = f"{config.STAGE_DIR}/radius-test.secret"
    request = f'User-Name = "{username}"\nUser-Password = "{password.replace(chr(92), chr(92) * 2).replace(chr(34), chr(92) + chr(34))}"\n' \
              f"Message-Authenticator = 0x00\n"
    client = ssh_client.connect()
    try:
        sftp = client.open_sftp()
        try:
            with sftp.open(secret_path, "w") as f:
                f.write(config.RADIUS_TEST_SECRET + "\n")
        finally:
            sftp.close()
        rc, out, err = ssh_client.run(
            client,
            [config.RADCLIENT_BIN, "-s", "-c", "1", "-r", "1", "-t", "3", "-S", secret_path, "127.0.0.1:1812", "auth"],
            timeout=20, input=request,
        )
    finally:
        try:
            ssh_client.run(client, ["rm", "-f", secret_path])
        finally:
            client.close()
    text = out + err
    accepted = _summary(text, "Accepted")
    rejected = _summary(text, "Rejected")
    result = "aceita" if accepted else "negada" if rejected else "sem resposta"
    audit.record(user=user, resource="radius", action="test", result="ok" if accepted or rejected else "falha",
                 detail=f"teste de autenticação de {username}: {result}")
    return {"username": username, "result": result}


def _summary(text: str, label: str) -> int:
    m = re.search(rf"{label}\s*:\s*(\d+)", text)
    return int(m.group(1)) if m else 0


# ------------------------------------------------------------------ logs: autenticação e contabilização
_AUTH_RE = re.compile(
    r"^(?P<ts>\w{3} \w{3} +\d+ [\d:]{8} \d{4}) : Auth: \(\d+\) (?P<what>Login OK|Login incorrect|Invalid user)"
    r"(?: \((?P<why>[^)]*)\))?: \[(?P<user>[^\]/]*)(?:/[^\]]*)?\] \(from client (?P<client>\S+) port (?P<port>\S+)"
    r"(?: cli (?P<cli>[^)]+))?\)"
)
_SECRET_RE = re.compile(
    r"^(?P<ts>\w{3} \w{3} +\d+ [\d:]{8} \d{4}) : \w+: .*?Received packet from (?P<src>[\d.]+) with invalid Message-Authenticator"
)
_UNKNOWN_RE = re.compile(
    r"^(?P<ts>\w{3} \w{3} +\d+ [\d:]{8} \d{4}) : \w+: .*?from unknown client (?P<src>[\d.]+)"
)


def _parse_ts(raw: str) -> str:
    try:
        return datetime.strptime(re.sub(r" +", " ", raw), "%a %b %d %H:%M:%S %Y").isoformat()
    except ValueError:
        return raw


def parse_auth_log(text: str, limit: int = 300) -> list[dict]:
    events = []
    for line in text.splitlines():
        if m := _AUTH_RE.match(line):
            what = {"Login OK": "aceita", "Login incorrect": "negada", "Invalid user": "usuario_desconhecido"}[m["what"]]
            if what == "negada" and (m["why"] or "").startswith("No Auth-Type found"):
                what = "usuario_desconhecido"
            events.append({"ts": _parse_ts(m["ts"]), "result": what, "user": m["user"], "client": m["client"],
                           "calling_station": (m["cli"] or "").strip() or None, "reason": m["why"]})
        elif m := _SECRET_RE.match(line):
            events.append({"ts": _parse_ts(m["ts"]), "result": "segredo_incorreto", "user": None, "client": m["src"],
                           "calling_station": None, "reason": "Message-Authenticator inválido (segredo do NAS diverge)"})
        elif m := _UNKNOWN_RE.match(line):
            events.append({"ts": _parse_ts(m["ts"]), "result": "nas_desconhecido", "user": None, "client": m["src"],
                           "calling_station": None, "reason": "pedido de um IP que não é cliente cadastrado"})
    return events[-limit:][::-1]


def parse_detail(text: str) -> list[dict]:
    """Registros dos arquivos `detail` (radacct): bloco com data + atributos indentados."""
    records, current = [], None
    for line in text.splitlines():
        if not line.strip():
            if current:
                records.append(current)
            current = None
            continue
        if not line[0].isspace():
            if current:
                records.append(current)
            current = {"_ts": line.strip()}
            continue
        if current is None or "=" not in line:
            continue
        key, _, value = line.strip().partition("=")
        current[key.strip()] = value.strip().strip('"')
    if current:
        records.append(current)
    return records


def sessions_from_detail(records: list[dict], limit: int = 200) -> list[dict]:
    sessions: dict[tuple, dict] = {}
    for r in records:
        sid = r.get("Acct-Session-Id")
        status = r.get("Acct-Status-Type")
        if not sid or status not in ("Start", "Stop", "Interim-Update"):
            continue
        key = (r.get("NAS-IP-Address") or r.get("Packet-Src-IP-Address"), sid)
        s = sessions.setdefault(key, {"session_id": sid, "user": r.get("User-Name"), "nas": key[0],
                                      "calling_station": r.get("Calling-Station-Id"), "start": None, "stop": None,
                                      "duration_s": None, "input_octets": None, "output_octets": None,
                                      "terminate_cause": None, "state": "aberta"})
        ts = _parse_ts(r["_ts"])
        if status == "Start":
            s["start"] = s["start"] or ts
        if status == "Stop":
            s.update(stop=ts, state="encerrada", terminate_cause=r.get("Acct-Terminate-Cause"))
        for attr, field in (("Acct-Session-Time", "duration_s"), ("Acct-Input-Octets", "input_octets"),
                            ("Acct-Output-Octets", "output_octets")):
            if r.get(attr, "").isdigit():
                s[field] = int(r[attr])
    out = sorted(sessions.values(), key=lambda s: s["start"] or s["stop"] or "", reverse=True)
    return out[:limit]


def _read_helper(kind: str) -> str:
    client = ssh_client.connect()
    try:
        rc, out, _ = ssh_client.run(client, ["sudo", "-n", config.RADIUS_READ_HELPER, kind], timeout=20)
    finally:
        client.close()
    if rc != 0:
        raise ssh_client.SshCommandError("Não foi possível ler os registros do FreeRADIUS.")
    return out


def auth_events() -> dict:
    events = parse_auth_log(_read_helper("auth"))
    totals = {}
    for e in events:
        totals[e["result"]] = totals.get(e["result"], 0) + 1
    return {"events": events, "totals": totals}


def sessions() -> dict:
    items = sessions_from_detail(parse_detail(_read_helper("acct")))
    return {"sessions": items, "open": sum(1 for s in items if s["state"] == "aberta")}
