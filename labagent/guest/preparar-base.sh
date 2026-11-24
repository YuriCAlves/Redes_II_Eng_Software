#!/bin/bash
# Prepara a imagem-base BASE-LINUX-v1 (Debian 12 ou 13). Rode como root DENTRO da VM base,
# na pasta labagent/guest copiada para ela. Ao final, desligue a VM e tire o snapshot "v1".
set -euo pipefail
cd "$(dirname "$0")"
GUEST_USER=aluno

if [ "${1:-}" != "--so-limpar" ]; then
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y task-xfce-desktop lightdm firefox-esr \
        libreoffice-writer openssh-server dnsutils curl
    id "$GUEST_USER" >/dev/null 2>&1 || useradd -m -s /bin/bash "$GUEST_USER"
    passwd -l "$GUEST_USER"            # sem senha na base: cada instância recebe a sua
    install -m 755 console-n2-firstboot /usr/local/sbin/console-n2-firstboot
    install -m 644 console-n2-firstboot.service /etc/systemd/system/console-n2-firstboot.service
    systemctl daemon-reload
    systemctl enable console-n2-firstboot.service
    systemctl set-default graphical.target
fi

if [ ! -x /usr/bin/VBoxControl ]; then
    echo "ATENÇÃO: Guest Additions não encontrados (VBoxControl). Instale-os antes do snapshot:" >&2
    echo "  Dispositivos > Inserir imagem de CD dos Adicionais para Convidado; depois" >&2
    echo "  apt-get install -y build-essential linux-headers-\$(uname -r) && sh /media/cdrom0/VBoxLinuxAdditions.run" >&2
fi

# Remover identidade e segredos da base (cada clone gera os seus na 1ª inicialização)
rm -f /etc/ssh/ssh_host_*
: > /etc/machine-id
rm -f /var/lib/dbus/machine-id /var/lib/console-n2/instance-id
rm -f /root/.bash_history /home/*/.bash_history
rm -rf /tmp/* /var/tmp/*
apt-get clean
journalctl --rotate >/dev/null 2>&1 || true
journalctl --vacuum-time=1s >/dev/null 2>&1 || true
rm -f /var/lib/NetworkManager/*.lease /var/lib/dhcp/*.leases 2>/dev/null || true
echo "Base pronta. Desligue com 'poweroff' e, no host: VBoxManage snapshot BASE-LINUX-v1 take v1"
