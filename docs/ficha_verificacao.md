# Ficha de verificação — N2 Console de Administração de Serviços

Preencha durante a demonstração. Marque `[x]` quando passar e anote data e evidência
(print, saída de comando, hash). Os campos já vêm em branco de propósito: **nenhum resultado
abaixo foi pré-preenchido**.

Testador: ____________________  Data: ___/___/______  Commit: ____________

## 1. Login, sessão e perfis (CP1)

| # | Verificação | Como verificar | Resultado | Evidência |
|---|---|---|---|---|
| 1.1 | Login válido abre o console | Entrar com `admin1` | [ ] | |
| 1.2 | Login inválido é recusado sem revelar o motivo | Senha errada → "Usuário ou senha inválidos" | [ ] | |
| 1.3 | Logout encerra a sessão | Sair e acessar `/api/auth/me` → 401 | [ ] | |
| 1.4 | Sessão expirada pede novo login | Aguardar o TTL ou apagar a sessão | [ ] | |
| 1.5 | Senhas com hash no SQLite | `sqlite3 console.db "select password_hash from users"` começa com `$argon2` | [ ] | |
| 1.6 | **Leitor não executa escrita** | Logado como `leitor1`, abrir `/api/files/upload` → **403** | [ ] | |
| 1.7 | Leitor: demais rotas de escrita | `/api/email/send`, `/api/ssh/hostname/change`, `/restore` → 403 | [ ] | |
| 1.8 | Operador não altera hostname | `oper1` em `/api/ssh/hostname/change` → 403 | [ ] | |
| 1.9 | Sem login, tudo → 401 | `curl http://192.168.100.10:8000/api/metrics` | [ ] | |

## 2. HTTP e métricas (CP2)

| # | Verificação | Como verificar | Resultado | Evidência |
|---|---|---|---|---|
| 2.1 | Cards com dados reais | Comparar CPU/RAM/disco/uptime com `top`, `free -m`, `df -h`, `uptime` | [ ] | |
| 2.2 | Gráfico CPU (azul) / RAM (vermelha) | Gerar carga e ver a curva subir | [ ] | |
| 2.3 | Latência HTTP | Card mostra ms de `site.empresa.test` | [ ] | |
| 2.4 | **Parar o apache2 → status muda em ≤ 5 s** | `sudo systemctl stop apache2` | [ ] | |
| 2.5 | Voltar o apache2 | `sudo systemctl start apache2` → "ativo" | [ ] | |
| 2.6 | Polling 5 s + timestamp | "Última atualização" muda a cada ~5 s | [ ] | |
| 2.7 | Aviso de dados desatualizados (> 15 s) | Derrubar o backend/rede e observar o aviso amarelo | [ ] | |

## 3. FTP (CP3)

| # | Verificação | Como verificar | Resultado | Evidência |
|---|---|---|---|---|
| 3.1 | Upload de PDF pelo formulário | Enviar um PDF | [ ] | |
| 3.2 | Arquivo aparece na listagem (nome, tamanho, data) | Aba Arquivos | [ ] | |
| 3.3 | **Três hashes coincidem** | Painel (original, FTP, HTTP) e `sha256sum` do arquivo baixado pelo link | [ ] | |
| 3.4 | Link HTTP abre o arquivo | `http://site.empresa.test/arquivos/<nome>` | [ ] | |
| 3.5 | Download pelo console | Botão "Baixar"; `sha256sum` confere | [ ] | |
| 3.6 | Extensão não permitida recusada | Enviar `.exe` → erro | [ ] | |
| 3.7 | Tamanho acima do limite recusado | Arquivo > `CONSOLE_MAX_UPLOAD_MB` → erro 413 | [ ] | |
| 3.8 | Nome com `../` recusado | `curl -F "file=@x.pdf;filename=../x.pdf"` → 400 | [ ] | |
| 3.9 | Erro de conexão ≠ erro de transferência | Parar vsftpd → "Falha de conexão com o servidor FTP" | [ ] | |
| 3.10 | Upload real via FTP (não cópia em disco) | `sudo journalctl -u vsftpd` ou `/var/log/vsftpd.log` mostra STOR/RETR | [ ] | |

## 4. E-mail (CP4)

| # | Verificação | Como verificar | Resultado | Evidência |
|---|---|---|---|---|
| 4.1 | Enviar `aluno` → `suporte` | Aba E-mail → Escrever | [ ] | |
| 4.2 | SMTP e IMAP com status separados | Painel mostra as duas linhas | [ ] | |
| 4.3 | Mensagem chega na INBOX de `suporte` | Trocar a conta e abrir Recebidas | [ ] | |
| 4.4 | Abrir e ler uma mensagem | Clicar na linha | [ ] | |
| 4.5 | **Persiste no servidor** | Enviar, fazer logout, novo login, ver em Enviados de `aluno` | [ ] | |
| 4.6 | "Envio falhou no SMTP" distinto | Parar o postfix e tentar enviar | [ ] | |
| 4.7 | "Enviado, mas falha ao salvar" distinto | Simular falha IMAP após o envio; sem reenvio automático | [ ] | |
| 4.8 | Conta fora da lista recusada | `account=root@empresa.test` → 400 | [ ] | |

## 5. SSH (CP5)

| # | Verificação | Como verificar | Resultado | Evidência |
|---|---|---|---|---|
| 5.1 | Identificação do servidor | Hostname, usuário, uptime, SO, kernel e host key | [ ] | |
| 5.2 | Host key confere com o known_hosts | Fingerprint do painel = `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` | [ ] | |
| 5.3 | Host key diferente é recusada | Alterar o `known_hosts` → erro "host key não confere" | [ ] | |
| 5.4 | **Alterar hostname** | Admin informa novo nome; `hostnamectl --static` no Debian mudou | [ ] | |
| 5.5 | **Restaurar** | Botão Restaurar; `hostnamectl --static` voltou ao original | [ ] | |
| 5.6 | Nome inválido recusado | `Maiúscula`, `a;b`, 64+ caracteres, hífen na ponta | [ ] | |
| 5.7 | Chave privada fora do repositório | `git ls-files | grep -i id_` vazio; `grep -r "PRIVATE KEY" .` vazio | [ ] | |

## 6. Logs e notificações (CP6)

| # | Verificação | Como verificar | Resultado | Evidência |
|---|---|---|---|---|
| 6.1 | **vsftpd, 50 linhas → logs reais** | Comparar com `journalctl -u vsftpd -n 50` | [ ] | |
| 6.2 | apache2 lê o `access.log` | Gerar requisições e ver aparecerem | [ ] | |
| 6.3 | Seletor 20 / 50 / 100 | Quantidade respeitada | [ ] | |
| 6.4 | Toast verde / vermelho / amarelo | Sucesso, falha e perda de comunicação | [ ] | |
| 6.5 | **Desligar o Debian → aviso de desatualização, sem valores fictícios** | Cards ficam esmaecidos com "desatualizado há X s" | [ ] | |
| 6.6 | Falha de SSH = "estado desconhecido" | Parar o sshd e abrir Logs → aviso amarelo, não "serviço parado" | [ ] | |
| 6.7 | Lista de operações da sessão | Upload, e-mail e SSH aparecem com status | [ ] | |

## 7. Qualidade geral

| # | Verificação | Como verificar | Resultado | Evidência |
|---|---|---|---|---|
| 7.1 | Testes automatizados passam | `python -m pytest -v` em `backend/` | [ ] | |
| 7.2 | Sem segredos no repositório | `git grep -iE "password|senha" -- ':!*.md' ':!*.example'` sem valores reais | [ ] | |
| 7.3 | README reproduzível | Instalar em máquina limpa seguindo só o README | [ ] | |
| 7.4 | Funciona sem internet | Desconectar e repetir 2.1, 3.1 e 4.1 | [ ] | |
| 7.5 | Histórico Git por checkpoint | `git log --oneline` | [ ] | |
