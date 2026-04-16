#!/usr/bin/env bash
set -euo pipefail

# ─── Botping VPS deployment script (Ubuntu 24.04 + Docker) ───
#
# Usage:
#   git clone https://github.com/YOUR_USER/botping.git /opt/botping
#   cd /opt/botping
#   bash deploy/setup-vps.sh
#
# This script:
#   1. Installs Docker CE + Docker Compose plugin (if not installed)
#   2. Creates .env from template (if missing)
#   3. Builds and starts the container
#
# Safe for systems running Amnezia VPN — Docker uses its own
# bridge networks (172.17.x.x) that don't conflict with VPN tunnels.

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

echo "=== Botping VPS Setup ==="
echo "Project directory: $PROJECT_DIR"
echo ""

# ─── 1. Docker ────────────────────────────────────────────────

install_docker() {
    echo ">>> Installing Docker CE..."
    apt-get update -qq
    apt-get install -y -qq ca-certificates curl gnupg

    install -m 0755 -d /etc/apt/keyrings
    if [ ! -f /etc/apt/keyrings/docker.asc ]; then
        curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
        chmod a+r /etc/apt/keyrings/docker.asc
    fi

    if [ ! -f /etc/apt/sources.list.d/docker.list ]; then
        echo \
          "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
          https://download.docker.com/linux/ubuntu \
          $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
          tee /etc/apt/sources.list.d/docker.list > /dev/null
    fi

    apt-get update -qq
    apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin
    echo ">>> Docker installed: $(docker --version)"
}

if command -v docker &>/dev/null; then
    echo "Docker already installed: $(docker --version)"
else
    if [ "$(id -u)" -ne 0 ]; then
        echo "ERROR: Docker is not installed and this script is not running as root."
        echo "Run: sudo bash deploy/setup-vps.sh"
        exit 1
    fi
    install_docker
fi

if ! docker compose version &>/dev/null; then
    echo "ERROR: docker compose plugin not found."
    echo "Install it: apt-get install docker-compose-plugin"
    exit 1
fi

# Add current user to docker group (skip if root)
if [ "$(id -u)" -ne 0 ]; then
    if ! groups | grep -q docker; then
        echo ">>> Adding $(whoami) to docker group (re-login required)..."
        sudo usermod -aG docker "$(whoami)"
    fi
fi

# ─── 2. .env ──────────────────────────────────────────────────

if [ ! -f "$PROJECT_DIR/.env" ]; then
    echo ""
    echo ">>> .env file not found — creating from template..."
    cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"

    read -rp "Enter ADMIN_BOT_TOKEN: " bot_token
    read -rp "Enter ADMIN_CHAT_IDS (comma-separated): " chat_ids

    sed -i "s|ADMIN_BOT_TOKEN=.*|ADMIN_BOT_TOKEN=$bot_token|" "$PROJECT_DIR/.env"
    sed -i "s|ADMIN_CHAT_IDS=.*|ADMIN_CHAT_IDS=$chat_ids|" "$PROJECT_DIR/.env"
    sed -i "s|DATABASE_PATH=.*|DATABASE_PATH=/app/data/botping.db|" "$PROJECT_DIR/.env"

    echo ">>> .env created. Review it:"
    echo "    nano $PROJECT_DIR/.env"
    echo ""
else
    echo ".env already exists — skipping."
    if ! grep -q "/app/data/" "$PROJECT_DIR/.env"; then
        echo "WARNING: DATABASE_PATH may not be set for Docker."
        echo "For Docker, use: DATABASE_PATH=/app/data/botping.db"
        echo "Edit: nano $PROJECT_DIR/.env"
    fi
fi

# ─── 3. Build & Start ────────────────────────────────────────

echo ""
echo ">>> Building and starting Botping..."
docker compose up -d --build

echo ""
echo "=== Done! ==="
echo ""
echo "Useful commands:"
echo "  cd $PROJECT_DIR"
echo "  docker compose logs -f          # live logs"
echo "  docker compose restart           # restart"
echo "  docker compose down              # stop"
echo "  git pull && docker compose up -d --build  # update"
echo ""
echo "Container status:"
docker compose ps
