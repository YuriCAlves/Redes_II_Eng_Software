# Console N2 — Administração de Serviços (Redes II)

Console web que roda **no próprio Debian** (`192.168.100.10`) e é acessado pelo navegador do
Ubuntu (`192.168.100.20`). Centraliza, numa interface só: login com perfis, métricas do servidor,
arquivos via FTP, e-mail (SMTP/IMAP), SSH e logs.

**Stack:** FastAPI + React (Vite, Chart.js) + SQLite. Sem internet na demonstração: tudo é
empacotado localmente (veja "Instalação offline").

| Checkpoint | Conteúdo | Status no código |
|---|---|---|
| CP1 | Base, login, 3 perfis, rotas protegidas | ✅ |
| CP2 | Dashboard e métricas em tempo real | ✅ |
| CP3 | Arquivos via FTP (upload, listagem, download, SHA-256) | ✅ |
| CP4 | E-mail (SMTP + IMAP, Enviados) | ✅ |
| CP5 | SSH (identificação, alterar/restaurar hostname) | ✅ |
| CP6 | Logs técnicos, notificações, operações da sessão | ✅ |
| CP7 | Mockup, diagrama, ficha de verificação | ✅ em `docs/` |

> "✅" significa que o código existe e a lógica foi coberta por testes automatizados com
> servidores simulados. **A validação contra os serviços reais do Debian é feita por você**, um
> checkpoint por vez, no roteiro abaixo; registre os resultados em `docs/ficha_verificacao.md`.

## Estrutura

```
console_n2/
├── backend/
│   ├── app/
│   │   ├── main.py            # FastAPI + serve o build do React
│   │   ├── config.py          # variáveis de ambiente / .env
│   │   ├── db.py, security.py, deps.py   # SQLite, Argon2id/sessões, autorização
│   │   ├── errors.py          # erros com código estável (frontend distingue)
│   │   ├── collectors.py      # CP2: /proc, systemctl, latência HTTP
│   │   ├── ftp_client.py      # CP3: ftplib
│   │   ├── mail_client.py     # CP4: smtplib/imaplib
│   │   ├── ssh_client.py      # CP5/CP6: paramiko
│   │   └── routers/           # auth, dashboard, files, mail, ssh, logs
│   ├── tests/                 # test_auth, test_collectors, test_modules
│   ├── create_user.py         # cria/atualiza usuários
│   ├── .env.example           # modelo de configuração (sem segredos)
│   └── requirements.txt
├── frontend/                  # React + Vite
├── scripts/registrar_host_key.sh
└── docs/                      # mockup/, arquitetura.md/.png/.mmd, ficha_verificacao.md
```

## Pré-requisitos no Debian

Python 3.11+ com `python3-venv`, e os serviços já funcionando: Apache (`site.empresa.test`),
vsftpd (passivo 30000-30010), Postfix (:25, sem autenticação), Dovecot (:143), OpenSSH.
Para compilar o frontend: Node 18+ e npm (pode ser outra máquina).

## Instalação

### 1. Frontend (precisa de internet uma vez)

```bash
cd frontend
npm install
npm run build          # gera frontend/dist (o backend serve esta pasta)
```

### 2. Backend

```bash
cd backend
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

**Instalação offline** (para a demonstração, sem internet): numa máquina com internet e o
mesmo Python/arquitetura do Debian, rode `pip download -r requirements.txt -d wheels/`, copie
`wheels/` e `frontend/dist` para o Debian e instale com
`pip install --no-index --find-links=wheels -r requirements.txt`.

### 3. Configuração (segredos ficam fora do Git)

```bash
cd backend
cp .env.example .env
chmod 600 .env
nano .env              # preencha as senhas; o .env é lido automaticamente ao iniciar
```

Variáveis principais (todas têm padrão, exceto as senhas e a chave SSH):

| Variável | Padrão | Uso |
|---|---|---|
| `CONSOLE_FTP_PASSWORD` | — (obrigatória no CP3) | Senha do `ftpuser` |
| `CONSOLE_FTP_DIR` | `/arquivos` | Diretório remoto, como o `ftpuser` o enxerga (chroot) |
| `CONSOLE_MAX_UPLOAD_MB` | `10` | Tamanho máximo de upload |
| `CONSOLE_MAIL_PASS_ALUNO` / `_SUPORTE` | — (obrigatórias no CP4) | Senha Linux de cada conta |
| `CONSOLE_SSH_KEY_PATH` | — (obrigatória no CP5/CP6) | Caminho da chave privada (fora do repositório) |
| `CONSOLE_SSH_KEY_PASSPHRASE` | vazio | Só se a chave tiver senha |
| `CONSOLE_SSH_KNOWN_HOSTS` | `backend/known_hosts` | Arquivo de host keys |
| `CONSOLE_SERVICE_HOST` | `192.168.100.10` | IP dos serviços |
| `CONSOLE_SITE_URL` | `http://site.empresa.test/` | Alvo da latência HTTP |
| `CONSOLE_FILES_PUBLIC_URL` | `http://site.empresa.test/arquivos/` | Base dos links públicos |
| `CONSOLE_SESSION_TTL` | `3600` | Duração da sessão (s) |
| `CONSOLE_COOKIE_SECURE` | `0` | `1` somente se servir via HTTPS |

### 4. Preparar o Debian para os módulos

**Nome do site** (backend e navegador precisam resolvê-lo). No Debian e no Ubuntu:

```bash
echo "192.168.100.10 site.empresa.test" | sudo tee -a /etc/hosts
```

**SSH (CP5/CP6)** — o backend conecta no próprio Debian como `yuri`:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_redes_n1 -N ""
cat ~/.ssh/id_ed25519_redes_n1.pub >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys

./scripts/registrar_host_key.sh      # grava backend/known_hosts e mostra os fingerprints: CONFIRA
```

Permissão para alterar o hostname (somente esse comando, sem senha):

```bash
sudo visudo -f /etc/sudoers.d/console-n2
# conteúdo (uma linha):
yuri ALL=(root) NOPASSWD: /usr/bin/hostnamectl set-hostname *
```

Permissão para ler logs (Apache e journal) sem sudo, depois **reinicie o backend**:

```bash
sudo usermod -aG adm,systemd-journal yuri     # vale em um novo login
```

**FTP:** o `ftpuser` precisa de escrita em `/srv/portal/arquivos` e `write_enable=YES` no vsftpd.
Se o chroot do `ftpuser` não faz `/arquivos` apontar para essa pasta, ajuste `CONSOLE_FTP_DIR`.

**IMAP:** se o login falhar, confira no Dovecot `disable_plaintext_auth = no` (rede interna, sem TLS).

### 5. Usuários do console

```bash
python create_user.py admin1  admin       # a senha (mín. 8 caracteres) é pedida sem eco
python create_user.py oper1   operador
python create_user.py leitor1 leitor
```

### 6. Executar

```bash
cd backend && . .venv/bin/activate
uvicorn app.main:app --host 192.168.100.10 --port 8000
```

Acesse, pelo Ubuntu, `http://192.168.100.10:8000`.
Em desenvolvimento: `uvicorn app.main:app --reload` e, em outro terminal, `npm run dev` em
`frontend/` (http://localhost:5173, com proxy de `/api`).

## Perfis e permissões

| Ação | leitor | operador | admin |
|---|:-:|:-:|:-:|
| Ver métricas, serviços, logs, SSH (info) | ✅ | ✅ | ✅ |
| Listar/baixar arquivos; listar/ler e-mails | ✅ | ✅ | ✅ |
| Upload de arquivo; enviar e-mail | 403 | ✅ | ✅ |
| Alterar/restaurar hostname | 403 | 403 | ✅ |

A autorização é feita **no backend** em cada rota (`require_role`); esconder botões no React é
só conveniência. Um leitor que digita a URL de uma rota de escrita recebe **403**.

## Roteiro de validação — um checkpoint por vez

Antes de tudo: `cd backend && python -m pytest -v` (deve passar). Depois siga na ordem; cada
checkpoint tem um critério de aceitação e o que precisa estar configurado.

**CP1 — login e perfis** *(nada extra a configurar)*
1. Entre como `leitor1`; o menu mostra as 6 abas.
2. Abra `http://192.168.100.10:8000/api/files/upload` → deve aparecer **403**.

**CP2 — métricas** *(nada extra; para a latência, `/etc/hosts`)*
1. A aba Visão Geral mostra CPU, memória, discos, uptime, latência, gráfico e serviços.
2. `sudo systemctl stop apache2` → em até **5 s** o apache2 aparece "inativo"; depois `start`.

**CP3 — FTP** *(`CONSOLE_FTP_PASSWORD`, `/etc/hosts`)*
1. Como `oper1`, envie um PDF. O painel mostra os **três SHA-256** (original, FTP, HTTP).
2. Compare com `sha256sum` do arquivo baixado pelo link `http://site.empresa.test/arquivos/<nome>`.
3. Teste `.exe` (recusado), arquivo grande (413) e `sudo systemctl stop vsftpd` (erro de conexão).

**CP4 — e-mail** *(senhas das contas)*
1. Como `oper1`, envie de `aluno@` para `suporte@`; o painel mostra SMTP e IMAP separados.
2. Troque para `suporte@`: a mensagem está em Recebidas. Abra-a.
3. Faça logout/login e veja a mensagem em Enviados de `aluno@` (persistência no servidor).

**CP5 — SSH** *(chave, known_hosts, sudoers)*
1. Como `admin1`: a aba SSH mostra hostname, usuário, uptime, SO e host key.
2. Altere o hostname; no Debian, `hostnamectl --static` mostra o novo nome. Clique em Restaurar:
   o nome original volta.

**CP6 — logs e notificações** *(grupos `adm`/`systemd-journal`)*
1. Logs → `vsftpd` + 50 linhas: aparecem as linhas reais (compare com `journalctl -u vsftpd -n 50`).
2. Derrube a conexão com o Debian: toast amarelo, badge "Sem comunicação" e cards esmaecidos
   com "desatualizado há X s" (nunca valores inventados).

**CP7 — entregáveis:** `docs/mockup/index.html` (abra no navegador), `docs/arquitetura.md`,
`docs/ficha_verificacao.md` (preencher) e este README.

## Testes automatizados

```bash
cd backend && . .venv/bin/activate
python -m pytest -v
```

`test_auth.py` cobre login, hash, 401/403 e o critério do CP1 (precisa de FastAPI);
`test_collectors.py` e `test_modules.py` cobrem métricas, validações, FTP, e-mail, SSH e logs
usando servidores simulados (não tocam nos serviços reais).

## Segurança — resumo

- Senhas com **Argon2id**; sessão em cookie `HttpOnly` + `SameSite=Lax`, token guardado só como hash.
- Header `X-Requested-With` obrigatório em métodos de escrita (anti-CSRF).
- Segredos (FTP, e-mail, chave SSH) só em `backend/.env`/`~/.ssh`; `.env`, `known_hosts` e chaves estão no `.gitignore`.
- FTP: nome de arquivo por lista de caracteres, extensão permitida, limite de tamanho, gravação em
  nome temporário + rename; nada de shell e nada de SFTP.
- SSH: `RejectPolicy` (host key desconhecida/diferente → recusa), comandos predefinidos montados
  por lista de argumentos, hostname validado por `^[a-z0-9][a-z0-9-]{0,61}[a-z0-9]$`.
- E-mail: contas e pastas por lista fixa; assunto sem quebra de linha (anti-injeção de cabeçalhos).
- Mensagens de erro nunca incluem senhas, caminhos de chave ou saídas internas dos serviços.

## Solução de problemas

| Sintoma | Causa provável |
|---|---|
| `Frontend não compilado` | Faltou `npm run build` em `frontend/` |
| 401 em tudo | Sem login naquele navegador/host (cookie é por endereço) |
| FTP: "Credenciais FTP não configuradas" | `CONSOLE_FTP_PASSWORD` ausente no `.env` |
| FTP: "Diretório remoto indisponível" | Ajuste `CONSOLE_FTP_DIR` ao chroot do `ftpuser` |
| Hash HTTP indisponível | `site.empresa.test` não resolve no Debian (`/etc/hosts`) |
| IMAP recusou o login | Senha da conta ou `disable_plaintext_auth` no Dovecot |
| SSH: "host key não confere" | `known_hosts` desatualizado: rode `scripts/registrar_host_key.sh` |
| SSH: "recusou a alteração do hostname" | Regra do sudoers ausente ou diferente |
| Logs: "permissão ou arquivo ausente" | `yuri` fora dos grupos `adm`/`systemd-journal` (e novo login) |

## Parte 2 (fora desta entrega)

Auditoria persistente, controles de iniciar/parar serviços e edição de configurações — marcados
com **PARTE 2** no mockup.
