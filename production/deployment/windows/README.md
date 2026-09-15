# Windows Production Deployment

Production deployment guide for `iotmonitor.sgu.ac.id`.

## Steps

1. Install Python, PostgreSQL, Nginx, NSSM, and Certbot.
2. Create database `iot_production_db`.
3. Copy `deployment/.env.production` to `.env`, fill in credentials.
4. Run `setup-ssl.ps1` to obtain SSL certificate.
5. Run `setup-nssm-service.ps1` to install the Flask/Waitress service.
6. Run `setup-firewall.ps1` to harden firewall rules.
7. Copy `nginx-iot-monitor.conf` to the Nginx config folder.

## Ports

- 80 / 443 — public web
- 8883 — MQTTS outbound to HiveMQ Cloud
- 5432 — PostgreSQL localhost only
- 5000 — Waitress/Flask localhost only

## TODO

- Port scripts from `iot_thesis_v4/deployment/windows/` and update paths / DB name.
