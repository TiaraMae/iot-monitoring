#!/bin/bash
# Complete Ubuntu Server setup script for IoT Monitoring (Hybrid mode)
# Run on a fresh Ubuntu 22.04 LTS mini PC
# Usage: sudo bash setup-ubuntu.sh

set -e

APP_DIR="/opt/iot-monitoring"
DB_NAME="iot_db"
DB_USER="iot_user"
APP_USER="iot"

echo "=== IoT Monitoring Server Setup (Hybrid: Local DB + HTTPS + HiveMQ Cloud) ==="

# 1. Update system
echo "[1/10] Updating system packages..."
apt update && apt upgrade -y

# 2. Install dependencies
echo "[2/10] Installing dependencies..."
apt install -y python3 python3-venv python3-pip postgresql postgresql-contrib \
    nginx certbot python3-certbot-nginx git ufw fail2ban net-tools

# 3. Create app user
echo "[3/10] Creating application user..."
if ! id "$APP_USER" &>/dev/null; then
    useradd -r -s /bin/false -d "$APP_DIR" -m "$APP_USER"
fi

# 4. Setup PostgreSQL
echo "[4/10] Setting up PostgreSQL..."
systemctl enable postgresql
systemctl start postgresql

# Generate random DB password if not set
DB_PASS="${DB_PASSWORD:-$(openssl rand -base64 32)}"
sudo -u postgres psql -c "CREATE USER $DB_USER WITH PASSWORD '$DB_PASS';" 2>/dev/null || true
sudo -u postgres psql -c "CREATE DATABASE $DB_NAME OWNER $DB_USER;" 2>/dev/null || true
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE $DB_NAME TO $DB_USER;"

# Bind PostgreSQL to localhost only
sed -i "s/#listen_addresses = 'localhost'/listen_addresses = '127.0.0.1'/g" /etc/postgresql/*/main/postgresql.conf
systemctl restart postgresql

echo "  PostgreSQL password for $DB_USER: $DB_PASS"

# 5. Clone / copy application
echo "[5/10] Setting up application directory..."
mkdir -p "$APP_DIR"
chown "$APP_USER:$APP_USER" "$APP_DIR"

echo "  NOTE: Copy your project files to $APP_DIR and run:"
echo "    cd $APP_DIR/iot_thesis_v4"
echo "    python3 -m venv venv"
echo "    venv/bin/pip install -r requirements.txt"

# 6. Create .env file template
echo "[6/10] Creating .env template..."
cat > "$APP_DIR/iot_thesis_v4/.env" <<EOF
FLASK_SECRET_KEY=$(openssl rand -hex 32)
DB_HOST=localhost
DB_PORT=5432
DB_NAME=$DB_NAME
DB_USER=$DB_USER
DB_PASSWORD=$DB_PASS
MQTT_HOST=YOUR_MQTT_BROKER.hivemq.cloud
MQTT_PORT=8883
MQTT_USER=YOUR_MQTT_USERNAME
MQTT_PASS=YOUR_HIVEMQ_PASSWORD_HERE
EOF
chown "$APP_USER:$APP_USER" "$APP_DIR/iot_thesis_v4/.env"
chmod 600 "$APP_DIR/iot_thesis_v4/.env"

# 7. Systemd service
echo "[7/10] Installing systemd service..."
cp "$APP_DIR/iot_thesis_v4/deployment/iot-backend.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable iot-backend

echo "  NOTE: Start the service after copying project files and installing deps:"
echo "    sudo systemctl start iot-backend"

# 8. Nginx
echo "[8/10] Setting up Nginx..."
cp "$APP_DIR/iot_thesis_v4/deployment/nginx-iot-monitor.conf" /etc/nginx/sites-available/iot-monitor
# Remove default site
rm -f /etc/nginx/sites-enabled/default
ln -sf /etc/nginx/sites-available/iot-monitor /etc/nginx/sites-enabled/iot-monitor

# Configure rate limiting for login
if ! grep -q "limit_req_zone" /etc/nginx/nginx.conf; then
    sed -i '/http {/a \    limit_req_zone $binary_remote_addr zone=login:10m rate=1r/s;' /etc/nginx/nginx.conf
fi

nginx -t && systemctl reload nginx
systemctl enable nginx

echo "  NOTE: Run Certbot after pointing your subdomain DNS to this server:"
echo "    sudo certbot --nginx -d your-subdomain.yourdomain.com"

# 9. Firewall
echo "[9/10] Configuring firewall..."
bash "$APP_DIR/iot_thesis_v4/deployment/setup-firewall.sh"

# 10. Fail2ban
echo "[10/10] Configuring Fail2ban..."
systemctl enable fail2ban
systemctl start fail2ban

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Next steps:"
echo "  1. Copy your project files to $APP_DIR/iot_thesis_v4"
echo "  2. Create Python venv and install requirements"
echo "  3. Import your database schema: sudo -u postgres psql $DB_NAME < schema.sql"
echo "  4. Update .env with your actual HiveMQ password"
echo "  5. Start backend: sudo systemctl start iot-backend"
echo "  6. Run Certbot: sudo certbot --nginx -d your-subdomain.yourdomain.com"
echo "  7. Check logs: sudo journalctl -u iot-backend -f"
