#!/usr/bin/env bash
# ==============================================================================
# RdpNet: Turnkey One-Command Installer for Ubuntu VPS (20.04, 22.04, 24.04 LTS)
# Can be executed directly via:
#   curl -sSL https://raw.githubusercontent.com/progossu/rdp-tunnel/main/scripts/install_vps.sh | sudo bash
# ==============================================================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

REPO_URL="${RDPNET_REPO:-https://github.com/progossu/rdp-tunnel.git}"
INSTALL_DIR="/opt/rdpnet"

echo -e "${BLUE}====================================================${NC}"
echo -e "${BLUE}       RdpNet Ubuntu VPS Turnkey Installer          ${NC}"
echo -e "${BLUE}====================================================${NC}"

if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}[!] Please run this installer as root or with sudo:${NC}"
    echo "    curl -sSL https://raw.githubusercontent.com/progossu/rdp-tunnel/main/scripts/install_vps.sh | sudo bash"
    exit 1
fi

echo -e "${YELLOW}[*] Checking dependencies (python3, git, curl)...${NC}"
apt-get update -qq
apt-get install -y -qq python3 git curl

# Check if running from an existing local checkout or standalone via curl | bash
LOCAL_SRC=""
if [ -n "${BASH_SOURCE[0]}" ] && [ -f "$(dirname "${BASH_SOURCE[0]}")/../server/main.py" ]; then
    LOCAL_SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fi

mkdir -p "${INSTALL_DIR}"

if [ -n "${LOCAL_SRC}" ] && [ "${LOCAL_SRC}" != "${INSTALL_DIR}" ]; then
    echo -e "${YELLOW}[*] Installing RdpNet from local repository into ${INSTALL_DIR}...${NC}"
    cp -r "${LOCAL_SRC}/server" "${INSTALL_DIR}/"
    cp -r "${LOCAL_SRC}/client" "${INSTALL_DIR}/" 2>/dev/null || true
    cp -r "${LOCAL_SRC}/scripts" "${INSTALL_DIR}/"
else
    echo -e "${YELLOW}[*] Fetching RdpNet from GitHub into ${INSTALL_DIR}...${NC}"
    TMP_CLONE=$(mktemp -d)
    git clone --depth 1 "${REPO_URL}" "${TMP_CLONE}"
    cp -r "${TMP_CLONE}/server" "${INSTALL_DIR}/"
    cp -r "${TMP_CLONE}/client" "${INSTALL_DIR}/"
    cp -r "${TMP_CLONE}/scripts" "${INSTALL_DIR}/"
    rm -rf "${TMP_CLONE}"
fi

# Generate Web UI admin credentials on first install (preserve on upgrade)
ENV_FILE="${INSTALL_DIR}/.env"
if [ ! -f "${ENV_FILE}" ]; then
    WEB_PASS=$(python3 -c "import secrets; print(secrets.token_urlsafe(12))")
    cat > "${ENV_FILE}" <<EOF
RDPNET_WEB_USER=admin
RDPNET_WEB_PASS=${WEB_PASS}
EOF
    chmod 600 "${ENV_FILE}"
fi

source "${ENV_FILE}"

echo -e "${YELLOW}[*] Configuring systemd service...${NC}"
cp "${INSTALL_DIR}/scripts/rdpnet.service" /etc/systemd/system/rdpnet.service
systemctl daemon-reload
systemctl enable rdpnet
systemctl restart rdpnet

if command -v ufw >/dev/null 2>&1; then
    if ufw status | grep -q "Status: active"; then
        echo -e "${YELLOW}[*] Configuring firewall ports (8443, 8080)...${NC}"
        ufw allow 8443/tcp comment "RdpNet Relay"
        ufw allow 8080/tcp comment "RdpNet Web Dashboard"
        ufw reload
    fi
fi

PUBLIC_IP=$(curl -s --max-time 5 https://api.ipify.org || hostname -I | awk '{print $1}')

sleep 1

if systemctl is-active --quiet rdpnet; then
    echo -e "${GREEN}====================================================${NC}"
    echo -e "${GREEN}  ✓ RdpNet successfully installed and running!      ${NC}"
    echo -e "${GREEN}====================================================${NC}"
    echo ""
    echo -e "Web Dashboard:    ${BLUE}http://${PUBLIC_IP}:8080${NC}"
    echo -e "Web Login:        ${YELLOW}${RDPNET_WEB_USER}${NC}"
    echo -e "Web Password:     ${YELLOW}${RDPNET_WEB_PASS}${NC}"
    echo -e "Relay Core Port:  ${BLUE}${PUBLIC_IP}:8443${NC}"
    echo ""
    echo -e "${YELLOW}Quick Start:${NC}"
    echo "1. Open http://${PUBLIC_IP}:8080 in your browser and log in."
    echo "2. Click 'Connect' on a network -> Download Ready ZIP (Host or Viewer)."
    echo "3. Unzip on Windows and run start_host.bat (or install_autostart.bat) / start_rdp.bat!"
    echo ""
else
    echo -e "${RED}[!] Error: rdpnet service failed to start. Check logs with: journalctl -u rdpnet -e${NC}"
    exit 1
fi
