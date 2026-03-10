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
echo "[1/7] Installing system dependencies..."
apt-get update -qq
apt-get install -y -qq \
    python3.11 python3.11-venv python3.11-dev \
    libatlas-base-dev libhdf5-dev \
    build-essential git \
    sqlite3

# --- Swap Configuration (important for training/optimization) ---
echo "[2/7] Configuring swap..."
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
echo "[3/7] Optimizing storage I/O..."
if ! grep -q "noatime" /etc/fstab; then
    sed -i 's/defaults/defaults,noatime,commit=120/' /etc/fstab
    echo "  Added noatime and commit=120 to fstab (reduces SD writes)"
fi

# --- Project Setup ---
echo "[4/7] Setting up project..."
if [ ! -d "$INSTALL_DIR" ]; then
    mkdir -p "$INSTALL_DIR"
    cp -r . "$INSTALL_DIR/"
    chown -R $USER:$USER "$INSTALL_DIR"
fi

mkdir -p "$INSTALL_DIR"/{data,models,logs}
chown -R $USER:$USER "$INSTALL_DIR"/{data,models,logs}

# --- Python Virtual Environment ---
echo "[5/7] Creating Python environment..."
sudo -u $USER python3.11 -m venv "$VENV_DIR"
sudo -u $USER "$VENV_DIR/bin/pip" install --upgrade pip wheel
sudo -u $USER "$VENV_DIR/bin/pip" install -r "$INSTALL_DIR/requirements_pi.txt"

# --- SystemD Service ---
echo "[6/7] Installing systemd service..."
cp "$INSTALL_DIR/scripts/richbot.service" /etc/systemd/system/richbot.service
systemctl daemon-reload
systemctl enable richbot.service
echo "  Service installed (start with: sudo systemctl start richbot)"

# --- Kernel Tuning ---
echo "[7/7] Applying kernel tuning..."
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
echo "  3. Start the bot:"
echo "     sudo systemctl start richbot"
echo "  4. Check status:"
echo "     sudo systemctl status richbot"
echo "     journalctl -u richbot -f"
echo "  5. Dashboard:"
echo "     cd /home/pi/richbot && .venv/bin/python main.py --dashboard"
echo ""
echo "Memory budget on Pi 5 (8GB):"
echo "  Bot:       ~300-400 MB (with TFLite inference)"
echo "  Dashboard: ~200-300 MB (optional)"
echo "  Optimizer: ~500-600 MB (run sparingly)"
echo "  System:    ~1-2 GB"
echo "  Free:      ~5-6 GB"
echo ""
