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
- **Calibration factor:** 30.0
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
float cf = (applianceType == "Dryer") ? 30.0 : 11.0;
return (trueVoltageMv / 1000.0) * cf;
```

The deductor is applied in firmware (`readCurrent()`) before the value is sent over MQTT. The backend stores `icompressor_offset` in the `appliances` table for historical reference, but it is **not applied** to current readings — the firmware already sends the deducted value.

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
[Need Calibration] --(Button 2, 5s hold)--> [Calibrating]
    |                                   (ice-bath method)
    |                                       |
    |<--(backend sends calibrationfailack)--|
    |                                       |
    |<--(backend sends calibrationsuccessack)
    v
[Need Baseline] --(web dashboard trigger)--> [Baselining]
    |                                       |
    |<--(backend sends baselinefailack)-----|
    |                                       |
    |<--(backend sends baselinesuccessack)
    v
[Normal] --(Button 1, 2s hold)--> [Maintenance Requested]

[Dryer Paired]
    |
    v
[Need Baseline] --(web dashboard trigger)--> [Baselining]
    |                                       |
    |<--(backend sends baselinefailack)-----|
    |                                       |
    |<--(backend sends baselinesuccessack)
    v
[Normal] --(Button 1, 2s hold)--> [Maintenance Requested]
```

**Dryer skips calibration entirely** and goes straight to `normal` after pairing (no calibration needed). Baseline is **web-triggered only** — physical Button 2 no longer starts baseline recording.

### 4.4 Calibration Procedure (HVAC Only)
1. User holds **Button 2** for **5 seconds**.
2. Node sends `event_button2_calibration_request` to backend.
3. Backend sends `startcalibration` to node.
4. Node captures **baseline** sensor readings (T1, T2, T3, H1, H2).
5. User places coil sensor (T3) in **ice water**.
6. Node waits for ΔT ≥ 8°C on T3 (with 10-minute timeout).
7. On success, node sends `calibration_success_request` with base/final readings.
8. Backend computes linear regression slopes/intercepts and stores them in `appliances` table.

> **Note:** Button 2 baseline trigger was removed. Baseline is now **web-triggered only** via `/api/device/<id>/remote_baseline`.

### 4.5 Baseline Procedure

#### HVAC Baseline
1. User clicks **"Start Baseline Training"** in the web dashboard.
2. Backend verifies appliance is **running** (`current ≥ 0.4 A`). Rejects if idle.
3. Backend sets `operational_status = 'baselining'`, sends `baselinestartack` to node, and starts a **15-minute** `threading.Timer`.
4. Node beeps twice to acknowledge.
5. Appliance runs normally during the 15-minute window.
6. When the timer fires, backend calculates means and standard deviations from all readings received during the window.
7. Results stored in `appliances` table as SPC baseline parameters.
8. Node receives `baselinesuccessack` (3 beeps) or `baselinefailack` (2 long beeps).

#### Dryer Baseline
1. User clicks **"Start Baseline Training"** in the web dashboard. **Can be started while idle** — no running pre-check.
2. Backend sets `operational_status = 'baselining'`, sends `baselinestartack` to node, and starts a **5-minute safety timer**.
3. Node beeps twice to acknowledge.
4. When the dryer starts running, the first running telemetry message cancels the safety timer and starts the **cycle-end watcher** (1-minute timeout).
5. Each subsequent running reading resets the cycle-end watcher.
6. When the dryer cycle ends (no running data for 1 minute), the watcher fires and completes baseline automatically.
7. Backend calculates stats from the recorded cycle data and stores them in `appliances`.
8. Node receives `baselinesuccessack` (3 beeps) or `baselinefailack` (2 long beeps).

> **Safety timeout:** If no running data arrives within 5 minutes of starting dryer baseline, the backend sends `baselinefailack` and returns to `normal`.

#### Cancellation
- Clicking **Cancel Baseline** in the dashboard calls `POST /api/device/<id>/cancel_baseline`.
- Backend cancels all timers, sends `baselinefailack` (2 long beeps), and returns to `normal`.
- **Old baseline data is preserved** — `do_set_baseline_calculated()` only overwrites baseline columns at successful completion.

### 4.6 LED State Machine (Priority Order)
LED shows the **highest-priority** active state:

| Priority | State | LED Pattern |
|----------|-------|-------------|
| 1 | Calibration in progress | Slow blink (1 s on / 1 s off) |
| 2 | Baselining in progress | Alternating fast/slow blink |
| 3 | WiFi disconnected | Fast blink (200 ms) |
| 4 | MQTT disconnected | Medium blink (500 ms) |
| 5 | Appliance running | Solid ON |
| 6 | Idle (all connected) | Brief flash every 10 s |

### 4.7 Buzzer Signals
| Trigger | Pattern |
|---------|---------|
| `baselinestartack` | 2 short beeps |
| `baselinesuccessack` | 3 short beeps |
| `baselinefailack` | 2 long beeps (900 ms) |
| `calibrationsuccessack` | 3 short beeps |
| `maintenancedenied` | 1 long beep (1500 ms) |
| Pairing success | 1 short beep |
| Pairing cleared | 1 long beep |

### 4.8 Data Gating & Running Status
- Firmware **always** includes `"status":"running"` or `"status":"idle"` in telemetry based on `avgCurrent >= 0.4 A`.
- Telemetry is **only published** when `lastAvgCurrent >= 0.4 A` (running). Idle samples are discarded, not buffered.
- Idle periods update `last_seen` via periodic `checkin` events (see §4.9).

### 4.9 Checkin / Offline Indicator System
To distinguish "device offline" from "device alive but idle":

- **Checkin event:** Node publishes `{"mac":"...","event":"checkin"}` every **10 minutes** when idle (`lastAvgCurrent < 0.4 A`).
- **Immediate checkin:** Node also sends a checkin immediately upon receiving `restore:normal` from the backend (after power-cycle reconnect).
- **Backend logic:** `api_device_latest` returns `is_offline = true` if `last_seen` is older than 10 minutes. Returns `has_data = false` if no telemetry readings exist yet.
- **Frontend display:**
  - `is_offline = true` → red banner: **"Device Offline"**
  - `!has_data` → yellow banner: **"Awaiting Sensor Data..."**
  - Online with data → banner hidden, normal values shown

### 4.10 Pairing Persistence (NVS)
`isPaired` and `applianceType` are persisted to ESP32 flash memory via the `Preferences` library (`nodecfg` namespace). This survives power cycles:
- On boot, firmware reads NVS and restores `applianceType` and `isPaired`.
- When `settype:dryer`/`hvac` is received after a reboot, `applyApplianceType()` sees `wasUnpaired = false` and prints **"PAIR CONFIRMED AGAIN"** instead of **"PAIR OK"** — no beep, no flow reset.
- When `settype:unpaired` is received, NVS keys are cleared so the next boot starts fresh.

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
id | email | password_hash | name | created_at
```

#### `appliances` (52 columns)
```sql
-- Identity & Status
id | user_id | name | type | brand | location | created_at
| operational_status | sub_type | baselining_since | calibration_started_at

-- Calibration slopes & intercepts (HVAC ice-bath calibration)
| treturn_slope | treturn_intercept
| rhreturn_slope | rhreturn_intercept
| tsupply_slope | tsupply_intercept
| rhsupply_slope | rhsupply_intercept
| tcoil_slope | tcoil_offset | icompressor_offset

-- Reserved offset columns (future use, currently unused)
| treturn_offset | rhreturn_offset | tsupply_offset | rhsupply_offset | tcoil_offset
| texhaust_offset | rhexhaust_offset | tambient_offset | rhambient_offset | imotor_offset

-- HVAC baselines (set after 10-min baseline recording)
| baseline_deltat_mean | baseline_deltat_std
| baseline_tcoil_mean | baseline_tcoil_std
| baseline_rhreturn_mean | baseline_rhreturn_std
| baseline_rhsupply_mean | baseline_rhsupply_std
| baseline_current_mean | baseline_current_std

-- Dryer baselines (set after 10-min baseline recording)
| baseline_heat_rise_mean | baseline_heat_rise_std
| baseline_rhexhaust_mean | baseline_rhexhaust_std
| baseline_rhambient_mean | baseline_rhambient_std
| baseline_pressure_mean | baseline_pressure_std

-- Thresholds
| threshold_current_min | threshold_current_max | threshold_texhaust_max
```

**Operational statuses:** `calibration_needed`, `calibrating`, `pending_baseline`, `baselining`, `normal`

#### `sensor_nodes`
```sql
id | mac_address | status | appliance_id | created_at | last_seen
```
Status values: `unpaired`, `paired`

#### `hvac_readings`
```sql
id | sensor_node_id | time | treturn | rhreturn | tsupply | rhsupply | tcoil | icompressor
```

#### `dryer_readings`
```sql
id | sensor_node_id | time | texhaust | rh_exhaust | tambient | rh_ambient | imotor | pressure
```
**Note:** `tambient` and `rh_ambient` exist in schema but are **not populated** by the current firmware. Only exhaust (`texhaust`, `rh_exhaust`) and pressure data are recorded.

#### `sensor_events`
```sql
id | sensor_node_mac | event_type | timestamp
```

#### `alerts`
```sql
id | appliance_id | alert_type | message | severity | created_at | acknowledged_at
```
- Populated by backend logic (e.g. end-of-cycle humidity spike for dryers).
- Frontend shows active alerts in a panel and allows acknowledgment.

#### `dryer_bme_readings`
Legacy table from early testbed iterations. Not used by the production `iot_thesis` system.

### 5.4 API Routes (Key Endpoints)

| Route | Auth | Purpose |
|-------|------|---------|
| `GET /dashboard` | Required | Main UI |
| `GET /api/unpaired_nodes` | Required | List unpaired nodes seen in last 30s |
| `GET /api/node/<id>/latest` | Required | Live unpaired node readings |
| `GET /api/device/<id>/latest` | Required | Latest calibrated reading + `is_offline`/`has_data` flags |
| `GET /api/device/<id>/latest_n` | Required | Last N calibrated readings for charts |
| `GET /api/device/<id>/table_data` | Required | Tabular data for detail view |
| `GET /api/device/<id>/spc_limits` | Required | Baseline mean, UCL, LCL for SPC bands |
| `GET /api/device/<id>/baseline_analysis` | Required | Detailed baseline stats per metric |
| `GET /api/device/<id>/hvac_analytics` | Required | Daily averages (last 30 days) |
| `GET /api/device/<id>/dryer_analytics` | Required | Cycle detection and stats |
| `GET /api/device/<id>/export_excel` | Required | Full data export with maintenance log |
| `POST /api/device/<id>/remote_baseline` | Required | Trigger baseline remotely |
| `POST /api/device/<id>/cancel_baseline` | Required | Cancel active baseline |
| `GET/POST /api/device/<id>/thresholds` | Required | Get/set alert thresholds |
| `GET/POST /api/device/<id>/alerts` | Required | Get active alerts / acknowledge |
| `POST /devices/pair` | Required | Pair node to new appliance |
| `POST /devices/<id>/forget` | Required | Unpair and delete appliance |
| `GET/POST /login` | Public | Authentication |
| `GET/POST /signup` | Public | Registration |

> **`api_device_latest` behavior:** Returns HTTP 200 for all states. Includes `is_offline` (no MQTT message >10 min) and `has_data` (telemetry exists) flags. Returns `idle` if last reading is >60 s old.

### 5.5 SPC Logic

For each appliance type, baseline statistics establish control limits:

**Inverter HVAC:**
- Uses statistical control limits: `mean ± 3σ` for ΔT, T_coil, RH, and current

**Non-Inverter HVAC & Gas Dryer:**
- Uses absolute current thresholds: `threshold_current_min` and `threshold_current_max`
- Other parameters use `mean ± 3σ`

**Dashboard visualization:**
- SPC bands rendered as horizontal dashed lines on Chart.js graphs
- Status badge changes color when latest reading exceeds UCL or falls below LCL

### 5.6 Dryer Cycle Detection

`dryer_analytics()` uses gap-based cycle detection on `dryer_readings`:

1. **Cycle start threshold:** `baseline_current_mean × 0.8` (or fallback `0.4 A`)
2. **Cycle end threshold:** `baseline_current_mean × 0.3` (or fallback `0.15 A`)
3. **Gap end:** >600 seconds between consecutive readings forces cycle finalization
4. **Noise filter:** Cycles shorter than 3 minutes are discarded
5. **Per-cycle stats:** min/max temp, start/end RH, ignition count, current consumption, spike average

> **Hysteresis explained:** Cycle starts when current rises above ~64% of baseline mean, ends when it drops below ~24%. This handles gas-dryer ignition gaps without splitting a single cycle.

> **Ignition peak detection:** Uses a dynamic state machine (`IDLE → RISING → FALLING`) with a **prominence threshold** of `max(0.5 A, baseline_current_mean × 0.25)`. Small noise fluctuations (e.g., ±0.03 A) are rejected. Real ignition peaks (typically +0.8–1.2 A above motor baseline) are confirmed. The drop length is dynamic — can be 2, 4, 10+ points.

### 5.7 Alert System

- Backend monitors running→idle transitions via `CYCLE_TRACKER`.
- For dryers: if end-of-cycle RH exceeds the configured threshold, an alert is inserted into the `alerts` table.
- Alerts are per-appliance, displayed in the dashboard alert panel.
- Operator-configurable thresholds via `/api/device/<id>/thresholds`.

### 5.8 Data Flow & MQTT Message Handling

1. **`on_mqtt_message()`** receives telemetry on `iot/nodes/<MAC>/telemetry`:
   - Only **running** data (`current ≥ 0.4 A`) is inserted into `hvac_readings` / `dryer_readings`.
   - Idle readings only update `sensor_nodes.last_seen`.
   - Unknown MACs are **auto-registered** into `sensor_nodes` with `status = 'unpaired'`.

2. **`handle_node_events()`** receives events on `iot/nodes/<MAC>/events`:
   - `event_request_config`: Updates `last_seen`, sends `settype:*` + `restore:*`.
   - `checkin`: Updates `last_seen` (lightweight keepalive).
   - `maintenance_request`: Allowed for dryers unconditionally; for HVAC only when `status = 'normal'`.
   - `calibration_success_request` / `calibration_fail_request`: HVAC calibration state machine.
   - **Deduplication:** `EVENT_DEDUPE_CACHE` prevents duplicate inserts within 5 seconds.

3. **`CYCLE_TRACKER`** monitors dryer running→idle transitions:
   - Tracks in-memory state per appliance.
   - Processes stale cycles (>60 s gap) on new running data.
   - Inserts humidity alerts when end-of-cycle RH exceeds threshold.

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

- **`rhsupply`** in `hvac_readings` — the DB column name is `rhsupply` (not `rh_upply`).
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

3. **Column naming convention:** The DB uses `rhreturn_*`, `rhsupply_*`, `rhexhaust_*`, `rhambient_*` (no underscore between `rh` and the location). Code must match this exactly.

4. **Button 2 durations (updated):** 2s = maintenance request, 5s = one-time HVAC calibration. **Baseline is web-triggered only** — Button 2 no longer starts baseline recording.

5. **Calibration factors are appliance-type dependent:**
   - HVAC (ZHT103C): factor = 11.0
   - Dryer (SCT-013): factor = 30.0

6. **Baseline timers are appliance-specific:**
   - **HVAC:** `threading.Timer(900)` — 15-minute fixed window.
   - **Dryer:** `threading.Timer(300)` — 5-minute safety timeout. Cancelled on first running data. A second `threading.Timer(60)` — cycle-end watcher — resets on every running reading and fires when the cycle ends.

7. **MQTT callbacks are async.** The `on_mqtt_message` handler spawns DB writes and node commands. Do not block it with synchronous I/O.

8. **The `fix_db_columns.py` script is a one-off utility.** It renames `subtype` → `sub_type` in `app.py` source. It is not a general migration tool.

9. **Sensor nodes determine their own type from backend commands.** The node firmware does not auto-detect HVAC vs Dryer; it waits for `settype:hvac` or `settype:dryer` from the backend.

10. **Current readings are stored with the firmware deductor already applied.** The `icompressor_offset` DB column exists for reference but is not actively used in current calculations. Temperature and humidity readings are calibrated on read via `apply_calibration()` in Python.

11. **The ESP32-C3 has limited RAM.** The offline queue (`MAX_QUEUE_SIZE = 200`) and string operations must not be increased without checking heap availability.

12. **Firmware only sends running telemetry.** Idle samples (`current < 0.4 A`) are discarded, not buffered. Do not expect idle data in the readings tables.

13. **`api_device_latest` must return HTTP 200 for all states.** Do not return 404 for missing data — the frontend relies on `is_offline` and `has_data` flags.

14. **Preferences (NVS) namespace is `nodecfg`.** Do not use a different namespace or collide with existing keys (`paired`, `type`).

---

## 9. External Assets (Not in Repo Yet)

The following are located in `D:\Tiara\IoT Predictive Maintenance Paper\` and are planned for future inclusion:

- **KiCad PCB files** (`IoT Monitoring PCB/`)
- **Reference papers and standards** (`HVAC/`, `GAS/`, `IoT PdM/`)
- **BME sensor test data** (`bme testing/`)
- **Historical firmware iterations** (`CODE/` — many versions predating `Update_SensorNode`)
- **Thesis proposal and technical implementation guide** (`Thesis/`)

---

## 10. Recent Changes & Changelog

### 2026-04-27 — Checkin / Offline Indicator Fix
- **Firmware:** Moved checkin timer from `beepShort()` to `loop()` (critical bug fix).
- **Firmware:** Added `publishCheckin()` helper; sends immediate checkin on `restore:normal`.
- **Firmware:** Persisted `isPaired` + `applianceType` in NVS via `Preferences` library.
- **Backend:** `event_request_config` now updates `last_seen` for **all** nodes, not just unpaired.
- **Backend:** Removed duplicate `checkin` handler in `handle_node_events()`.

### 2026-04-27 — Dashboard Enhancements
- **Frontend:** Dryer cycle analytics Start/End columns now show full date+time (`toLocaleString`).
- **Frontend:** `updateMiniCards()` rewritten to use `is_offline`/`has_data` flags instead of `.catch()`.
- **Frontend:** Added offline badge (`detail-offline-status`) to detail modal header.

### 2026-05-01 — Dryer Cycle Detection Fix
- **Backend:** Gap threshold lowered from **600s → 60s** to correctly split separate dryer runs.
- **Backend:** `cycle_start` decoupled from `threshold_current_min`. Now fixed at **0.4A** (matching firmware running gate) to ensure new cycles can start after gaps.
- **Backend:** Noise filter reduced from **3.0 min → 1.0 min** to preserve short dryer bursts that were incorrectly discarded.
- **Backend:** Added temporary debug logging to `dryer_analytics()` (subsequently cleaned up) to diagnose missing cycles.

### 2026-04-30 — BME280 Hardware Failure Confirmed
- **Hardware:** Created `BME280_Test.ino` diagnostic sketch (multiple iterations: simple, gas-test-style, I2C lockup recovery, dual-address 0x76/0x77).
- **Hardware:** I2C scan shows **no devices found at all** on GPIO 8/9. Same sensor was previously detected but returned constant values (24.3°C / 67.3% / 707.5 hPa).
- **Hardware:** User confirmed no external pull-up resistors on PCB. Old 3.3V module likely had built-in pull-ups; replacement modules do not.
- **Hardware:** Tested on breadboard with fresh wiring — still not detected.
- **Hardware:** Gas dryer test firmware (`esp32dryertest.ino`) also returns `❌ BME FAIL` — confirming sensor is **dead**, not a code issue.
- **Hardware:** I2C lockup recovery attempted (SDA not stuck low) — no effect.
- **Hardware:** Both I2C addresses tried (0x76, 0x77) — no response.
- **Root cause:** BME280 chip is dead (likely undervoltage damage from 5V-rated module powered at 3.3V, or ESD). No software fix possible.
- **Update (2026-05-03):** Sensor replaced with new 3.3V-native module. BME280 now detected and reading correctly.

### 2026-04-30 — Chart Tooltip Timestamp Fix & SPC Line Stability
- **Frontend:** Eliminated global `chartTimeLabels` array. Each Chart.js instance now owns its own `timeLabels` array, preventing cross-chart contamination and phantom timestamps.
- **Frontend:** Tooltip callback changed from `chartTimeLabels[context[0].dataIndex]` to `context[0].chart.timeLabels[context[0].dataIndex]`.
- **Frontend:** `pushToCharts` trimming loop now shifts per-chart `timeLabels` in lockstep with that chart's data.
- **Frontend:** `applySPCLines()` now mutates SPC line datasets **in-place** (push/pop/assign) instead of wholesale array replacement (`Array(n).fill(...)`). This avoids Chart.js v4 metadata invalidation that caused `dataIndex` misalignment during tooltip renders.
- **Frontend:** Cache-busting query parameter (`?v=2`) added to CDN script tags to force browsers to load updated code.

### 2026-04-30 — DB Timezone Fix & Backend Hardening
- **Database:** `dryer_readings.time` column migrated from `TIMESTAMP WITHOUT TIME ZONE` to `TIMESTAMPTZ` to match `hvac_readings`. PostgreSQL converted existing naive local timestamps (assumed `Asia/Bangkok` UTC+7) into proper UTC-backed values, fixing browser misinterpretation that caused 7-hour timestamp shifts.
- **Backend:** `on_mqtt_message` now uses `datetime.now(timezone.utc)` instead of `datetime.now()` (local time) when computing `actual_time`. Eliminates ambiguity when server timezone differs from UTC.
- **Backend:** `ago_ms` / `ago` values capped with `max(0, ...)` to prevent negative deltas from creating future timestamps.
- **Backend:** Future-timestamp guard clamps `actual_time` to `now_utc + 1 minute` if the device clock is wrong or sends bogus age values.
- **Backend:** `api_device_latest` staleness check hardened: `is_stale = time_val > now or (now - time_val).total_seconds() > stale_threshold_seconds`. Future-dated readings now correctly show "Idle / Data Stale" instead of fake "Running".

### 2026-04-29 — Baseline Redesign, Ignition Detection, UI Fixes
- **Backend:** Dryer baseline redesigned from fixed 15-minute timer to **cycle-based recording**.
  - Can start while idle (no running pre-check).
  - 5-minute safety timeout: fails if no running data arrives.
  - 1-minute cycle-end detection: auto-completes baseline when cycle ends.
  - `BASELINE_DRYER_TRACKER` for cycle-end watcher timers.
  - Reconnect abort now cancels all baseline timers properly.
- **Backend:** HVAC baseline keeps running pre-check + 15-minute timer.
- **Backend:** Cancel baseline endpoint now cleans up both `BASELINE_TIMER_TRACKER` and `BASELINE_DRYER_TRACKER`.
- **Backend:** Dryer ignition detection replaced rigid "3-drop" rule with dynamic state machine (`IDLE → RISING → FALLING`) + prominence threshold `max(0.5 A, baseline_current_mean × 0.25)`.
- **Backend:** Excel export returns valid `.xlsx` with "No data" message instead of JSON 404 when empty.
- **Backend:** Excel export, analytics, live cards/charts, and calibration progress all now filter by `appliances.created_at` to prevent old pairing data leaks.
- **Frontend:** `btn-rebaseline` only shown after `baseline_analysis` confirms baseline stats exist.
- **Frontend:** `cancelRemoteBaseline()` wired to backend `POST /api/device/<id>/cancel_baseline`.
- **Frontend:** Cancel button added to baselining action bar for both HVAC and Dryer.

### Prior Session — Major Features Added
- Web-triggered baseline only (removed Button 2 baseline trigger).
- Alert system (`alerts` table, configurable thresholds, humidity alerts for dryers).
- Dryer cycle tracker with gap-based detection (>600 s gap) and 3-minute minimum duration filter.
- `CYCLE_TRACKER` for end-of-cycle humidity monitoring.
- `api_device_latest` staleness fix: returns `idle` if last reading >60 s old.
- Chart deduplication (`timeMs <= latestChartTimeMs` guard + `initChartsRequestId`).
- Maintenance history filtered by `appliance.created_at`.
- Auto-register unknown MAC on `event_request_config`.
- Config request retry every 10 s for unpaired nodes.
- `calibrationAcked = true` for dryers immediately (fixes maintenance denied bug).

---

## 11. Known Issues

| Issue | Status | Notes |
|-------|--------|-------|
| BME280 completely dead / not detected | **Fixed** | Original sensor failed (no I2C response). Replaced with new 3.3V-native module; sensor now working correctly. |
| BME280 reads constant values / abnormal pressure | **Hardware** | Sensor returns identical T/H/P across 10s intervals (e.g., 24.3°C / 67.3% / 707.5 hPa). Early warning sign of sensor failure. Caused by undervoltage, missing pull-ups, or defective sensor. |
| BME280 reads 182°C / 100% RH / −204 hPa | **Hardware** | Sensor fault — check I2C wiring or replace BME280 |
| `TESTING_GUIDE.md` untracked | **Git** | Exists in working tree but not committed |
| Excel export empty data error | **Fixed** | Returns valid `.xlsx` with "No data available" message |
| Rebaseline button visible before baseline | **Fixed** | Only shown after `baseline_analysis` confirms stats exist |
| Chart tooltip shows wrong timestamp after SPC lines render | **Fixed** | Per-chart `timeLabels` + in-place SPC updates prevent index drift |
| `dryer_readings.time` stored naive local time (browser showed UTC+7 offset) | **Fixed** | Migrated to `TIMESTAMPTZ`; backend now inserts UTC consistently |
| `alerts` table schema may need manual migration | **DB** | Run CREATE TABLE if not already present in local PG / Neon |

---

## 12. Quick Reference: File Responsibilities

| File | Responsibility |
|------|---------------|
| `iot_thesis/app.py` | Everything backend: HTTP API, MQTT, DB, auth, SPC math, Excel export, alerts, cycle tracking |
| `iot_thesis/templates/dashboard.html` | Main SPA frontend (Chart.js, real-time cards, detail modal, alert panel) |
| `iot_thesis/Update_SensorNode.ino` | Production ESP32 firmware for both HVAC and Dryer |
| `gas_dryer_test/esp32dryertest.py` | Testbed Flask dashboard for BME280 validation |
| `gas_dryer_test/esp32dryertest.ino` | Testbed ESP32 firmware (BME280 + SCT-013) |
| `fix_db_columns.py` | One-off source migration: `subtype` → `sub_type` |
| `requirements.txt` | Python dependencies for both backends |
| `.env.example` | Template for all overrideable credentials |
| `TESTING_GUIDE.md` | Step-by-step testing procedures (untracked) |
