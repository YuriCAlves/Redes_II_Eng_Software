import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { hhmmss } from "./components.jsx";

const Ctx = createContext(null);
export const useNotify = () => useContext(Ctx);

const STATUS_CLASS = { "em andamento": "run", concluída: "done", falha: "fail" };

export function NotifyProvider({ children }) {
  const [toasts, setToasts] = useState([]);
  const [ops, setOps] = useState([]); // operações da sessão (só no navegador)
  const [lost, setLost] = useState(null); // {lastOk} enquanto sem comunicação
  const seq = useRef(0);
  const lostRef = useRef(false);
  const lastOk = useRef(Date.now());

  const dismiss = useCallback((id) => setToasts((t) => t.filter((x) => x.id !== id)), []);

  const toast = useCallback(
    (kind, text) => {
      const id = ++seq.current;
      setToasts((t) => [...t.slice(-4), { id, kind, text }]);
      setTimeout(() => dismiss(id), kind === "ok" ? 5000 : 9000);
    },
    [dismiss]
  );

  // Perda/retomada de comunicação com o Debian (emitida pelo api.js)
  useEffect(() => {
    const onComm = (e) => {
      if (e.detail.ok) {
        lastOk.current = Date.now();
        if (lostRef.current) {
          lostRef.current = false;
          setLost(null);
          toast("ok", "Comunicação com o Debian restabelecida.");
        }
      } else if (!lostRef.current) {
        lostRef.current = true;
        setLost({ lastOk: lastOk.current });
        toast("warn", "Perda de comunicação com o Debian. Os dados exibidos podem estar desatualizados.");
      }
    };
    window.addEventListener("console:comm", onComm);
    return () => window.removeEventListener("console:comm", onComm);
  }, [toast]);

  // Registra e acompanha uma operação: tipo, horário e status (em andamento/concluída/falha).
  // `verify(resultado)` devolve um texto se o resultado, apesar de HTTP 200, deve contar como falha.
  const run = useCallback(
    async (type, label, fn, { verify } = {}) => {
      const id = ++seq.current;
      const patch = (p) => setOps((o) => o.map((x) => (x.id === id ? { ...x, ...p } : x)));
      setOps((o) => [{ id, type, label, startedAt: Date.now(), status: "em andamento" }, ...o].slice(0, 50));
      try {
        const result = await fn();
        const problem = verify ? verify(result) : null;
        if (problem) {
          patch({ status: "falha", message: problem });
          toast("error", `${label}: ${problem}`);
        } else {
          patch({ status: "concluída" });
          toast("ok", `${label}: concluída`);
        }
        return result;
      } catch (err) {
        patch({ status: "falha", message: err.message });
        if (err.status !== 0) toast("error", `${label}: ${err.message}`); // status 0 já avisou a perda
        throw err;
      }
    },
    [toast]
  );

  return <Ctx.Provider value={{ toasts, dismiss, toast, run, ops, lost }}>{children}</Ctx.Provider>;
}

export function Toasts() {
  const { toasts, dismiss } = useNotify();
  return (
    <div className="toasts" aria-live="polite">
      {toasts.map((t) => (
        <div key={t.id} className={`toast ${t.kind}`} onClick={() => dismiss(t.id)} role="status">
          {t.text}
        </div>
      ))}
    </div>
  );
}

export function CommBadge() {
  const { lost } = useNotify();
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!lost) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [lost]);
  if (!lost) return null;
  const s = Math.floor((now - lost.lastOk) / 1000);
  return <span className="comm-badge">Sem comunicação · desatualizado há {s} s</span>;
}

export function OperationsPanel() {
  const { ops } = useNotify();
  return (
    <section className="card ops">
      <h3>Operações recentes (desta sessão)</h3>
      {ops.length === 0 ? (
        <p className="muted">Nenhuma operação ainda. Uploads, envios de e-mail e alterações de SSH aparecem aqui.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Hora</th>
              <th>Tipo</th>
              <th>Operação</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {ops.map((o) => (
              <tr key={o.id}>
                <td className="muted">{hhmmss(o.startedAt)}</td>
                <td>{o.type}</td>
                <td>
                  {o.label}
                  {o.message && <div className="muted small">{o.message}</div>}
                </td>
                <td>
                  <span className={`pill op-${STATUS_CLASS[o.status]}`}>{o.status}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
