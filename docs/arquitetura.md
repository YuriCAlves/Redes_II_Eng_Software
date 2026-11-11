# Arquitetura

![Diagrama](arquitetura.png)

Navegador (Ubuntu) → Backend FastAPI (Debian, porta 8000) → camada de integração → serviços.
O navegador **só** fala com o backend; as credenciais de FTP, e-mail e a chave SSH ficam no
servidor (`backend/.env` e `~/.ssh`), nunca no frontend nem no Git.

```mermaid
flowchart LR
    B["<b>Navegador</b><br/>Ubuntu 192.168.100.20<br/>React: telas, polling 5 s,<br/>notificações"]

    subgraph D["Debian 192.168.100.10"]
        API["<b>Backend FastAPI</b> :8000<br/>login com sessão<br/>perfis admin / operador / leitor<br/>permissão checada em cada rota<br/>SQLite local"]

        C1["<b>Integração</b><br/>/proc + systemctl show<br/>+ urllib (latência)"]
        C2["<b>Integração</b><br/>ftplib (modo passivo)"]
        C3["<b>Integração</b><br/>smtplib + imaplib"]
        C4["<b>Integração</b><br/>paramiko (chave só no backend,<br/>known_hosts obrigatório)"]

        S1["<b>Apache</b> :80<br/>site.empresa.test<br/>publica /srv/portal/arquivos"]
        S2["<b>vsftpd</b> :21<br/>passivo 30000-30010<br/>grava em /srv/portal/arquivos"]
        S3["<b>Postfix</b> :25<br/>SMTP"]
        S4["<b>Dovecot</b> :143<br/>IMAP"]
        S5["<b>OpenSSH</b> :22"]
    end

    B -- "HTTP :8000<br/>cookie HttpOnly<br/>+ X-Requested-With" --> API
    API --> C1 & C2 & C3 & C4
    C1 -- "latência HTTP" --> S1
    C2 -- "upload / listagem / download" --> S2
    C3 -- "envia" --> S3
    C3 -- "lê / APPEND em Enviados" --> S4
    C4 -- "info, hostname, logs" --> S5
```

| Camada | Tecnologia | Responsabilidade |
|---|---|---|
| Navegador | React + Vite + Chart.js | Telas, polling de 5 s, notificações, lista de operações da sessão |
| Backend | FastAPI, SQLite, Argon2id | Login com sessão, perfis, **autorização em cada rota**, orquestração |
| Integração | `/proc`, `systemctl show`, `ftplib`, `smtplib`, `imaplib`, `paramiko` | Falar com cada serviço; validar entradas; classificar erros |
| Serviços | Apache, vsftpd, Postfix, Dovecot, OpenSSH | Os serviços reais do Debian |

Fluxo do upload (CP3): navegador → `POST /api/files/upload` → valida nome/extensão/tamanho →
SHA-256 do original → `ftplib` (STOR em arquivo temporário + RNTO) → RETR e SHA-256 do recuperado →
GET no Apache e SHA-256 do publicado → resposta com os três hashes.
