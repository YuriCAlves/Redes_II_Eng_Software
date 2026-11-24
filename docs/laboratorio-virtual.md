# Laboratório virtual sob demanda (Incremento 3)

Desktops Linux criados pelo console a partir de uma imagem-base, executando no VirtualBox do
**host físico (Windows)** e acessados pelo Ubuntu cliente por RDP (VRDE do VirtualBox).

## 1. Arquitetura

| Componente | Onde | Responsabilidade |
|---|---|---|
| Console/API (`backend/app/lab.py`, `routers/lab.py`) | Debian | Catálogo, perfis, cotas, reserva de capacidade, estado, autorização, auditoria |
| Provisionador (fila em `lab.py`, `process_once`) | Debian (thread do backend) | Leva cada instância ao próximo estado; reconcilia banco e host |
| Executor (`labagent/`) | Host Windows | Único que chama o `VBoxManage`; operações delimitadas, token + origem |
| Imagem-base `BASE-LINUX-v1` (snapshot `v1`) | Host, desligada | Debian 12 + XFCE + Guest Additions + script de 1ª inicialização |
| Instância `LAB-<usuário>-<op>` | Host | Clone ligado (*linked clone*) da base, com MAC, hostname, chaves e senhas próprios |
| Ubuntu | VM | Cliente: pede o desktop no navegador e acessa por RDP (Remmina) |

```
            rede host-only 192.168.56.0/24 (sem DHCP)             segmento de serviços 192.168.100.0/24
  Debian 192.168.56.10 ── HTTP :8765 (token) ──► Host 192.168.56.1     (Kea entrega IP/DNS aos clones)
  Ubuntu 192.168.56.20 ── RDP :5001-5010 ─────► Host 192.168.56.1     Debian .10 · Ubuntu .20 · clones .100-.150
```

* **Backend → agente:** HTTP na rede host-only, `Authorization: Bearer <token>`, o agente só aceita
  a origem `192.168.56.10` e só escuta em `192.168.56.1`. O firewall do Windows repete a regra.
* **Ubuntu → desktop:** RDP para `192.168.56.1:<porta>`. O VRDE é servido pelo próprio VirtualBox,
  então o acesso funciona mesmo se a rede do convidado falhar; cada instância tem porta e senha
  RDP próprias (`VBoxAuthSimple`, só o hash SHA-256 sai do Debian).
* **Rede do convidado:** a placa do clone fica no segmento de serviços (herdada da base, com MAC
  novo). O Kea do Incremento 2 entrega endereço e DNS; o console registra o IP pelos Guest
  Additions ou, como plano B, pela concessão do Kea para o MAC do clone.
* **NAT** pode existir na base só para atualizações; não é usado pelo laboratório.

### Estados e reserva

`solicitada → em_criacao → iniciando → disponivel → desligada`, mais `falha`, `em_exclusao` e
`excluida`. CPU/RAM ficam **reservadas** de `solicitada` até `disponivel`; desligar ou falhar
libera a reserva; o **disco** continua contando (limite de VMs e cota) até a exclusão. A reserva é
derivada do estado e a admissão roda em `BEGIN IMMEDIATE` no SQLite, então pedidos concorrentes
nunca reservam a mesma capacidade (teste `test_concurrent_requests_never_exceed_capacity`).

**Prontidão:** a instância só vira `disponivel` quando (1) a VM está `running`, (2) a porta RDP
aceita conexão e (3) o script do convidado sinalizou `/ConsoleN2/Ready` com o identificador da
operação — depois de trocar hostname, `machine-id`, chaves SSH e a senha do usuário `aluno`. Só
então o proprietário vê os dados de acesso.

**Recuperação:** cada pedido tem um `op_id`; o agente marca a VM com ele (`extradata`). Em timeout
de clonagem ou reinício do backend/agente, o provisionador **consulta o host pelo `op_id` antes de
clonar de novo**. Em falha do primeiro boot, remove só o clone daquela operação; em falha de um
reinício, desliga a VM e preserva o disco. O agente recusa qualquer operação em VM sem a marca do
projeto, em VM de outra operação ou na imagem-base.

### Persistência dos dados

O desktop é **persistente**: os arquivos do aluno ficam no disco da instância e sobrevivem a
desligar/ligar e ao logout. Só a **exclusão** (pedida pelo proprietário ou por um admin, com
confirmação) apaga a VM e o disco. Uma nova versão da base (`BASE-LINUX-v2` ou snapshot `v2`, e
`version` novo no catálogo) vale só para instâncias futuras; as existentes continuam na versão em
que nasceram (o console mostra `imagem vN` em cada uma).

## 2. Checkpoints de instalação

Faça na ordem; cada um termina com uma verificação.

### Checkpoint L1 — Inventariar o host e definir limites

No **PowerShell do Windows**, com a conta que administra as VMs:

```powershell
$vbox = "C:\Program Files\Oracle\VirtualBox\VBoxManage.exe"
Get-CimInstance Win32_Processor | Select Name, NumberOfCores, NumberOfLogicalProcessors
"{0:N1} GiB de RAM" -f ((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB)
Get-PSDrive C | Select Used, Free
& $vbox --version
& $vbox list vms
& $vbox list runningvms
```

Com Debian e Ubuntu ligados, anote no Gerenciador de Tarefas o uso de CPU e RAM. Calcule a
capacidade do laboratório: RAM total − (uso do Windows + Debian + Ubuntu) − margem de 2 GiB.
vCPUs não são núcleos novos: não ultrapasse o número de **núcleos físicos** menos 1 para o host.

**Verificação:** registre CPU, RAM, disco livre, Windows e versão do VirtualBox em
`docs/ficha_verificacao.md`, com os valores escolhidos para `CONSOLE_LAB_MAX_VCPUS`,
`CONSOLE_LAB_MAX_MEMORY_MB` e `CONSOLE_LAB_MAX_INSTANCES` (disco: o clone ligado começa com
alguns MiB e cresce com o uso; reserve pelo menos 5 GiB livres por instância).

### Checkpoint L2 — Rede host-only entre backend, agente e cliente

No Windows (VirtualBox: *Arquivo → Ferramentas → Gerenciador de Rede → Redes Host-only*), crie ou
use uma placa host-only com **IP 192.168.56.1/24 e servidor DHCP desligado**. Pelo terminal:

```powershell
& $vbox list hostonlyifs                      # anote o nome exato, ex.: "VirtualBox Host-Only Ethernet Adapter"
& $vbox hostonlyif ipconfig "VirtualBox Host-Only Ethernet Adapter" --ip 192.168.56.1 --netmask 255.255.255.0
& $vbox list dhcpservers                      # se houver um nessa rede, desligue:
& $vbox dhcpserver modify --interface "VirtualBox Host-Only Ethernet Adapter" --disable
```

Com cada VM **desligada**, adicione uma placa nessa rede (ex.: placa 3 do Debian e placa 3 do
Ubuntu; use um número de placa livre):

```powershell
& $vbox modifyvm "Debian" --nic3 hostonly --hostonlyadapter3 "VirtualBox Host-Only Ethernet Adapter"
& $vbox modifyvm "Ubuntu" --nic3 hostonly --hostonlyadapter3 "VirtualBox Host-Only Ethernet Adapter"
```

Ligue as VMs e configure endereço fixo **sem gateway** (o segmento de serviços e o DHCP não mudam):

```bash
ip -br link                                   # no Debian: descubra o nome da placa nova (ex.: enp0s9)
sudo nmcli con add type ethernet ifname enp0s9 con-name lab-hostonly \
  ipv4.method manual ipv4.addresses 192.168.56.10/24 ipv4.never-default yes ipv6.method disabled
# no Ubuntu, o mesmo com 192.168.56.20/24
```

**Verificação:** `ping -c2 192.168.56.1` no Debian e no Ubuntu; `ip route` sem rota padrão nova.

### Checkpoint L3 — Acesso remoto (VRDE) com senha por instância

1. Baixe o **Oracle VirtualBox Extension Pack da mesma versão** do VirtualBox e instale
   (*Arquivo → Ferramentas → Gerenciador de Extensões*). Uso educacional/pessoal é permitido pela
   licença PUEL.
2. Ative a autenticação simples do VirtualBox (vale só para VMs com `--vrdeauthtype external`; as
   VMs existentes não mudam):

```powershell
& $vbox setproperty vrdeauthlibrary "VBoxAuthSimple"
& $vbox list extpacks
& $vbox list systemproperties | Select-String "auth library"
```

**Verificação:** `list extpacks` mostra o Extension Pack com `Usable: true` e a biblioteca de
autenticação aparece como `VBoxAuthSimple`. Sem isso o agente recusa configurar instâncias (412).

### Checkpoint L4 — Imagem-base versionada

1. Crie a VM **`BASE-LINUX-v1`**: Debian 12 (netinst), 1 vCPU, 2 GiB, disco dinâmico de 20 GiB,
   placa 1 no **mesmo segmento de serviços** em que está a `enp0s8` do Debian (192.168.100.0/24).
   Instale com usuário administrador próprio da base (não use senha pessoal).
2. Instale os **Guest Additions** (*Dispositivos → Inserir imagem de CD dos Adicionais*):
   ```bash
   sudo apt-get install -y build-essential linux-headers-$(uname -r)
   sudo sh /media/cdrom0/VBoxLinuxAdditions.run
   ```
3. Copie os arquivos do convidado do Debian e rode a preparação **dentro da base**:
   ```bash
   sudo apt-get install -y openssh-client
   scp -r yuri@192.168.100.10:Documentos/Redes_II_Eng_Software/labagent/guest ~/guest
   sudo ~/guest/preparar-base.sh        # instala XFCE, cria "aluno", ativa o 1º boot e limpa segredos
   rm -rf ~/guest && sudo poweroff
   ```
4. No Windows, congele a versão e registre o checksum:
   ```powershell
   & $vbox snapshot "BASE-LINUX-v1" take "v1" --description "Debian 12 XFCE - catálogo v1"
   & $vbox showvminfo "BASE-LINUX-v1" | Select-String "^State|Name: v1"
   & $vbox list hdds | Select-String "BASE-LINUX-v1"      # mostra o caminho (Location) do .vdi
   Get-FileHash "<Location do .vdi da base>" -Algorithm SHA256
   ```
   Não ligue mais a base: os clones dependem do snapshot `v1`. Uma atualização vira `v2`
   (preparar, `preparar-base.sh --so-limpar`, novo snapshot e nova entrada no catálogo).

**Verificação:** a base está `poweroff`, tem o snapshot `v1`, e versão + SHA-256 estão na ficha.

### Checkpoint L5 — Agente no host

1. Instale o **Python 3.12** para Windows (marque *Add python.exe to PATH*) e traga a pasta
   `labagent` do repositório (`git clone` no Windows ou cópia da pasta).
2. Gere o token e crie a configuração:
   ```powershell
   cd labagent
   py -3 -c "import secrets; print(secrets.token_urlsafe(32))"
   Copy-Item agent.example.json agent.json
   notepad agent.json        # cole o token; confira images, max_cpus e max_memory_mb (do L1)
   ```
3. Libere no firewall **só** as origens necessárias e inicie o agente:
   ```powershell
   New-NetFirewallRule -DisplayName "Console N2 - agente" -Direction Inbound -Protocol TCP `
     -LocalAddress 192.168.56.1 -LocalPort 8765 -RemoteAddress 192.168.56.10 -Action Allow
   New-NetFirewallRule -DisplayName "Console N2 - RDP do laboratório" -Direction Inbound -Protocol TCP `
     -LocalAddress 192.168.56.1 -LocalPort 5001-5010 -RemoteAddress 192.168.56.20 -Action Allow
   .\run_agent.ps1
   ```
   Se o Windows perguntar sobre o `python.exe`, permita só em **redes privadas**; as regras acima
   já liberam o necessário. Para o agente iniciar com o Windows, crie uma tarefa agendada
   "Ao fazer logon" executando `powershell -File <pasta>\run_agent.ps1` com a mesma conta.

**Verificação (no Debian):**
```bash
TOKEN='cole-o-token'
curl -s -H "Authorization: Bearer $TOKEN" http://192.168.56.1:8765/v1/inventory | python3 -m json.tool | head -30
curl -s -o /dev/null -w '%{http_code}\n' http://192.168.56.1:8765/v1/inventory       # 401: sem token
```
No inventário: `extension_pack: true`, `vrde_auth_library` com `VBoxAuthSimple` e a imagem com
`present: true`. Do Ubuntu, a mesma chamada com token deve dar **403** (origem não autorizada).

### Checkpoint L6 — Console

No `backend/.env` do Debian:

```bash
CONSOLE_LAB_AGENT_URL=http://192.168.56.1:8765
CONSOLE_LAB_AGENT_TOKEN=cole-o-token
CONSOLE_LAB_MAX_VCPUS=2          # valores do Checkpoint L1
CONSOLE_LAB_MAX_MEMORY_MB=4096
CONSOLE_LAB_MAX_INSTANCES=2
```

Reinicie o backend. Os perfis (`p1` 1 vCPU/2 GiB para operador e admin; `p2` 2 vCPUs/4 GiB só
para admin) e a imagem ficam em `backend/app/lab_catalog.json` — ajuste e justifique pelo L1.

**Verificação:** a aba **Laboratório** mostra a capacidade, o host medido (CPU, RAM livre, disco,
versão do VirtualBox) e o catálogo, sem o aviso "Agente do host indisponível".

## 3. Validação (seção 10)

| Teste | Como | Resultado esperado |
|---|---|---|
| T-VDI-01 | `oper1` solicita `p1`; acompanha a tarefa; no Ubuntu, `remmina -c rdp://oper1@192.168.56.1:5001` com a senha RDP; login como `aluno`; abre o terminal e roda `hostnamectl` e `ip -br addr` | Instância com UUID e dono próprios, 1 vCPU/2 GiB, hostname `lab-oper1-…`, IP do Kea |
| T-VDI-02 | Clique duplo em Solicitar / repetir o pedido; `oper1` pede uma 2ª instância; admin pede `p2` com `p1` ativa | Nenhuma VM duplicada; "Cota excedida"; "Capacidade insuficiente" (auditados como `negado`) |
| T-VDI-03 | Durante "Em criação", feche o agente (Ctrl+C) e reabra; ou reinicie o backend | Erro transitório visível; a tarefa continua sem novo clone; trilha preservada |
| T-VDI-04 | Outro operador abre a instância de `oper1` pela URL `/api/lab/instances/<id>`; `oper1` exclui a sua | 403 auditado; exclusão remove só aquela VM (base e Debian/Ubuntu intactos em `VBoxManage list vms`) |
| Falha controlada | Pare o agente e peça uma instância; ou desligue a VM por dentro do desktop | Pedido fica na fila e conclui quando o agente volta; desligamento detectado e reserva liberada |

**Medição:** com uma instância por vez, rode no Debian

```bash
python3 scripts/lab_medicao.py --user admin1 --profiles p1 p2 --repeat 3 --out docs/medicao-lab.csv
```

O CSV traz, por rodada, o tempo até a prontidão, CPU/RAM/disco livres do host e CPU/RAM medidos da
instância ociosa. Compare `p1` × `p2` e registre na ficha: tempo médio até a prontidão, consumo
**atribuído** (perfil) × **medido** (processo da VM), quantas sessões foram testadas e o limite
observado (por exemplo, a 2ª instância recusada por memória). Se o host não comportar Debian,
Ubuntu e o desktop juntos, use o próprio Windows como cliente RDP (`mstsc /v:192.168.56.1:5001`)
e registre essa adaptação.

## 4. Limites desta demonstração

Um host comprova provisionamento, isolamento e acesso remoto; não comprova alta disponibilidade
nem capacidade para uma turma. O provisionador processa as instâncias em sequência (uma clonagem
por vez). As senhas de acesso ficam no banco do console (legível só pelo backend) e são apagadas
na exclusão; a senha do convidado sai das propriedades da VM assim que ela fica pronta.
