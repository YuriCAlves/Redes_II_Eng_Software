from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from . import config, lab
from .db import init_db
from .routers import audit, auth, dashboard, dhcp, dns, files, lab as lab_router, logs, mail, radius, ssh


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    # Fila do laboratório: só com o agente configurado. A 1ª volta reconcilia
    # banco e host (instâncias que estavam no meio de uma tarefa continuam).
    if config.LAB_WORKER and config.LAB_AGENT_URL:
        lab.start_worker()
    yield
    lab.stop_worker()


app = FastAPI(title="Console N2 - Administração de Serviços", lifespan=lifespan)

app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(files.router)
app.include_router(mail.router)
app.include_router(ssh.router)
app.include_router(logs.router)
app.include_router(dns.router)
app.include_router(dhcp.router)
app.include_router(lab_router.router)
app.include_router(radius.router)
app.include_router(audit.router)


@app.get("/api/health")
def health():
    return {"status": "ok"}


# ---- Serve o frontend React compilado (SPA) ----
_dist = config.FRONTEND_DIST


@app.get("/{full_path:path}", include_in_schema=False)
def spa(full_path: str):
    if full_path.startswith("api/"):
        raise HTTPException(status_code=404)
    if not _dist.exists():
        raise HTTPException(
            status_code=404,
            detail="Frontend não compilado. Rode 'npm run build' em frontend/.",
        )
    candidate = (_dist / full_path).resolve()
    # Impede path traversal para fora de dist/
    if full_path and candidate.is_file() and _dist.resolve() in candidate.parents:
        return FileResponse(candidate)
    return FileResponse(_dist / "index.html")
