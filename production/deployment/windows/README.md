# Windows Deployment — IoT Monitoring (Production)

Target host: the Windows mini-PC serving `iotmonitor.sgu.ac.id` (local IP:
see `AGENTS.md` §1 — network details live only in that gitignored file, never in
tracked docs). All steps run on that host in **Administrator PowerShell**. Ported from `iot_thesis_v4/deployment/windows`
for the production app (`run:app`, Waitress, single-process lock).

## Architecture

```
ESP32 nodes ──MQTTS 8883──> EMQX Cloud ──MQTTS 8883──> Backend (Waitress :5000)
Browsers ──443/80──> Nginx (C:\nginx) ──> 127.0.0.1:5000 (Waitress)
Backend ──localhost──> PostgreSQL 16/18 (:5432, local only)
```

## 0. Prerequisites

- Windows mini-PC with a fixed local IP; public 80/443 forwarded to it.
- PostgreSQL installed, `iot_production_db` created, user + password set.
- Python 3.11+ on PATH; repo checked out to `C:\IoTMonitoring_Production`
  (adjust `-AppDir` in the service script if different).
- NSSM extracted to `C:\nssm\` (https://nssm.cc/download).
- Nginx for Windows extracted to `C:\nginx\` (runs from `C:\nginx`).
- DNS `iotmonitor.sgu.ac.id` → public IP.

## 1. Backend configuration

```powershell
cd C:\IoTMonitoring_Production
copy deployment\.env.production .env   # then fill in real values:
#   FLASK_SECRET_KEY   (openssl rand -hex 32 or [guid]::NewGuid())
#   DB_PASSWORD
#   MQTT_HOST / MQTT_USER / MQTT_PASS  (EMQX Cloud Serverless deployment, port 8883)
pip install -r requirements.txt
```

Smoke-test manually first:

```powershell
python run.py        # dev server; open http://127.0.0.1:5000
```

> **Timezone:** all system times are WIB (UTC+7). Set the host clock/timezone
> to Jakarta — the backend logs the active timezone at startup.

## 2. Backend as a Windows service (auto-start, restart on failure)

```powershell
powershell -ExecutionPolicy Bypass -File deployment\windows\setup-nssm-service.ps1
# optional: -AppDir C:\Other\Path -ServiceName IoT-Backend -PythonPath C:\path\python.exe
```

Serves `run:app` via Waitress on 127.0.0.1:5000, logs to `logs\service.*.log`
(10 MB rotation), auto-starts on boot, restarts 5 s after failure.

> **Only ONE backend may run.** `run.py`/`python -m app` acquire
> `backend.lock`; a second instance prints "Another backend is already
> running" and exits — starting it manually while the service runs is safe
> but will be denied.

## 3. Firewall

```powershell
powershell -ExecutionPolicy Bypass -File deployment\windows\setup-firewall.ps1
```

Allows inbound 80/443, blocks inbound 5432/5000 (local only), allows
outbound 8883 (MQTT) and 443.

## 4. Nginx

Pre-SSL (temporary, plain HTTP on 80):

1. Copy `deployment\windows\nginx-temp-http.conf` to `C:\nginx\conf\sites-enabled\`
   (or inline it into `C:\nginx\conf\nginx.conf`).
2. `C:\nginx\nginx.exe`  (start)

Make nginx survive reboots (recommended) — install it as an auto-start
service (also required so the 03:00 Certbot renewal always finds nginx up):

```powershell
powershell -ExecutionPolicy Bypass -File deployment\windows\setup-nginx-service.ps1
```

After SSL exists, delete the temp config and use `nginx-iot-monitor.conf`
(80 → ACME challenge + 301; 443 TLS 1.2/3 with the Let's Encrypt cert;
`/login` rate-limited; `/static` aliased to `C:\IoTMonitoring_Production\app\static`).

> **Gotcha:** save all nginx configs as UTF-8 **without BOM** — a BOM makes
> nginx fail to start with a cryptic error.

## 5. SSL (Let's Encrypt)

```powershell
powershell -ExecutionPolicy Bypass -File deployment\windows\setup-ssl.ps1
```

- Requires Certbot for Windows at `C:\Certbot\bin\certbot.exe` (installs certs
  to `C:\Certbot\live\iotmonitor.sgu.ac.id\`).
- Uses the **webroot** plugin (`C:\nginx\html`) — nginx must be running during
  issuance and renewal.
- Creates a daily 03:00 renewal task (`Certbot-AutoRenew-iotmonitor.sgu.ac.id`)
  that reloads nginx after a successful renew.
- **Never stop nginx in a renewal pre-hook** — that exact mistake caused the
  2026-09-02 outage in v4. Webroot validation needs nginx alive.

## 6. Verification

```powershell
Get-Service IoT-Backend                                  # Running
Invoke-WebRequest http://127.0.0.1:5000 -UseBasicParsing   # backend up
# from an EXTERNAL network (no NAT hairpin on this line):
#   https://iotmonitor.sgu.ac.id  -> dashboard (login page)
```

Check `logs\service.err.log` if the service won't start.

## Management cheat-sheet

| Task | Command |
|---|---|
| Restart backend | `Restart-Service IoT-Backend` |
| Stop for maintenance | `Stop-Service IoT-Backend` |
| Remove service | `C:\nssm\nssm.exe remove IoT-Backend confirm` |
| Reload nginx | `C:\nginx\nginx.exe -s reload` |
| Nginx service | `Restart-Service IoT-Nginx` |
| Force renew cert | `Start-ScheduledTask -TaskName Certbot-AutoRenew-iotmonitor.sgu.ac.id` |
| Backend logs | `C:\IoTMonitoring_Production\logs\` |

## Still TODO (Ubuntu path)

`deployment/ubuntu/` is intentionally empty — the production host is Windows.
The v4 Ubuntu suite (systemd + UFW + certbot) can be ported later if the
backend ever moves to Linux; the app itself is deployment-agnostic.
