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
| Incr. 1 | DNS interno (BIND 9): registros, consulta, restauração | ✅ |
| Incr. 2 | DHCP (Kea): reservas, concessões, restauração | ✅ |
| Incr. 3 | Laboratório virtual sob demanda (VirtualBox) | 🔜 ver `docs/plano-extensoes.md` |
| Incr. 4 | Serviço de rede exclusivo | 🔜 ver `docs/plano-extensoes.md` |
| Parte 2 | Auditoria persistente | ✅ (`audit.py`, aba Auditoria) |

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
│   │   ├── dns_client.py      # Incremento 1: zona BIND 9 via SSH
│   │   ├── dhcp_client.py     # Incremento 2: kea-dhcp4.conf + leases via SSH
│   │   ├── audit.py           # Parte 2 (7.3): auditoria persistente + versionamento de config
│   │   └── routers/           # auth, dashboard, files, mail, ssh, logs, dns, dhcp, audit
│   ├── tests/                 # test_auth, test_collectors, test_modules, test_dns, test_dhcp
│   ├── create_user.py         # cria/atualiza usuários
│   ├── .env.example           # modelo de configuração (sem segredos)
│   └── requirements.txt
├── frontend/                  # React + Vite (abas: ..., DNS, DHCP, Auditoria)
├── scripts/registrar_host_key.sh
└── docs/                      # mockup/, arquitetura.md/.png/.mmd, ficha_verificacao.md, plano-extensoes.md
```

## Pré-requisitos no Debian

Python 3.11+ com `python3-venv`, e os serviços já funcionando: Apache (`site.empresa.test`),
vsftpd (passivo 30000-30010), Postfix (:25, sem autenticação), Dovecot (:143), OpenSSH, e para a
Parte 2: `bind9` (`apt install bind9 bind9-utils`) e `kea-dhcp4-server` (`apt install kea-dhcp4-server`).
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

**DNS (incremento 1)** — o console só gerencia um *bloco* dentro do arquivo de zona; o cabeçalho
(TTL/SOA/NS) é escrito uma única vez, à mão. Crie `/etc/bind/db.empresa.test`:

```
$TTL 3600
@       IN  SOA   ns.empresa.test. admin.empresa.test. (
                  2026112301 ; serial (AAAAMMDDnn; o console incrementa a cada publicação)
                  3600       ; refresh
                  900        ; retry
                  604800     ; expire
                  3600 )     ; minimum
@       IN  NS    ns.empresa.test.
ns      IN  A     192.168.100.10
; ===== CONSOLE:INICIO (gerado pelo console, nao editar a mao) =====
site    IN  A     192.168.100.10
mail    IN  A     192.168.100.10
@       IN  MX  10 mail.empresa.test.
; ===== CONSOLE:FIM =====
```

> O serial precisa ser **um número** já na criação do arquivo — o BIND recusa a zona inteira se
> houver texto ali (`not a valid number`). Depois disso o console localiza a linha `; serial` e
> sempre grava um valor maior que o atual.

E a zona reversa `/etc/bind/db.192.168.100` (mesmos marcadores, registros `PTR`):

```
$TTL 3600
@       IN  SOA   ns.empresa.test. admin.empresa.test. (
                  2026112301 ; serial
                  3600       ; refresh
                  900        ; retry
                  604800     ; expire
                  3600 )     ; minimum
@       IN  NS    ns.empresa.test.
; ===== CONSOLE:INICIO (gerado pelo console, nao editar a mao) =====
10  IN  PTR ns.empresa.test.
; ===== CONSOLE:FIM =====
```

Registre as duas zonas em `/etc/named.conf.local` (ou `named.conf.default-zones`) e restrinja a
recursão à rede do laboratório em `named.conf.options` (`allow-recursion { 192.168.100.0/24; };` —
nunca um resolvedor aberto). Confirme com `sudo named-checkconf && sudo systemctl restart bind9`.

As permissões do console para DNS e DHCP (sem sudo irrestrito e sem curingas) estão prontas em
[`deploy/sudoers/console-n2-servicos`](deploy/sudoers/console-n2-servicos) — veja
[Permissões DNS/DHCP](#permissões-dnsdhcp-sudoers) abaixo.

**DHCP (incremento 2)** — o console reescreve `/etc/kea/kea-dhcp4.conf` inteiro a cada alteração de
reserva (sempre validado com `kea-dhcp4 -t` antes de aplicar). O esqueleto pronto está em
[`deploy/kea/kea-dhcp4.conf`](deploy/kea/kea-dhcp4.conf) — instale trocando a interface:

```bash
ip -br addr                                   # anote a interface 192.168.100.x
sed 's/enp0s8/SUA_INTERFACE/' deploy/kea/kea-dhcp4.conf | sudo tee /etc/kea/kea-dhcp4.conf >/dev/null
sudo -u _kea kea-dhcp4 -t /etc/kea/kea-dhcp4.conf   # como _kea: veja a nota abaixo
```

> `sudo kea-dhcp4 -t` (como root) falha com `Unable to open file`: o perfil AppArmor do
> `kea-dhcp4` não dá ao root o poder de ignorar permissões, e `/etc/kea` é `750 _kea:_kea`.
> Por isso o console também valida como `_kea`, numa cópia `kea-dhcp4.candidate.json` em `/etc/kea`.

Conteúdo:

```json
{
  "Dhcp4": {
    "interfaces-config": { "interfaces": ["enp0s8"] },
    "lease-database": { "type": "memfile", "persist": true, "name": "/var/lib/kea/kea-leases4.csv" },
    "valid-lifetime": 3600,
    "subnet4": [
      {
        "id": 1,
        "subnet": "192.168.100.0/24",
        "pools": [{ "pool": "192.168.100.100 - 192.168.100.150" }],
        "option-data": [{ "name": "domain-name-servers", "data": "192.168.100.10" }],
        "reservations": []
      }
    ]
  }
}
```

Use a interface **interna** do laboratório (nunca a NAT) e confirme que nenhum outro DHCP responde
nesse segmento (nem o do hipervisor).

#### Permissões DNS/DHCP (sudoers)

Tudo **no Debian**, dentro da pasta do projeto (o arquivo vem do repositório — não copie regras
de sudoers de PDF ou navegador, porque a quebra de linha corrompe o arquivo):

```bash
sudo install -d -o yuri -g yuri -m 700 /var/lib/console-n2
sudo visudo -cf deploy/sudoers/console-n2-servicos
sudo install -m 440 -o root -g root deploy/sudoers/console-n2-servicos /etc/sudoers.d/
sudo -l -U yuri
```

`/var/lib/console-n2` é onde o console grava a configuração nova para validar com
`named-checkzone` / `kea-dhcp4 -t` antes de aplicar. Os nomes de arquivo ali são fixos, por isso
as regras não precisam de `*` (curinga em argumento de sudoers deixaria o `cp` aceitar caminhos
extras). Se a unidade do Kea não se chamar `kea-dhcp4-server` (`systemctl list-unit-files
'*kea*'`), ajuste a última regra do arquivo **e** `CONSOLE_KEA_SYSTEMD_UNIT` no `.env`.

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
| Consultar DNS/DHCP (zona, reservas, concessões) | ✅ | ✅ | ✅ |
| Criar/remover/restaurar registro DNS ou reserva DHCP | 403 | 403 | ✅ |
| Consultar auditoria | 403 | próprias ações | tudo |

A linha "Criar/remover/restaurar registro DNS ou reserva DHCP" é admin-only porque a seção 7 do
enunciado classifica isso como "alterar configurações de serviços" — mesma categoria do hostname.

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

**Incremento 1 — DNS** *(zona preparada, sudoers DNS, `/etc/hosts` do Ubuntu limpo de entradas de teste)*
1. Como `admin1`, crie um registro A (ex.: `ftp` → `192.168.100.10`) e confirme: `dig @192.168.100.10
   ftp.empresa.test A +noall +answer` no Ubuntu mostra o endereço aplicado.
2. Consulte um nome inexistente pela aba DNS: status deve ser `NXDOMAIN`.
3. Remova o registro criado e confira que a consulta volta a falhar; clique em "Restaurar versão
   anterior" depois de uma edição e confirme que o estado anterior volta.
4. Como `oper1`, tente criar um registro pela aba: deve aparecer 403.

**Incremento 2 — DHCP** *(Kea configurado, sudoers DHCP, nenhum outro DHCP no segmento)*
1. No Ubuntu, renove o IP (`sudo dhclient -r && sudo dhclient enp0s3` ou equivalente do seu
   gerenciador de rede) e confira que o endereço aparece em Concessões.
2. Como `admin1`, crie uma reserva para o MAC do Ubuntu e renove de novo: o IP reservado deve ser
   o entregue (compare com `ip -br addr` no Ubuntu).
3. Tente reservar `192.168.100.10` (IP do Debian): deve ser recusado (400).
4. Restaure a configuração anterior e confirme que a reserva desaparece.

**Auditoria** *(nada extra a configurar)*
1. Faça login/logout e uma alteração de DNS ou DHCP; a aba Auditoria deve mostrar os eventos na
   hora, com usuário, ação, resultado e um identificador de correlação.
2. Como `oper1`, confirme que só aparecem as próprias ações; como `lia`, que a aba retorna 403.

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
- DNS/DHCP: o texto da zona/configuração nunca entra num argumento de shell — é gravado via SFTP e
  só então validado (`named-checkzone` / `kea-dhcp4 -t`) antes de substituir o arquivo real; o
  nome/tipo/valor de cada registro é validado por regex, e IPs de infraestrutura (Debian/Ubuntu)
  nunca podem virar reserva DHCP. Cada alteração guarda a versão anterior (`config_versions`) para
  permitir restauração.
- Auditoria: tabela `audit_log` só recebe INSERT (sem rota de update/delete), e nunca grava senha,
  token, chave ou corpo de mensagem — só usuário, perfil, recurso, ação, resultado e um detalhe curto.

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
| DNS: "zona não tem os marcadores do console" | Arquivo de zona não tem os comentários `CONSOLE:INICIO`/`CONSOLE:FIM` (veja a seção DNS acima) |
| DNS: "named-checkzone recusou a zona" | Registro inválido (confira nome/valor) ou SOA/NS corrompidos no cabeçalho |
| DHCP: "kea-dhcp4 -t recusou a configuração" | Reserva com MAC/IP inválido, ou JSON corrompido no arquivo base |
| `kea-dhcp4 -t`: "Unable to open file" com o arquivo existindo | AppArmor: rode como `sudo -u _kea kea-dhcp4 -t ...` |
| DHCP: "Kea não recarregou (systemctl)" | Nome da unidade diferente: confira com `systemctl list-unit-files '*kea*'` e ajuste `CONSOLE_KEA_SYSTEMD_UNIT` |

## Parte 2 — entrega final (N2)

DNS, DHCP e auditoria persistente já estão implementados (ver tabela no topo). Faltam os dois
incrementos mais pesados — **laboratório virtual sob demanda** (seção 8.3) e o **serviço de rede
exclusivo** (seção 8.4) — cujo plano, decisões de arquitetura e sugestão de serviço estão em
`docs/plano-extensoes.md`.
