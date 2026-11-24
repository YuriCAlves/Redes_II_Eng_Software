import { useCallback, useEffect, useState } from "react";
import { api } from "../api.js";
import { canAtLeast, useAuth } from "../auth.jsx";
import { ErrorBox, fmtDate } from "../components.jsx";
import { useNotify } from "../notify.jsx";

const RESULT_LABEL = {
  aceita: "Aceita",
  negada: "Negada",
  usuario_desconhecido: "Usuário desconhecido",
  segredo_incorreto: "Segredo do NAS incorreto",
  nas_desconhecido: "NAS não cadastrado",
};
const RESULT_CLASS = { aceita: "ativo", negada: "falha", usuario_desconhecido: "falha", segredo_incorreto: "inativo", nas_desconhecido: "inativo" };
const fmtSecs = (s) => (s == null ? "—" : s >= 3600 ? `${(s / 3600).toFixed(1)} h` : s >= 60 ? `${Math.round(s / 60)} min` : `${s}s`);
const fmtBytes = (n) => (n == null ? "—" : n > 1e6 ? `${(n / 1e6).toFixed(1)} MB` : n > 1e3 ? `${(n / 1e3).toFixed(1)} kB` : `${n} B`);

export default function Radius() {
  const { user, expire } = useAuth();
  const { run } = useNotify();
  const isAdmin = canAtLeast(user, "admin");
  const canTest = canAtLeast(user, "operador");
  const [cfg, setCfg] = useState(null);
  const [events, setEvents] = useState(null);
  const [sessions, setSessions] = useState(null);
  const [error, setError] = useState(null);
  const [msg, setMsg] = useState({ kind: "", text: "" });
  const [busy, setBusy] = useState(false);
  const [client, setClient] = useState({ name: "", ipaddr: "", description: "", require_ma: false });
  const [policy, setPolicy] = useState({ name: "", session_timeout: 3600, idle_timeout: 600, blocked: false, reply_message: "" });
  const [newUser, setNewUser] = useState({ username: "", password: "", policy: "padrao", scheme: "ssha2-256" });
  const [reset, setReset] = useState({ username: "", password: "" });
  const [test, setTest] = useState({ username: "", password: "", result: null });
  const [load, setLoad] = useState({ count: 1000, scheme: "ssha2-256", rounds: 5000, password: "" });
  const [secret, setSecret] = useState(null);
  const [showAll, setShowAll] = useState(false);

  const loadAll = useCallback(async () => {
    try {
      const [c, e, s] = await Promise.all([api("/radius/config"), api("/radius/events"), api("/radius/sessions")]);
      setCfg(c);
      setEvents(e);
      setSessions(s);
      setError(null);
    } catch (err) {
      if (err.status === 401) expire();
      else setError(err);
    }
  }, [expire]);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  async function act(label, path, body, after) {
    setBusy(true);
    setMsg({ kind: "", text: "" });
    try {
      const out = await run("RADIUS", label, () => api(path, { method: "POST", body }));
      if (out && out.clients) setCfg((c) => ({ ...c, ...out }));
      after?.(out);
      loadAll();
      return out;
    } catch (err) {
      setMsg({ kind: "error", text: err.message });
    } finally {
      setBusy(false);
    }
  }

  async function showSecret(name) {
    if (secret?.name === name) return setSecret(null);
    try {
      setSecret(await api(`/radius/client/${name}/secret`));
    } catch (err) {
      setMsg({ kind: "error", text: err.message });
    }
  }

  async function runTest(e) {
    e.preventDefault();
    setBusy(true);
    try {
      const r = await api("/radius/test", { method: "POST", body: { username: test.username, password: test.password } });
      setTest({ ...test, password: "", result: r.result });
      loadAll();
    } catch (err) {
      setTest({ ...test, result: null });
      setMsg({ kind: "error", text: err.message });
    } finally {
      setBusy(false);
    }
  }

  const policies = cfg?.policies ?? [];

  return (
    <div className="dash">
      <div className="dash-head">
        <h2>RADIUS (FreeRADIUS) — autenticação, autorização e contabilização</h2>
        <button onClick={loadAll}>Atualizar</button>
      </div>
      <ErrorBox error={error} />
      {msg.text && <div className={`alert ${msg.kind || "ok"}`}>{msg.text}</div>}
      {!isAdmin && <div className="alert warn">Somente administradores alteram clientes, políticas e usuários RADIUS.</div>}

      {events && (
        <section className="card">
          <h3>Autenticações recentes</h3>
          <div className="row small">
            {Object.entries(events.totals).map(([k, v]) => (
              <span key={k} className={`pill ${RESULT_CLASS[k] || "desconhecido"}`}>
                {RESULT_LABEL[k] || k}: {v}
              </span>
            ))}
            {Object.keys(events.totals).length === 0 && <span className="muted">Nenhuma autenticação registrada ainda.</span>}
          </div>
          <table>
            <thead>
              <tr>
                <th>Quando</th>
                <th>Resultado</th>
                <th>Usuário</th>
                <th>Cliente (NAS)</th>
                <th>Estação</th>
                <th>Motivo</th>
              </tr>
            </thead>
            <tbody>
              {events.events.slice(0, showAll ? 300 : 12).map((ev, i) => (
                <tr key={i}>
                  <td className="small">{fmtDate(ev.ts)}</td>
                  <td>
                    <span className={`pill ${RESULT_CLASS[ev.result] || "desconhecido"}`}>{RESULT_LABEL[ev.result] || ev.result}</span>
                  </td>
                  <td>{ev.user || "—"}</td>
                  <td>{ev.client}</td>
                  <td className="small">{ev.calling_station || "—"}</td>
                  <td className="small muted">{ev.reason || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {events.events.length > 12 && (
            <button type="button" className="linklike small" onClick={() => setShowAll(!showAll)}>
              {showAll ? "Mostrar só as 12 mais recentes" : `Mostrar todas (${events.events.length})`}
            </button>
          )}
          {canTest && (
            <form onSubmit={runTest} className="form row-form">
              <label>
                Testar usuário
                <input value={test.username} onChange={(e) => setTest({ ...test, username: e.target.value })} />
              </label>
              <label>
                Senha
                <input type="password" value={test.password} onChange={(e) => setTest({ ...test, password: e.target.value })} />
              </label>
              <button type="submit" disabled={busy || !test.username || !test.password}>
                Enviar Access-Request
              </button>
              {test.result && (
                <span className={`pill ${test.result === "aceita" ? "ativo" : "falha"}`}>Resultado: {test.result}</span>
              )}
            </form>
          )}
        </section>
      )}

      {sessions && (
        <section className="card">
          <h3>Sessões contabilizadas ({sessions.open} abertas)</h3>
          <table>
            <thead>
              <tr>
                <th>Usuário</th>
                <th>NAS</th>
                <th>Estação</th>
                <th>Início</th>
                <th>Fim</th>
                <th>Duração</th>
                <th>Entrada / saída</th>
                <th>Estado</th>
              </tr>
            </thead>
            <tbody>
              {sessions.sessions.slice(0, 40).map((s) => (
                <tr key={`${s.nas}-${s.session_id}`}>
                  <td>{s.user}</td>
                  <td>{s.nas}</td>
                  <td className="small">{s.calling_station || "—"}</td>
                  <td className="small">{fmtDate(s.start)}</td>
                  <td className="small">{fmtDate(s.stop)}</td>
                  <td>{fmtSecs(s.duration_s)}</td>
                  <td className="small">
                    {fmtBytes(s.input_octets)} / {fmtBytes(s.output_octets)}
                  </td>
                  <td>
                    <span className={`pill ${s.state === "aberta" ? "ativo" : "desconhecido"}`}>{s.state}</span>
                    {s.terminate_cause && <div className="muted small">{s.terminate_cause}</div>}
                  </td>
                </tr>
              ))}
              {sessions.sessions.length === 0 && (
                <tr>
                  <td colSpan={8} className="muted">
                    Nenhuma sessão contabilizada nos últimos 7 dias.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </section>
      )}

      {cfg && (
        <>
          <section className="card">
            <h3>Clientes NAS</h3>
            <table>
              <thead>
                <tr>
                  <th>Nome</th>
                  <th>Endereço</th>
                  <th>Descrição</th>
                  <th>Message-Authenticator</th>
                  {isAdmin && <th></th>}
                </tr>
              </thead>
              <tbody>
                {cfg.clients.map((c) => (
                  <tr key={c.name}>
                    <td>{c.name}</td>
                    <td>{c.ipaddr}</td>
                    <td className="muted">{c.description || "—"}</td>
                    <td className="small">{c.require_ma ? "exigido" : "opcional"}</td>
                    {isAdmin && (
                      <td className="lab-actions">
                        <button className="secondary small" onClick={() => showSecret(c.name)}>
                          {secret?.name === c.name ? "Ocultar segredo" : "Ver segredo"}
                        </button>
                        <button
                          className="secondary small danger"
                          disabled={busy}
                          onClick={() =>
                            window.confirm(`Remover o cliente ${c.name}?`) &&
                            act(`Remover cliente ${c.name}`, "/radius/client/delete", { name: c.name })
                          }
                        >
                          Remover
                        </button>
                      </td>
                    )}
                  </tr>
                ))}
                {cfg.clients.length === 0 && (
                  <tr>
                    <td colSpan={5} className="muted">
                      Nenhum cliente NAS cadastrado.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
            {secret && (
              <div className="alert ok">
                Segredo de <strong>{secret.name}</strong> ({secret.ipaddr}): <code>{secret.secret}</code> — use em
                <code> /etc/pam_radius_auth.conf</code> do NAS. A consulta foi registrada na auditoria.
              </div>
            )}
            {isAdmin && (
              <form
                className="form row-form"
                onSubmit={(e) => {
                  e.preventDefault();
                  act(`Cadastrar cliente ${client.name}`, "/radius/client", client, () =>
                    setClient({ name: "", ipaddr: "", description: "", require_ma: false })
                  );
                }}
              >
                <label>
                  Nome
                  <input value={client.name} onChange={(e) => setClient({ ...client, name: e.target.value })} placeholder="ubuntu" />
                </label>
                <label>
                  IP do NAS
                  <input
                    value={client.ipaddr}
                    onChange={(e) => setClient({ ...client, ipaddr: e.target.value })}
                    placeholder="192.168.100.20"
                  />
                </label>
                <label>
                  Descrição
                  <input value={client.description} onChange={(e) => setClient({ ...client, description: e.target.value })} />
                </label>
                <label className="check">
                  <input
                    type="checkbox"
                    checked={client.require_ma}
                    onChange={(e) => setClient({ ...client, require_ma: e.target.checked })}
                  />
                  Exigir Message-Authenticator
                </label>
                <button type="submit" disabled={busy || !client.name || !client.ipaddr}>
                  Cadastrar (segredo gerado)
                </button>
              </form>
            )}
          </section>

          <section className="card">
            <h3>Políticas</h3>
            <table>
              <thead>
                <tr>
                  <th>Nome</th>
                  <th>Sessão máx.</th>
                  <th>Ociosidade</th>
                  <th>Acesso</th>
                  <th>Mensagem</th>
                  <th>Usuários</th>
                  {isAdmin && <th></th>}
                </tr>
              </thead>
              <tbody>
                {policies.map((p) => (
                  <tr key={p.name}>
                    <td>{p.name}</td>
                    <td>{p.blocked ? "—" : fmtSecs(p.session_timeout)}</td>
                    <td>{p.blocked || !p.idle_timeout ? "—" : fmtSecs(p.idle_timeout)}</td>
                    <td>
                      <span className={`pill ${p.blocked ? "falha" : "ativo"}`}>{p.blocked ? "nega" : "permite"}</span>
                    </td>
                    <td className="small muted">{p.reply_message || "—"}</td>
                    <td>{p.users}</td>
                    {isAdmin && (
                      <td className="lab-actions">
                        <button className="secondary small" onClick={() => setPolicy({ ...p })}>
                          Editar
                        </button>
                        <button
                          className="secondary small danger"
                          disabled={busy || p.users > 0}
                          onClick={() => act(`Excluir política ${p.name}`, "/radius/policy/delete", { name: p.name })}
                        >
                          Excluir
                        </button>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
            {isAdmin && (
              <form
                className="form row-form"
                onSubmit={(e) => {
                  e.preventDefault();
                  act(`Salvar política ${policy.name}`, "/radius/policy", {
                    ...policy,
                    session_timeout: Number(policy.session_timeout),
                    idle_timeout: Number(policy.idle_timeout),
                  });
                }}
              >
                <label>
                  Nome
                  <input value={policy.name} onChange={(e) => setPolicy({ ...policy, name: e.target.value })} />
                </label>
                <label>
                  Sessão (s)
                  <input
                    type="number"
                    value={policy.session_timeout}
                    onChange={(e) => setPolicy({ ...policy, session_timeout: e.target.value })}
                  />
                </label>
                <label>
                  Ociosidade (s)
                  <input
                    type="number"
                    value={policy.idle_timeout}
                    onChange={(e) => setPolicy({ ...policy, idle_timeout: e.target.value })}
                  />
                </label>
                <label>
                  Mensagem
                  <input value={policy.reply_message} onChange={(e) => setPolicy({ ...policy, reply_message: e.target.value })} />
                </label>
                <label className="check">
                  <input type="checkbox" checked={policy.blocked} onChange={(e) => setPolicy({ ...policy, blocked: e.target.checked })} />
                  Negar acesso
                </label>
                <button type="submit" disabled={busy || !policy.name}>
                  Salvar política
                </button>
              </form>
            )}
          </section>

          <section className="card">
            <h3>Usuários RADIUS</h3>
            <table>
              <thead>
                <tr>
                  <th>Usuário</th>
                  <th>Política</th>
                  <th>Hash da senha</th>
                  <th>Estado</th>
                  <th>Criado</th>
                  {isAdmin && <th></th>}
                </tr>
              </thead>
              <tbody>
                {cfg.users.map((u) => (
                  <tr key={u.username}>
                    <td>{u.username}</td>
                    <td>
                      {isAdmin ? (
                        <select
                          value={u.policy}
                          disabled={busy}
                          onChange={(e) =>
                            act(`Mudar ${u.username} para ${e.target.value}`, "/radius/user/update", {
                              username: u.username,
                              policy: e.target.value,
                            })
                          }
                        >
                          {policies.map((p) => (
                            <option key={p.name}>{p.name}</option>
                          ))}
                        </select>
                      ) : (
                        u.policy
                      )}
                    </td>
                    <td className="small">{u.scheme}</td>
                    <td>
                      <span className={`pill ${u.enabled ? "ativo" : "falha"}`}>{u.enabled ? "ativo" : "bloqueado"}</span>
                    </td>
                    <td className="small">{fmtDate(u.created)}</td>
                    {isAdmin && (
                      <td className="lab-actions">
                        <button
                          className="secondary small"
                          disabled={busy}
                          onClick={() =>
                            act(`${u.enabled ? "Bloquear" : "Desbloquear"} ${u.username}`, "/radius/user/update", {
                              username: u.username,
                              enabled: !u.enabled,
                            })
                          }
                        >
                          {u.enabled ? "Bloquear" : "Desbloquear"}
                        </button>
                        <button className="secondary small" onClick={() => setReset({ username: u.username, password: "" })}>
                          Nova senha
                        </button>
                        <button
                          className="secondary small danger"
                          disabled={busy}
                          onClick={() =>
                            window.confirm(`Remover ${u.username}?`) &&
                            act(`Remover ${u.username}`, "/radius/user/delete", { username: u.username })
                          }
                        >
                          Remover
                        </button>
                      </td>
                    )}
                  </tr>
                ))}
                {cfg.users.length === 0 && (
                  <tr>
                    <td colSpan={6} className="muted">
                      Nenhum usuário RADIUS.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
            {isAdmin && reset.username && (
              <form
                className="form row-form"
                onSubmit={(e) => {
                  e.preventDefault();
                  act(`Redefinir senha de ${reset.username}`, "/radius/user/update", reset, () => setReset({ username: "", password: "" }));
                }}
              >
                <label>
                  Nova senha de {reset.username}
                  <input type="password" value={reset.password} onChange={(e) => setReset({ ...reset, password: e.target.value })} />
                </label>
                <button type="submit" disabled={busy || reset.password.length < 8}>
                  Redefinir
                </button>
                <button type="button" className="secondary" onClick={() => setReset({ username: "", password: "" })}>
                  Cancelar
                </button>
              </form>
            )}
            {isAdmin && (
              <form
                className="form row-form"
                onSubmit={(e) => {
                  e.preventDefault();
                  act(`Criar usuário ${newUser.username}`, "/radius/user", newUser, () =>
                    setNewUser({ ...newUser, username: "", password: "" })
                  );
                }}
              >
                <label>
                  Usuário
                  <input value={newUser.username} onChange={(e) => setNewUser({ ...newUser, username: e.target.value })} />
                </label>
                <label>
                  Senha (mín. 8)
                  <input type="password" value={newUser.password} onChange={(e) => setNewUser({ ...newUser, password: e.target.value })} />
                </label>
                <label>
                  Política
                  <select value={newUser.policy} onChange={(e) => setNewUser({ ...newUser, policy: e.target.value })}>
                    {policies.map((p) => (
                      <option key={p.name}>{p.name}</option>
                    ))}
                  </select>
                </label>
                <label>
                  Hash
                  <select value={newUser.scheme} onChange={(e) => setNewUser({ ...newUser, scheme: e.target.value })}>
                    <option value="ssha2-256">SSHA2-256</option>
                    <option value="sha512-crypt">SHA-512 crypt</option>
                  </select>
                </label>
                <button type="submit" disabled={busy || !newUser.username || newUser.password.length < 8}>
                  Criar usuário
                </button>
              </form>
            )}
          </section>

          {isAdmin && (
            <section className="card">
              <h3>Experimento de desempenho — base de carga</h3>
              <p className="muted small">
                Gera usuários <code>carga00001…</code> com a mesma senha aleatória (mostrada uma vez) para o script
                <code> scripts/radius_carga.py</code> no Ubuntu. Hoje: {cfg.load_users.count} usuário(s) de carga
                {cfg.load_users.schemes.length ? ` (${cfg.load_users.schemes.join(", ")})` : ""}.
              </p>
              <form
                className="form row-form"
                onSubmit={(e) => {
                  e.preventDefault();
                  act(`Gerar ${load.count} usuários de carga`, "/radius/load", {
                    count: Number(load.count),
                    scheme: load.scheme,
                    rounds: Number(load.rounds),
                  }, (out) => out && setLoad((l) => ({ ...l, password: out.load_password })));
                }}
              >
                <label>
                  Quantidade
                  <input type="number" value={load.count} onChange={(e) => setLoad({ ...load, count: e.target.value })} />
                </label>
                <label>
                  Hash
                  <select value={load.scheme} onChange={(e) => setLoad({ ...load, scheme: e.target.value })}>
                    <option value="ssha2-256">SSHA2-256</option>
                    <option value="sha512-crypt">SHA-512 crypt</option>
                  </select>
                </label>
                {load.scheme === "sha512-crypt" && (
                  <label>
                    Rodadas
                    <input type="number" value={load.rounds} onChange={(e) => setLoad({ ...load, rounds: e.target.value })} />
                  </label>
                )}
                <button type="submit" disabled={busy}>
                  Gerar base
                </button>
                <button
                  type="button"
                  className="secondary"
                  disabled={busy || !cfg.load_users.count}
                  onClick={() => act("Remover usuários de carga", "/radius/load/delete", undefined, () => setLoad((l) => ({ ...l, password: "" })))}
                >
                  Remover base
                </button>
              </form>
              {load.password && (
                <div className="alert ok">
                  Senha dos usuários de carga: <code>{load.password}</code> (não será mostrada de novo).
                </div>
              )}
            </section>
          )}

          {isAdmin && (
            <div className="row">
              <button
                className="secondary"
                disabled={busy}
                onClick={() => window.confirm("Voltar à configuração anterior do RADIUS?") && act("Restaurar versão anterior", "/radius/restore")}
              >
                Restaurar versão anterior
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
