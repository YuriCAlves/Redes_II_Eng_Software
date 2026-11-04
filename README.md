# Console N2 — Administração de Serviços (Redes II)

Console web (FastAPI + React + SQLite) que roda no Debian (`192.168.100.10`) e é
acessado pelo navegador do Ubuntu (`192.168.100.20`).

**Estado atual: Checkpoint 1** — estrutura, login com sessão, 3 perfis
(`leitor` < `operador` < `admin`), rotas protegidas no backend e menu com as 6 abas.

## Estrutura

```
console_n2/
├── backend/
│   ├── app/
│   │   ├── main.py          # app FastAPI + serve o build do React
│   │   ├── config.py        # configuração por variáveis de ambiente
│   │   ├── db.py            # SQLite (users, sessions)
│   │   ├── security.py      # Argon2id + sessões no servidor
│   │   ├── deps.py          # current_user / require_role (autorização)
│   │   └── routers/         # auth.py e modules.py (esqueletos dos módulos)
│   ├── tests/test_auth.py
│   ├── create_user.py       # cria/atualiza usuários
│   └── requirements.txt
└── frontend/                # React + Vite
```

## Pré-requisitos

- **Máquina com internet (build):** Node 18+ e npm; Python 3.11+.
- **Debian (demo, sem internet):** Python 3.11+ (Debian 13 já traz) e `python3-venv`.

## 1. Preparar os pacotes (com internet)

```bash
# Frontend: gera frontend/dist
cd frontend
npm install
npm run build

# Backend: baixa as wheels para instalar offline no Debian
cd ../backend
pip download -r requirements.txt -d wheels/
```

Copie o projeto (incluindo `frontend/dist` e `backend/wheels`) para o Debian, por exemplo:

```bash
scp -r console_n2 yuri@192.168.100.10:~/
```

> Use a mesma arquitetura/versão de Python nas duas máquinas para as wheels serem compatíveis.

## 2. Instalar no Debian (offline)

```bash
cd ~/console_n2/backend
python3 -m venv .venv
. .venv/bin/activate
pip install --no-index --find-links=wheels -r requirements.txt
```

## 3. Criar usuários

```bash
python create_user.py admin1   admin     # a senha é pedida de forma oculta
python create_user.py oper1    operador
python create_user.py leitor1  leitor
```

## 4. Executar

```bash
cd ~/console_n2/backend
. .venv/bin/activate
uvicorn app.main:app --host 192.168.100.10 --port 8000
```

Acesse, pelo Ubuntu: `http://192.168.100.10:8000`

> Se for usar Virtual Host no Apache como proxy reverso, aponte-o para `127.0.0.1:8000`
> e rode o uvicorn com `--host 127.0.0.1`.

## Variáveis de ambiente (nenhum valor real no repositório)

| Variável | Padrão | Descrição |
|---|---|---|
| `CONSOLE_DB_PATH` | `backend/console.db` | Caminho do SQLite |
| `CONSOLE_SESSION_TTL` | `3600` | Duração da sessão (s) |
| `CONSOLE_COOKIE_SECURE` | `0` | `1` apenas se servir via HTTPS |
| `CONSOLE_FRONTEND_DIST` | `frontend/dist` | Pasta do build do React |

## Desenvolvimento

```bash
# terminal 1
cd backend && . .venv/bin/activate && uvicorn app.main:app --reload
# terminal 2
cd frontend && npm run dev      # http://localhost:5173 (proxy /api -> :8000)
```

## Testes (critério de aceitação do CP1)

```bash
cd backend && . .venv/bin/activate && python -m pytest -v
```

Cobrem: hash Argon2 no banco, login/logout, 401 sem sessão, e **leitor acessando
rota de escrita pela URL → HTTP 403**.

Teste manual equivalente (logado como `leitor1`, no navegador):
`http://192.168.100.10:8000/api/files/upload` → `{"detail":"Permissão insuficiente"}` (403).

## Matriz de permissões (CP1)

| Rota | leitor | operador | admin |
|---|:-:|:-:|:-:|
| `GET /api/metrics`, `/services`, `/files`, `/email/inbox`, `/ssh/info`, `/logs` | ✅ | ✅ | ✅ |
| `/api/files/upload`, `/api/email/send` | 403 | ✅ | ✅ |
| `/api/ssh/hostname/change`, `/restore` | 403 | 403 | ✅ |

Segurança: senhas com Argon2id; sessão em cookie `HttpOnly` + `SameSite=Lax`, token
guardado só como hash no banco; header `X-Requested-With` obrigatório em métodos de
escrita (anti-CSRF); autorização sempre no backend (`require_role`).
