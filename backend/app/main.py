from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from . import config
from .db import init_db
from .routers import audit, auth, dashboard, dhcp, dns, files, logs, mail, ssh


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Console N2 - Administração de Serviços", lifespan=lifespan)

app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(files.router)
app.include_router(mail.router)
app.include_router(ssh.router)
app.include_router(logs.router)
app.include_router(dns.router)
app.include_router(dhcp.router)
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
