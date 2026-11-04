import { useEffect, useState } from "react";
import { api } from "../api.js";
import { useAuth } from "../auth.jsx";

// Página provisória: chama o endpoint de leitura da aba para provar que a
// autenticação e as permissões do backend funcionam. Será substituída por
// telas reais nos checkpoints seguintes.
export default function ModulePage({ tab }) {
  const { expire } = useAuth();
  const [data, setData] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    setData(null);
    setError("");
    api(tab.endpoint)
      .then(setData)
      .catch((err) => {
        if (err.status === 401) expire();
        else setError(`${err.status ?? ""} ${err.message}`);
      });
  }, [tab, expire]);

  return (
    <section className="card">
      <h2>{tab.label}</h2>
      <p className="muted">Módulo a ser implementado no {tab.cp}.</p>
      {error && <div className="alert error">{error}</div>}
      {data && <pre>{JSON.stringify(data, null, 2)}</pre>}
    </section>
  );
}
