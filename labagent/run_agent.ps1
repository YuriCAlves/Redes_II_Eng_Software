# Inicia o agente do laboratório no host Windows, com a conta que administra as VMs.
# Uso (PowerShell, dentro da pasta labagent):  .\run_agent.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Test-Path ".venv")) {
    py -3 -m venv .venv
    .\.venv\Scripts\python.exe -m pip install -r requirements.txt
}
$cfg = Get-Content agent.json -Raw | ConvertFrom-Json
.\.venv\Scripts\python.exe -m uvicorn agent:app --host $cfg.listen_host --port $cfg.listen_port
