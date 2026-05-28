#!/bin/bash
# Firewall setup script for IoT Monitoring Mini PC
# Run as root: sudo bash setup-firewall.sh

set -e

echo "=== Setting up UFW firewall ==="

# Default policies
ufw default deny incoming
ufw default allow outgoing

# SSH (admin access)
ufw allow 22/tcp comment 'SSH admin access'

# Web (HTTP redirects to HTTPS)
ufw allow 80/tcp comment 'HTTP redirect to HTTPS'
ufw allow 443/tcp comment 'HTTPS web dashboard'

# MQTTS (ESP32 devices)
ufw allow 8883/tcp comment 'MQTTS ESP32 devices'

# Enable firewall
ufw --force enable

echo "=== Firewall rules applied ==="
ufw status verbose

echo ""
echo "Port summary:"
echo "  22/tcp   -> SSH (admin only)"
echo "  80/tcp   -> HTTP (redirects to 443)"
echo "  443/tcp  -> HTTPS (Nginx + Flask)"
echo "  8883/tcp -> MQTTS (HiveMQ Cloud / ESP32)"
echo "  5432/tcp -> PostgreSQL (localhost ONLY, not exposed)"
echo ""
echo "To restrict SSH to a specific admin IP, run:"
echo "  sudo ufw delete allow 22/tcp"
echo "  sudo ufw allow from YOUR_ADMIN_IP to any port 22"
