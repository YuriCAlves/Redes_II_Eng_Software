import { useCallback, useEffect, useState } from "react";
import { api } from "../api.js";
import { canAtLeast, useAuth } from "../auth.jsx";
import { ErrorBox, fmtDate } from "../components.jsx";
import { useNotify } from "../notify.jsx";

function epochToDate(v) {
  const n = Number(v);
  return Number.isFinite(n) && n > 0 ? fmtDate(new Date(n * 1000).toISOString()) : "—";
}

export default function Dhcp() {
  const { user, expire } = useAuth();
  const { run } = useNotify();
  const isAdmin = canAtLeast(user, "admin");
  const [cfg, setCfg] = useState(null);
  const [leases, setLeases] = useState(null);
  const [error, setError] = useState(null);
  const [form, setForm] = useState({ "hw-address": "", "ip-address": "", hostname: "" });
  const [formError, setFormError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [c, l] = await Promise.all([api("/dhcp/config"), api("/dhcp/leases")]);
      setCfg(c);
      setLeases(l.leases);
      setError(null);
    } catch (e) {
      if (e.status === 401) expire();
      else setError(e);
    }
  }, [expire]);

  useEffect(() => {
    load();
  }, [load]);

  async function create(e) {
    e.preventDefault();
    setFormError("");
    setBusy(true);
    try {
      const body = { "hw-address": form["hw-address"].trim(), "ip-address": form["ip-address"].trim() };
      if (form.hostname.trim()) body.hostname = form.hostname.trim();
      await run("DHCP", `Criar reserva ${body["ip-address"]}`, () => api("/dhcp/reservation", { method: "POST", body }));
      setForm({ "hw-address": "", "ip-address": "", hostname: "" });
      load();
    } catch (err) {
      setFormError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function remove(ip) {
    setBusy(true);
    try {
      await run("DHCP", `Remover reserva ${ip}`, () =>
        api("/dhcp/reservation/delete", { method: "POST", body: { "ip-address": ip } })
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
      await run("DHCP", "Restaurar configuração anterior", () => api("/dhcp/restore", { method: "POST" }));
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
        <h2>DHCP (Kea) — {cfg?.subnet ?? "…"}</h2>
        <button onClick={load}>Atualizar</button>
      </div>
      <ErrorBox error={error} />

      {cfg && (
        <section className="card">
          <h3>Sub-rede</h3>
          <dl className="kv">
            <dt>Sub-rede</dt>
            <dd>{cfg.subnet}</dd>
            <dt>Faixa dinâmica</dt>
            <dd>{cfg.pools.join(", ") || "—"}</dd>
            <dt>Duração da concessão</dt>
            <dd>{cfg.valid_lifetime ? `${cfg.valid_lifetime}s` : "—"}</dd>
          </dl>
        </section>
      )}

      <section className="card">
        <h3>Reservas</h3>
        <table>
          <thead>
            <tr>
              <th>MAC</th>
              <th>IP reservado</th>
              <th>Hostname</th>
              {isAdmin && <th></th>}
            </tr>
          </thead>
          <tbody>
            {cfg?.reservations.map((r) => (
              <tr key={r["ip-address"]}>
                <td>
                  <code>{r["hw-address"]}</code>
                </td>
                <td>{r["ip-address"]}</td>
                <td className="muted">{r.hostname || "—"}</td>
                {isAdmin && (
                  <td>
                    <button className="secondary small" onClick={() => remove(r["ip-address"])} disabled={busy}>
                      Remover
                    </button>
                  </td>
                )}
              </tr>
            ))}
            {cfg && cfg.reservations.length === 0 && (
              <tr>
                <td colSpan={isAdmin ? 4 : 3} className="muted">
                  Nenhuma reserva cadastrada.
                </td>
              </tr>
            )}
          </tbody>
        </table>

        {isAdmin ? (
          <>
            <form onSubmit={create} className="form row-form">
              <label>
                MAC
                <input
                  value={form["hw-address"]}
                  onChange={(e) => setForm({ ...form, "hw-address": e.target.value })}
                  placeholder="aa:bb:cc:dd:ee:ff"
                />
              </label>
              <label>
                IP reservado
                <input
                  value={form["ip-address"]}
                  onChange={(e) => setForm({ ...form, "ip-address": e.target.value })}
                  placeholder="192.168.100.x"
                />
              </label>
              <label>
                Hostname (opcional)
                <input value={form.hostname} onChange={(e) => setForm({ ...form, hostname: e.target.value })} />
              </label>
              <button type="submit" disabled={busy || !form["hw-address"] || !form["ip-address"]}>
                Criar reserva
              </button>
            </form>
            {formError && <div className="alert error">{formError}</div>}
            <div className="row">
              <button type="button" className="secondary" onClick={restore} disabled={busy}>
                Restaurar configuração anterior
              </button>
            </div>
          </>
        ) : (
          <div className="alert warn">Somente administradores podem criar, remover ou restaurar reservas.</div>
        )}
      </section>

      <section className="card">
        <h3>Concessões ativas (leases)</h3>
        <table>
          <thead>
            <tr>
              <th>Endereço</th>
              <th>Cliente (MAC)</th>
              <th>Hostname</th>
              <th>Validade</th>
              <th>Expira em</th>
              <th>Estado</th>
            </tr>
          </thead>
          <tbody>
            {leases?.map((l) => (
              <tr key={l.address}>
                <td>{l.address}</td>
                <td>
                  <code>{l.hwaddr}</code>
                </td>
                <td className="muted">{l.hostname || "—"}</td>
                <td className="muted">{l.valid_lifetime ? `${l.valid_lifetime}s` : "—"}</td>
                <td className="muted">{epochToDate(l.expire)}</td>
                <td>
                  <span className={`pill ${l.state === "ativa" ? "ativo" : "inativo"}`}>{l.state}</span>
                </td>
              </tr>
            ))}
            {leases && leases.length === 0 && (
              <tr>
                <td colSpan={6} className="muted">
                  Nenhuma concessão no momento.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </section>
    </div>
  );
}
