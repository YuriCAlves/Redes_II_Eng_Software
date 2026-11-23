import { useCallback, useEffect, useState } from "react";
import { api } from "../api.js";
import { useAuth } from "../auth.jsx";
import { ErrorBox, fmtDate } from "../components.jsx";

const RESULT_CLASS = { ok: "ativo", falha: "inativo", negado: "inativo" };

export default function Audit() {
  const { user, expire } = useAuth();
  const [rows, setRows] = useState(null);
  const [resource, setResource] = useState("");
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    try {
      const qs = resource ? `?resource=${encodeURIComponent(resource)}` : "";
      setRows(await api(`/audit${qs}`));
      setError(null);
    } catch (e) {
      if (e.status === 401) expire();
      else setError(e);
    }
  }, [resource, expire]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="dash">
      <div className="dash-head">
        <h2>Auditoria</h2>
        <button onClick={load}>Atualizar</button>
      </div>
      {user?.role !== "admin" && (
        <div className="alert warn">Você vê apenas as suas próprias ações. Administradores veem o histórico global.</div>
      )}
      <section className="card">
        <div className="row">
          <label>
            Recurso
            <select value={resource} onChange={(e) => setResource(e.target.value)}>
              <option value="">(todos)</option>
              <option value="auth">auth</option>
              <option value="dns">dns</option>
              <option value="dhcp">dhcp</option>
              <option value="labvm">labvm</option>
            </select>
          </label>
        </div>
        <ErrorBox error={error} />
        <table>
          <thead>
            <tr>
              <th>Quando</th>
              <th>Usuário</th>
              <th>Perfil</th>
              <th>Recurso</th>
              <th>Ação</th>
              <th>Resultado</th>
              <th>Detalhe</th>
              <th>Correlação</th>
            </tr>
          </thead>
          <tbody>
            {rows?.map((r) => (
              <tr key={r.id}>
                <td className="muted">{fmtDate(r.ts)}</td>
                <td>{r.username}</td>
                <td className="muted">{r.role}</td>
                <td>{r.resource}</td>
                <td>{r.action}</td>
                <td>
                  <span className={`pill ${RESULT_CLASS[r.result] || ""}`}>{r.result}</span>
                </td>
                <td className="muted small">{r.detail || "—"}</td>
                <td className="muted">
                  <code>{r.correlation_id}</code>
                </td>
              </tr>
            ))}
            {rows && rows.length === 0 && (
              <tr>
                <td colSpan={8} className="muted">
                  Nenhum evento registrado ainda.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </section>
    </div>
  );
}
