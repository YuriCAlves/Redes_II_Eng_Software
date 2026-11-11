// CP3 — Módulo FTP/Arquivos
// React — upload com verificação SHA-256, listagem e download

import { useState, useEffect, useCallback } from "react";

const API = "/api/files";
const ALLOWED = ["pdf", "txt", "png", "jpg", "jpeg"];

export default function FilesPage() {
  const [files, setFiles]       = useState([]);
  const [loading, setLoading]   = useState(false);
  const [uploading, setUploading] = useState(false);
  const [result, setResult]     = useState(null);   // resultado do último upload
  const [error, setError]       = useState(null);
  const [selectedFile, setSelectedFile] = useState(null);

  const fetchList = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await fetch(`${API}/list`);
      if (!r.ok) throw new Error((await r.json()).detail || "Erro ao listar arquivos");
      const data = await r.json();
      setFiles(data.files);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchList(); }, [fetchList]);

  // ── Validação client-side ────────────────────────────────────────────────
  function validateFile(file) {
    if (!file) return "Nenhum arquivo selecionado.";
    const ext = file.name.split(".").pop().toLowerCase();
    if (!ALLOWED.includes(ext))
      return `Extensão .${ext} não permitida. Use: ${ALLOWED.join(", ")}`;
    if (file.size > 10 * 1024 * 1024)
      return "Arquivo maior que 10 MB.";
    return null;
  }

  async function handleUpload(e) {
    e.preventDefault();
    setResult(null);
    setError(null);

    const validationError = validateFile(selectedFile);
    if (validationError) { setError(validationError); return; }

    const formData = new FormData();
    formData.append("file", selectedFile);

    setUploading(true);
    try {
      const r = await fetch(`${API}/upload`, { method: "POST", body: formData });
      const data = await r.json();
      if (!r.ok) throw new Error(data.detail || "Falha no upload");
      setResult(data);
      await fetchList();             // atualiza listagem após upload
      setSelectedFile(null);
      e.target.reset();
    } catch (e) {
      setError(e.message);
    } finally {
      setUploading(false);
    }
  }

  function handleDownload(filename) {
    window.location.href = `${API}/download/${encodeURIComponent(filename)}`;
  }

  // ── Render ───────────────────────────────────────────────────────────────
  return (
    <div className="p-6 space-y-8 max-w-4xl mx-auto">
      <h1 className="text-2xl font-semibold">Arquivos / FTP</h1>

      {/* Upload */}
      <section className="bg-white rounded-xl shadow p-6 space-y-4">
        <h2 className="text-lg font-medium">Enviar arquivo</h2>
        <form onSubmit={handleUpload} className="flex flex-col gap-3">
          <input
            type="file"
            accept=".pdf,.txt,.png,.jpg,.jpeg"
            onChange={(e) => setSelectedFile(e.target.files[0] || null)}
            className="border rounded p-2 text-sm"
          />
          <p className="text-xs text-gray-500">
            Permitido: {ALLOWED.join(", ")} · Máx. 10 MB
          </p>
          <button
            type="submit"
            disabled={uploading || !selectedFile}
            className="self-start bg-blue-600 text-white px-4 py-2 rounded hover:bg-blue-700
                       disabled:opacity-50 text-sm font-medium"
          >
            {uploading ? "Enviando…" : "Enviar"}
          </button>
        </form>

        {/* Resultado do upload */}
        {result && (
          <div className={`rounded-lg p-4 text-sm space-y-1 ${
            result.integrity_ok ? "bg-green-50 border border-green-200" : "bg-red-50 border border-red-200"
          }`}>
            <p className="font-semibold">
              {result.integrity_ok ? "✓ Integridade verificada" : "✗ Hash diverge — arquivo pode estar corrompido"}
            </p>
            <p><span className="font-mono text-xs">SHA-256 original:&nbsp;</span>
              <span className="font-mono text-xs break-all">{result.sha256_original}</span></p>
            <p><span className="font-mono text-xs">SHA-256 recuperado:</span>
              <span className="font-mono text-xs break-all">{result.sha256_recovered}</span></p>
            <a
              href={result.http_url}
              target="_blank"
              rel="noreferrer"
              className="text-blue-600 underline text-xs"
            >
              Acessar via HTTP ↗
            </a>
          </div>
        )}

        {error && (
          <p className="text-red-600 text-sm bg-red-50 border border-red-200 rounded p-3">
            {error}
          </p>
        )}
      </section>

      {/* Listagem */}
      <section className="bg-white rounded-xl shadow p-6">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-medium">Arquivos no servidor</h2>
          <button
            onClick={fetchList}
            disabled={loading}
            className="text-sm text-blue-600 hover:underline disabled:opacity-50"
          >
            {loading ? "Carregando…" : "↺ Atualizar"}
          </button>
        </div>

        {files.length === 0 && !loading ? (
          <p className="text-gray-500 text-sm">Nenhum arquivo encontrado.</p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b text-left text-gray-500 text-xs uppercase">
                <th className="pb-2 pr-4">Nome</th>
                <th className="pb-2 pr-4">Tamanho</th>
                <th className="pb-2 pr-4">Data</th>
                <th className="pb-2">Ações</th>
              </tr>
            </thead>
            <tbody>
              {files.map((f) => (
                <tr key={f.name} className="border-b last:border-0 hover:bg-gray-50">
                  <td className="py-2 pr-4 font-mono">{f.name}</td>
                  <td className="py-2 pr-4 text-gray-600">{f.size_human}</td>
                  <td className="py-2 pr-4 text-gray-600">{f.date}</td>
                  <td className="py-2 flex gap-3">
                    <button
                      onClick={() => handleDownload(f.name)}
                      className="text-blue-600 hover:underline"
                    >
                      Baixar
                    </button>
                    <a
                      href={f.http_url}
                      target="_blank"
                      rel="noreferrer"
                      className="text-gray-500 hover:underline"
                    >
                      HTTP ↗
                    </a>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
