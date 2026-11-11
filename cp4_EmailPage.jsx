// CP4 — Módulo E-mail (SMTP + IMAP)
// React — composição, INBOX, Enviados e leitura de mensagem

import { useState, useEffect, useCallback } from "react";

const API = "/api/email";
const ACCOUNTS = ["aluno@empresa.test", "suporte@empresa.test"];

export default function EmailPage() {
  const [account, setAccount]     = useState(ACCOUNTS[0]);
  const [tab, setTab]             = useState("inbox"); // "inbox" | "sent" | "compose"
  const [messages, setMessages]   = useState([]);
  const [openMsg, setOpenMsg]     = useState(null);
  const [loading, setLoading]     = useState(false);
  const [sending, setSending]     = useState(false);
  const [sendResult, setSendResult] = useState(null);
  const [error, setError]         = useState(null);

  // Formulário de composição
  const [to, setTo]           = useState("");
  const [subject, setSubject] = useState("");
  const [body, setBody]       = useState("");

  const fetchMessages = useCallback(async () => {
    setLoading(true);
    setError(null);
    setOpenMsg(null);
    setMessages([]);
    const folder = tab === "sent" ? "sent" : "inbox";
    try {
      const r = await fetch(`${API}/${folder}/${encodeURIComponent(account)}`);
      if (!r.ok) throw new Error((await r.json()).detail || "Erro ao carregar mensagens");
      const data = await r.json();
      setMessages(data.messages);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [account, tab]);

  useEffect(() => {
    if (tab !== "compose") fetchMessages();
  }, [tab, account, fetchMessages]);

  async function openMessage(uid) {
    setError(null);
    const folder = tab === "sent" ? "sent/message" : "message";
    try {
      const r = await fetch(`${API}/${folder}/${encodeURIComponent(account)}/${uid}`);
      if (!r.ok) throw new Error((await r.json()).detail || "Erro ao abrir mensagem");
      setOpenMsg(await r.json());
    } catch (e) {
      setError(e.message);
    }
  }

  async function handleSend(e) {
    e.preventDefault();
    setSendResult(null);
    setError(null);
    if (!to || !subject || !body) { setError("Preencha todos os campos."); return; }

    setSending(true);
    try {
      const r = await fetch(`${API}/send`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sender: account, to, subject, body }),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.detail || "Erro ao enviar");
      setSendResult(data);
      if (data.smtp_status === "ok") { setTo(""); setSubject(""); setBody(""); }
    } catch (e) {
      setError(e.message);
    } finally {
      setSending(false);
    }
  }

  // ── Render ───────────────────────────────────────────────────────────────
  return (
    <div className="p-6 max-w-5xl mx-auto space-y-6">
      <div className="flex flex-wrap items-center gap-4">
        <h1 className="text-2xl font-semibold">E-mail</h1>

        {/* Seletor de conta */}
        <select
          value={account}
          onChange={(e) => { setAccount(e.target.value); setTab("inbox"); }}
          className="border rounded px-3 py-1.5 text-sm"
        >
          {ACCOUNTS.map((a) => (
            <option key={a} value={a}>{a}</option>
          ))}
        </select>
      </div>

      {/* Abas */}
      <div className="flex gap-1 border-b">
        {[
          { id: "inbox",   label: "Recebidas" },
          { id: "sent",    label: "Enviados"  },
          { id: "compose", label: "✉ Novo"    },
        ].map(({ id, label }) => (
          <button
            key={id}
            onClick={() => { setTab(id); setOpenMsg(null); setSendResult(null); setError(null); }}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition ${
              tab === id
                ? "border-blue-600 text-blue-600"
                : "border-transparent text-gray-500 hover:text-gray-700"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {error && (
        <p className="text-red-600 text-sm bg-red-50 border border-red-200 rounded p-3">{error}</p>
      )}

      {/* ── Composição ── */}
      {tab === "compose" && (
        <section className="bg-white rounded-xl shadow p-6 space-y-4">
          <h2 className="font-medium text-gray-700">Nova mensagem — remetente: <strong>{account}</strong></h2>
          <form onSubmit={handleSend} className="space-y-3">
            <input
              type="email"
              placeholder="Para"
              value={to}
              onChange={(e) => setTo(e.target.value)}
              className="w-full border rounded px-3 py-2 text-sm"
            />
            <input
              type="text"
              placeholder="Assunto"
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              className="w-full border rounded px-3 py-2 text-sm"
            />
            <textarea
              placeholder="Corpo da mensagem"
              value={body}
              onChange={(e) => setBody(e.target.value)}
              rows={8}
              className="w-full border rounded px-3 py-2 text-sm font-mono"
            />
            <button
              type="submit"
              disabled={sending}
              className="bg-blue-600 text-white px-5 py-2 rounded hover:bg-blue-700
                         disabled:opacity-50 text-sm font-medium"
            >
              {sending ? "Enviando…" : "Enviar"}
            </button>
          </form>

          {/* Resultado separado SMTP / IMAP */}
          {sendResult && (
            <div className="text-sm space-y-1 bg-gray-50 border rounded p-4">
              <StatusLine
                label="SMTP (entrega)"
                status={sendResult.smtp_status}
                error={sendResult.smtp_error}
              />
              <StatusLine
                label="IMAP (Enviados)"
                status={sendResult.imap_status}
                error={sendResult.imap_error}
              />
            </div>
          )}
        </section>
      )}

      {/* ── Lista de mensagens ── */}
      {(tab === "inbox" || tab === "sent") && !openMsg && (
        <section className="bg-white rounded-xl shadow">
          <div className="flex items-center justify-between px-6 py-4 border-b">
            <span className="text-sm text-gray-500">
              {loading ? "Carregando…" : `${messages.length} mensagem(s)`}
            </span>
            <button onClick={fetchMessages} className="text-sm text-blue-600 hover:underline">
              ↺ Atualizar
            </button>
          </div>

          {!loading && messages.length === 0 && (
            <p className="p-6 text-gray-400 text-sm">Nenhuma mensagem.</p>
          )}

          <ul>
            {messages.map((m) => (
              <li
                key={m.uid}
                onClick={() => openMessage(m.uid)}
                className="px-6 py-3 border-b last:border-0 hover:bg-gray-50 cursor-pointer"
              >
                <div className="flex justify-between text-sm">
                  <span className="font-medium truncate max-w-xs">{m.from}</span>
                  <span className="text-gray-400 text-xs shrink-0 ml-2">{m.date}</span>
                </div>
                <p className="text-sm text-gray-700 truncate">{m.subject}</p>
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* ── Leitura ── */}
      {openMsg && (
        <section className="bg-white rounded-xl shadow p-6 space-y-4">
          <button
            onClick={() => setOpenMsg(null)}
            className="text-sm text-blue-600 hover:underline"
          >
            ← Voltar
          </button>
          <div className="space-y-1 text-sm border-b pb-4">
            <p><span className="text-gray-500 w-16 inline-block">De:</span>{openMsg.from}</p>
            <p><span className="text-gray-500 w-16 inline-block">Para:</span>{openMsg.to}</p>
            <p><span className="text-gray-500 w-16 inline-block">Assunto:</span>
              <strong>{openMsg.subject}</strong></p>
            <p><span className="text-gray-500 w-16 inline-block">Data:</span>{openMsg.date}</p>
          </div>
          <pre className="whitespace-pre-wrap font-mono text-sm text-gray-800 leading-relaxed">
            {openMsg.body}
          </pre>
        </section>
      )}
    </div>
  );
}

function StatusLine({ label, status, error }) {
  const ok = status === "ok";
  const skipped = status === "skipped";
  return (
    <p>
      <span className="font-medium">{label}:</span>{" "}
      <span className={ok ? "text-green-600" : skipped ? "text-gray-400" : "text-red-600"}>
        {ok ? "✓ OK" : skipped ? "— (não executado)" : `✗ Falha — ${error}`}
      </span>
    </p>
  );
}
