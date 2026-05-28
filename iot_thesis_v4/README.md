# IoT Monitoring System v4 — Production-Ready Deployment

This is the **fourth-generation** backend and frontend for the IoT-Based Monitoring and Alert System for Split HVAC and Gas Dryers.

v4 is identical in functionality to v3 but has been hardened for production deployment and public repository sharing:
- **No hardcoded credentials** — all sensitive values require environment variables
- **Full TLS validation** — ESP32 firmware uses ISRG Root X1 CA certificate instead of `setInsecure()`
- **Deployment scripts included** — Ubuntu server setup, nginx config, systemd service, firewall rules
- **Clean firmware for GitHub** — `_Clean` variant with placeholder credentials committed; real version excluded via `.gitignore`

## What's New in v4 (vs v3)

- **No Hardcoded Defaults:** `MQTT_HOST`, `MQTT_USER`, and `MQTT_PASS` in `app.py` require environment variables. Missing values raise `RuntimeError` instead of falling back to hardcoded credentials.
- **Full TLS Certificate Validation:** ESP32 firmware embeds the ISRG Root X1 CA certificate and uses `setCACert()` instead of `setInsecure()`. This validates the HiveMQ Cloud server certificate chain.
- **Production Deployment Scripts:** `deployment/` directory contains:
  - `setup-ubuntu.sh` — automated server provisioning
  - `nginx-iot-monitor.conf` — reverse proxy with HTTPS and rate limiting
  - `iot-backend.service` — systemd service for Gunicorn
  - `setup-firewall.sh` — UFW rules (22/80/443/8883)
  - `.env.production` — environment template with placeholders
- **Clean Firmware for GitHub:** `Update_SensorNode_Clean/` contains sanitized firmware with placeholder credentials. The real `Update_SensorNode/` is excluded via `.gitignore`.
- **Backend Owns CF/Deductor:** (Retained from v3) The backend stores calibration factor (CF) and deductor per appliance. Node receives them via MQTT and computes `CurrentA` locally.
- **All Telemetry Stored:** (Retained from v3) Every 10-second window is stored in PostgreSQL. Filtered/unfiltered toggle controls display.
- **Threshold Standardization:** All current thresholds are consistently **0.25 A**.

## Architecture

```
iot_thesis_v4/
├── app.py                  # Flask backend (no hardcoded defaults, env-only)
├── templates/
│   ├── dashboard.html      # Main SPA frontend
│   ├── login.html          # User login
│   └── signup.html         # User registration
├── Update_SensorNode/      # Real firmware with credentials (local only, gitignored)
├── Update_SensorNode_Clean/  # Sanitized firmware for GitHub
│   └── Update_SensorNode_Clean.ino
├── deployment/             # Production deployment scripts
│   ├── .env.production     # Production env template (placeholders)
│   ├── nginx-iot-monitor.conf
│   ├── iot-backend.service
│   ├── setup-firewall.sh
│   └── setup-ubuntu.sh
└── requirements.txt        # Includes gunicorn
```

## Database

v3 uses the **same PostgreSQL schema** as v2 with two added columns:

```sql
ALTER TABLE appliances ADD COLUMN IF NOT EXISTS cf REAL;
ALTER TABLE appliances ADD COLUMN IF NOT EXISTS deductor REAL;
```

**Default values:**
- HVAC: `cf = 11.0`, `deductor = 0.033`
- Dryer: `cf = 33.0`, `deductor = 0.111`

Existing rows must be backfilled with a one-time migration before deploying.

## SPC Metrics

Unchanged from v2. See v2 `README.md` or `AGENTS.md` for the full metric tables.

## Workflow

1. **Pair Device** — backend inserts appliance with default CF/deductor based on type.
2. **Node Receives Config** — on `event_request_config`, backend sends `settype:*` + `setcf:*` + `setdeductor:*` + `restore:*`.
3. **Node Computes Current** — using received CF/deductor, persists in NVS.
4. **Calibrate HVAC** (same as v2 — ice-bath method)
5. **Configure Baseline** (same as v2 — manual UCL/LCL input)
6. **Monitor** — real-time charts with optional unfiltered view.
7. **Export** — choose whether to include idle data.

## Data Retention Note

Because v4 stores **all** telemetry (not just running), database size will grow approximately **3–5×** faster than v2. Consider implementing a retention policy for long-term production use.

## Deployment

v4 includes production deployment scripts for Ubuntu. See `deployment/setup-ubuntu.sh` for automated provisioning.

**Quick start:**
1. Copy `deployment/.env.production` to `.env` and fill in real credentials
2. Run `deployment/setup-ubuntu.sh` as root
3. The script installs PostgreSQL, nginx, Certbot, and configures the firewall
4. Flask runs under Gunicorn via systemd; nginx reverse-proxies ports 80/443

**Ports:**
- 22 — SSH
- 80 — HTTP (redirects to HTTPS)
- 443 — HTTPS (dashboard)
- 8883 — MQTTS outbound to HiveMQ Cloud
- 5432 — PostgreSQL (localhost only)

## Recent Fixes

### 2026-05-14
- **Chart6 destroy fix:** `initCharts` now destroys and nulls `chart6` alongside charts 1–5. Prevents "Canvas is already in use" error on filter toggle that was leaving all charts empty.
- **History fetch cache-busting:** `&_cb=${Date.now()}` appended to fetch URL to bypass browser cache on rapid toggles.
- **setTimeout in onFilterChange:** 50ms delay before `initCharts` lets Chart.js finish cleanup.
- **Delta RH section visibility:** HVAC `initCharts` now shows `section-chart-6` (was hidden by dryer view and never restored).
- **Dryer pushToCharts fix:** Explicit `null, null` for val5/val6 prevents argument misalignment.
- **Radio button sync:** Filter radio buttons reset to "Filtered" on modal open to match `showIdle = false` default.
- **Export modal sync:** Pre-fills export dates from active history range when in history mode.
- **Idle data checkbox:** Explicitly sends `filtered=false` when checked so idle data is actually exported.

### 2026-05-15 — Inverter/Non-Inverter Pairing Fix
- **Root cause:** Pairing form sent `name="subtype"` but backend read `request.form.get('sub_type')`. Mismatch caused every device to default to `'noninverter'` regardless of user selection.
- **Fix:** Form field changed to `name="sub_type"` and option value to `value="noninverter"`.
- **Card display:** Template now only shows `sub_type` for HVAC (`'HVAC' in a.type`), hiding it for dryers.
- **DB update:** Set `sub_type = 'inverter'` for "AC WS 1" (189), "1 - AC01" (197), "5 - AC Home 01" (202).

### 2026-05-17 — Humidity Calibration Fully Removed
- **Change:** All humidity `apply_calibration()` calls removed. RH values now use **raw sensor values** throughout (dashboard, charts, exports, SPC, fault detection).
- **Reason:** Linear regression calibration for humidity was unreliable — operating conditions frequently produced values outside the 0–100% physical range. Raw values were adopted as the definitive approach after advisor consultation.
- **Impact:** Dashboard, charts, exports, and SPC baselines all use raw RH. `rhreturn_slope`, `rhreturn_intercept`, `rhsupply_slope`, `rhsupply_intercept` columns are deprecated and unused.

### 2026-05-15 — Humidity Calibration Clamp Reverted (Superseded)
- ~~Removed `clamp_to=(0, 100)` from humidity `apply_calibration()` calls.~~
- **Superseded by 2026-05-17 entry above** — humidity calibration has been fully removed, not just unclamped.

### 2026-05-16 — Monthly Energy Consumption Pie Chart
- **Pie chart** at top of dashboard showing monthly energy grouped by appliance type (HVAC = blue, Dryer = orange).
- **Month selector** only shows months that have actual sensor data (queries DB via `/api/energy_months`).
- **Summary panel:** Total kWh, per-type breakdown with percentages, sorted per-appliance list.
- **Excel export** with month, export date, type, name, energy, and total.
- **Backend:** `_compute_energy_kwh()`, `/api/energy_summary`, `/api/energy_summary/export`, `/api/energy_months`.
- **Updates every 5 seconds.** Forgotten devices excluded.

### 2026-05-12
- **Delta RH chart (chart6)** added for HVAC — shows `abs(RHreturn - RHsupply)` with pink `#EC4899` line.

### 2026-05-17
- **Baseline removal feature** — New ` Remove Baseline` button appears when baseline exists. `DELETE /api/device/<id>/baseline_config` clears all baseline rows and sets `baseline_configured = FALSE`. Frontend wipes SPC lines from charts and refreshes UI state.
- **Discord alert testing documented** — Fault alerts can be tested by setting tight UCL/LCL baselines (e.g., Current UCL = 2.01 for a 2.0 A motor baseline) so normal running data immediately crosses thresholds. This fires real Discord alerts with the maintenance-ticket embed format. See AGENTS.md §11 for examples, limitations (10-min cooldown, DB pollution), and cleanup instructions.
- **Idle point styling removed** — `updateChart` no longer applies gray tiny-dot styling to idle points. Idle and running points now render identically. The filtered/unfiltered toggle still controls visibility.
- **Dryer cycle RH refined** — `start_rh` computed from first 6 RH readings (was single point). `end_rh_avg` computed from last 6 RH readings (was last 10). Both live fault detection and historical analytics updated.

### 2026-05-10
- **DHT22 stuck detection** in firmware — per-window (10s) stuck-value detection with `dht.begin()` re-init after 3 consecutive stuck windows.
