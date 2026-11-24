#!/usr/bin/env bash
# ==============================================================================
# RdpNet: Turnkey Installer for Ubuntu VPS (20.04, 22.04, 24.04 LTS)
# Installs Relay Core (:8443) and Web Dashboard (:8080) as systemd service
# ==============================================================================

set -e

# Color output
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${BLUE}====================================================${NC}"
echo -e "${BLUE}       RdpNet Ubuntu VPS Turnkey Installer          ${NC}"
echo -e "${BLUE}====================================================${NC}"

# Check for root privileges
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}[!] Please run this installer as root or with sudo:${NC}"
    echo "    sudo bash install_vps.sh"
    exit 1
fi

# Install Python 3 if missing
echo -e "${YELLOW}[*] Checking Python 3 dependencies...${NC}"
if command -v python3 >/dev/null 2>&1 && command -v curl >/dev/null 2>&1; then
    echo -e "${GREEN}[✓] Python 3 and curl are already installed.${NC}"
else
    echo -e "${YELLOW}[*] Waiting for background apt process to release lock...${NC}"
    while fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1 || fuser /var/lib/apt/lists/lock >/dev/null 2>&1; do
        sleep 2
    done
    apt-get update -qq
    apt-get install -y -qq python3 curl
fi

INSTALL_DIR="/opt/rdpnet"
echo -e "${YELLOW}[*] Installing RdpNet into ${INSTALL_DIR}...${NC}"
mkdir -p "${INSTALL_DIR}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Clean copy server code to avoid nested directories on updates
rm -rf "${INSTALL_DIR}/server" "${INSTALL_DIR}/client"
cp -r "${SCRIPT_DIR}/server" "${INSTALL_DIR}/"
cp -r "${SCRIPT_DIR}/client" "${INSTALL_DIR}/" 2>/dev/null || true

# Install systemd service
echo -e "${YELLOW}[*] Configuring systemd service...${NC}"
cp "${SCRIPT_DIR}/scripts/rdpnet.service" /etc/systemd/system/rdpnet.service
systemctl daemon-reload
systemctl enable rdpnet
systemctl restart rdpnet

# Configure UFW firewall if active
if command -v ufw >/dev/null 2>&1; then
    if ufw status | grep -q "Status: active"; then
        echo -e "${YELLOW}[*] Configuring firewall ports (8443, 8080)...${NC}"
        ufw allow 8443/tcp comment "RdpNet Relay"
        ufw allow 8080/tcp comment "RdpNet Web Dashboard"
        ufw reload
    fi
fi

# Detect Server Public IP
PUBLIC_IP=$(curl -s https://api.ipify.org || hostname -I | awk '{print $1}')

sleep 1

if systemctl is-active --quiet rdpnet; then
    ADMIN_PASS=$(cat "${INSTALL_DIR}/admin.secret" 2>/dev/null || echo "See logs: journalctl -u rdpnet")

    echo -e "${GREEN}====================================================${NC}"
    echo -e "${GREEN}  ✓ RdpNet successfully installed and running!      ${NC}"
    echo -e "${GREEN}====================================================${NC}"
    echo ""
    echo -e "Web Management Dashboard:  ${BLUE}http://${PUBLIC_IP}:8080${NC}"
    echo -e "Web Admin Password:        ${GREEN}${ADMIN_PASS}${NC}"
    echo -e "Relay Core Port:           ${BLUE}${PUBLIC_IP}:8443${NC}"
    echo ""
    echo -e "${YELLOW}Quick Start:${NC}"
    echo "1. Open http://${PUBLIC_IP}:8080 in your browser and enter the Admin Password above."
    echo "2. Create or select your isolated network and copy the one-click connect command."
    echo "3. Run rdp-agent.exe on your Windows machines!"
    echo ""
    echo "Password file location:    ${INSTALL_DIR}/admin.secret"
    echo ""
    echo "Service management commands:"
    echo "  systemctl status rdpnet"
    echo "  systemctl restart rdpnet"
    echo "  journalctl -u rdpnet -f"
    echo ""
else
    echo -e "${RED}[!] Error: rdpnet service failed to start. Showing recent logs:${NC}"
    echo "----------------------------------------------------"
    journalctl -u rdpnet -n 25 --no-pager || true
    echo "----------------------------------------------------"
    exit 1
fi
