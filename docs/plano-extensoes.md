# Plano dos incrementos finais (Projeto Final N2 — Parte 2)

Este documento cobre os quatro incrementos obrigatórios da seção 8 do enunciado: o que já está
implementado e testado (DNS, DHCP), e o plano para o que falta (laboratório virtual, serviço
exclusivo), com as decisões de arquitetura por trás de cada escolha.

## 1. Status atual

| Incremento | Status | Onde está o código |
|---|---|---|
| 1 — DNS (BIND 9) | ✅ implementado e testado | `backend/app/dns_client.py`, `routers/dns.py`, `tests/test_dns.py`, `frontend/src/pages/Dns.jsx` |
| 2 — DHCP (Kea) | ✅ implementado e testado | `backend/app/dhcp_client.py`, `routers/dhcp.py`, `tests/test_dhcp.py`, `frontend/src/pages/Dhcp.jsx` |
| Auditoria persistente (7.3) | ✅ implementado | `backend/app/audit.py`, `routers/audit.py`, `frontend/src/pages/Audit.jsx` |
| 3 — Laboratório virtual | ✅ implementado e testado (com agente simulado) | `backend/app/lab.py`, `labvm_client.py`, `routers/lab.py`, `tests/test_lab.py`, `labagent/`, `frontend/src/pages/Lab.jsx`; roteiro em `docs/laboratorio-virtual.md` |
| 4 — Serviço exclusivo (RADIUS) | ✅ implementado e testado (validação do Prof. Matias pendente) | `backend/app/radius_client.py`, `radius_hash.py`, `routers/radius.py`, `tests/test_radius.py`, `frontend/src/pages/Radius.jsx`, `deploy/freeradius/`; roteiro em `docs/radius.md` |

## 2. Decisões de arquitetura (ADRs)

### ADR-001 — Comunicação remota por SSH "self-loopback" com sudoers mínimo, não um agente novo

**Contexto:** o console já roda no próprio Debian e usa SSH de volta para si mesmo (`ssh_client.py`)
para separar o processo web (sem privilégios) de ações que exigem root, usando `sudo -n` com regras
estreitas por comando (padrão usado desde o CP5, para `hostnamectl`).

**Alternativas consideradas:** (a) um agente HTTP local rodando como root, (b) dar sudo irrestrito
ao usuário do backend, (c) reusar o padrão SSH existente.

**Escolha:** (c). **Justificativa:** o enunciado exige "conta técnica de privilégio mínimo" e proíbe
"sudo irrestrito"; reaproveitar `ssh_client.py` (já testado, já com `RejectPolicy` de host key) para
DNS e DHCP evita duplicar lógica de conexão/erro e mantém um único ponto de auditoria de comandos
remotos. **Consequência:** todo comando remoto novo (DNS/DHCP) é uma lista fixa de argumentos —
nunca uma string concatenada com entrada do usuário — igual ao que já existia para hostname/logs.

### ADR-002 — Zona/configuração geridas como "bloco administrado" com marcadores, não um parser completo

**Contexto:** BIND e Kea têm formatos de arquivo diferentes (zona RFC1035 vs. JSON), e um parser
genérico de zona (comentários, diretivas `$ORIGIN`, múltiplos formatos de registro) é um projeto por
si só, com risco real de corromper a zona do SOA.

**Alternativas:** (a) parser completo de zona, (b) um bloco delimitado por marcadores que o console
possui inteiramente, com o cabeçalho (TTL/SOA/NS) escrito uma única vez à mão pelo aluno.

**Escolha:** (b) para o DNS; para o DHCP, como a configuração já é JSON, o console lê/escreve o
objeto inteiro, mexendo só no array `reservations` da sub-rede. **Consequência:** simplicidade e
segurança (o SOA nunca é tocado por um bug de serialização) ao custo de uma preparação manual única
documentada no README; isso é equivalente ao que a seção 8.1 já pede ("implementar uma zona... com
SOA/NS... incluir MX") como configuração inicial.

### ADR-003 — Validar e versionar antes de aplicar, sempre

**Contexto:** seção 7.2 exige que "cada configuração tenha versão e validação" e que "uma alteração
inválida deverá ser recusada sem substituir a versão válida".

**Escolha:** todo `_apply()` (DNS e DHCP) segue a mesma sequência: grava em arquivo temporário →
valida com a ferramenta oficial (`named-checkzone` / `kea-dhcp4 -t`) → só então sobrescreve o
arquivo real → recarrega o serviço. A versão anterior (que já era válida) é salva em
`config_versions` **antes** da sobrescrita, nunca depois — assim uma falha no meio do caminho nunca
deixa a tabela de versões inconsistente com o arquivo real.

### ADR-004 — Laboratório virtual: agente HTTP próprio no host físico (Windows), separado do console

**Contexto:** o VirtualBox roda no host físico (Windows); o console, no Debian virtualizado. A
seção 8.3 exige agente no host, solicitações autenticadas, nenhum comando livre e separação entre
console, provisionador e executor.

**Alternativas:** (a) backend chamando `VBoxManage` por SSH no Windows; (b) API web do VirtualBox
(`vboxwebsrv`/SOAP); (c) agente próprio em FastAPI com operações fixas.

**Escolha:** (c). **Justificativa:** (a) exigiria um servidor SSH no Windows e transformaria cada
operação em linha de comando remota; (b) expõe a API inteira do VirtualBox, inclusive operações
sobre a base e as VMs do Debian/Ubuntu. O agente próprio expõe só inventariar, clonar, configurar,
iniciar, consultar, desligar e excluir; valida UUID, nome, perfil, porta e imagem; chama o
`VBoxManage` com argumentos separados e timeout; aceita só o token e a origem do Debian; e recusa
VMs sem a marca `ConsoleN2/Managed` ou de outra operação. **Consequências:** mais um processo para
instalar no host (roteiro L5) e um token a proteger; o estado continua no VirtualBox (marcas em
`extradata`), então o agente é sem estado e pode reiniciar a qualquer momento.

**Provisionador:** fila persistente no próprio banco do console (uma thread no backend percorre as
instâncias em estado não final). Escolhida no lugar de Celery/Redis por não exigir serviços novos
no Debian; a reconciliação a cada volta substitui a "retomada de tarefa" de uma fila externa.

### ADR-005 — Serviço exclusivo: RADIUS (FreeRADIUS) em vez de coleta central de eventos

**Contexto:** a seção 8.4 exige um serviço exclusivo por categoria, com operação real pelo console,
efeito em cliente real e experimento de desempenho. **Alternativas:** (a) coleta central de eventos
(rsyslog), (b) RADIUS (FreeRADIUS), (c) sincronização de tempo (chrony).
**Escolha:** (b). **Justificativa:** cobre autenticação, autorização e contabilização — mais
operações administrativas reais (clientes NAS, usuários, políticas, sessões) do que (a) ou (c), e
tem efeito facilmente observável no cliente (login aceito/negado). **Consequência:** exige preparar o
Ubuntu como NAS (`pam_radius_auth`) e cuidar de dois segredos sensíveis (segredo do NAS e senhas),
guardados só no Debian e com senha apenas como hash.

### ADR-006 — Acesso ao desktop pelo VRDE do VirtualBox com senha por instância

**Contexto:** o desktop precisa de acesso remoto pelo Ubuntu (RDP/VNC) e a prontidão deve ser
verificável. **Alternativas:** (a) xrdp dentro do convidado; (b) VNC no convidado; (c) VRDE do
VirtualBox (RDP servido pelo hipervisor) com `VBoxAuthSimple`.

**Escolha:** (c). **Justificativa:** não depende da rede do convidado para o acesso (o RDP é do
host), não exige servidor gráfico remoto na imagem e permite porta e senha próprias por instância
(o Debian envia só o SHA-256 da senha). A prontidão combina o hipervisor (`running` + porta RDP
respondendo) com um sinal do convidado (`/ConsoleN2/Ready`, gravado pelo script de 1ª inicialização
depois da personalização). **Consequências:** exige o Oracle Extension Pack (licença PUEL, uso
educacional) e a biblioteca `VBoxAuthSimple` como padrão do VirtualBox; o acesso é RDP sem TLS,
restrito à rede host-only e à origem do Ubuntu pelo firewall do Windows.

## 3. Rastreabilidade (exemplo, formato da seção 9.1)

| Requisito | Componente | Teste | Evidência |
|---|---|---|---|
| RF-DNS-01 — criar registro pelo painel | `dns_client.create_record` + `routers/dns.py` | `test_create_record_applies_and_versions` | consulta do Ubuntu (`dig`) + evento em `/api/audit` |
| RF-DNS-02 — recusar registro inválido | `dns_client.validate_*` | `test_invalid_*` (test_dns.py) | resposta 400 no painel |
| RF-DHCP-01 — reservar IP por MAC | `dhcp_client.create_reservation` | `test_create_reservation_applies_and_versions` | concessão renovada no Ubuntu bate com a reserva |
| RF-DHCP-02 — recusar IP de infraestrutura | `dhcp_client.validate_ip_in_subnet` | `test_ip_must_be_in_subnet_and_not_infra` | resposta 400 no painel |
| RF-VDI-01 — criar desktop a partir da base com perfil e dono | `lab.request_instance` + `_Step.on_em_criacao` + agente `clone/configure` | `test_full_flow_until_ready_and_access`, `test_clone_marks_vm_and_is_idempotent` | aba Laboratório + `VBoxManage list vms` + `hostnamectl` no desktop |
| RF-VDI-02 — reservar capacidade e respeitar cota, sem duplicar | `lab.request_instance` (BEGIN IMMEDIATE, `op_id`) | `test_concurrent_requests_never_exceed_capacity`, `test_concurrent_duplicate_op_creates_one`, `test_quota_per_user` | recusa auditada como `negado` |
| RF-VDI-03 — retomar tarefa após falha do agente/backend | `_Step.run` + `find_by_op` | `test_clone_timeout_checks_host_before_retrying`, `test_agent_down_then_back`, `test_backend_restart_reconciles` | transições da tarefa na aba |
| RF-VDI-04 — acesso só do proprietário; exclusão preserva base | `lab.authorize`, `lab.access`, agente `managed_info` | `test_other_user_cannot_see_or_access`, `test_never_touches_base_or_unmanaged_vms` | 403 auditado; base em `list vms` |
| RF-EXT-01 — gerenciar NAS, políticas e usuários pelo painel | `radius_client.create_client/save_policy/create_user` | `test_create_client_and_user_applies_and_versions`, `test_render_users_and_clients` | `ssh maria@ubuntu` aceito; aba RADIUS |
| RF-EXT-02 — autenticação aceita/negada e contabilização | FreeRADIUS + `parse_auth_log`, `sessions_from_detail` | `test_parse_auth_log`, `test_sessions_from_detail`, `test_block_policy_and_restore` | sessão encerrada com duração; bloqueio → negado |
| RF-EXT-03 — configuração inválida nunca carregada | `radius_client._apply` (rollback) | `test_rejected_config_is_rolled_back_without_restart` | FreeRADIUS segue atendendo |
| RF-AUD-01 — toda alteração gera evento auditável | `audit.record` chamado em cada `*_client.py` | `test_admin_permissions` (checa `/api/audit`) | linha na aba Auditoria com `correlation_id` |

(Como no exemplo do próprio enunciado, esta tabela não representa testes reais contra os servidores
físicos — isso só acontece no roteiro de validação do README, checkpoint por vez.)

## 4. Incremento 3 — Laboratório virtual (implementado)

Arquitetura, estados, política de reserva, persistência, checkpoints de instalação (host Windows,
rede host-only, VRDE, imagem-base, agente e console), testes T-VDI e roteiro de medição estão em
[`docs/laboratorio-virtual.md`](laboratorio-virtual.md). Decisões: ADR-004 e ADR-006.

## 5. Incremento 4 — serviço exclusivo: Autenticação RADIUS (opção 5)

**Status:** escolhido pelo discente e **implementado**; a validação do Prof. Matias (seção 8.4) ainda
precisa ser registrada abaixo — se ele pedir mudança de escopo, o registro e o módulo serão
ajustados. Instalação, validação e experimento: [`docs/radius.md`](radius.md).

### 5.1 Registro (formato da seção 8.4)

| Campo | Conteúdo |
|---|---|
| Responsável | Yuri Carlos Alves |
| Categoria | 5 — Autenticação RADIUS (AAA: autenticação, autorização e contabilização) |
| Software | FreeRADIUS 3 no Debian (`freeradius`, `freeradius-utils`); no Ubuntu, `freeradius-utils` (radtest/radclient) e `libpam-radius-auth` |
| Problema | A empresa fictícia não tem ponto único de autenticação para equipamentos e serviços de rede (NAS): cada um mantém senhas próprias, não há revogação central nem registro de quem usou o quê e por quanto tempo. |
| Operações do console | Listar/criar/remover **clientes NAS** (IP + segredo compartilhado); listar/criar/bloquear/remover **usuários RADIUS** (senha guardada só como hash); associar usuário a **política** (grupo com `Session-Timeout` e bloqueio por horário/estado); consultar **autenticações aceitas/negadas** e **sessões contabilizadas** (início, fim, duração, bytes); validar a configuração antes de aplicar e restaurar a versão anterior. |
| Recursos necessários | Debian `192.168.100.10` (UDP 1812/1813, sem exposição fora da rede interna); Ubuntu `192.168.100.20` como NAS e cliente real; regras de sudoers específicas (sem curinga), no mesmo padrão de DNS/DHCP. |
| Testes | T-EXT-01: usuário criado pelo console faz login SSH no Ubuntu via `pam_radius_auth` → aceito; bloqueado pelo console → negado; sessão aparece na contabilização e na auditoria. T-EXT-02: falha controlada (segredo do NAS divergente) → pedidos descartados e diagnosticados no log → correção pelo console → reteste aceito. Desempenho: ver 5.3. |
| Validação do professor | *(pendente — preencher com data e observações)* |

### 5.2 Desenho (mesmo padrão de DNS/DHCP)

- `radius_client.py` + `routers/radius.py` + `pages/Radius.jsx`, SSH self-loopback e sudoers mínimo
  (ADR-001); leitura para `leitor+`, escrita só `admin`; auditoria em toda ação.
- Arquivos gerenciados inteiros pelo console em `/etc/freeradius/3.0/console-n2/` (estado JSON +
  `clients.conf` + `users` gerados), incluídos uma única vez pelo `deploy/freeradius/instalar.sh`,
  sem tocar no resto (ADR-002).
- Validar com `freeradius -XC` antes de reiniciar: como o FreeRADIUS só valida a árvore no lugar
  dela, o candidato é instalado e, se recusado, a versão anterior volta na mesma operação; só uma
  configuração aceita é versionada e carregada (ADR-003).
- Contabilização lida dos arquivos `detail` de `radacct`; aceitos/negados lidos do log com
  `auth = yes`. Segredos de NAS e hashes de senha nunca voltam para o navegador nem para a auditoria.

### 5.3 Hipótese de desempenho (seção 8.5)

**Hipótese:** o custo do esquema de hash de senha domina a latência de autenticação PAP sob carga:
com `Crypt-Password` (SHA-512, 5000 rodadas) a vazão máxima cai de forma mensurável em relação a
`SSHA2-256-Password`, enquanto o número de usuários no arquivo (100 vs. 5000) tem efeito menor.

**Carga controlada:** `radclient` a partir do Ubuntu, com número de pedidos e paralelismo fixos
(ex.: 1000 pedidos; 1, 10 e 50 em paralelo), repetido 3 vezes por cenário.
**Métricas:** pedidos/s, latência média e p95, perdas (sem resposta), CPU do `freeradius`.
**Comparação:** 2 esquemas de hash × 2 tamanhos de base. **Limites:** apontar o paralelismo em que
aparecem perdas ou timeouts e o que limita (CPU da VM, fila do servidor).
