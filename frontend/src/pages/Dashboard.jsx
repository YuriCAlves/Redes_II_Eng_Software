import { useEffect, useRef, useState } from "react";
import Chart from "chart.js/auto";
import { api } from "../api.js";
import { useAuth } from "../auth.jsx";
import { ServicesTable } from "../components.jsx";

const POLL_MS = 5000; // polling das métricas
const MAX_SAMPLES = 180; // 180 x 5 s = 15 min de histórico no gráfico
const STALE_AFTER_S = 15; // aviso se a coleta ficar mais que isso sem atualizar

const hhmmss = (ms) => new Date(ms).toLocaleTimeString("pt-BR");

function Card({ title, value, sub, stale }) {
  return (
    <div className={`card stat${stale ? " stale" : ""}`}>
      <div className="stat-title">{title}</div>
      <div className="stat-value">{value}</div>
      {sub && <div className="stat-sub muted">{sub}</div>}
    </div>
  );
}

function diskCard(d, stale) {
  const title = `Disco ${d.path}`;
  if (d.error) return <Card key={d.path} title={title} value="—" sub={d.error} stale={stale} />;
  return (
    <Card
      key={d.path}
      title={title}
      value={`${d.percent}%`}
      sub={`${d.used_gb} / ${d.total_gb} GB`}
      stale={stale}
    />
  );
}

export default function Dashboard() {
  const { expire } = useAuth();
  const [metrics, setMetrics] = useState(null);
  const [services, setServices] = useState(null);
  const [servicesError, setServicesError] = useState(false);
  const [latency, setLatency] = useState(null);
  const [samples, setSamples] = useState([]); // {t, cpu, ram}
  const [lastOk, setLastOk] = useState(null); // instante da última coleta bem-sucedida
  const [now, setNow] = useState(Date.now());

  const canvasRef = useRef(null);
  const chartRef = useRef(null);

  // ---- Polling a cada 5 s (sem sobrepor requisições) ----
  useEffect(() => {
    let alive = true;
    let busy = false;

    async function poll() {
      if (busy) return;
      busy = true;
      const [m, s, l] = await Promise.allSettled([
        api("/metrics"),
        api("/services"),
        api("/http-latency"),
      ]);
      busy = false;
      if (!alive) return;

      if ([m, s, l].some((r) => r.status === "rejected" && r.reason?.status === 401)) {
        expire();
        return;
      }

      if (m.status === "fulfilled") {
        const t = Date.now();
        setMetrics(m.value);
        setLastOk(t);
        setSamples((prev) =>
          [...prev, { t, cpu: m.value.cpu_percent, ram: m.value.memory.percent }].slice(-MAX_SAMPLES)
        );
      }
      // Em falha, nunca mostra valor inventado: serviços viram "sem dados"
      if (s.status === "fulfilled") {
        setServices(s.value.services);
        setServicesError(false);
      } else {
        setServicesError(true);
      }
      if (l.status === "fulfilled") setLatency(l.value);
    }

    poll();
    const id = setInterval(poll, POLL_MS);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [expire]);

  // Relógio de 1 s só para atualizar "há X segundos"
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  // ---- Gráfico Chart.js (CPU azul, RAM vermelha) ----
  useEffect(() => {
    chartRef.current = new Chart(canvasRef.current, {
      type: "line",
      data: {
        labels: [],
        datasets: [
          { label: "CPU %", data: [], borderColor: "#2563eb", backgroundColor: "#2563eb", pointRadius: 0, tension: 0.25 },
          { label: "RAM %", data: [], borderColor: "#dc2626", backgroundColor: "#dc2626", pointRadius: 0, tension: 0.25 },
        ],
      },
      options: {
        animation: false,
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        scales: {
          y: { min: 0, max: 100, ticks: { callback: (v) => `${v}%` } },
          x: { ticks: { maxTicksLimit: 8 } },
        },
      },
    });
    return () => chartRef.current?.destroy();
  }, []);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    chart.data.labels = samples.map((s) => hhmmss(s.t));
    chart.data.datasets[0].data = samples.map((s) => s.cpu);
    chart.data.datasets[1].data = samples.map((s) => s.ram);
    chart.update();
  }, [samples]);

  const ageS = lastOk ? Math.floor((now - lastOk) / 1000) : null;
  const stale = lastOk === null ? false : ageS > STALE_AFTER_S;
  const mem = metrics?.memory;

  return (
    <div className="dash">
      <div className="dash-head">
        <h2>Visão Geral</h2>
        <span className="muted">
          {lastOk ? `Última atualização: ${hhmmss(lastOk)}` : "Aguardando primeira coleta…"}
        </span>
      </div>

      {stale && (
        <div className="alert warn">
          Dados desatualizados há {ageS} s — sem comunicação com o Debian. Os valores abaixo são os da
          última coleta bem-sucedida.
        </div>
      )}

      <div className="grid">
        <Card title="CPU" value={metrics ? `${metrics.cpu_percent}%` : "—"} stale={stale} />
        <Card
          title="Memória"
          value={mem ? `${mem.percent}%` : "—"}
          sub={mem ? `${mem.used_mb} / ${mem.total_mb} MB` : ""}
          stale={stale}
        />
        {metrics ? metrics.disks.map((d) => diskCard(d, stale)) : null}
        <Card title="Uptime" value={metrics ? metrics.uptime_human : "—"} stale={stale} />
        <Card
          title="Latência HTTP"
          value={latency?.ok ? `${latency.duration_ms} ms` : "—"}
          sub={
            latency
              ? latency.ok
                ? `HTTP ${latency.status_code} · ${latency.url}`
                : `${latency.error} · ${latency.url}`
              : ""
          }
          stale={stale}
        />
      </div>

      <section className="card">
        <h3>CPU e memória (últimos 15 min)</h3>
        <div className="chart-box">
          <canvas ref={canvasRef} />
        </div>
      </section>

      <section className="card">
        <h3>Serviços</h3>
        {servicesError && !services && <div className="alert error">Não foi possível consultar os serviços.</div>}
        {servicesError && services && (
          <div className="alert warn">Falha ao atualizar os serviços; exibindo a última leitura.</div>
        )}
        {services && (
          <ServicesTable services={services} dim={stale || servicesError} />
        )}
      </section>
    </div>
  );
}
