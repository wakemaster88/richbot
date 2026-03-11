#!/usr/bin/env bash
# RichBot Raspberry Pi Setup Script
# Run as: sudo bash scripts/setup_pi.sh
set -euo pipefail

ACTUAL_USER="${SUDO_USER:-pi}"
INSTALL_DIR="/home/$ACTUAL_USER/richbot"
VENV_DIR="$INSTALL_DIR/.venv"

echo "==========================================="
echo "  RichBot — Raspberry Pi Setup"
echo "==========================================="
echo "  User: $ACTUAL_USER"
echo "  Install: $INSTALL_DIR"
echo "==========================================="

# --- Detect Python ---
PYTHON=""
for v in python3.12 python3.11 python3; do
    if command -v $v &>/dev/null; then
        PYTHON=$v
        break
    fi
done
if [ -z "$PYTHON" ]; then
    echo "ERROR: No Python 3 found!"
    exit 1
fi
PY_VERSION=$($PYTHON --version 2>&1)
echo "  Python: $PY_VERSION ($PYTHON)"

# --- System Dependencies ---
echo "[1/8] Installing system dependencies..."
apt-get update -qq
apt-get install -y -qq \
    python3-venv python3-dev python3-pip \
    libopenblas-dev \
    build-essential git \
    sqlite3 || {
    echo "  Some packages failed, trying alternatives..."
    apt-get install -y -qq python3-full python3-dev build-essential git sqlite3
}

# --- Swap Configuration ---
echo "[2/8] Configuring swap..."
SWAP_SIZE=2048
if [ -f /etc/dphys-swapfile ]; then
    CURRENT_SWAP=$(grep CONF_SWAPSIZE /etc/dphys-swapfile | cut -d= -f2)
    if [ "$CURRENT_SWAP" -lt "$SWAP_SIZE" ] 2>/dev/null; then
        sed -i "s/CONF_SWAPSIZE=.*/CONF_SWAPSIZE=$SWAP_SIZE/" /etc/dphys-swapfile
        systemctl restart dphys-swapfile
        echo "  Swap increased to ${SWAP_SIZE}MB"
    else
        echo "  Swap already >= ${SWAP_SIZE}MB"
    fi
else
    if swapon --show | grep -q /swapfile; then
        echo "  Swapfile already active"
    elif [ -f /swapfile ]; then
        echo "  Swapfile exists, activating..."
        chmod 600 /swapfile
        swapon /swapfile 2>/dev/null || echo "  Swapfile already in use"
    else
        echo "  Creating ${SWAP_SIZE}MB swapfile..."
        fallocate -l ${SWAP_SIZE}M /swapfile
        chmod 600 /swapfile
        mkswap /swapfile
        swapon /swapfile
        grep -q "/swapfile" /etc/fstab || echo "/swapfile swap swap defaults 0 0" >> /etc/fstab
    fi
fi

# --- SD Card / Storage Optimization ---
echo "[3/8] Optimizing storage I/O..."
if ! grep -q "noatime" /etc/fstab; then
    sed -i 's/defaults/defaults,noatime,commit=120/' /etc/fstab
    echo "  Added noatime and commit=120 to fstab (reduces SD writes)"
fi

# --- Project Setup ---
echo "[4/8] Setting up project..."
if [ ! -d "$INSTALL_DIR" ]; then
    mkdir -p "$INSTALL_DIR"
    cp -r . "$INSTALL_DIR/"
    chown -R $ACTUAL_USER:$ACTUAL_USER "$INSTALL_DIR"
fi

mkdir -p "$INSTALL_DIR"/{data,models,logs}
chown -R $ACTUAL_USER:$ACTUAL_USER "$INSTALL_DIR"/{data,models,logs}

# --- Python Virtual Environment ---
echo "[5/8] Creating Python environment..."
sudo -u $ACTUAL_USER $PYTHON -m venv "$VENV_DIR"
sudo -u $ACTUAL_USER "$VENV_DIR/bin/pip" install --upgrade pip wheel
sudo -u $ACTUAL_USER "$VENV_DIR/bin/pip" install -r "$INSTALL_DIR/requirements_pi.txt"

# --- tmpfs for Logs (RAM-based, reduces SD writes) ---
echo "[6/8] Setting up tmpfs for logs..."
if ! grep -q "richbot/logs" /etc/fstab; then
    echo "tmpfs $INSTALL_DIR/logs tmpfs defaults,noatime,nosuid,nodev,size=32M,mode=0755,uid=$(id -u $ACTUAL_USER),gid=$(id -g $ACTUAL_USER) 0 0" >> /etc/fstab
    mount -a 2>/dev/null || true
    echo "  Logs directory mounted as tmpfs (32MB RAM disk)"
    echo "  Note: Logs are lost on reboot — use journalctl for persistent logs"
fi

# --- SystemD Service ---
echo "[7/8] Installing systemd service..."
cat > /etc/systemd/system/richbot.service << SVCEOF
[Unit]
Description=RichBot Grid Trading Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$ACTUAL_USER
Group=$ACTUAL_USER
WorkingDirectory=$INSTALL_DIR
ExecStartPre=/bin/sleep 10
ExecStart=$VENV_DIR/bin/python main.py --config config_pi.json
Restart=always
RestartSec=30
StartLimitBurst=5
StartLimitIntervalSec=600

MemoryMax=2G
MemoryHigh=1536M
CPUWeight=80

StandardOutput=journal
StandardError=journal
SyslogIdentifier=richbot

Environment=PYTHONUNBUFFERED=1
Environment=TF_CPP_MIN_LOG_LEVEL=3
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=MALLOC_TRIM_THRESHOLD_=100000
EnvironmentFile=-$INSTALL_DIR/.env

NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=$INSTALL_DIR/data $INSTALL_DIR/logs $INSTALL_DIR/models

[Install]
WantedBy=multi-user.target
SVCEOF
systemctl daemon-reload
systemctl enable richbot.service
echo "  Service installed (start with: sudo systemctl start richbot)"

# --- Kernel Tuning ---
echo "[8/8] Applying kernel tuning..."
SYSCTL_CONF="/etc/sysctl.d/99-richbot.conf"
cat > "$SYSCTL_CONF" << 'SYSCTL'
# Reduce swappiness (prefer RAM over swap)
vm.swappiness=10
# Less aggressive dirty page flushing (reduce SD writes)
vm.dirty_ratio=40
vm.dirty_background_ratio=10
vm.dirty_writeback_centisecs=1500
# Network buffers for WebSocket stability
net.core.rmem_max=2097152
net.core.wmem_max=2097152
SYSCTL
sysctl -p "$SYSCTL_CONF" > /dev/null

echo ""
echo "==========================================="
echo "  Setup Complete!"
echo "==========================================="
echo ""
echo "Next steps:"
echo "  1. Edit /home/pi/richbot/.env with your API keys"
echo "  2. Copy trained models to /home/pi/richbot/models/"
echo "     (Train on desktop: python main.py --train-ml)"
echo "  3. Set up Neon DB + Vercel Dashboard:"
echo "     a) Create Neon project: https://console.neon.tech"
echo "     b) Add NEON_DATABASE_URL to .env"
echo "     c) Set CLOUD_ENABLED=true in .env"
echo "     d) Deploy web/ to Vercel:"
echo "        cd web && npx vercel"
echo "     e) Set DATABASE_URL + BOT_ID in Vercel env vars"
echo "     f) Push schema: cd web && npx prisma db push"
echo "  4. Start the bot:"
echo "     sudo systemctl start richbot"
echo "  5. Check status:"
echo "     sudo systemctl status richbot"
echo "     journalctl -u richbot -f"
echo ""
echo "Memory budget on Pi 5 (8GB):"
echo "  Bot:       ~150-250 MB (optimized deps + cloud sync)"
echo "  System:    ~1-2 GB"
echo "  Free:      ~5-6 GB"
echo ""
echo "Optimizations applied:"
echo "  - tmpfs for logs (no SD card writes)"
echo "  - WAL journal + batched SQLite writes"
echo "  - noatime + commit=120 for SD"
echo "  - Kernel tuning for low swappiness"
echo "  - Minimal dependencies (no optuna/streamlit/scipy)"
echo ""
