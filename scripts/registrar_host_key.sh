#!/usr/bin/env bash
# Registra a host key do Debian em backend/known_hosts e mostra as duas impressões digitais
# para você CONFERIR (a conexão SSH do console recusa host key desconhecida).
set -euo pipefail

HOST="${1:-192.168.100.10}"
DEST="$(cd "$(dirname "$0")/.." && pwd)/backend/known_hosts"

ssh-keyscan -t ed25519 "$HOST" > "$DEST" 2>/dev/null
chmod 600 "$DEST"

echo "Host key lida da rede ($HOST):"
ssh-keygen -lf "$DEST"
echo
if [ -r /etc/ssh/ssh_host_ed25519_key.pub ]; then
  echo "Host key local (/etc/ssh/ssh_host_ed25519_key.pub):"
  ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
  echo
  echo "As duas linhas devem ter o MESMO SHA256. Se forem diferentes, apague $DEST."
else
  echo "Rode no próprio Debian para comparar: ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub"
fi
echo "Arquivo gravado em: $DEST"
