# AGENTS.md — IoT Monitoring & Predictive Maintenance Thesis

> **If you are an AI agent working on this repository, read this file first.**
> This document provides the architectural, hardware, and software context needed to make safe, correct changes.

---

## 1. Project Identity

- **Title:** IoT-Based Monitoring and Alert System for Split HVAC and Commercial Gas Dryers
- **Type:** Undergraduate Thesis — Mechanical Engineering
- **Author:** Tiara Mae Muljana
- **Institution:** Swiss German University (SGU)
- **Repository:** [github.com/TiaraMae/iot-monitoring](https://github.com/TiaraMae/iot-monitoring)

### Goal
Develop a retrofit-friendly, low-cost IoT sensor node that attaches to existing residential split-type air conditioners and commercial gas dryers. The system monitors critical thermal and electrical parameters, establishes per-device Statistical Process Control (SPC) baselines, and alerts users when deviation patterns indicate impending failure.

---

## 2. Repository Layout — Two Independent Systems

This repo contains **two completely separate systems**. Do not mix their code, configs, or architectures.

```
iot-monitoring/
├── gas_dryer_test/          # STANDALONE PCB VALIDATION TESTBED
│   ├── esp32dryertest.py    # Simple Flask dashboard + MQTT listener
│   └── esp32dryertest/      # Arduino firmware (BME280 + SCT-013)
│       └── esp32dryertest.ino
│
└── iot_thesis/              # FULL PRODUCTION SYSTEM
    ├── app.py               # Flask backend (MQTT, DB, auth, SPC, API)
    ├── fix_db_columns.py    # One-off DB migration utility
    ├── templates/
    │   ├── dashboard.html   # Single-page monitoring UI
    │   ├── login.html
    │   └── signup.html
    └── Update_SensorNode/   # Production ESP32 firmware
        └── Update_SensorNode.ino
```

### 2.1 `iot_thesis/` — Production System
- Multi-tenant user authentication (bcrypt + Flask-Login)
- Device pairing and unpairing workflows
- Per-appliance calibration (HVAC) and baseline training (both)
- Real-time SPC limit monitoring with Chart.js dashboards
- Maintenance event logging
- Excel export with maintenance logs
- Supports both **inverter** and **non-inverter** HVAC sub-types

### 2.2 `gas_dryer_test/` — PCB Validation Testbed
- Created **before** the production system to verify the custom PCB could read sensors and transmit over MQTT.
- Simpler architecture: single device, no auth, no calibration, no baselining.
- Uses **Neon Cloud PostgreSQL** (not local DB).
- Publishes to a fixed topic: `dryer/BME_TEST_01/telemetry`.
- **Not** part of the production architecture.

---

## 3. Hardware Specifications

### 3.1 MCU
- **ESP32-C3 Super Mini** (RISC-V, Wi-Fi + Bluetooth 5)
- Power: USB-C / 5V adapter

### 3.2 Pin Map (`Update_SensorNode.ino`)

| Pin | Function | Notes |
|-----|----------|-------|
| GPIO 0 | `PINSCTADC` | Current sensor ADC input |
| GPIO 1 | `PINBUTTON` | Maintenance request button (INPUT_PULLUP) |
| GPIO 3 | `PINBUTTON2` | Calibration / Baseline button (INPUT_PULLUP) |
| GPIO 4 | `PINLED` | Status LED |
| GPIO 5 | `PINDHT1` | DHT22 #1 (HVAC return air) |
| GPIO 6 | `PINDHT2` | DHT22 #2 (HVAC supply air) |
| GPIO 7 | `PINDS18B20` | DS18B20 (evaporator coil) |
| GPIO 8 | `PINI2CSDA` | I2C SDA (BME280) |
| GPIO 9 | `PINI2CSCL` | I2C SCL (BME280) |
| GPIO 10 | `PINBUZZER` | Piezo buzzer |

### 3.3 Sensors by Appliance Type

#### HVAC (Split Air Conditioner)
| Sensor | Port | Purpose |
|--------|------|---------|
| DHT22 | GPIO 5 | Return air (intake) temp & humidity |
| DHT22 | GPIO 6 | Supply air (cold output) temp & humidity |
| DS18B20 | GPIO 7 | Evaporator coil temperature |
| **ZHT103C** | GPIO 0 | Compressor live wire current |

- **Current sensor:** ZHT103C
- **Calibration factor:** 11.0
- **RMS deductor:** 0.033

#### Gas Dryer
| Sensor | Port | Purpose |
|--------|------|---------|
| BME280 | I2C (GPIO 8/9) | Exhaust duct temp, humidity, pressure |
| **SCT-013** | GPIO 0 | Motor live wire current |

- **Current sensor:** SCT-013
- **Calibration factor:** 37.0
- **RMS deductor:** 0.111

### 3.4 Current Sensing Math

Both firmwares use the same signal chain:

1. Sample ADC at 1 kHz for 200 ms (`analogRead` in loop)
2. Compute mean and mean-square → variance → RMS ADC
3. `esp_adc_cal_raw_to_voltage(rmsADC, &adc1_chars)` → true mV
4. `true_mV / 1000.0 * calibration_factor` → raw amps
5. `raw_amps - deductor` → final reported current

In `Update_SensorNode.ino`:
```cpp
float cf = (applianceType == "Dryer") ? 37.0 : 11.0;
return (trueVoltageMv / 1000.0) * cf;
```

The deductor is applied as a database column `icompressor_offset` in `app.py`:
```python
default_offset = -0.111 if "Dryer" in dev_type else -0.033
```

---

## 4. Firmware Architecture (`Update_SensorNode.ino`)

### 4.1 Telemetry Timing
- Samples every **2 seconds**
- Averages **5 samples** (10-second publish interval)
- Publishes to: `iot/nodes/<MAC>/telemetry`
- Events publish to: `iot/nodes/<MAC>/events`
- Subscribes to control topic: `iot/nodes/<MAC>/control`

### 4.2 Offline Buffering
- Up to **200 readings** stored in RAM (`std::vector<BufferedData>`)
- Flushes automatically when MQTT reconnects
- Each flushed message gets an `ago_ms` field indicating stale age

### 4.3 State Machine (User-Facing)

```
[Unpaired] --(backend sends settype:hvac/dryer)--> [Paired]

[HVAC Paired]
    |
    v
[Need Calibration] --(Button 2 hold)--> [Calibrating]
    |                                   (ice-bath method)
    |                                       |
    |<--(backend sends calibrationfailack)--|
    |                                       |
    |<--(backend sends calibrationsuccessack)
    v
[Need Baseline] --(Button 2 hold)--> [Baselining]
    |                                       |
    |<--(backend sends baselinefailack)-----|
    |                                       |
    |<--(backend sends baselinesuccessack)
    v
[Normal] --(Button 1 hold)--> [Maintenance Requested]
```

**Dryer skips calibration entirely** and goes straight to `Need Baseline` after pairing.

### 4.4 Calibration Procedure (HVAC Only)
1. User holds **Button 2** for 2 seconds.
2. Node sends `event_button2_action_request` to backend.
3. Backend sends `startcalibration` to node.
4. Node captures **baseline** sensor readings (T1, T2, T3, H1, H2).
5. User places coil sensor (T3) in **ice water**.
6. Node waits for ΔT ≥ 8°C on T3 (with 10-minute timeout).
7. On success, node sends `calibration_success_request` with base/final readings.
8. Backend computes linear regression slopes/intercepts and stores them in `appliances` table.

### 4.5 Baseline Procedure (Both)
1. User holds **Button 2** for 2 seconds (or triggers remotely from dashboard).
2. Backend sets `operational_status = 'baselining'` and records `baselining_since`.
3. Node records data for **10 minutes** while appliance runs normally.
4. Backend background thread (30-second poll) checks elapsed time.
5. After 600 seconds, backend calculates means and standard deviations:
   - HVAC: ΔT, T_coil, RH_return, RH_supply, current
   - Dryer: heat_rise, RH_exhaust, pressure, current
6. Results stored in `appliances` table as SPC baseline parameters.

### 4.6 LED / Buzzer Signals
| State | LED | Buzzer |
|-------|-----|--------|
| WiFi disconnected | Fast blink (200 ms) | — |
| MQTT disconnected | Medium blink (500 ms) | — |
| All connected | Solid ON | — |
| Button pressed | — | 1 short beep (120 ms) |
| Acknowledged | — | 2 short beeps |
| Success | — | 3 short beeps |
| Failure / Denied | — | 1 long beep (900–1500 ms) |

---

## 5. Backend Architecture (`iot_thesis/app.py`)

### 5.1 Threading Model
- **Main thread:** Flask HTTP server (`host='0.0.0.0', port=5000`)
- **MQTT thread:** `mqtt_client.loop_start()` — handles incoming telemetry and events
- **Baseline thread:** `baseline_calc_thread()` — daemon thread polling every 30 seconds for completed baselines

### 5.2 Database Connection Pool
- Uses `psycopg2.pool.SimpleConnectionPool(1, 10)`
- `get_conn()` / `release_conn()` pattern throughout
- **Pool failure is non-fatal** — app logs error and continues

### 5.3 Key Database Tables

#### `users`
```sql
id | email | password_hash | name
```

#### `appliances`
Core fields:
```sql
id | user_id | name | type | brand | location
| operational_status | sub_type | created_at | baselining_since
| treturn_slope | treturn_intercept | rh_return_slope | rhreturn_intercept
| tsupply_slope | tsupply_intercept | rhsupply_slope | rhsupply_intercept
| tcoil_slope | tcoil_offset | icompressor_offset
| baseline_deltat_mean | baseline_deltat_std
| baseline_tcoil_mean | baseline_tcoil_std
| baseline_rh_return_mean | baseline_rh_return_std
| baseline_rh_supply_mean | baseline_rh_supply_std
| baseline_current_mean | baseline_current_std
| threshold_current_min | threshold_current_max
| baseline_heat_rise_mean | baseline_heat_rise_std
| baseline_rh_exhaust_mean | baseline_rh_exhaust_std
| baseline_pressure_mean | baseline_pressure_std
```

**Operational statuses:** `calibration_needed`, `calibrating`, `pending_baseline`, `baselining`, `normal`

#### `sensor_nodes`
```sql
id | mac_address | status | appliance_id | last_seen
```
Status values: `unpaired`, `paired`

#### `hvac_readings`
```sql
id | sensor_node_id | time | treturn | rhreturn | tsupply | rh_upply | tcoil | icompressor
```
⚠️ **Note:** `rh_upply` is the actual DB column name (typo from early development). Do not "fix" it in schema unless you migrate the DB.

#### `dryer_readings`
```sql
id | sensor_node_id | time | texhaust | rh_exhaust | pressure | imotor
```

#### `sensor_events`
```sql
id | sensor_node_mac | event_type | timestamp
```

### 5.4 API Routes (Key Endpoints)

| Route | Auth | Purpose |
|-------|------|---------|
| `GET /dashboard` | Required | Main UI |
| `GET /api/unpaired_nodes` | Required | List unpaired nodes seen in last 30s |
| `GET /api/node/<id>/latest` | Required | Live unpaired node readings |
| `GET /api/device/<id>/latest` | Required | Latest calibrated reading for appliance |
| `GET /api/device/<id>/latest_n` | Required | Last N calibrated readings for charts |
| `GET /api/device/<id>/table_data` | Required | Tabular data for detail view |
| `GET /api/device/<id>/spc_limits` | Required | Baseline mean, UCL, LCL for SPC bands |
| `GET /api/device/<id>/hvac_analytics` | Required | Daily averages (last 30 days) |
| `GET /api/device/<id>/dryer_analytics` | Required | Cycle detection and stats |
| `GET /api/device/<id>/export_excel` | Required | Full data export with maintenance log |
| `POST /api/device/<id>/remote_baseline` | Required | Trigger baseline remotely |
| `POST /api/device/<id>/cancel_baseline` | Required | Cancel active baseline |
| `POST /devices/pair` | Required | Pair node to new appliance |
| `POST /devices/<id>/forget` | Required | Unpair and delete appliance |
| `GET/POST /login` | Public | Authentication |
| `GET/POST /signup` | Public | Registration |

### 5.5 SPC Logic

For each appliance type, baseline statistics establish control limits:

**Inverter HVAC:**
- Uses statistical control limits: `mean ± 3σ` for ΔT, T_coil, RH, and current

**Non-Inverter HVAC & Gas Dryer:**
- Uses absolute current thresholds: `threshold_current_min` and `threshold_current_max` (±20% of baseline mean)
- Other parameters use `mean ± 3σ`

**Dashboard visualization:**
- SPC bands rendered as horizontal dashed lines on Chart.js graphs
- Status badge changes color when latest reading exceeds UCL or falls below LCL

### 5.6 Calibration Data Flow

1. Node sends `calibration_success_request` event with `base` and `final` JSON objects
2. Backend validates deltas: T3 ≥ 7.5°C, T2 ≥ 2.5°C, T1 ≥ 2.5°C
3. If valid, computes 2-point linear regression for each sensor pair:
   - `t1_m, t1_c` = slope/intercept mapping DHT1 → reference (T3)
   - `t2_m, t2_c` = slope/intercept mapping DHT2 → reference (T3)
   - `h2_m, h2_c` = slope/intercept mapping H2 → reference (H1)
4. Stores in `appliances` table
5. Sets `operational_status = 'pending_baseline'`

### 5.7 Known Code Artifacts

- **`rh_upply` typo** in `hvac_readings` INSERT (line ~646). This matches the actual database column name.
- **Hardcoded fallback credentials** remain in `os.getenv(..., default)` calls. These defaults are for backward compatibility only.
- **`ago_ms` vs `ago`** — telemetry payload uses `ago` (seconds) in some contexts and `ago_ms` in others. Backend handles both.

---

## 6. Frontend (`dashboard.html`)

- **Single-page application** built with vanilla JS + Chart.js + Luxon adapter
- **Sidebar navigation:** Appliances list, Nodes list, Add Device modal
- **Main view:** Real-time cards + 4 Chart.js canvases (Temp, Humidity, Current, SPC)
- **Detail modal:** Per-device analytics, wiring guide, maintenance log, Excel export
- **Auto-refresh:** 3-second polling for latest data when a device is selected
- **Responsive:** Collapsible sidebar for mobile

### Wiring Guide Displayed in UI
- HVAC: DHT22 (Port 1) = Return, DHT22 (Port 2) = Supply, DS18B20 (Port 3) = Coil, ZHT103C (Port 6) = Compressor
- Dryer: BME280 (Port 4) = Exhaust, SCT-013 (Port 6) = Motor

---

## 7. Environment Variables

The following can be set in a `.env` file (see `.env.example`):

| Variable | Used In | Default |
|----------|---------|---------|
| `FLASK_SECRET_KEY` | `app.py` | `iot-thesis-secret-change-this-in-production` |
| `MQTT_HOST` | `app.py`, `esp32dryertest.py` | HiveMQ cloud broker |
| `MQTT_PORT` | `app.py`, `esp32dryertest.py` | `8883` |
| `MQTT_USER` | `app.py`, `esp32dryertest.py` | `esp32user` / `esp32dryertest` |
| `MQTT_PASS` | `app.py`, `esp32dryertest.py` | hardcoded fallback |
| `DB_HOST` | `app.py` | `localhost` |
| `DB_PORT` | `app.py` | `5432` |
| `DB_NAME` | `app.py` | `iot_db` |
| `DB_USER` | `app.py` | `postgres` |
| `DB_PASSWORD` | `app.py` | `IOTTHESIS` |
| `NEON_DATABASE_URL` | `esp32dryertest.py` | hardcoded Neon URL |

**Agents must not commit a `.env` file.** It is in `.gitignore`.

---

## 8. Common Pitfalls for Agents

1. **Do not mix `gas_dryer_test/` and `iot_thesis/` architectures.** They are independent systems with different DBs, MQTT topics, and firmware.

2. **Do not commit `__pycache__/` or `.env`.** Both are in `.gitignore`.

3. **Do not "fix" the `rh_upply` typo** in SQL INSERTs unless you also migrate the production database schema.

4. **Calibration factors are appliance-type dependent:**
   - HVAC (ZHT103C): factor = 11.0
   - Dryer (SCT-013): factor = 37.0

5. **The baseline calculation thread runs every 30 seconds.** Any DB schema changes affecting `appliances` or baseline logic must be compatible with this polling interval.

6. **MQTT callbacks are async.** The `on_mqtt_message` handler spawns DB writes and node commands. Do not block it with synchronous I/O.

7. **The `fix_db_columns.py` script is a one-off utility.** It renames `subtype` → `sub_type` in `app.py` source. It is not a general migration tool.

8. **Sensor nodes determine their own type from backend commands.** The node firmware does not auto-detect HVAC vs Dryer; it waits for `settype:hvac` or `settype:dryer` from the backend.

9. **Current readings are stored raw in DB** and calibrated on read via `apply_calibration()` in Python. The only exception is the `icompressor_offset` which is a DB column applied at query time.

10. **The ESP32-C3 has limited RAM.** The offline queue (`MAX_QUEUE_SIZE = 200`) and string operations must not be increased without checking heap availability.

---

## 9. External Assets (Not in Repo Yet)

The following are located in `D:\Tiara\IoT Predictive Maintenance Paper\` and are planned for future inclusion:

- **KiCad PCB files** (`IoT Monitoring PCB/`)
- **Reference papers and standards** (`HVAC/`, `GAS/`, `IoT PdM/`)
- **BME sensor test data** (`bme testing/`)
- **Historical firmware iterations** (`CODE/` — many versions predating `Update_SensorNode`)
- **Thesis proposal and technical implementation guide** (`Thesis/`)

---

## 10. Quick Reference: File Responsibilities

| File | Responsibility |
|------|---------------|
| `iot_thesis/app.py` | Everything backend: HTTP API, MQTT, DB, auth, SPC math, Excel export |
| `iot_thesis/templates/dashboard.html` | Main SPA frontend |
| `iot_thesis/Update_SensorNode.ino` | Production ESP32 firmware for both HVAC and Dryer |
| `gas_dryer_test/esp32dryertest.py` | Testbed Flask dashboard for BME280 validation |
| `gas_dryer_test/esp32dryertest.ino` | Testbed ESP32 firmware (BME280 + SCT-013) |
| `fix_db_columns.py` | One-off source migration: `subtype` → `sub_type` |
| `requirements.txt` | Python dependencies for both backends |
| `.env.example` | Template for all overrideable credentials |
