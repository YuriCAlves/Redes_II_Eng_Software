// CP5 — Módulo SSH
// React — informações do servidor, alteração e restauração de hostname

import { useState, useEffect } from "react";

const API = "/api/ssh";
const HOSTNAME_RE = /^[a-z0-9][a-z0-9-]{0,61}[a-z0-9]$/;

export default function SSHPage() {
  const [info, setInfo]           = useState(null);
  const [loadingInfo, setLoadingInfo] = useState(false);
  const [newHostname, setNewHostname] = useState("");
  const [changeResult, setChangeResult] = useState(null); // { previous, new }
  const [restoreResult, setRestoreResult] = useState(null);
  const [working, setWorking]     = useState(false);
  const [error, setError]         = useState(null);
  const [validationError, setValidationError] = useState(null);

  async function fetchInfo() {
    setLoadingInfo(true);
    setError(null);
    try {
      const r = await fetch(`${API}/info`);
      if (!r.ok) throw new Error((await r.json()).detail || "Erro ao consultar servidor");
      setInfo(await r.json());
    } catch (e) {
      setError(e.message);
    } finally {
      setLoadingInfo(false);
    }
  }

  useEffect(() => { fetchInfo(); }, []);

  function validateHostname(value) {
    if (!value) return "Campo obrigatório.";
    if (!HOSTNAME_RE.test(value))
      return "Use apenas letras minúsculas, números e hífens. Mínimo 2, máximo 63 caracteres.";
    return null;
  }

  async function handleChange(e) {
    e.preventDefault();
    setChangeResult(null);
    setRestoreResult(null);
    setError(null);

    const v = validateHostname(newHostname);
    if (v) { setValidationError(v); return; }
    setValidationError(null);

    setWorking(true);
    try {
      const r = await fetch(`${API}/hostname/change`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ new_hostname: newHostname }),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.detail || "Falha ao alterar hostname");
      setChangeResult(data);
      setNewHostname("");
      await fetchInfo();
    } catch (e) {
      setError(e.message);
    } finally {
      setWorking(false);
    }
  }

  async function handleRestore() {
    setChangeResult(null);
    setRestoreResult(null);
    setError(null);
    setWorking(true);
    try {
      const r = await fetch(`${API}/hostname/restore`, { method: "POST" });
      const data = await r.json();
      if (!r.ok) throw new Error(data.detail || "Falha ao restaurar hostname");
      setRestoreResult(data);
      await fetchInfo();
    } catch (e) {
      setError(e.message);
    } finally {
      setWorking(false);
    }
  }

  // ── Render ───────────────────────────────────────────────────────────────
  return (
    <div className="p-6 max-w-3xl mx-auto space-y-6">
      <h1 className="text-2xl font-semibold">SSH — Administração Remota</h1>

      {error && (
        <p className="text-red-600 text-sm bg-red-50 border border-red-200 rounded p-3">{error}</p>
      )}

      {/* Informações do servidor */}
      <section className="bg-white rounded-xl shadow p-6 space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="font-medium text-gray-700">Identificação do servidor</h2>
          <button
            onClick={fetchInfo}
            disabled={loadingInfo}
            className="text-sm text-blue-600 hover:underline disabled:opacity-50"
          >
            {loadingInfo ? "Consultando…" : "↺ Atualizar"}
          </button>
        </div>

        {info ? (
          info.status === "unreachable" ? (
            <p className="text-yellow-700 bg-yellow-50 border border-yellow-200 rounded p-3 text-sm">
              Servidor inacessível — estado desconhecido.
            </p>
          ) : (
            <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm">
              <InfoRow label="Hostname"        value={info.hostname} />
              <InfoRow label="Usuário remoto"  value={info.user}     />
              <InfoRow label="Uptime"          value={info.uptime}   />
              <InfoRow label="Kernel"          value={info.kernel}   />
            </dl>
          )
        ) : loadingInfo ? (
          <p className="text-gray-400 text-sm">Carregando…</p>
        ) : null}
      </section>

      {/* Alteração de hostname */}
      <section className="bg-white rounded-xl shadow p-6 space-y-4">
        <h2 className="font-medium text-gray-700">Alterar hostname</h2>

        <form onSubmit={handleChange} className="flex flex-col gap-3">
          {info && info.hostname && (
            <p className="text-sm text-gray-500">
              Hostname atual: <span className="font-mono font-medium">{info.hostname}</span>
            </p>
          )}
          <div>
            <input
              type="text"
              placeholder="Novo hostname (ex.: debian-redes)"
              value={newHostname}
              onChange={(e) => {
                setNewHostname(e.target.value.toLowerCase());
                setValidationError(null);
              }}
              className="w-full border rounded px-3 py-2 text-sm font-mono"
            />
            {validationError && (
              <p className="text-red-500 text-xs mt-1">{validationError}</p>
            )}
            <p className="text-xs text-gray-400 mt-1">
              Apenas letras minúsculas, números e hífens · 2 a 63 caracteres
            </p>
          </div>

          <div className="flex gap-3">
            <button
              type="submit"
              disabled={working || !newHostname}
              className="bg-blue-600 text-white px-4 py-2 rounded hover:bg-blue-700
                         disabled:opacity-50 text-sm font-medium"
            >
              {working ? "Aguarde…" : "Alterar"}
            </button>

            {/* Botão Restaurar — ativo após qualquer alteração bem-sucedida na sessão */}
            {changeResult && (
              <button
                type="button"
                onClick={handleRestore}
                disabled={working}
                className="border border-gray-300 px-4 py-2 rounded hover:bg-gray-50
                           disabled:opacity-50 text-sm text-gray-700"
              >
                Restaurar ({changeResult.previous})
              </button>
            )}
          </div>
        </form>

        {/* Resultado da alteração */}
        {changeResult && (
          <div className="bg-green-50 border border-green-200 rounded p-3 text-sm space-y-1">
            <p className="font-semibold text-green-700">✓ Hostname alterado com sucesso</p>
            <p>Anterior: <span className="font-mono">{changeResult.previous}</span></p>
            <p>Novo: <span className="font-mono font-medium">{changeResult.new}</span></p>
          </div>
        )}

        {/* Resultado da restauração */}
        {restoreResult && (
          <div className="bg-blue-50 border border-blue-200 rounded p-3 text-sm space-y-1">
            <p className="font-semibold text-blue-700">✓ Hostname restaurado</p>
            <p>Valor restaurado: <span className="font-mono">{restoreResult.restored}</span></p>
            <p>Confirmação do servidor: <span className="font-mono">{restoreResult.current}</span></p>
          </div>
        )}
      </section>
    </div>
  );
}

function InfoRow({ label, value }) {
  return (
    <>
      <dt className="text-gray-500">{label}</dt>
      <dd className="font-mono">{value ?? "—"}</dd>
    </>
  );
}
