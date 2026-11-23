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
| 3 — Laboratório virtual | 🔜 planejado (seção 4 deste documento) | — |
| 4 — Serviço exclusivo | 🔜 a validar com o Prof. Matias (seção 5) | — |

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

### ADR-004 — Laboratório virtual via um agente separado no host físico (não dentro das VMs)

Ver detalhamento na seção 4. Resumo: o agente que fala com `VBoxManage` **tem que** rodar no host
físico (onde o VirtualBox realmente executa), nunca dentro do Debian virtualizado — o enunciado é
explícito sobre isso ("não é necessário instalar VirtualBox dentro do Debian virtualizado"). Isso
implica um segundo processo (fora do FastAPI principal), autenticado, numa rede host-only própria.

## 3. Rastreabilidade (exemplo, formato da seção 9.1)

| Requisito | Componente | Teste | Evidência |
|---|---|---|---|
| RF-DNS-01 — criar registro pelo painel | `dns_client.create_record` + `routers/dns.py` | `test_create_record_applies_and_versions` | consulta do Ubuntu (`dig`) + evento em `/api/audit` |
| RF-DNS-02 — recusar registro inválido | `dns_client.validate_*` | `test_invalid_*` (test_dns.py) | resposta 400 no painel |
| RF-DHCP-01 — reservar IP por MAC | `dhcp_client.create_reservation` | `test_create_reservation_applies_and_versions` | concessão renovada no Ubuntu bate com a reserva |
| RF-DHCP-02 — recusar IP de infraestrutura | `dhcp_client.validate_ip_in_subnet` | `test_ip_must_be_in_subnet_and_not_infra` | resposta 400 no painel |
| RF-AUD-01 — toda alteração gera evento auditável | `audit.record` chamado em cada `*_client.py` | `test_admin_permissions` (checa `/api/audit`) | linha na aba Auditoria com `correlation_id` |

(Como no exemplo do próprio enunciado, esta tabela não representa testes reais contra os servidores
físicos — isso só acontece no roteiro de validação do README, checkpoint por vez.)

## 4. Plano para o Incremento 3 — Laboratório virtual sob demanda

Ainda não implementado. Resumo do desenho (a implementar em uma próxima etapa):

**Componentes novos:**
- `labvm-agent/` — um processo **separado**, rodando no host físico (fora de qualquer VM), com sua
  própria API HTTP autenticada (token fixo em `.env`, nunca exposta fora de uma rede host-only). Só
  ele chama `VBoxManage`. Operações expostas: `inventory`, `clone`, `configure`, `start`,
  `status`, `stop`, `delete` — cada uma validando UUID/nome/perfil antes de montar o argv do
  `VBoxManage` (mesmo princípio de "nunca concatenar entrada do usuário" já usado no SSH).
- `backend/app/labvm_client.py` — fala HTTP com o agente (não SSH, já que agente e backend estão em
  máquinas diferentes); guarda catálogo de imagens, perfis (`1 vCPU/2GiB`, `2 vCPU/4GiB`), cotas por
  usuário e o estado de cada instância (tabela `labvm_instances` no SQLite: uuid, proprietário,
  perfil, estado, criado_em).
- Fluxo de criação: reserva de cota em transação → enfileira pedido com um `correlation_id` →
  agente clona a imagem-base → personaliza (hostname, chaves) → inicia headless → backend consulta
  prontidão (porta do RDP/VNC responde) → só então libera os dados de acesso ao usuário.
- Admissão: nunca reservar mais do que a capacidade do host permite; pedido duplicado (mesmo
  `correlation_id`) não deve clonar duas vezes.

**Por que não entrou nesta entrega:** é o incremento mais pesado do enunciado (o próprio professor
atribui 3 pontos a ele por complexidade) e depende de decisões que só você pode validar no seu host
físico: nome da interface host-only, se o VirtualBox aceita clonar/iniciar via `VBoxManage` sem
senha para o seu usuário administrador de VMs, e qual sistema usar como imagem-base. Posso montar o
agente e o `labvm_client.py` numa próxima etapa — me diga o nome da interface host-only já criada no
VirtualBox e se você quer RDP (xrdp) ou VNC no convidado.

## 5. Incremento 4 — serviço de rede exclusivo (a registrar com o Prof. Matias)

**Sugestão (a validar):** opção **7 — Coleta central de eventos** (um coletor `syslog` central).

**Por quê:** é a opção que mais aproveita o que você já construiu. O console já lê logs via SSH
(`ssh_client.read_logs`, CP6) e já tem auditoria persistente — um coletor central de syslog é uma
extensão natural e tecnicamente distinta da auditoria do console (que é só do que ACONTECE no
painel; o coletor recebe logs de **outras máquinas**, como pede a tabela da seção 8.4: "Diferente
da auditoria do console"). Com `rsyslog` (já instalado no Debian) escutando UDP/TCP e o Ubuntu
configurado para encaminhar logs, você consegue:
- console mostrando eventos por origem, com filtro e paginação (reaproveita a UI da aba Logs);
- retenção configurável (rotação do rsyslog);
- hipótese de desempenho mensurável: gerar carga controlada de eventos/segundo (um script simples
  com `logger` ou `socket` UDP) e medir taxa de perda e atraso — exatamente o tipo de "métrica,
  comparação e limites observados" que a seção 8.5 pede para o nível avançado.

**Alternativas também viáveis**, caso o professor já tenha outro grupo nessa categoria:
- **5 — Autenticação RADIUS** (FreeRADIUS): também combina com o módulo de auditoria/usuários já
  existente, mas exige mais infraestrutura de teste (um cliente 802.1X ou PPP) para gerar aceitação/
  rejeição real.
- **6 — Sincronização de tempo** (chrony): o mais simples de implementar e medir (desvio de
  relógio), mas tecnicamente mais raso para o "nível avançado" da disciplina.

**Antes de implementar:** registre a escolha com o Prof. Matias (responsável, categoria, software,
problema, operações do console, recursos necessários, testes) — a seção 8.4 do enunciado exige essa
validação antes do código. Assim que confirmar, eu monto o módulo (`syslog_client.py`, router,
testes e página) seguindo o mesmo padrão de DNS/DHCP (SSH self-loopback, validação antes de aplicar,
auditoria em cada ação).
