#!/usr/bin/env bash
# RichBot Auto-Update Script
# Pulls latest code from GitHub, installs deps if changed, restarts bot
set -euo pipefail

INSTALL_DIR="${1:-/home/pi/richbot}"
VENV_DIR="$INSTALL_DIR/.venv"
LOG_TAG="richbot-update"

log() { logger -t "$LOG_TAG" "$1"; echo "[$LOG_TAG] $1"; }

cd "$INSTALL_DIR"

OLD_HEAD=$(git rev-parse HEAD 2>/dev/null || echo "none")
git fetch origin main --quiet 2>/dev/null

REMOTE_HEAD=$(git rev-parse origin/main 2>/dev/null || echo "none")

if [ "$OLD_HEAD" = "$REMOTE_HEAD" ]; then
    log "Bereits aktuell ($OLD_HEAD)"
    exit 0
fi

log "Update gefunden: $OLD_HEAD -> $REMOTE_HEAD"

git reset --hard origin/main --quiet

if git diff "$OLD_HEAD" "$REMOTE_HEAD" --name-only | grep -q "requirements_pi.txt"; then
    log "requirements_pi.txt geaendert — installiere Abhaengigkeiten..."
    "$VENV_DIR/bin/pip" install -r "$INSTALL_DIR/requirements_pi.txt" --quiet 2>/dev/null
fi

SHORT_OLD=$(echo "$OLD_HEAD" | cut -c1-7)
SHORT_NEW=$(echo "$REMOTE_HEAD" | cut -c1-7)
CHANGES=$(git log --oneline "$OLD_HEAD..$REMOTE_HEAD" 2>/dev/null | head -5)

log "Update abgeschlossen: $SHORT_OLD -> $SHORT_NEW"
log "Aenderungen: $CHANGES"

systemctl restart richbot 2>/dev/null && log "Bot neugestartet" || log "Neustart fehlgeschlagen"
