#!/bin/bash
# Instala el bot en un servidor Ubuntu/Debian y lo programa para ejecutarse cada hora.
# Uso (en el servidor, como root):  bash instalar.sh
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
apt-get update -qq && apt-get install -y -qq python3-venv >/dev/null
python3 -m venv "$DIR/.venv"
"$DIR/.venv/bin/pip" install -q -r "$DIR/requirements.txt"
[ -f "$DIR/.env" ] || { cp "$DIR/.env.ejemplo" "$DIR/.env"; chmod 600 "$DIR/.env"; }

cat > /etc/systemd/system/bot-trading.service <<UNIT
[Unit]
Description=Bot de trading (una ronda)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=$DIR
ExecStart=$DIR/.venv/bin/python $DIR/bot.py
TimeoutStartSec=600
UNIT

cat > /etc/systemd/system/bot-trading.timer <<UNIT
[Unit]
Description=Ejecuta el bot de trading cada hora

[Timer]
OnCalendar=*-*-* *:02:00
Persistent=true

[Install]
WantedBy=timers.target
UNIT

systemctl daemon-reload
echo
echo "Instalado. Siguientes pasos:"
echo "  1. Rellena las claves:      nano $DIR/.env"
echo "  2. Prueba una ronda:        $DIR/.venv/bin/python $DIR/bot.py --simular"
echo "  3. Actívalo cada hora:      systemctl enable --now bot-trading.timer"
echo "  Ver lo que hace:            journalctl -u bot-trading -f"
