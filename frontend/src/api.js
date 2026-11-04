// Cliente HTTP. O cookie de sessão é HttpOnly (o JS nunca o vê).
// O header X-Requested-With é exigido pelo backend em métodos de escrita (anti-CSRF).
export async function api(path, { method = "GET", body } = {}) {
  const res = await fetch(`/api${path}`, {
    method,
    credentials: "same-origin",
    headers: {
      "X-Requested-With": "console",
      ...(body ? { "Content-Type": "application/json" } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });

  let data = null;
  try {
    data = await res.json();
  } catch {
    /* resposta sem corpo JSON */
  }

  if (!res.ok) {
    const err = new Error(data?.detail || `Erro ${res.status}`);
    err.status = res.status;
    throw err;
  }
  return data;
}
