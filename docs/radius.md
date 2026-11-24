# Serviço exclusivo — Autenticação RADIUS (Incremento 4, opção 5)

Registro, decisão (ADR-005) e hipótese de desempenho estão em `docs/plano-extensoes.md`, seção 5.
Este documento traz a arquitetura, a instalação em checkpoints, a validação e o experimento.

## 1. Arquitetura

| Peça | Onde | Papel |
|---|---|---|
| FreeRADIUS 3 | Debian `192.168.100.10`, UDP 1812 (auth) e 1813 (acct) | Autentica, autoriza (políticas) e contabiliza |
| `backend/app/radius_client.py` + aba **RADIUS** | Console | Gerencia clientes NAS, políticas e usuários; mostra autenticações e sessões; testa autenticação |
| `/etc/freeradius/3.0/console-n2/` | Debian | `console-n2.json` (estado), `clients.conf` e `users` gerados — incluídos uma única vez na configuração |
| `/usr/local/sbin/console-n2-radius-read` | Debian (root, só leitura) | Entrega ao console o `radius.log` (aceitas/negadas) e os arquivos `detail` (contabilização) |
| Ubuntu `192.168.100.20` | NAS e cliente | `pam_radius_auth` no SSH: o login real passa pelo RADIUS e gera contabilização |

**Fluxo de uma alteração:** o console lê o estado atual → valida a entrada (regex estritas; senha só
como hash SSHA2-256 ou SHA-512 crypt) → grava estado + arquivos gerados → `freeradius -XC` como
`freerad` → se recusado, **reinstala a versão anterior na mesma operação** e responde 400 → se
aceito, guarda a versão anterior em `config_versions` e reinicia o serviço. O serviço em execução
nunca carrega uma configuração inválida. Os arquivos de passagem (com segredos) são apagados ao
final.

**Políticas:** cada usuário pertence a uma política (`padrao` 1 h, `visitante` 15 min, `suspenso`
nega). Uma política envia `Session-Timeout`, `Idle-Timeout` e `Reply-Message` no Access-Accept ou
força `Auth-Type := Reject`. Bloquear um usuário também gera `Auth-Type := Reject`.

**Segurança:** escrita só para admin; leitor vê configuração (sem segredos), autenticações e sessões;
operador pode testar uma autenticação. O segredo de um NAS só aparece para admin, com auditoria.
Senhas nunca são guardadas, registradas ou passadas na linha de comando: o teste do console envia a
senha pela entrada padrão do `radclient` e o segredo por arquivo temporário. Pedidos de IPs que não
são NAS cadastrados são descartados e aparecem como "NAS não cadastrado".

## 2. Checkpoints de instalação

### Checkpoint R1 — FreeRADIUS preparado para o console

No Debian, na pasta do repositório atualizado (`git pull`):

```bash
sudo deploy/freeradius/instalar.sh
```

O script (idempotente) instala `freeradius` e `freeradius-utils`, cria `/etc/freeradius/3.0/console-n2`
(`root:freerad`, 750), inclui os arquivos do console em `clients.conf` e em
`mods-config/files/authorize`, liga `auth = yes` no log (sem registrar senhas), troca o segredo
`testing123` do cliente `localhost` por um aleatório, instala o leitor de logs e valida com
`freeradius -XC`.

**Verificação:** a saída termina com `Configuração do FreeRADIUS: OK` e `active (running)`. Anote a
linha `CONSOLE_RADIUS_TEST_SECRET=...` que o script mostrou.

### Checkpoint R2 — Permissões e `.env`

```bash
cd deploy/sudoers && sudo visudo -cf console-n2-servicos && sudo install -m 440 console-n2-servicos /etc/sudoers.d/ && cd ../..
echo 'CONSOLE_RADIUS_TEST_SECRET=cole-o-valor-do-R1' >> backend/.env
sudo -k
sudo -n /usr/bin/cat /etc/freeradius/3.0/console-n2/console-n2.json | head -3
sudo -n -u freerad /usr/sbin/freeradius -XC | tail -1
sudo -n /usr/local/sbin/console-n2-radius-read auth | tail -2
```

**Verificação:** os três últimos comandos rodam sem pedir senha; o segundo imprime
`Configuration appears to be OK`. Reinicie o backend.

### Checkpoint R3 — Cliente NAS e usuário pelo console

Na aba **RADIUS**, como `admin1`:

1. **Clientes NAS:** cadastre `ubuntu` com IP `192.168.100.20`, **sem** marcar "Exigir
   Message-Authenticator" (o `libpam-radius-auth` do Ubuntu pode não enviar esse atributo; marque
   só para NAS que enviam, como `radclient`/`radtest` 3.2 e `pam_radius` 3.0+). Clique em
   **Ver segredo** e copie.
2. **Usuários RADIUS:** crie `maria` (senha de 8+ caracteres, política `padrao`).
3. **Autenticações recentes:** em "Testar usuário", envie `maria` com a senha certa e depois com uma
   errada.

**Verificação:** o teste mostra **aceita** e depois **negada**; as duas linhas aparecem na tabela
com o motivo da recusa; a aba **Auditoria** tem os eventos `client.create`, `user.create`,
`client.secret.view` e `test` (sem senha nem segredo).

### Checkpoint R4 — Ubuntu como NAS (login SSH autenticado pelo RADIUS)

No Ubuntu:

```bash
sudo apt install -y libpam-radius-auth freeradius-utils
echo '192.168.100.10  COLE-O-SEGREDO-DO-NAS  3' | sudo tee /etc/pam_radius_auth.conf >/dev/null
sudo chmod 600 /etc/pam_radius_auth.conf
sudo adduser --disabled-password --gecos "" maria     # conta local SEM senha: quem autentica é o RADIUS
sudo cp /etc/pam.d/sshd /etc/pam.d/sshd.antes-radius
sudo sed -i '1i auth sufficient pam_radius_auth.so' /etc/pam.d/sshd
echo 'session optional pam_radius_auth.so' | sudo tee -a /etc/pam.d/sshd >/dev/null
grep -E '^(UsePAM|PasswordAuthentication|KbdInteractiveAuthentication)' /etc/ssh/sshd_config
```

`UsePAM yes` e `PasswordAuthentication yes` precisam valer (em
`/etc/ssh/sshd_config.d/*.conf` também). Se mudar algo: `sudo systemctl restart ssh`.

**Verificação:** do Debian, `ssh maria@192.168.100.20` com a senha cadastrada no console entra; ao
sair (`exit`), a aba RADIUS mostra a autenticação **aceita** vinda do NAS `ubuntu` e uma sessão
**encerrada** com duração. Para desfazer: `sudo cp /etc/pam.d/sshd.antes-radius /etc/pam.d/sshd`.

## 3. Validação (seção 10)

| Teste | Ação | Efeito esperado |
|---|---|---|
| T-EXT-01 | Login SSH de `maria` no Ubuntu; no console, **Bloquear** `maria`; novo login | 1º aceito com sessão contabilizada; depois do bloqueio, **negado** (`Auth-Type := Reject`); tudo na auditoria |
| T-EXT-01b | Mover `maria` para `visitante` e logar | Access-Accept com `Session-Timeout = 900` (veja com `radtest maria <senha> 192.168.100.10 0 <segredo>` no Ubuntu) |
| T-EXT-02 | Falha controlada: no Ubuntu, `radtest maria <senha> 192.168.100.10 0 segredo-errado` e um login SSH com o segredo errado em `/etc/pam_radius_auth.conf` | Os dois falham; o `radtest` (que envia Message-Authenticator) aparece como **Segredo do NAS incorreto** vindo de 192.168.100.20; o `pam_radius` antigo, sem esse atributo, aparece como negada por senha que não confere. Corrija o segredo e repita: aceito |
| T-EXT-02b | Configuração inválida: edite à mão `console-n2.json` com um hash quebrado e tente criar um usuário | Console responde que a configuração é ilegível ou foi recusada; o FreeRADIUS continua atendendo com a versão anterior; **Restaurar versão anterior** devolve o estado |
| T-SEG | `oper1` tenta criar usuário pela URL da API | 403 registrado na auditoria |

## 4. Experimento de desempenho (seção 8.5)

**Hipótese:** o custo do esquema de hash domina a autenticação PAP sob carga — com SHA-512 crypt
(5000 rodadas) a vazão máxima cai muito em relação a SSHA2-256, enquanto o tamanho da base (100 ×
5000 usuários) tem efeito pequeno.

**Preparação:** na aba RADIUS, **Experimento → Gerar base** com 100 usuários SSHA2-256 e anote a
senha mostrada. No Ubuntu, guarde o segredo do NAS num arquivo (`nano ~/nas.secret; chmod 600
~/nas.secret`) e copie o script: `scp yuri@192.168.100.10:Documentos/Redes_II_Eng_Software/scripts/radius_carga.py .`

**Carga controlada** (1000 pedidos por rodada, 3 rodadas, 1/10/50 trabalhadores em paralelo):

```bash
python3 radius_carga.py --secret-file ~/nas.secret --password 'SENHA-DA-BASE' --users 100 \
  --requests 1000 --parallel 1 10 50 --label "ssha2-100" --csv resultados-radius.csv
```

Repita gerando cada base nova pelo console (a nova substitui a anterior):
`ssha2-5000` (5000 usuários SSHA2-256), `crypt-100` e `crypt-5000` (SHA-512 crypt, 5000 rodadas).
Durante cada rodada, no Debian, `top -b -n 5 -d 1 -p $(pgrep -d, freeradius)` registra a CPU.

**Métricas** (CSV): vazão (req/s), latência média, p50/p95/p99, aceitos, negados, perdidos (%).
**Comparação:** 2 esquemas × 2 tamanhos × 3 paralelismos. **Limites a observar:** o paralelismo
em que a latência p95 dispara ou surgem perdas (timeout de 2 s) e o que limita (CPU do Debian; no
crypt, a verificação de senha). Pedidos com senha errada esperam de propósito ~1 s
(`reject_delay` do FreeRADIUS) — por isso a carga usa a senha certa; rode uma rodada pequena com
senha errada para mostrar esse efeito.

Ao final, **Remover base** apaga os usuários de carga.
