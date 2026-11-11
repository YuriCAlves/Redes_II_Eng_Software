import { useState } from "react";
import { api } from "../api.js";
import { useAuth } from "../auth.jsx";
import { ErrorBox, fmtDate } from "../components.jsx";

const SERVICES = ["apache2", "vsftpd", "postfix", "dovecot", "ssh"];
const LINES = [20, 50, 100];

export default function Logs() {
  const { expire } = useAuth();
  const [service, setService] = useState("vsftpd");
  const [lines, setLines] = useState(50);
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      setData(await api(`/logs?service=${service}&lines=${lines}`));
    } catch (e) {
      if (e.status === 401) return expire();
      setError(e); // "estado desconhecido" ou falha do comando; nunca vira "serviço parado"
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="dash">
      <h2>Logs técnicos</h2>
      <section className="card form">
        <div className="row">
          <label className="inline">
            Serviço
            <select value={service} onChange={(e) => setService(e.target.value)}>
              {SERVICES.map((s) => (
                <option key={s}>{s}</option>
              ))}
            </select>
          </label>
          <label className="inline">
            Linhas
            <select value={lines} onChange={(e) => setLines(Number(e.target.value))}>
              {LINES.map((n) => (
                <option key={n}>{n}</option>
              ))}
            </select>
          </label>
          <button onClick={load} disabled={loading}>
            {loading ? "Lendo…" : "Atualizar"}
          </button>
          {data && <span className="muted small">Última atualização: {fmtDate(data.timestamp)}</span>}
        </div>
        <p className="muted small">
          {service === "apache2" ? "Fonte: /var/log/apache2/access.log" : `Fonte: journalctl -u ${service}`} (via SSH, somente leitura)
        </p>
      </section>

      <ErrorBox error={error} />
      {error && data && <div className="alert warn">Exibindo a última leitura bem-sucedida ({fmtDate(data.timestamp)}).</div>}

      <pre className={`log${error ? " stale" : ""}`} tabIndex={0}>
        {data ? (data.lines.length ? data.lines.join("\n") : "(sem linhas de log)") : "Escolha o serviço e clique em Atualizar."}
      </pre>
    </div>
  );
}
