import { useCallback, useEffect, useState } from "react";
import { api } from "../api.js";
import { canAtLeast, useAuth } from "../auth.jsx";
import { ErrorBox, fmtDate } from "../components.jsx";
import { useNotify } from "../notify.jsx";

const q = encodeURIComponent;

function SendStatus({ r }) {
  const smtpOk = r.smtp.ok;
  return (
    <div className="card status-box">
      <h3>Resultado do envio</h3>
      <div className={`alert ${smtpOk ? "ok" : "error"}`}>
        <strong>SMTP:</strong> {smtpOk ? "mensagem aceita pelo servidor." : `envio falhou — ${r.smtp.error}`}
      </div>
      {smtpOk && (
        <div className={`alert ${r.imap.ok ? "ok" : "warn"}`}>
          <strong>IMAP (Enviados):</strong>{" "}
          {r.imap.ok
            ? "cópia salva na pasta Enviados."
            : `a mensagem FOI enviada, mas não foi salva em Enviados — ${r.imap.error} Não reenvie.`}
        </div>
      )}
    </div>
  );
}

export default function Email() {
  const { user, expire } = useAuth();
  const { run } = useNotify();
  const canSend = canAtLeast(user, "operador");
  const [accounts, setAccounts] = useState([]);
  const [account, setAccount] = useState("");
  const [view, setView] = useState("inbox"); // inbox | sent | compose
  const [list, setList] = useState(null);
  const [listError, setListError] = useState(null);
  const [open, setOpen] = useState(null);
  const [openError, setOpenError] = useState(null);
  const [form, setForm] = useState({ to: "", subject: "", body: "" });
  const [sending, setSending] = useState(false);
  const [sendResult, setSendResult] = useState(null);
  const [formError, setFormError] = useState("");

  useEffect(() => {
    api("/email/accounts")
      .then((d) => {
        setAccounts(d.accounts);
        setAccount(d.accounts[0] || "");
      })
      .catch((e) => (e.status === 401 ? expire() : setListError(e)));
  }, [expire]);

  const load = useCallback(async () => {
    if (!account || view === "compose") return;
    setList(null);
    setOpen(null);
    setListError(null);
    try {
      const d = await api(`/email/messages?account=${q(account)}&folder=${view}`);
      setList(d.messages);
    } catch (e) {
      if (e.status === 401) expire();
      else setListError(e);
    }
  }, [account, view, expire]);

  useEffect(() => {
    load();
  }, [load]);

  async function openMessage(uid) {
    setOpenError(null);
    setOpen({ uid, loading: true });
    try {
      setOpen(await api(`/email/message?account=${q(account)}&folder=${view}&uid=${uid}`));
    } catch (e) {
      setOpen(null);
      setOpenError(e);
    }
  }

  async function send(e) {
    e.preventDefault();
    setFormError("");
    if (!form.to.trim()) return setFormError("Informe o destinatário.");
    setSending(true);
    setSendResult(null);
    try {
      const r = await run(
        "E-mail",
        `Enviar e-mail para ${form.to.trim()}`,
        () => api("/email/send", { method: "POST", body: { account, ...form, to: form.to.trim() } }),
        {
          verify: (r) =>
            !r.smtp.ok
              ? `envio falhou no SMTP (${r.smtp.error})`
              : !r.imap.ok
              ? "enviado, mas falha ao salvar em Enviados (não reenviar)"
              : null,
        }
      );
      setSendResult(r);
      if (r.smtp.ok) setForm({ to: "", subject: "", body: "" });
    } catch (err) {
      setFormError(err.message);
    } finally {
      setSending(false);
    }
  }

  const tabs = [
    ["inbox", "Recebidas"],
    ["sent", "Enviados"],
    ...(canSend ? [["compose", "Escrever"]] : []),
  ];

  return (
    <div className="dash">
      <div className="dash-head">
        <h2>E-mail</h2>
        <label className="inline">
          Conta
          <select
            value={account}
            onChange={(e) => {
              setAccount(e.target.value);
              setSendResult(null);
            }}
          >
            {accounts.map((a) => (
              <option key={a}>{a}</option>
            ))}
          </select>
        </label>
      </div>

      <div className="subtabs">
        {tabs.map(([key, label]) => (
          <button key={key} className={view === key ? "on" : ""} onClick={() => { setView(key); setOpen(null); }}>
            {label}
          </button>
        ))}
        {view !== "compose" && <button onClick={load}>Atualizar</button>}
      </div>

      {view === "compose" ? (
        <>
          <form className="card form" onSubmit={send}>
            <label>
              De
              <input value={account} readOnly />
            </label>
            <label>
              Para
              <input value={form.to} onChange={(e) => setForm({ ...form, to: e.target.value })} placeholder="suporte@empresa.test" />
            </label>
            <label>
              Assunto
              <input value={form.subject} maxLength={200} onChange={(e) => setForm({ ...form, subject: e.target.value })} />
            </label>
            <label>
              Mensagem
              <textarea rows={8} value={form.body} onChange={(e) => setForm({ ...form, body: e.target.value })} />
            </label>
            {formError && <div className="alert error">{formError}</div>}
            <div>
              <button type="submit" disabled={sending}>
                {sending ? "Enviando…" : "Enviar"}
              </button>
            </div>
          </form>
          {sendResult && <SendStatus r={sendResult} />}
        </>
      ) : (
        <div className="mail-grid">
          <section className="card">
            <ErrorBox error={listError} />
            {!list && !listError && <p className="muted">Carregando…</p>}
            {list && list.length === 0 && <p className="muted">Nenhuma mensagem.</p>}
            {list && list.length > 0 && (
              <table className="clickable">
                <thead>
                  <tr>
                    <th>{view === "sent" ? "Para" : "De"}</th>
                    <th>Assunto</th>
                    <th>Data</th>
                  </tr>
                </thead>
                <tbody>
                  {list.map((m) => (
                    <tr key={m.uid} onClick={() => openMessage(m.uid)} className={open?.uid === m.uid ? "sel" : ""}>
                      <td>{view === "sent" ? m.to : m.from}</td>
                      <td>{m.subject}</td>
                      <td className="muted">{fmtDate(m.date)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
          <section className="card reader">
            <ErrorBox error={openError} />
            {!open && !openError && <p className="muted">Selecione uma mensagem para ler.</p>}
            {open?.loading && <p className="muted">Abrindo…</p>}
            {open && !open.loading && (
              <>
                <h3>{open.subject}</h3>
                <p className="muted small">
                  De: {open.from}
                  <br />
                  Para: {open.to}
                  <br />
                  {fmtDate(open.date)}
                  {open.has_attachments && <> · contém anexos (não exibidos)</>}
                </p>
                <pre className="mail-body">{open.body}</pre>
              </>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
