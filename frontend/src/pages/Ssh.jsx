import { useCallback, useEffect, useState } from "react";
import { api } from "../api.js";
import { canAtLeast, useAuth } from "../auth.jsx";
import { ErrorBox, fmtDate } from "../components.jsx";
import { useNotify } from "../notify.jsx";

// Mesma regra do backend (^[a-z0-9][a-z0-9-]{0,61}[a-z0-9]$); aqui serve só para avisar cedo.
const HOSTNAME_RE = /^[a-z0-9][a-z0-9-]{0,61}[a-z0-9]$/;

export default function Ssh() {
  const { user, expire } = useAuth();
  const { run } = useNotify();
  const isAdmin = canAtLeast(user, "admin");
  const [info, setInfo] = useState(null);
  const [error, setError] = useState(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState("");
  const [result, setResult] = useState(null); // {kind, previous, current}

  const load = useCallback(async () => {
    try {
      setInfo(await api("/ssh/info"));
      setError(null);
    } catch (e) {
      if (e.status === 401) expire();
      else setError(e);
    }
  }, [expire]);

  useEffect(() => {
    load();
  }, [load]);

  const valid = HOSTNAME_RE.test(name);

  async function change(e) {
    e.preventDefault();
    setFormError("");
    if (!valid) return setFormError("Use 2 a 63 caracteres: letras minúsculas, números e hífen (sem hífen nas pontas).");
    setBusy(true);
    try {
      const r = await run("SSH", `Alterar hostname para ${name}`, () =>
        api("/ssh/hostname/change", { method: "POST", body: { hostname: name } })
      );
      setResult({ kind: "Alteração", ...r });
      setName("");
      load();
    } catch (err) {
      setFormError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function restore() {
    setFormError("");
    setBusy(true);
    try {
      const r = await run("SSH", "Restaurar hostname original", () =>
        api("/ssh/hostname/restore", { method: "POST" })
      );
      setResult({ kind: "Restauração", ...r });
      load();
    } catch (err) {
      setFormError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="dash">
      <div className="dash-head">
        <h2>SSH</h2>
        <button onClick={load}>Atualizar</button>
      </div>

      <ErrorBox error={error} />

      {info && (
        <section className="card">
          <h3>Servidor</h3>
          <dl className="kv">
            <dt>Hostname atual</dt>
            <dd>{info.hostname}</dd>
            <dt>Endereço</dt>
            <dd>{info.host}</dd>
            <dt>Usuário SSH</dt>
            <dd>{info.user}</dd>
            <dt>Sistema</dt>
            <dd>{info.os || "—"}</dd>
            <dt>Kernel</dt>
            <dd>{info.kernel}</dd>
            <dt>Uptime</dt>
            <dd>{info.uptime}</dd>
            <dt>Host key (SHA-256)</dt>
            <dd>
              <code className="hash">{info.fingerprint ?? "—"}</code>
            </dd>
            <dt>Consultado em</dt>
            <dd>{fmtDate(info.timestamp)}</dd>
          </dl>
        </section>
      )}

      {isAdmin ? (
        <section className="card form">
          <h3>Alterar hostname</h3>
          <form onSubmit={change} className="form">
            <label>
              Novo hostname {info && <span className="muted">(atual: {info.hostname})</span>}
              <input value={name} onChange={(e) => setName(e.target.value.trim())} maxLength={63} placeholder="ex.: servidor-teste" />
            </label>
            {name && !valid && <div className="alert warn">Formato inválido (a-z, 0-9 e hífen; 2 a 63 caracteres).</div>}
            {formError && <div className="alert error">{formError}</div>}
            <div className="row">
              <button type="submit" disabled={busy || !valid}>
                Alterar
              </button>
              <button type="button" className="secondary" onClick={restore} disabled={busy || !info?.original_hostname}>
                Restaurar{info?.original_hostname ? ` (${info.original_hostname})` : ""}
              </button>
            </div>
          </form>
          {result && (
            <div className="alert ok">
              <strong>{result.kind} concluída.</strong> Antes: <code>{result.previous}</code> → Depois:{" "}
              <code>{result.current}</code>
            </div>
          )}
        </section>
      ) : (
        <div className="alert warn">Somente administradores podem alterar ou restaurar o hostname.</div>
      )}
    </div>
  );
}
