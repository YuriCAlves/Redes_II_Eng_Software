// Cliente HTTP. O cookie de sessão é HttpOnly (o JS nunca o vê).
// O header X-Requested-With é exigido pelo backend em métodos de escrita (anti-CSRF).

// Avisa a interface quando o backend (Debian) fica inalcançável ou volta.
function emitComm(ok) {
  window.dispatchEvent(new CustomEvent("console:comm", { detail: { ok } }));
}

export async function api(path, { method = "GET", body, form } = {}) {
  const headers = { "X-Requested-With": "console" };
  let payload;
  if (form) {
    payload = form; // FormData: o navegador define o Content-Type com o boundary
  } else if (body) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }

  let res;
  try {
    res = await fetch(`/api${path}`, { method, credentials: "same-origin", headers, body: payload });
  } catch {
    emitComm(false);
    const err = new Error("Sem comunicação com o Debian.");
    err.status = 0;
    err.code = "network";
    throw err;
  }
  emitComm(true);

  let data = null;
  try {
    data = await res.json();
  } catch {
    /* resposta sem corpo JSON */
  }

  if (!res.ok) {
    const d = data?.detail;
    let message = `Erro ${res.status}`;
    if (typeof d === "string") message = d;
    else if (d?.message) message = d.message;
    else if (Array.isArray(d)) message = "Dados inválidos.";
    const err = new Error(message);
    err.status = res.status;
    err.code = d?.code;
    throw err;
  }
  return data;
}
