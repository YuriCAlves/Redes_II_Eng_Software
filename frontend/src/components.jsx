export const hhmmss = (ms) => new Date(ms).toLocaleTimeString("pt-BR");

export function fmtBytes(n) {
  if (n == null) return "—";
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v < 10 ? 1 : 0)} ${units[i]}`;
}

export function fmtDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString("pt-BR");
}

export function ServicesTable({ services, dim }) {
  return (
    <table className={dim ? "stale" : ""}>
      <thead>
        <tr>
          <th>Serviço</th>
          <th>Estado</th>
          <th>systemd</th>
          <th>Porta</th>
        </tr>
      </thead>
      <tbody>
        {services.map((sv) => (
          <tr key={sv.name}>
            <td>{sv.name}</td>
            <td>
              <span className={`pill ${sv.status}`}>{sv.status}</span>
            </td>
            <td className="muted">{sv.active_state ? `${sv.active_state} (${sv.sub_state})` : "—"}</td>
            <td className="muted">
              {sv.port} · {sv.port_state}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// Mostra "unknown_state" (sem SSH) como aviso amarelo; o resto como erro.
export function ErrorBox({ error }) {
  if (!error) return null;
  const kind = error.code === "unknown_state" || error.code === "network" ? "warn" : "error";
  return <div className={`alert ${kind}`}>{error.message}</div>;
}
