#!/usr/bin/env bash
# RichBot Raspberry Pi 5 Setup Script
# Run as: sudo bash scripts/setup_pi.sh
set -euo pipefail

INSTALL_DIR="/home/pi/richbot"
VENV_DIR="$INSTALL_DIR/.venv"
USER="pi"

echo "==========================================="
echo "  RichBot — Raspberry Pi 5 Setup"
echo "==========================================="

# --- System Dependencies ---
echo "[1/8] Installing system dependencies..."
apt-get update -qq
apt-get install -y -qq \
    python3.11 python3.11-venv python3.11-dev \
    libatlas-base-dev \
    build-essential git \
    sqlite3

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
    echo "  Creating ${SWAP_SIZE}MB swapfile..."
    fallocate -l ${SWAP_SIZE}M /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    echo "/swapfile swap swap defaults 0 0" >> /etc/fstab
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
    chown -R $USER:$USER "$INSTALL_DIR"
fi

mkdir -p "$INSTALL_DIR"/{data,models,logs}
chown -R $USER:$USER "$INSTALL_DIR"/{data,models,logs}

# --- Python Virtual Environment ---
echo "[5/8] Creating Python environment..."
sudo -u $USER python3.11 -m venv "$VENV_DIR"
sudo -u $USER "$VENV_DIR/bin/pip" install --upgrade pip wheel
sudo -u $USER "$VENV_DIR/bin/pip" install -r "$INSTALL_DIR/requirements_pi.txt"

# --- tmpfs for Logs (RAM-based, reduces SD writes) ---
echo "[6/8] Setting up tmpfs for logs..."
if ! grep -q "richbot/logs" /etc/fstab; then
    echo "tmpfs $INSTALL_DIR/logs tmpfs defaults,noatime,nosuid,nodev,size=32M,mode=0755,uid=$(id -u $USER),gid=$(id -g $USER) 0 0" >> /etc/fstab
    mount -a 2>/dev/null || true
    echo "  Logs directory mounted as tmpfs (32MB RAM disk)"
    echo "  Note: Logs are lost on reboot — use journalctl for persistent logs"
fi

# --- SystemD Service ---
echo "[7/8] Installing systemd service..."
cp "$INSTALL_DIR/scripts/richbot.service" /etc/systemd/system/richbot.service
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
