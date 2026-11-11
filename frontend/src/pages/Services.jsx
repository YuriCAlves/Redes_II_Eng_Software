import { useEffect, useState } from "react";
import { api } from "../api.js";
import { useAuth } from "../auth.jsx";
import { ErrorBox, ServicesTable, hhmmss } from "../components.jsx";

export default function Services() {
  const { expire } = useAuth();
  const [services, setServices] = useState(null);
  const [error, setError] = useState(null);
  const [updated, setUpdated] = useState(null);

  useEffect(() => {
    let alive = true;
    async function load() {
      try {
        const d = await api("/services");
        if (!alive) return;
        setServices(d.services);
        setError(null);
        setUpdated(Date.now());
      } catch (e) {
        if (!alive) return;
        if (e.status === 401) expire();
        else setError(e); // mantém a última leitura, marcada como desatualizada
      }
    }
    load();
    const id = setInterval(load, 5000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [expire]);

  return (
    <section className="card">
      <div className="dash-head">
        <h2>Serviços</h2>
        <span className="muted">{updated ? `Última atualização: ${hhmmss(updated)}` : "Carregando…"}</span>
      </div>
      <ErrorBox error={error} />
      {services && <ServicesTable services={services} dim={!!error} />}
      <p className="muted small">
        Parte 2 (não implementada): botões de iniciar/parar/reiniciar serviços e edição de configurações.
      </p>
    </section>
  );
}
