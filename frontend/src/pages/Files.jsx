import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { canAtLeast, useAuth } from "../auth.jsx";
import { ErrorBox, fmtBytes, fmtDate } from "../components.jsx";
import { useNotify } from "../notify.jsx";

const ALLOWED = ["pdf", "txt", "png", "jpg", "jpeg"];

function HashRow({ label, value, ok }) {
  return (
    <tr>
      <td>{label}</td>
      <td>
        <code className="hash">{value ?? "—"}</code>
      </td>
      <td>{ok === null || ok === undefined ? "—" : ok ? "✅ confere" : "❌ diverge"}</td>
    </tr>
  );
}

export default function Files() {
  const { user, expire } = useAuth();
  const { run } = useNotify();
  const canWrite = canAtLeast(user, "operador"); // só esconde o formulário; o backend é quem bloqueia
  const [files, setFiles] = useState(null);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [overwrite, setOverwrite] = useState(false);
  const [formError, setFormError] = useState("");
  const fileRef = useRef(null);

  const load = useCallback(async () => {
    try {
      const d = await api("/files");
      setFiles(d.files);
      setError(null);
    } catch (e) {
      if (e.status === 401) expire();
      else setError(e);
    }
  }, [expire]);

  useEffect(() => {
    load();
  }, [load]);

  async function submit(e) {
    e.preventDefault();
    setFormError("");
    const f = fileRef.current?.files?.[0];
    if (!f) return setFormError("Escolha um arquivo.");
    const ext = f.name.includes(".") ? f.name.split(".").pop().toLowerCase() : "";
    if (!ALLOWED.includes(ext)) return setFormError(`Extensão não permitida. Use: ${ALLOWED.join(", ")}.`);

    const form = new FormData();
    form.append("file", f);
    form.append("overwrite", overwrite ? "true" : "false");
    setUploading(true);
    setResult(null);
    try {
      const r = await run("Upload FTP", `Upload de ${f.name}`, () => api("/files/upload", { method: "POST", form }), {
        verify: (r) =>
          !r.match_ftp
            ? "o hash recuperado via FTP diverge do original"
            : r.match_http === false
            ? "o hash do arquivo publicado no Apache diverge do original"
            : null,
      });
      setResult(r);
      fileRef.current.value = "";
      load();
    } catch (err) {
      setFormError(err.message);
    } finally {
      setUploading(false);
    }
  }

  return (
    <div className="dash">
      <h2>Arquivos (FTP)</h2>

      {canWrite ? (
        <form className="card form" onSubmit={submit}>
          <h3>Enviar arquivo</h3>
          <label>
            Arquivo ({ALLOWED.join(", ")})
            <input type="file" ref={fileRef} accept={ALLOWED.map((x) => "." + x).join(",")} />
          </label>
          <label className="check">
            <input type="checkbox" checked={overwrite} onChange={(e) => setOverwrite(e.target.checked)} />
            Substituir se já existir
          </label>
          {formError && <div className="alert error">{formError}</div>}
          <div>
            <button type="submit" disabled={uploading}>
              {uploading ? "Enviando via FTP…" : "Enviar"}
            </button>
          </div>
        </form>
      ) : (
        <div className="alert warn">Seu perfil (leitor) pode listar e baixar arquivos, mas não enviar.</div>
      )}

      {result && (
        <section className="card">
          <h3>Resultado da transferência</h3>
          <p>
            <strong>{result.name}</strong> · {fmtBytes(result.size)}
            {result.replaced ? " · substituiu o arquivo existente" : ""}
          </p>
          <table>
            <thead>
              <tr>
                <th>SHA-256</th>
                <th>Valor</th>
                <th>Comparação</th>
              </tr>
            </thead>
            <tbody>
              <HashRow label="Original (antes de enviar)" value={result.sha256_original} ok={true} />
              <HashRow label="Recuperado via FTP" value={result.sha256_ftp} ok={result.match_ftp} />
              <HashRow label="Publicado no Apache (HTTP)" value={result.sha256_http} ok={result.match_http} />
            </tbody>
          </table>
          {result.http_error && <div className="alert warn">Hash HTTP indisponível: {result.http_error}.</div>}
          <p>
            Link publicado:{" "}
            <a href={result.url} target="_blank" rel="noreferrer">
              {result.url}
            </a>
          </p>
        </section>
      )}

      <section className="card">
        <div className="dash-head">
          <h3>Arquivos no servidor</h3>
          <button onClick={load}>Atualizar</button>
        </div>
        <ErrorBox error={error} />
        {files && files.length === 0 && <p className="muted">Nenhum arquivo no diretório remoto.</p>}
        {files && files.length > 0 && (
          <table>
            <thead>
              <tr>
                <th>Nome</th>
                <th>Tamanho</th>
                <th>Modificado</th>
                <th>Ações</th>
              </tr>
            </thead>
            <tbody>
              {files.map((f) => (
                <tr key={f.name}>
                  <td>{f.name}</td>
                  <td className="muted">{fmtBytes(f.size)}</td>
                  <td className="muted">{fmtDate(f.modified)}</td>
                  <td className="actions">
                    <a href={`/api/files/download?name=${encodeURIComponent(f.name)}`}>Baixar</a>
                    <a href={f.url} target="_blank" rel="noreferrer">
                      Link HTTP
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
