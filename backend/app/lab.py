"""Incremento 3 - laboratório virtual sob demanda: admissão, fila e reconciliação.

Separação exigida pelo enunciado (seção 8.3):
 - console/API (este módulo + routers/lab.py): catálogo, cotas, reserva de
   capacidade, estado das instâncias, autorização e auditoria;
 - provisionador (a fila deste módulo, `Worker`): leva cada instância de um
   estado ao seguinte chamando o agente;
 - executor (labagent/, no host físico): o único que fala com o VBoxManage.

Estados: solicitada -> em_criacao -> iniciando -> disponivel -> desligada,
mais falha, em_exclusao e excluida. Toda transição é gravada em
lab_transitions e na auditoria, com o correlation_id do pedido.

Reserva: a CPU/RAM fica reservada enquanto a instância está em solicitada,
em_criacao, iniciando ou disponivel; desligar ou falhar libera a reserva (o
disco continua ocupado até a exclusão). A reserva é DERIVADA do estado — não
existe contador separado que possa ficar inconsistente — e a admissão roda em
transação BEGIN IMMEDIATE, que serializa pedidos concorrentes.

Idempotência: cada pedido traz um op_id (gerado pelo navegador). Repetir o
mesmo op_id devolve a mesma instância; no host, o agente também procura a VM
pelo op_id antes de clonar, então uma resposta perdida ou um timeout nunca
gera clone duplicado.
"""
import hashlib
import json
import re
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timezone

from . import audit, config, labvm_client as agent
from .errors import Conflict, Forbidden, InvalidInput, NotFound

RESERVING = ("solicitada", "em_criacao", "iniciando", "disponivel")
ACTIVE = ("solicitada", "em_criacao", "iniciando", "disponivel", "desligada", "falha", "em_exclusao")
GUEST_USER = "aluno"
SYSTEM = {"username": "sistema", "role": "-"}
OP_RE = re.compile(r"^[0-9a-f]{32}$")
SECRET_FIELDS = ("vrde_password", "guest_password")

# Instâncias que ainda ocupam disco no host (contam na cota e no limite de VMs).
_HOLDS_DISK = "state != 'excluida' AND NOT (state = 'falha' AND vbox_uuid IS NULL AND cleanup_pending = 0)"


# ------------------------------------------------------------------ utilitários
def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ts(dt: datetime | None = None) -> str:
    return (dt or _now()).strftime("%Y-%m-%d %H:%M:%S")


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def _elapsed(ts: str | None) -> float:
    start = _parse(ts)
    return (_now() - start).total_seconds() if start else 0.0


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=15, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


class _Txn:
    """BEGIN IMMEDIATE: um único escritor por vez, então duas admissões
    concorrentes nunca leem a mesma capacidade livre."""

    def __enter__(self) -> sqlite3.Connection:
        self.conn = _connect()
        self.conn.execute("BEGIN IMMEDIATE")
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        try:
            self.conn.execute("ROLLBACK" if exc_type else "COMMIT")
        finally:
            self.conn.close()
        return False


def _slug(username: str) -> str:
    return re.sub(r"[^a-z0-9]", "", username.lower())[:16] or "u"


_catalog_cache: dict = {}


def catalog() -> dict:
    path = config.LAB_CATALOG_PATH
    mtime = path.stat().st_mtime
    if _catalog_cache.get("mtime") != mtime:
        _catalog_cache.update(mtime=mtime, data=json.loads(path.read_text(encoding="utf-8")))
    return _catalog_cache["data"]


def _find(items: list[dict], key: str) -> dict | None:
    return next((i for i in items if i["id"] == key), None)


def public(row: sqlite3.Row | dict) -> dict:
    """Instância sem segredos (para listagens e auditoria)."""
    data = {k: row[k] for k in row.keys() if k not in SECRET_FIELDS}
    ready, requested = _parse(data.get("ready_at")), _parse(data.get("requested_at"))
    data["seconds_to_ready"] = round((ready - requested).total_seconds()) if ready and requested else None
    data["holds_reservation"] = data["state"] in RESERVING
    return data


def _get_row(conn, instance_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM lab_instances WHERE id = ?", (instance_id,)).fetchone()
    if row is None:
        raise NotFound("Instância não encontrada.")
    return row


def _usage(conn, exclude_id: int | None = None) -> dict:
    marks = ",".join("?" * len(RESERVING))
    args = [*RESERVING]
    extra = ""
    if exclude_id is not None:
        extra = " AND id != ?"
        args.append(exclude_id)
    cpus, mem, n = conn.execute(
        f"SELECT COALESCE(SUM(cpus),0), COALESCE(SUM(memory_mb),0), COUNT(*) FROM lab_instances "
        f"WHERE state IN ({marks}){extra}", args,
    ).fetchone()
    disk = conn.execute(f"SELECT COUNT(*) FROM lab_instances WHERE {_HOLDS_DISK}").fetchone()[0]
    return {"reserved_cpus": cpus, "reserved_memory_mb": mem, "reserving_instances": n, "instances_on_disk": disk}


def _check_capacity(conn, cpus: int, memory_mb: int, exclude_id: int | None = None) -> None:
    use = _usage(conn, exclude_id)
    if use["reserved_cpus"] + cpus > config.LAB_MAX_VCPUS or use["reserved_memory_mb"] + memory_mb > config.LAB_MAX_MEMORY_MB:
        raise Conflict(
            f"Capacidade insuficiente: reservados {use['reserved_cpus']}/{config.LAB_MAX_VCPUS} vCPU e "
            f"{use['reserved_memory_mb']}/{config.LAB_MAX_MEMORY_MB} MiB. Desligue ou exclua uma instância."
        )


def _transition(conn, row, to_state: str, detail: str = "", *, expect: str | None = None, **fields) -> bool:
    """Muda o estado SÓ se ele ainda for `expect` (compare-and-set) e registra a transição."""
    expect = expect or row["state"]
    sets = {"state": to_state, "state_since": _ts(), **fields}
    assign = ", ".join(f"{k} = ?" for k in sets)
    cur = conn.execute(
        f"UPDATE lab_instances SET {assign} WHERE id = ? AND state = ?", (*sets.values(), row["id"], expect)
    )
    if cur.rowcount != 1:
        return False
    conn.execute(
        "INSERT INTO lab_transitions (instance_id, ts, from_state, to_state, detail) VALUES (?,?,?,?,?)",
        (row["id"], _ts(), expect, to_state, detail[:300]),
    )
    return True


def _audit_transition(row, to_state: str, detail: str, user: dict | None = None, result: str = "ok") -> None:
    audit.record(
        user=user or SYSTEM, resource="lab", action=f"lab.{to_state}", result=result,
        detail=f"{row['name']} (#{row['id']}, dono {row['owner']}): {detail}"[:500],
        correlation_id=row["correlation_id"],
    )


# ------------------------------------------------------------------ autorização
def authorize(user: dict, row, action: str) -> None:
    """Proprietário pode tudo na sua instância; admin pode ver, desligar e excluir
    as dos outros, mas os DADOS DE ACESSO são só do proprietário."""
    if row["owner"] == user["username"]:
        return
    if user["role"] == "admin" and action in ("view", "stop", "delete"):
        return
    audit.record(user=user, resource="lab", action=f"lab.{action}", result="negado",
                 detail=f"instância #{row['id']} de outro usuário", correlation_id=row["correlation_id"])
    raise Forbidden("Esta instância pertence a outro usuário.")


def _authorized(user: dict, instance_id: int, action: str) -> None:
    """Confere o dono FORA da transação (a auditoria de recusa usa outra conexão).
    O dono de uma instância nunca muda, então a checagem continua valendo dentro dela."""
    conn = _connect()
    try:
        row = _get_row(conn, instance_id)
    finally:
        conn.close()
    authorize(user, row, action)


# ------------------------------------------------------------------ consultas
def list_instances(user: dict) -> list[dict]:
    conn = _connect()
    try:
        if user["role"] == "admin":
            rows = conn.execute("SELECT * FROM lab_instances WHERE state != 'excluida' ORDER BY id DESC").fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM lab_instances WHERE owner = ? AND state != 'excluida' ORDER BY id DESC",
                (user["username"],),
            ).fetchall()
        return [public(r) for r in rows]
    finally:
        conn.close()


def get_instance(user: dict, instance_id: int) -> dict:
    conn = _connect()
    try:
        row = _get_row(conn, instance_id)
        authorize(user, row, "view")
        transitions = conn.execute(
            "SELECT ts, from_state, to_state, detail FROM lab_transitions WHERE instance_id = ? ORDER BY id",
            (instance_id,),
        ).fetchall()
        return {**public(row), "transitions": [dict(t) for t in transitions]}
    finally:
        conn.close()


def quota_for(user: dict) -> dict:
    limit = config.LAB_QUOTA.get(user["role"], 0)
    conn = _connect()
    try:
        used = conn.execute(
            f"SELECT COUNT(*) FROM lab_instances WHERE owner = ? AND {_HOLDS_DISK}", (user["username"],)
        ).fetchone()[0]
    finally:
        conn.close()
    return {"limit": limit, "used": used}


_inventory_cache: dict = {"at": 0.0, "data": None, "error": None}


def capacity() -> dict:
    conn = _connect()
    try:
        use = _usage(conn)
        assigned = conn.execute(
            "SELECT COALESCE(SUM(measured_cpu),0), COALESCE(SUM(measured_ram_mb),0) FROM lab_instances "
            "WHERE state = 'disponivel'"
        ).fetchone()
    finally:
        conn.close()
    if time.monotonic() - _inventory_cache["at"] > 10:
        try:
            _inventory_cache.update(data=agent.inventory(), error=None)
        except agent.AgentUnavailable as exc:
            _inventory_cache.update(data=None, error=exc.message)
        except (agent.AgentTimeout, agent.AgentError) as exc:
            _inventory_cache.update(data=None, error=exc.message)
        _inventory_cache["at"] = time.monotonic()
    return {
        "limits": {
            "max_vcpus": config.LAB_MAX_VCPUS,
            "max_memory_mb": config.LAB_MAX_MEMORY_MB,
            "max_instances": config.LAB_MAX_INSTANCES,
        },
        "assigned": use,
        "measured_instances": {"cpu_percent": round(assigned[0], 1), "ram_used_mb": round(assigned[1], 1)},
        "agent": {"ok": _inventory_cache["data"] is not None, "error": _inventory_cache["error"]},
        "host": _inventory_cache["data"],
    }


# ------------------------------------------------------------------ ações do usuário
def request_instance(user: dict, image_id: str, profile_id: str, op_id: str) -> tuple[dict, bool]:
    """Admissão: valida, confere cota e capacidade e reserva em UMA transação."""
    op_id = op_id.replace("-", "").lower()
    if not OP_RE.match(op_id):
        raise InvalidInput("Identificador de operação inválido.")
    cat = catalog()
    image = _find(cat["images"], image_id)
    profile = _find(cat["profiles"], profile_id)
    if image is None or profile is None:
        raise InvalidInput("Imagem ou perfil fora do catálogo.")
    if user["role"] not in profile["roles"]:
        audit.record(user=user, resource="lab", action="lab.request", result="negado",
                     detail=f"perfil {profile_id} não autorizado para {user['role']}")
        raise Forbidden("Seu perfil de acesso não pode usar este perfil de VM.")

    cid = audit.new_correlation_id()
    try:
        with _Txn() as conn:
            existing = conn.execute("SELECT * FROM lab_instances WHERE op_id = ?", (op_id,)).fetchone()
            if existing is not None:  # pedido repetido (duplo clique, reenvio): mesma instância
                if existing["owner"] != user["username"]:
                    raise Conflict("Identificador de operação já usado.")
                return public(existing), False
            used = conn.execute(
                f"SELECT COUNT(*) FROM lab_instances WHERE owner = ? AND {_HOLDS_DISK}", (user["username"],)
            ).fetchone()[0]
            limit = config.LAB_QUOTA.get(user["role"], 0)
            if used >= limit:
                raise Conflict(f"Cota excedida: você já tem {used} de {limit} instância(s).")
            _check_capacity(conn, profile["cpus"], profile["memory_mb"])
            if _usage(conn)["instances_on_disk"] >= config.LAB_MAX_INSTANCES:
                raise Conflict(f"Limite de {config.LAB_MAX_INSTANCES} VMs do laboratório atingido (disco).")
            taken = {r[0] for r in conn.execute(
                "SELECT vrde_port FROM lab_instances WHERE state != 'excluida' AND vrde_port IS NOT NULL")}
            port = next((p for p in range(config.LAB_VRDE_PORTS[0], config.LAB_VRDE_PORTS[1] + 1) if p not in taken), None)
            if port is None:
                raise Conflict("Nenhuma porta de acesso remoto livre.")
            slug = _slug(user["username"])
            name = f"LAB-{slug}-{op_id[:8]}"
            now = _ts()
            cur = conn.execute(
                "INSERT INTO lab_instances (op_id, correlation_id, owner, owner_role, name, image_id, image_version, "
                "profile_id, cpus, memory_mb, state, vrde_port, vrde_user, vrde_password, guest_password, "
                "requested_at, state_since) VALUES (?,?,?,?,?,?,?,?,?,?, 'solicitada', ?,?,?,?,?,?)",
                (op_id, cid, user["username"], user["role"], name, image["id"], image["version"], profile["id"],
                 profile["cpus"], profile["memory_mb"], port, slug,
                 secrets.token_urlsafe(12), secrets.token_urlsafe(12), now, now),
            )
            conn.execute(
                "INSERT INTO lab_transitions (instance_id, ts, from_state, to_state, detail) VALUES (?,?,?,?,?)",
                (cur.lastrowid, now, None, "solicitada",
                 f"{image['id']} v{image['version']}, perfil {profile['id']} ({profile['cpus']} vCPU/{profile['memory_mb']} MiB)"),
            )
            row = _get_row(conn, cur.lastrowid)
    except Conflict as exc:
        audit.record(user=user, resource="lab", action="lab.request", result="negado", detail=exc.message,
                     correlation_id=cid)
        raise
    except sqlite3.IntegrityError:
        # mesmo op_id chegou em paralelo: a outra requisição já criou a instância
        conn = _connect()
        try:
            row = conn.execute("SELECT * FROM lab_instances WHERE op_id = ?", (op_id,)).fetchone()
        finally:
            conn.close()
        if row is None or row["owner"] != user["username"]:
            raise Conflict("Pedido concorrente; tente novamente.")
        return public(row), False
    audit.record(user=user, resource="lab", action="lab.request",
                 detail=f"{name}: {image['id']} v{image['version']}, perfil {profile['id']}", correlation_id=cid)
    wake()
    return public(row), True


def request_start(user: dict, instance_id: int) -> dict:
    _authorized(user, instance_id, "start")
    with _Txn() as conn:
        row = _get_row(conn, instance_id)
        if row["state"] not in ("desligada", "falha") or not row["vbox_uuid"] or row["cleanup_pending"]:
            raise Conflict("Só é possível iniciar uma instância desligada (ou em falha com a VM preservada).")
        _check_capacity(conn, row["cpus"], row["memory_mb"], exclude_id=row["id"])
        _transition(conn, row, "iniciando", "início pedido pelo usuário", pending_action=None, error=None)
    _audit_transition(row, "iniciando", "início pedido", user)
    wake()
    return get_instance(user, instance_id)


def request_stop(user: dict, instance_id: int) -> dict:
    _authorized(user, instance_id, "stop")
    with _Txn() as conn:
        row = _get_row(conn, instance_id)
        if row["state"] != "disponivel":
            raise Conflict("Só é possível desligar uma instância disponível.")
        if not row["pending_action"]:
            conn.execute("UPDATE lab_instances SET pending_action = 'desligar', action_since = ? WHERE id = ?",
                         (_ts(), row["id"]))
    audit.record(user=user, resource="lab", action="lab.stop", detail=f"{row['name']} (#{row['id']})",
                 correlation_id=row["correlation_id"])
    wake()
    return get_instance(user, instance_id)


def request_delete(user: dict, instance_id: int) -> dict:
    _authorized(user, instance_id, "delete")
    with _Txn() as conn:
        row = _get_row(conn, instance_id)
        if row["state"] in ("excluida", "em_exclusao"):
            raise Conflict("A instância já está sendo excluída.")
        nothing_on_host = row["state"] == "falha" and not row["vbox_uuid"] and not row["cleanup_pending"]
        if nothing_on_host:
            _transition(conn, row, "excluida", "registro removido (nada no host)", deleted_at=_ts(),
                        vrde_password=None, guest_password=None, vrde_port=None)
        else:
            _transition(conn, row, "em_exclusao", f"exclusão pedida por {user['username']}",
                        pending_action=None)
    _audit_transition(row, "excluida" if nothing_on_host else "em_exclusao", "exclusão pedida", user)
    wake()
    return get_instance(user, instance_id)


def access(user: dict, instance_id: int) -> dict:
    """Dados de conexão: só do proprietário e só depois da prontidão confirmada."""
    conn = _connect()
    try:
        row = _get_row(conn, instance_id)
    finally:
        conn.close()
    if row["owner"] != user["username"]:
        audit.record(user=user, resource="lab", action="lab.access", result="negado",
                     detail=f"dados de acesso da instância #{row['id']} de outro usuário",
                     correlation_id=row["correlation_id"])
        raise Forbidden("Somente o proprietário vê os dados de acesso.")
    if row["state"] != "disponivel":
        raise Conflict("O desktop ainda não está disponível.")
    audit.record(user=user, resource="lab", action="lab.access", detail=f"{row['name']} (#{row['id']})",
                 correlation_id=row["correlation_id"])
    target = f"{config.LAB_VRDE_HOST}:{row['vrde_port']}"
    return {
        "protocol": "RDP (VRDE do VirtualBox)",
        "host": config.LAB_VRDE_HOST,
        "port": row["vrde_port"],
        "rdp_user": row["vrde_user"],
        "rdp_password": row["vrde_password"],
        "guest_user": GUEST_USER,
        "guest_password": row["guest_password"],
        "commands": {
            "remmina": f"remmina -c rdp://{row['vrde_user']}@{target}",
            "xfreerdp": f"xfreerdp /v:{target} /u:{row['vrde_user']} /cert:ignore",
        },
    }


# ------------------------------------------------------------------ provisionador
class _Step:
    """Uma passada do provisionador sobre UMA instância. Todo efeito no host
    passa pelo agente; todo efeito no banco é compare-and-set."""

    def __init__(self, row):
        self.row = row

    # -- banco
    def move(self, to_state: str, detail: str, **fields) -> bool:
        conn = _connect()
        try:
            ok = _transition(conn, self.row, to_state, detail, **fields)
        finally:
            conn.close()
        if ok:
            _audit_transition(self.row, to_state, detail, result="falha" if to_state == "falha" else "ok")
            self.refresh()
        return ok

    def update(self, **fields) -> None:
        assign = ", ".join(f"{k} = ?" for k in fields)
        conn = _connect()
        try:
            conn.execute(f"UPDATE lab_instances SET {assign} WHERE id = ? AND state = ?",
                         (*fields.values(), self.row["id"], self.row["state"]))
        finally:
            conn.close()
        self.refresh()

    def note(self, message: str) -> None:
        """Erro transitório (agente fora, timeout): fica visível, a tarefa continua depois."""
        if self.row["error"] != message:
            conn = _connect()
            try:
                conn.execute("UPDATE lab_instances SET error = ? WHERE id = ?", (message, self.row["id"]))
                conn.execute(
                    "INSERT INTO lab_transitions (instance_id, ts, from_state, to_state, detail) VALUES (?,?,?,?,?)",
                    (self.row["id"], _ts(), self.row["state"], self.row["state"], message[:300]),
                )
            finally:
                conn.close()
            self.refresh()

    def refresh(self) -> None:
        conn = _connect()
        try:
            self.row = _get_row(conn, self.row["id"])
        finally:
            conn.close()

    def fail(self, message: str, *, cleanup: bool) -> None:
        if self.move("falha", message, error=message, pending_action=None, cleanup_pending=1 if cleanup else 0):
            if cleanup:
                self.on_falha()

    # -- estados
    def run(self) -> None:
        handler = getattr(self, f"on_{self.row['state']}", None)
        if handler is None:
            return
        try:
            handler()
        except agent.AgentUnavailable:
            self.note("Agente do laboratório indisponível; a tarefa continua quando ele voltar.")
        except agent.AgentTimeout:
            self.note("O agente não confirmou a última operação a tempo; o host será conferido antes de repetir.")

    def on_solicitada(self) -> None:
        if self.move("em_criacao", "provisionamento iniciado"):
            self.on_em_criacao()

    def on_em_criacao(self) -> None:
        r = self.row
        if not r["vbox_uuid"]:
            vm = agent.find_by_op(r["op_id"])  # conferir o host ANTES de (re)clonar
            if vm is None:
                try:
                    vm = agent.clone(r["op_id"], r["name"], r["image_id"], r["vrde_user"])
                except agent.AgentError as exc:
                    return self.fail(f"Clonagem recusada pelo agente: {exc.message}", cleanup=True)
            self.update(vbox_uuid=vm["uuid"], mac=(vm.get("macs") or [None])[0], error=None)
            r = self.row
        try:
            agent.configure(r["vbox_uuid"], {
                "op_id": r["op_id"],
                "cpus": r["cpus"],
                "memory_mb": r["memory_mb"],
                "vrde_port": r["vrde_port"],
                "vrde_user": r["vrde_user"],
                "vrde_password_sha256": hashlib.sha256(r["vrde_password"].encode()).hexdigest(),
                "hostname": r["name"].lower(),
                "guest_password": r["guest_password"],
            })
        except agent.AgentError as exc:
            return self.fail(f"Configuração recusada pelo agente: {exc.message}", cleanup=True)
        self.move("iniciando", f"clone {r['vbox_uuid']} configurado ({r['cpus']} vCPU/{r['memory_mb']} MiB, "
                               f"porta {r['vrde_port']}, MAC {r['mac']})", error=None, pending_action=None)

    def on_iniciando(self) -> None:
        r = self.row
        first_boot = r["ready_at"] is None
        st = agent.status(r["vbox_uuid"])
        if st is None:
            self.update(vbox_uuid=None)
            return self.fail("A VM não existe mais no host.", cleanup=False)
        if st["state"] not in ("running", "starting"):
            if r["pending_action"] == "iniciar_enviado":
                return self.fail(f"A VM parou durante a inicialização (estado {st['state']}).", cleanup=first_boot)
            try:
                agent.start(r["vbox_uuid"], r["op_id"])
            except agent.AgentError as exc:
                return self.fail(f"Início recusado pelo agente: {exc.message}", cleanup=first_boot)
            self.update(pending_action="iniciar_enviado", action_since=_ts(), error=None)
            return
        if st.get("guest_ready") and st.get("vrde_listening"):
            ip = st.get("ip") or _ip_from_dhcp(r["mac"])
            secs = round(_elapsed(r["state_since"]))
            fields = {"pending_action": None, "error": None, "ip": ip, "mac": (st.get("macs") or [r["mac"]])[0]}
            if first_boot:
                fields["ready_at"] = _ts()
            self.move("disponivel", f"desktop pronto em {secs}s (convidado personalizado, RDP respondendo, IP {ip or '?'})",
                      **fields)
            return
        if _elapsed(r["state_since"]) > config.LAB_READY_TIMEOUT:
            if first_boot:
                return self.fail(f"Desktop não ficou pronto em {config.LAB_READY_TIMEOUT}s.", cleanup=True)
            try:
                agent.stop(r["vbox_uuid"], r["op_id"], force=True)
            except (agent.AgentError, agent.AgentUnavailable, agent.AgentTimeout):
                pass  # o estado real será reconciliado; a reserva é liberada de qualquer forma
            self.fail(f"Desktop não ficou pronto em {config.LAB_READY_TIMEOUT}s; VM desligada, disco preservado.",
                      cleanup=False)

    def on_disponivel(self) -> None:
        r = self.row
        st = agent.status(r["vbox_uuid"])
        if st is None:
            self.update(vbox_uuid=None)
            return self.fail("A VM desapareceu do host.", cleanup=False)
        measured = st.get("measured") or {}
        self.update(measured_cpu=measured.get("cpu_percent"), measured_ram_mb=measured.get("ram_used_mb"),
                    measured_at=_ts())
        if st["state"] not in ("running", "starting", "stopping"):
            by_console = (self.row["pending_action"] or "").startswith("desligar")
            self.move("desligada", "desligada pelo console" if by_console else f"desligada fora do console ({st['state']})",
                      pending_action=None, action_since=None, measured_cpu=None, measured_ram_mb=None)
            return
        action = self.row["pending_action"]
        if action == "desligar":
            agent.stop(r["vbox_uuid"], r["op_id"])
            self.update(pending_action="desligar_enviado", action_since=_ts())
        elif action == "desligar_enviado" and _elapsed(self.row["action_since"]) > config.LAB_STOP_TIMEOUT:
            agent.stop(r["vbox_uuid"], r["op_id"], force=True)
            self.note(f"O convidado não atendeu ao ACPI em {config.LAB_STOP_TIMEOUT}s; desligamento forçado.")

    def on_falha(self) -> None:
        r = self.row
        if not r["cleanup_pending"]:
            return
        uuid = r["vbox_uuid"]
        if not uuid:
            vm = agent.find_by_op(r["op_id"])  # timeout de clonagem: a VM pode existir sem uuid no banco
            uuid = vm["uuid"] if vm else None
        if uuid:
            try:
                agent.delete(uuid, r["op_id"])  # o agente confere que a VM é desta operação
            except agent.AgentError as exc:
                if exc.http_status != 404:
                    return self.note(f"Limpeza do clone parcial falhou: {exc.message}")
        self.update(cleanup_pending=0, vbox_uuid=None, mac=None)
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO lab_transitions (instance_id, ts, from_state, to_state, detail) VALUES (?,?,?,?,?)",
                (r["id"], _ts(), "falha", "falha", "recursos parciais removidos; reserva liberada"),
            )
        finally:
            conn.close()
        _audit_transition(r, "falha", "recursos parciais removidos; reserva liberada")

    def on_em_exclusao(self) -> None:
        r = self.row
        uuid = r["vbox_uuid"]
        if not uuid:
            vm = agent.find_by_op(r["op_id"])
            uuid = vm["uuid"] if vm else None
        if uuid:
            try:
                agent.delete(uuid, r["op_id"])
            except agent.AgentError as exc:
                if exc.http_status != 404:
                    return self.note(f"Exclusão recusada pelo agente: {exc.message}")
        self.move("excluida", "VM e disco removidos; base e outras VMs preservadas", deleted_at=_ts(),
                  vbox_uuid=None, vrde_password=None, guest_password=None, vrde_port=None,
                  pending_action=None, measured_cpu=None, measured_ram_mb=None)


def _ip_from_dhcp(mac: str | None) -> str | None:
    """Plano B para o endereço: a concessão do Kea (incremento 2) pelo MAC do clone."""
    if not mac:
        return None
    try:
        from . import dhcp_client
        for lease in dhcp_client.read_leases()["leases"]:
            if (lease.get("hwaddr") or "").lower() == mac.lower():
                return lease.get("address")
    except Exception:  # noqa: BLE001 - endereço é informativo; nunca derruba a prontidão
        return None
    return None


def process_once() -> int:
    """Uma volta da fila: avança cada instância que tem trabalho. Devolve quantas foram vistas."""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM lab_instances WHERE state IN ('solicitada','em_criacao','iniciando','disponivel','em_exclusao') "
            "OR (state = 'falha' AND cleanup_pending = 1) ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    for row in rows:
        try:
            _Step(row).run()
        except Exception as exc:  # noqa: BLE001 - uma instância com erro não pode parar a fila
            _Step(row).note(f"Erro inesperado no provisionador: {type(exc).__name__}")
    return len(rows)


# ------------------------------------------------------------------ thread da fila
_wake = threading.Event()
_stop = threading.Event()
_thread: threading.Thread | None = None


def wake() -> None:
    _wake.set()


def _loop() -> None:
    while not _stop.is_set():
        try:
            process_once()  # ao reiniciar o backend, a 1ª volta já reconcilia banco e host
        except Exception:  # noqa: BLE001
            pass
        _wake.wait(config.LAB_POLL_INTERVAL)
        _wake.clear()


def start_worker() -> None:
    global _thread
    if _thread is None or not _thread.is_alive():
        _stop.clear()
        _thread = threading.Thread(target=_loop, name="lab-worker", daemon=True)
        _thread.start()


def stop_worker() -> None:
    _stop.set()
    _wake.set()
