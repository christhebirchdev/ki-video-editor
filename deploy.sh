#!/bin/bash
# =============================================================
# KI Video Editor — Deploy Script
# Ausführen auf dem Server: bash deploy.sh
# =============================================================
set -e

APP_DIR="/opt/ki-video-editor"
SERVICE_NAME="ki-video-editor"
PYTHON="python3"
PIP="pip3"

echo "=== KI Video Editor Setup ==="

# 1. Verzeichnis anlegen
echo "[1/6] Verzeichnis anlegen..."
sudo mkdir -p "$APP_DIR"
sudo chown "$USER:$USER" "$APP_DIR"

# 2. Dateien kopieren
echo "[2/6] Dateien kopieren..."
cp -r . "$APP_DIR/"

# 3. Virtuelle Umgebung
echo "[3/6] Python-Umgebung einrichten..."
cd "$APP_DIR"
$PYTHON -m venv venv
source venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q

# 4. ffmpeg prüfen
echo "[4/6] ffmpeg prüfen..."
if ! command -v ffmpeg &> /dev/null; then
    echo "  ffmpeg nicht gefunden — installiere..."
    sudo apt-get update -q && sudo apt-get install -y ffmpeg -q
else
    echo "  ffmpeg OK: $(ffmpeg -version 2>&1 | head -1)"
fi

# 5. .env prüfen
echo "[5/6] .env prüfen..."
if [ ! -f "$APP_DIR/.env" ]; then
    echo "  FEHLER: .env fehlt in $APP_DIR"
    echo "  Bitte .env anlegen (siehe .env.example)"
    exit 1
fi
echo "  .env gefunden OK"

# 6. systemd Service einrichten
echo "[6/6] systemd Service einrichten..."
sudo tee /etc/systemd/system/${SERVICE_NAME}.service > /dev/null <<EOF
[Unit]
Description=KI Video Editor
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$APP_DIR
Environment="PATH=$APP_DIR/venv/bin"
EnvironmentFile=$APP_DIR/.env
# Nur lokal binden — der öffentliche Zugriff läuft über den Reverse-Proxy (Caddy)
# mit HTTPS + Passwortschutz. 1 Worker, weil die Analyst-Warteschlange prozess-lokal ist.
ExecStart=$APP_DIR/venv/bin/uvicorn main:app --host 127.0.0.1 --port 8001 --workers 1
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable ${SERVICE_NAME}
sudo systemctl restart ${SERVICE_NAME}

sleep 2
STATUS=$(sudo systemctl is-active ${SERVICE_NAME})
echo ""
echo "============================================"
echo "  KI Video Editor Status: $STATUS"
echo "  Lokal: http://127.0.0.1:8001  (öffentlich nur über Caddy-Proxy + HTTPS)"
echo "============================================"
