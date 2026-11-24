import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { canAtLeast, useAuth } from "../auth.jsx";
import { ErrorBox, fmtDate } from "../components.jsx";
import { useNotify } from "../notify.jsx";

const STATE_LABEL = {
  solicitada: "Solicitada",
  em_criacao: "Em criação",
  iniciando: "Iniciando",
  disponivel: "Disponível",
  desligada: "Desligada",
  falha: "Falha",
  em_exclusao: "Em exclusão",
  excluida: "Excluída",
};
const TRANSIENT = new Set([
  "solicitada",
  "em_criacao",
  "iniciando",
  "em_exclusao",
]);

// O console roda em http:// na rede interna: crypto.randomUUID() só existe em
// contexto seguro, então o identificador da operação vem de getRandomValues().
function newOpId() {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

// O backend grava UTC como "AAAA-MM-DD HH:MM:SS".
const utc = (ts) => (ts ? fmtDate(ts.replace(" ", "T") + "Z") : "—");
const mib = (n) =>
  n == null
    ? "—"
    : n >= 1024
      ? `${(n / 1024).toFixed(1)} GiB`
      : `${Math.round(n)} MiB`;

function Meter({ label, used, total, unit }) {
  const pct = total ? Math.min(100, Math.round((used / total) * 100)) : 0;
  return (
    <div className="meter">
      <div className="meter-head">
        <span>{label}</span>
        <span className="muted">
          {used} / {total} {unit}
        </span>
      </div>
      <div className="meter-bar">
        <div
          className={`meter-fill ${pct >= 100 ? "full" : ""}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

function Secret({ value }) {
  const [shown, setShown] = useState(false);
  return (
    <span className="secret">
      <code>{shown ? value : "••••••••••"}</code>
      <button
        type="button"
        className="secondary small"
        onClick={() => setShown(!shown)}
      >
        {shown ? "Ocultar" : "Mostrar"}
      </button>
    </span>
  );
}

export default function Lab() {
  const { user, expire } = useAuth();
  const { run } = useNotify();
  const canRequest = canAtLeast(user, "operador");
  const isAdmin = canAtLeast(user, "admin");
  const [catalog, setCatalog] = useState(null);
  const [cap, setCap] = useState(null);
  const [instances, setInstances] = useState([]);
  const [error, setError] = useState(null);
  const [form, setForm] = useState({ image_id: "", profile_id: "" });
  const [formError, setFormError] = useState("");
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(null); // detalhes da instância aberta
  const [access, setAccess] = useState({}); // id -> dados de acesso
  const opId = useRef(newOpId());

  const load = useCallback(async () => {
    try {
      const calls = [api("/lab/catalog"), api("/lab/capacity")];
      if (canRequest) calls.push(api("/lab/instances"));
      const [c, k, list] = await Promise.all(calls);
      setCatalog(c);
      setCap(k);
      setInstances(list || []);
      setForm((f) => ({
        image_id: f.image_id || c.images[0]?.id || "",
        profile_id: f.profile_id || c.profiles.find((p) => p.allowed)?.id || "",
      }));
      setError(null);
    } catch (e) {
      if (e.status === 401) expire();
      else setError(e);
    }
  }, [canRequest, expire]);

  useEffect(() => {
    load();
  }, [load]);

  // Acompanha a tarefa enquanto houver instância em transição.
  const busyStates = instances.some(
    (i) => TRANSIENT.has(i.state) || i.pending_action,
  );
  useEffect(() => {
    const t = setInterval(load, busyStates ? 4000 : 15000);
    return () => clearInterval(t);
  }, [busyStates, load]);

  async function loadDetail(id) {
    if (open?.id === id) return setOpen(null);
    try {
      setOpen(await api(`/lab/instances/${id}`));
    } catch (e) {
      setFormError(e.message);
    }
  }

  async function request(e) {
    e.preventDefault();
    setFormError("");
    setBusy(true);
    const body = { ...form, op_id: opId.current };
    try {
      const r = await run("Laboratório", "Solicitar desktop virtual", () =>
        api("/lab/instances", { method: "POST", body }),
      );
      if (!r.created)
        setFormError(
          "Este pedido já tinha sido registrado; nenhuma VM nova foi criada.",
        );
      opId.current = newOpId();
      load();
    } catch (err) {
      // Sem resposta do Debian: mantém o mesmo op_id para que reenviar não duplique a VM.
      if (err.status !== 0) opId.current = newOpId();
      setFormError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function action(inst, verb, label) {
    setBusy(true);
    setFormError("");
    try {
      await run("Laboratório", `${label} ${inst.name}`, () =>
        api(`/lab/instances/${inst.id}/${verb}`, { method: "POST" }),
      );
      setAccess((a) => ({ ...a, [inst.id]: undefined }));
      load();
      if (open?.id === inst.id) setOpen(await api(`/lab/instances/${inst.id}`));
    } catch (err) {
      setFormError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function showAccess(inst) {
    if (access[inst.id])
      return setAccess((a) => ({ ...a, [inst.id]: undefined }));
    try {
      const data = await api(`/lab/instances/${inst.id}/access`);
      setAccess((a) => ({ ...a, [inst.id]: data }));
    } catch (err) {
      setFormError(err.message);
    }
  }

  const host = cap?.host?.host;
  const quota = catalog?.quota;

  return (
    <div className="dash">
      <div className="dash-head">
        <h2>Laboratório virtual</h2>
        <button onClick={load}>Atualizar</button>
      </div>
      <ErrorBox error={error} />

      {cap && (
        <section className="card">
          <h3>Capacidade</h3>
          {!cap.agent.ok && (
            <div className="alert warn">
              Agente do host indisponível: {cap.agent.error || "sem resposta"}.
              Pedidos ficam na fila e continuam quando ele voltar.
            </div>
          )}
          <div className="grid lab-grid">
            <div>
              <div className="stat-title">Reservado (atribuído)</div>
              <Meter
                label="vCPU"
                used={cap.assigned.reserved_cpus}
                total={cap.limits.max_vcpus}
                unit="vCPU"
              />
              <Meter
                label="Memória"
                used={cap.assigned.reserved_memory_mb}
                total={cap.limits.max_memory_mb}
                unit="MiB"
              />
              <Meter
                label="VMs no disco"
                used={cap.assigned.instances_on_disk}
                total={cap.limits.max_instances}
                unit="VMs"
              />
            </div>
            <div>
              <div className="stat-title">Consumo medido das instâncias</div>
              <dl className="kv">
                <dt>CPU (processos das VMs)</dt>
                <dd>{cap.measured_instances.cpu_percent}%</dd>
                <dt>Memória em uso</dt>
                <dd>{mib(cap.measured_instances.ram_used_mb)}</dd>
              </dl>
            </div>
            <div>
              <div className="stat-title">Host físico (medido)</div>
              {host ? (
                <dl className="kv">
                  <dt>CPU</dt>
                  <dd>
                    {host.cpu_percent ?? "—"}% · {host.cpu_physical ?? "?"}{" "}
                    núcleos / {host.cpu_logical ?? "?"} threads
                  </dd>
                  <dt>RAM livre</dt>
                  <dd>
                    {mib(host.ram_available_mb)} de {mib(host.ram_total_mb)}
                  </dd>
                  <dt>Disco livre</dt>
                  <dd>{host.disk_free_gb ?? "—"} GB</dd>
                  <dt>VirtualBox</dt>
                  <dd>
                    {cap.host.vbox_version} · Extension Pack{" "}
                    {cap.host.extension_pack ? "ok" : "ausente"}
                  </dd>
                </dl>
              ) : (
                <p className="muted">Sem dados do host.</p>
              )}
            </div>
          </div>
        </section>
      )}

      {catalog && (
        <section className="card">
          <h3>Solicitar desktop</h3>
          {canRequest ? (
            <>
              <form onSubmit={request} className="form row-form">
                <label>
                  Imagem
                  <select
                    value={form.image_id}
                    onChange={(e) =>
                      setForm({ ...form, image_id: e.target.value })
                    }
                  >
                    {catalog.images.map((i) => (
                      <option key={i.id} value={i.id}>
                        {i.name} — v{i.version}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Perfil
                  <select
                    value={form.profile_id}
                    onChange={(e) =>
                      setForm({ ...form, profile_id: e.target.value })
                    }
                  >
                    {catalog.profiles.map((p) => (
                      <option key={p.id} value={p.id} disabled={!p.allowed}>
                        {p.name}
                        {p.allowed ? "" : " (sem permissão)"}
                      </option>
                    ))}
                  </select>
                </label>
                <button
                  type="submit"
                  disabled={
                    busy ||
                    !form.image_id ||
                    !form.profile_id ||
                    quota?.used >= quota?.limit
                  }
                >
                  Solicitar
                </button>
              </form>
              <p className="muted small">
                Sua cota: {quota?.used ?? 0} de {quota?.limit ?? 0}{" "}
                instância(s).{" "}
                {
                  catalog.images.find((i) => i.id === form.image_id)
                    ?.description
                }
              </p>
              {formError && <div className="alert error">{formError}</div>}
            </>
          ) : (
            <div className="alert warn">
              Leitores só consultam catálogo e capacidade. Pedir desktops exige
              perfil operador.
            </div>
          )}
        </section>
      )}

      {canRequest && (
        <section className="card">
          <h3>{isAdmin ? "Instâncias do laboratório" : "Minhas instâncias"}</h3>
          <table>
            <thead>
              <tr>
                <th>Instância</th>
                {isAdmin && <th>Dono</th>}
                <th>Perfil (atribuído)</th>
                <th>Medido</th>
                <th>Estado</th>
                <th>Endereço</th>
                <th>Pronta em</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {instances.map((i) => {
                const mine = i.owner === user.username;
                return (
                  <tr key={i.id}>
                    <td>
                      <button
                        type="button"
                        className="linklike"
                        onClick={() => loadDetail(i.id)}
                      >
                        {i.name}
                      </button>
                      <div className="muted small">
                        {i.image_id} v{i.image_version}
                      </div>
                    </td>
                    {isAdmin && <td>{i.owner}</td>}
                    <td>
                      {i.cpus} vCPU · {mib(i.memory_mb)}
                      <div className="muted small">
                        {i.holds_reservation ? "reserva ativa" : "sem reserva"}
                      </div>
                    </td>
                    <td className="small">
                      {i.state === "disponivel"
                        ? `${i.measured_cpu ?? "—"}% · ${mib(i.measured_ram_mb)}`
                        : "—"}
                    </td>
                    <td>
                      <span className={`pill lab-${i.state}`}>
                        {STATE_LABEL[i.state] || i.state}
                      </span>
                      {i.pending_action?.startsWith("desligar") && (
                        <div className="muted small">desligando…</div>
                      )}
                      {i.error && (
                        <div className="small lab-error">{i.error}</div>
                      )}
                    </td>
                    <td className="small">
                      {i.ip || "—"}
                      {i.mac && <div className="muted">{i.mac}</div>}
                    </td>
                    <td className="small">
                      {i.seconds_to_ready != null
                        ? `${i.seconds_to_ready}s`
                        : "—"}
                    </td>
                    <td>
                      <div className="lab-actions">
                        {mine && i.state === "disponivel" && (
                          <button
                            className="small"
                            onClick={() => showAccess(i)}
                          >
                            {access[i.id] ? "Fechar acesso" : "Acessar"}
                          </button>
                        )}
                        {(mine || isAdmin) &&
                          i.state === "disponivel" &&
                          !i.pending_action && (
                            <button
                              className="secondary small"
                              disabled={busy}
                              onClick={() => action(i, "stop", "Desligar")}
                            >
                              Desligar
                            </button>
                          )}
                        {mine &&
                          ["desligada", "falha"].includes(i.state) &&
                          i.vbox_uuid &&
                          !i.cleanup_pending && (
                            <button
                              className="small"
                              disabled={busy}
                              onClick={() => action(i, "start", "Iniciar")}
                            >
                              Iniciar
                            </button>
                          )}
                        {(mine || isAdmin) && i.state !== "em_exclusao" && (
                          <button
                            className="secondary small danger"
                            disabled={busy}
                            onClick={() => {
                              if (
                                window.confirm(
                                  `Excluir ${i.name}? A VM e o disco dela serão removidos.`,
                                )
                              )
                                action(i, "delete", "Excluir");
                            }}
                          >
                            Excluir
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
              {instances.length === 0 && (
                <tr>
                  <td colSpan={isAdmin ? 8 : 7} className="muted">
                    Nenhuma instância.
                  </td>
                </tr>
              )}
            </tbody>
          </table>

          {instances
            .filter((i) => access[i.id])
            .map((i) => {
              const a = access[i.id];
              return (
                <div key={i.id} className="alert ok lab-access">
                  <strong>Acesso a {i.name}</strong> — {a.protocol}
                  <dl className="kv">
                    <dt>Servidor</dt>
                    <dd>
                      <code>
                        {a.host}:{a.port}
                      </code>
                    </dd>
                    <dt>Usuário RDP</dt>
                    <dd>
                      <code>{a.rdp_user}</code>
                    </dd>
                    <dt>Senha RDP</dt>
                    <dd>
                      <Secret value={a.rdp_password} />
                    </dd>
                    <dt>Login no desktop</dt>
                    <dd>
                      <code>{a.guest_user}</code> /{" "}
                      <Secret value={a.guest_password} />
                    </dd>
                    <dt>No Ubuntu</dt>
                    <dd>
                      <code>{a.commands.remmina}</code>
                      <br />
                      <code>{a.commands.xfreerdp}</code>
                    </dd>
                  </dl>
                </div>
              );
            })}

          {open && (
            <div className="lab-detail">
              <h3>
                Tarefa de {open.name}{" "}
                <span className="muted small">
                  · correlação {open.correlation_id}
                </span>
              </h3>
              <dl className="kv">
                <dt>UUID no VirtualBox</dt>
                <dd>
                  <code>{open.vbox_uuid || "—"}</code>
                </dd>
                <dt>Pedido em</dt>
                <dd>{utc(open.requested_at)}</dd>
                <dt>Pronta em</dt>
                <dd>{utc(open.ready_at)}</dd>
                <dt>Porta RDP</dt>
                <dd>{open.vrde_port ?? "—"}</dd>
              </dl>
              <table>
                <thead>
                  <tr>
                    <th>Quando</th>
                    <th>De</th>
                    <th>Para</th>
                    <th>Detalhe</th>
                  </tr>
                </thead>
                <tbody>
                  {open.transitions.map((t, n) => (
                    <tr key={n}>
                      <td className="small">{utc(t.ts)}</td>
                      <td className="small">
                        {STATE_LABEL[t.from_state] || "—"}
                      </td>
                      <td className="small">{STATE_LABEL[t.to_state]}</td>
                      <td className="small">{t.detail}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}
    </div>
  );
}
