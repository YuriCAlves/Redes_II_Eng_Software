#!/bin/bash
# Preparação ÚNICA do FreeRADIUS para o Console N2 (incremento 4). Rode no Debian:
#   sudo deploy/freeradius/instalar.sh
# Idempotente: pode rodar de novo sem duplicar nada.
set -euo pipefail
cd "$(dirname "$0")"
RADDB=/etc/freeradius/3.0
DIR=$RADDB/console-n2
INC_CLIENTS="\$INCLUDE $DIR/clients.conf"
INC_USERS="\$INCLUDE $DIR/users"

apt-get install -y freeradius freeradius-utils

# 1. Pasta gerenciada pelo console (root:freerad, só leitura para o serviço)
install -d -o root -g freerad -m 750 "$DIR"
[ -f "$DIR/console-n2.json" ] || install -o root -g freerad -m 640 console-n2.json "$DIR/console-n2.json"
[ -f "$DIR/clients.conf" ] || install -o root -g freerad -m 640 /dev/null "$DIR/clients.conf"
[ -f "$DIR/users" ] || install -o root -g freerad -m 640 /dev/null "$DIR/users"

# 2. Incluir os arquivos do console na configuração do FreeRADIUS
grep -qxF "$INC_CLIENTS" "$RADDB/clients.conf" || printf '\n# Clientes NAS gerenciados pelo Console N2\n%s\n' "$INC_CLIENTS" >> "$RADDB/clients.conf"
AUTHZ=$RADDB/mods-config/files/authorize
if ! grep -qxF "$INC_USERS" "$AUTHZ"; then
    tmp=$(mktemp)
    { printf '# Usuarios e politicas gerenciados pelo Console N2 (antes das entradas DEFAULT)\n%s\n\n' "$INC_USERS"; cat "$AUTHZ"; } > "$tmp"
    cat "$tmp" > "$AUTHZ" && rm -f "$tmp"
fi

# 3. Registrar autenticações aceitas/negadas no radius.log (nunca as senhas)
sed -i -E 's/^(\s*)auth = no/\1auth = yes/' "$RADDB/radiusd.conf"
sed -i -E 's/^(\s*)auth_badpass = yes/\1auth_badpass = no/; s/^(\s*)auth_goodpass = yes/\1auth_goodpass = no/' "$RADDB/radiusd.conf"

# 4. Segredo forte para o cliente localhost (teste de autenticação feito pelo console)
if grep -Eq '^\s*secret\s*=\s*testing123\s*$' "$RADDB/clients.conf"; then
    NEW=$(head -c 24 /dev/urandom | base64 | tr -d '/+=' | cut -c1-32)
    sed -i -E "s/^(\s*secret\s*=\s*)testing123\s*$/\1$NEW/" "$RADDB/clients.conf"
    echo
    echo ">>> Coloque no backend/.env:  CONSOLE_RADIUS_TEST_SECRET=$NEW"
    echo
fi

# 5. Leitor de logs para o console
install -o root -g root -m 755 console-n2-radius-read /usr/local/sbin/console-n2-radius-read

# 6. Validar e (re)iniciar
sudo -u freerad /usr/sbin/freeradius -XC >/tmp/freeradius-check.log 2>&1 \
    && echo "Configuração do FreeRADIUS: OK" \
    || { tail -20 /tmp/freeradius-check.log; echo "freeradius -XC recusou a configuração" >&2; exit 1; }
systemctl enable --now freeradius
systemctl restart freeradius
systemctl --no-pager --lines=0 status freeradius
