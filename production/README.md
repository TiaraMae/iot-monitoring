# IoT Monitoring System

An end-to-end monitoring platform for split HVAC units (split duct, residential split AC, standing AC) and gas dryers.

Each appliance is fitted with an ESP32-C3 sensor node that measures air temperatures, current draw, and — for dryers — exhaust humidity and pressure. Data flows over MQTT into a Flask backend (PostgreSQL) and is presented on a live web dashboard with energy reporting, SPC baselines, and fault alerting.

## Architecture

```
[Sensor Node] --MQTT--> [EMQX Cloud] --MQTT--> [Flask Backend] --> [PostgreSQL]
                                                          |
                                                       [Dashboard SPA]
```

- **Sensor node** — ESP32-C3, published telemetry over `iot/production/nodes/{mac}/...`.
- **Backend** — modular Flask app that ingests telemetry, manages devices, runs the alert engines, and serves the dashboard.
- **Frontend** — single-page dashboard with live device cards, detail charts, baselines, and Excel exports.

---

## Features

### Sensor Node (Firmware)

Firmware lives in `firmware/Update_SensorNode_Production/` and runs on an ESP32-C3. One firmware image supports both HVAC and gas-dryer modes.

> Before compiling: copy `secrets.h.example` to `secrets.h` (same folder) and fill in your WiFi and MQTT broker credentials. `secrets.h` is gitignored and must never be committed.

**Sensors**
- HVAC: two DS18B20 probes — Port 1 (GPIO 5, input/return air) and Port 2 (GPIO 6, output/supply air).
- Dryer: one BME280 on I2C (GPIO 8/9) for exhaust temperature, RH, and pressure.
- Current clamp on ADC (GPIO 0) for compressor / motor current.

**Controls and indicators**
- Yellow button (GPIO 1): 2-second hold sends a `maintenance_request` event (only when paired and calibrated).
- Hidden button (GPIO 3): 5-second hold requests offset calibration (HVAC only).
- LED shows running (solid), WiFi down (fast blink), MQTT down (medium blink), or idle (brief 10 s blink).
- Buzzer gives audio feedback for pairing, calibration success/failure, maintenance ack/deny, and connection loss.

**MQTT namespace** — `iot/production/nodes/{mac}/`
- Publishes: `telemetry`, `events`
- Subscribes: `control`

**Telemetry payload**
- Common: `mac`, `type` (`HVAC`/`Dryer`), `calstate`, `CurrentA`, `status` (`running`/`idle`)
- HVAC: `DS1Temp`, `DS2Temp`
- Dryer: `BME280Temp`, `BME280Hum`, `BME280Pres`

**Events emitted**
| Event | Meaning |
|---|---|
| `checkin` | Sent on MQTT connect and every 10 minutes while idle |
| `event_request_config` | Sent on connect and every 10 s while unpaired; asks backend for `settype`/`setcf`/`setdeductor`/`restore` |
| `maintenance_request` | Yellow button long press |
| `event_button2_offset_calibration_request` | Hidden button long press (HVAC only) |
| `calibration_progress` | Throttled (~2 s) supply-probe drop during calibration |
| `calibration_success_request` | Supply probe dropped ≥ 8 °C below baseline |
| `calibration_fail_request` | 10-minute timeout or approval timeout |

**Control commands accepted**
| Command | Meaning |
|---|---|
| `settype:hvac\|dryer\|unpaired` | Set appliance type |
| `setcf:<value>` / `setdeductor:<value>` | Current-clamp calibration values |
| `restore:offsetcalibrationneeded` / `restore:normal` | Status restore |
| `baseline:set` | Baselines saved on backend |
| `startcalibration` | Approves a hidden-button calibration request |
| `offsetcalibrationsuccessack` / `offsetcalibrationfailack` | Calibration result ack |
| `maintenanceack` / `maintenancedenied` / `actiondenied:busy` | Maintenance / busy replies |

**Offline buffering**
- When MQTT disconnects, telemetry is queued in memory (up to 200 readings).
- On reconnect, queued readings are flushed with `ago`/`agoms` fields so the backend backdates them.
- Telemetry streams whenever the node is paired — including during calibration, so the dashboard shows the live drop.

### Backend

The Flask backend (`app/`) ingests telemetry, manages devices, and serves the dashboard. All credentials come from environment variables — no hardcoded secrets.

| Module | Responsibility |
|---|---|
| `app/__init__.py` | App factory, blueprint registration, sessions, rate limiting |
| `app/config.py` | Environment-only config and constants |
| `app/db.py` | PostgreSQL connection pool + idempotent startup migrations |
| `app/mqtt.py` | MQTT client, topic dispatch (events vs telemetry) |
| `app/telemetry.py` | Ingest telemetry, apply offsets, store readings, trigger alert checks |
| `app/devices.py` | Pairing/unpairing, node events, settings, Excel export |
| `app/calibration.py` | Offset-calibration handshake and handlers |
| `app/alerts.py` | Delta-T alert engine and dryer fault detection |
| `app/analytics.py` | Energy reports, dryer cycle analytics, SPC baselines |
| `app/auth.py` | Login/signup, password hashing, session handling |
| `app/models.py` | Database helper functions |
| `app/api.py` | Blueprint aggregator |

Key behaviors:
- **Offsets:** HVAC readings are corrected with `treturn_offset`/`tsupply_offset` before storage; corrected values live in `hvac_readings`.
- **Gauge pressure:** computed as `BME280Pres − atmospheric_pressure` (falls back to the SPC pressure baseline if unset).
- **Running state:** `current ≥ 0.25 A`.
- **Offline detection:** a paired node streams every 10 s; silence beyond 120 s marks it offline.
- **Current-clamp profiles:** CF/deductor are owned by the backend and pushed to the node at pairing (persisted in ESP32 NVS):
  - SCT013-015 → CF 33.0, deductor 0.111
  - ZHT103C → CF 11.0, deductor 0.033

### Offset Calibration (HVAC)

A drop-based, firmware-monitored protocol that assumes **both probes sit at the same point** and treats **sensor 1 (Port 1, return/DS1) as the trusted reference**:

1. Place both probes together at the same point. Hidden button (5 s hold) → node sends `event_button2_offset_calibration_request`; backend zeroes stored offsets, marks the device `calibrating`, and approves with `startcalibration`.
2. The firmware captures a baseline from the next valid sample window (AC off), then monitors the **reference probe (DS1)** for cooling.
3. Turn the AC on in cooling mode — when **DS1 drops ≥ 8 °C** below its baseline, the node sends `calibration_success_request` with base/final readings; otherwise a `calibration_fail_request` after 10 minutes. DS2 must follow (≥ 2.5 °C drop) — a supply probe that doesn't track the reference is rejected as faulty.
4. The backend sanity-gates the result (supply must cool ≥ 7.5 °C — rising temperatures never count — and return ≥ 2.5 °C), then aligns sensor 2 to sensor 1: **sensor 1 (Port 1, return) is the trusted reference** (its offset stays 0); sensor 2 (Port 2, supply) gets the offset c = base_ds1 − base_ds2, i.e. the affine model y = m·x + c with m = 1 (a single shared calibration point fixes c only). Both probes then read identically at the calibration point. Success marks the appliance `normal` and records `calibrated_at`; failure reverts to `offset_calibration_needed` with the previous offsets restored. If the node loses power or Wi-Fi mid-calibration, reconnecting automatically returns it to “Calibration Required” and the calibration is simply re-run — the dashboard never stays stuck on “Calibrating...”. **Data display starts at the initial calibration** (the raw calibration-session recordings are discarded at that moment); a later re-calibration keeps all history since the initial calibration.

The dashboard shows a calibration progress bar (`Supply: X °C | Drop: Y / 8.0 °C`) and completes or reverts within one poll — no page refresh needed.

### Alerts

- **HVAC — `fault_hvac_low_delta_t`:** raised when the compressor is running (`current ≥ 0.25 A`) and delta-T stays below the configured LCL for the configured delay (default 5 minutes; the delay resets only when the compressor stops, not on every reading). The LCL and delay are set in the dashboard's baseline editor and are the same values the alert engine reads.
- **Dryer:** cycle-based fault detection for belt snap, roller wear, incomplete drying, and exhaust ventilation blockage.
- **Notifications:** alerts can be forwarded to a Discord webhook (configured per user in the dashboard).

### Dashboard

Single-page app served at `/dashboard` with a fixed sidebar and top navbar.

- **Dashboard view**
  - Monthly energy consumption doughnut chart, total kWh, HVAC/dryer breakdown, per-appliance list, month selector, and Excel export.
  - Device cards with live mini-readings, status badges, and a click-to-open detail modal. The Idle/Running badge sits above the appliance-condition badge (Normal/Warning/Critical). HVAC cards also show **Avg ΔT (24h run)** — the average delta-T over the last 24 h for running readings only, skipping the first 5 minutes of each compressor run. A card strip shows **Device Offline** (red) when the node is silent past the 120 s timeout, or **Awaiting Sensor Data** (yellow) when the node is online but has no readings yet.
- **Device detail modal** (polled every 5 s while open)
  - Live/history chart modes and a **Running only / All data** toggle — Running only shows running data only (`current ≥ 0.25 A`); an empty-state hint explains when no running data exists.
  - HVAC: Return & Supply chart, Delta-T chart, Compressor Current chart. Dryer: Exhaust Temperature chart, Motor Current chart. X-axis ticks render actual reading times in local time.
  - Inline SPC baseline editor (HVAC: delta-T LCL + alert delay only — delta-T is the sole HVAC alert trigger; dryer: exhaust temp, RH, current, pressure), with last-updated timestamp.
  - Alert list with acknowledge/resolve actions and maintenance history.
  - Per-device Excel export with date-stamped filenames (`MMDDYYYY-HHMMSS`, `_filtered`/`_unfiltered` suffix).
  - Calibration-required / calibrating / baseline-not-configured action bars.
- **Add Device view** — scans for unpaired nodes, MAC dropdown, appliance type selector with HVAC technology mapping, current-sensor selector, and a dynamic wiring guide.
- **Discord Alert Settings** — save and test a Discord webhook URL.

---

## Quick Start (Local Development)

1. Copy `.env.example` to `.env` and fill in your MQTT/DB credentials.
2. Create a PostgreSQL database named `iot_production_db` (schema is created automatically on startup).
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Run:
   ```bash
   python -m app
   ```

> Local development works over plain HTTP — the secure-session-cookie setting only activates when `FLASK_ENV=production`.

---

## Hardware Wiring

### HVAC
- DS18B20 probe #1 → Port 1 (GPIO 5) → input/return air
- DS18B20 probe #2 → Port 2 (GPIO 6) → output/supply air
- Current clamp → ADC (GPIO 0)

### Dryer
- BME280 → I2C (GPIO 8/9)
- Current clamp → ADC (GPIO 0)

---

## Pairing & Calibration

1. Power on the ESP32 with the production firmware.
2. In the dashboard, open **Add Device**, scan for unpaired nodes, and select the MAC.
3. Choose the device type (**HVAC** or **Gas Dryer**); for HVAC also choose inverter or non-inverter.
4. Choose the current-sensor model installed on the node — the backend sets the matching CF/deductor automatically (fixed afterwards; changing the sensor means re-pairing).
5. After pairing, calibrate the offset (HVAC, see below), then set the delta-T LCL and alert delay in the device's baseline editor.

### Offset calibration (HVAC)

1. Make sure the AC is **OFF** and both probes have settled at room temperature.
2. Hold the **hidden button for 5 seconds** until the buzzer beeps — the backend approves and the firmware captures the baseline.
3. Immediately start the AC in cooling mode — the supply probe must drop **8 °C** below its baseline (up to 10 minutes).
4. Three short beeps = success (offsets stored, status → Normal). Two long beeps = failed or timed out; repeat from step 1.

### Re-calibration and cancel (HVAC)

When a sensor drifts or is replaced, use **Re-calibrate** in the device detail modal (next to the baseline buttons) instead of forgetting the device — no data is lost:

1. Click **Re-calibrate** (device must be `Normal`). Status → `offset_calibration_needed` and the node pauses telemetry immediately.
2. Run the normal hidden-button flow above (AC off → hold hidden button 5 s → AC cooling → 8 °C drop).
3. New offsets replace the old ones; readings before and after stay intact.

**Cancel Calibration** is available while a calibration is pending (`offset_calibration_needed`) or running (`calibrating`, including the 10-minute hardware window) — but only for devices that have been calibrated before. Cancel restores the exact previous state — old offsets, status `normal`, and the node's telemetry gate re-opens immediately. The **first calibration after pairing (or after forget + re-pair) is mandatory: there is no cancel, and no data is shown until it completes.**

Endpoints: `POST /api/device/<id>/recalibrate`, `POST /api/device/<id>/calibration/cancel`.

---

## Alert Logic

- **HVAC:** if the compressor is running (`current ≥ 0.25 A`) and delta-T stays below the configured LCL for the configured running delay, a `fault_hvac_low_delta_t` alert is raised and (optionally) sent to Discord.
- **Dryer:** cycle-based detection flags belt snap, roller wear, incomplete drying, and exhaust ventilation blockage.

---

## Deployment

See `deployment/windows/README.md` for production Windows server setup with Nginx, Waitress/NSSM, and Let's Encrypt.
