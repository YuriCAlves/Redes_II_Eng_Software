import { useCallback, useEffect, useState } from "react";
import { api } from "../api.js";
import { canAtLeast, useAuth } from "../auth.jsx";
import { ErrorBox, fmtDate } from "../components.jsx";
import { useNotify } from "../notify.jsx";

const FWD_TYPES = ["A", "CNAME", "MX", "TXT"];

function ZonePanel({ zoneKey, title, isAdmin }) {
  const { expire } = useAuth();
  const { run } = useNotify();
  const [zone, setZone] = useState(null);
  const [error, setError] = useState(null);
  const [form, setForm] = useState({ name: "", type: zoneKey === "fwd" ? "A" : "PTR", value: "", priority: "10" });
  const [formError, setFormError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setZone(await api(`/dns/${zoneKey}`));
      setError(null);
    } catch (e) {
      if (e.status === 401) expire();
      else setError(e);
    }
  }, [zoneKey, expire]);

  useEffect(() => {
    load();
  }, [load]);

  async function create(e) {
    e.preventDefault();
    setFormError("");
    setBusy(true);
    try {
      const body = { zone: zoneKey, name: form.name.trim(), type: form.type, value: form.value.trim() };
      if (form.type === "MX") body.priority = Number(form.priority);
      await run("DNS", `Criar ${form.type} ${form.name}`, () => api("/dns/record", { method: "POST", body }));
      setForm({ ...form, name: "", value: "" });
      load();
    } catch (err) {
      setFormError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function remove(r) {
    setBusy(true);
    try {
      await run("DNS", `Remover ${r.type} ${r.name}`, () =>
        api("/dns/record/delete", { method: "POST", body: { zone: zoneKey, name: r.name, type: r.type, value: r.value } })
      );
      load();
    } catch (err) {
      setFormError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function restore() {
    setBusy(true);
    try {
      await run("DNS", `Restaurar versão anterior (${title})`, () => api(`/dns/${zoneKey}/restore`, { method: "POST" }));
      load();
    } catch (err) {
      setFormError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card">
      <div className="dash-head">
        <h3>{title}</h3>
        <button onClick={load}>Atualizar</button>
      </div>
      <ErrorBox error={error} />
      {zone && (
        <table>
          <thead>
            <tr>
              <th>Nome</th>
              <th>Tipo</th>
              {zoneKey === "fwd" && <th>Prioridade</th>}
              <th>Valor</th>
              {isAdmin && <th></th>}
            </tr>
          </thead>
          <tbody>
            {zone.records.map((r, i) => (
              <tr key={i}>
                <td>{r.name}</td>
                <td>{r.type}</td>
                {zoneKey === "fwd" && <td className="muted">{r.priority ?? "—"}</td>}
                <td>
                  <code>{r.value}</code>
                </td>
                {isAdmin && (
                  <td>
                    <button className="secondary small" onClick={() => remove(r)} disabled={busy}>
                      Remover
                    </button>
                  </td>
                )}
              </tr>
            ))}
            {zone.records.length === 0 && (
              <tr>
                <td colSpan={isAdmin ? 5 : 4} className="muted">
                  Nenhum registro gerenciado pelo console ainda.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      )}

      {isAdmin ? (
        <>
          <form onSubmit={create} className="form row-form">
            <label>
              Nome {zoneKey === "rev" && <span className="muted">(último octeto)</span>}
              <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder={zoneKey === "fwd" ? "ex.: ftp ou @" : "ex.: 30"} />
            </label>
            {zoneKey === "fwd" && (
              <label>
                Tipo
                <select value={form.type} onChange={(e) => setForm({ ...form, type: e.target.value })}>
                  {FWD_TYPES.map((t) => (
                    <option key={t}>{t}</option>
                  ))}
                </select>
              </label>
            )}
            {zoneKey === "fwd" && form.type === "MX" && (
              <label>
                Prioridade
                <input type="number" value={form.priority} onChange={(e) => setForm({ ...form, priority: e.target.value })} />
              </label>
            )}
            <label>
              Valor
              <input
                value={form.value}
                onChange={(e) => setForm({ ...form, value: e.target.value })}
                placeholder={zoneKey === "fwd" ? (form.type === "A" ? "192.168.100.x" : "fqdn.empresa.test.") : "fqdn.empresa.test."}
              />
            </label>
            <button type="submit" disabled={busy || !form.name || !form.value}>
              Criar
            </button>
          </form>
          {formError && <div className="alert error">{formError}</div>}
          <div className="row">
            <button type="button" className="secondary" onClick={restore} disabled={busy}>
              Restaurar versão anterior
            </button>
          </div>
        </>
      ) : (
        <div className="alert warn">Somente administradores podem criar, remover ou restaurar registros.</div>
      )}
    </section>
  );
}

function QueryPanel() {
  const [name, setName] = useState("site.empresa.test");
  const [type, setType] = useState("A");
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  async function run(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setResult(await api(`/dns/query/run?name=${encodeURIComponent(name)}&type=${type}`));
    } catch (err) {
      setError(err);
      setResult(null);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card">
      <h3>Consulta (equivalente ao dig)</h3>
      <form onSubmit={run} className="form row-form">
        <label>
          Nome
          <input value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label>
          Tipo
          <select value={type} onChange={(e) => setType(e.target.value)}>
            {["A", "CNAME", "MX", "TXT", "PTR", "NS", "SOA"].map((t) => (
              <option key={t}>{t}</option>
            ))}
          </select>
        </label>
        <button type="submit" disabled={busy || !name}>
          Consultar
        </button>
      </form>
      <ErrorBox error={error} />
      {result && (
        <div className={`alert ${result.status === "NOERROR" ? "ok" : "warn"}`}>
          <strong>{result.status}</strong> — servidor {result.server} · {result.elapsed_ms} ms
          {result.answers.length > 0 && (
            <ul className="plain-list">
              {result.answers.map((a, i) => (
                <li key={i}>
                  <code>{a}</code>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}

export default function Dns() {
  const { user } = useAuth();
  const isAdmin = canAtLeast(user, "admin");
  return (
    <div className="dash">
      <div className="dash-head">
        <h2>DNS (BIND 9) — zona empresa.test</h2>
      </div>
      <QueryPanel />
      <ZonePanel zoneKey="fwd" title="Zona direta (empresa.test)" isAdmin={isAdmin} />
      <ZonePanel zoneKey="rev" title="Zona reversa (192.168.100.0/24)" isAdmin={isAdmin} />
    </div>
  );
}
