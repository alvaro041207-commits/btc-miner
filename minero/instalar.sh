#!/bin/bash
# Uso: sudo bash instalar.sh TU_DIRECCION_BTC
set -e
[ -z "$1" ] && { echo "Uso: sudo bash instalar.sh TU_DIRECCION_BTC"; exit 1; }
mkdir -p /opt/btc-miner
cp "$(dirname "$0")/miner.py" /opt/btc-miner/
sed "s/TU_DIRECCION_BTC/$1/" "$(dirname "$0")/btc-miner.service" > /etc/systemd/system/btc-miner.service
systemctl daemon-reload
systemctl enable --now btc-miner
echo "Listo. Ver el registro con:  journalctl -u btc-miner -f"
