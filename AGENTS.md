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

## 2. Repository Layout — Three Independent Systems

This repo contains **three completely separate systems**. Do not mix their code, configs, or architectures.

```
iot-monitoring/
├── gas_dryer_test/          # STANDALONE PCB VALIDATION TESTBED
│   ├── esp32dryertest.py    # Simple Flask dashboard + MQTT listener
│   └── esp32dryertest/      # Arduino firmware (BME280 + SCT-013)
│       └── esp32dryertest.ino
│
├── iot_thesis/              # FULL PRODUCTION SYSTEM v1 (auto-baseline)
│   ├── app.py               # Flask backend (MQTT, DB, auth, SPC, API)
│   ├── fix_db_columns.py    # One-off DB migration utility
│   ├── templates/
│   │   ├── dashboard.html   # Single-page monitoring UI
│   │   ├── login.html
│   │   └── signup.html
│   └── Update_SensorNode/   # Production ESP32 firmware
│       └── Update_SensorNode.ino
│
├── iot_thesis_v2/           # v2 (manual SPC + fault alerts + Discord)
│   ├── app.py               # Flask backend (manual baseline, fault detection, Discord)
│   ├── templates/
│   ├── Update_SensorNode/   # Cleaned firmware (baseline:set only)
│   ├── Update_SensorNode_Data_Auto/  # Continuous telemetry variant
│   ├── AGENTS.md
│   ├── FAULTALERT.md
│   └── README.md
│
└── iot_thesis_v3/           # ACTIVE DEVELOPMENT v3 (backend-driven CF, continuous telemetry)
    ├── app.py               # Flask backend (CF/deductor per appliance, all data stored)
    ├── templates/
    │   └── dashboard.html   # 6-chart HVAC, filtered/unfiltered toggle, idle styling
    ├── Update_SensorNode/   # Firmware receives CF/deductor via MQTT, computes CurrentA
    ├── AGENTS.md
    ├── FAULTALERT.md
    └── README.md
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
- **Calibration factor:** 33.0
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
float cf = (applianceType == "Dryer") ? 33.0 : 11.0;
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
| `startcalibration` | 1 short beep |
| `maintenancedenied` | 1 long beep (1500 ms) |
| Pairing success | 1 short beep |
| Pairing cleared | 1 long beep |

> **Note:** Button 2 5s hold (HVAC calibration request) is **silent** — no local beep. The only audible feedback is the `startcalibration` ack from the backend (1 short beep).

### 4.8 Data Gating & Running Status
- Firmware **always** includes `"status":"running"` or `"status":"idle"` in telemetry based on `avgCurrent >= 0.25 A`.
- **Standard v2 firmware (`Update_SensorNode.ino`):** Telemetry is **only published** when `calibrationAcked == true` AND `avgCurrent >= 0.25 A` (running). Idle samples are discarded, not buffered. This prevents unpaired or calibration-needed nodes from leaking data.
- **Data Auto variant (`Update_SensorNode_Data_Auto.ino`):** Once `calibrationAcked == true`, telemetry is **always** published regardless of current. Idle windows are sent with `"status":"idle"`. The backend only inserts `"running"` data into readings tables.
- Idle periods update `last_seen` via periodic `checkin` events (see §4.9).

### 4.9 Checkin / Offline Indicator System
To distinguish "device offline" from "device alive but idle":

- **Checkin event:** Node publishes `{"mac":"...","event":"checkin"}` every **10 minutes** when idle (`lastAvgCurrent < 0.25 A`).
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

1. **Cycle start threshold:** Fixed at **0.4 A** (matching firmware running gate). Decoupled from `threshold_current_min` so new cycles can start after gaps.
2. **Cycle end threshold:** `baseline_current_mean × 0.3` (or fallback `0.15 A`)
3. **Gap end:** >**60 seconds** between consecutive readings forces cycle finalization
4. **Noise filter:** Cycles shorter than **1 minute** are discarded
5. **Per-cycle stats:** min/max temp, start/end RH, ignition count, current consumption, spike average, motor baseline median

> **Hysteresis explained:** Cycle starts when current rises above the 0.4 A firmware gate, ends when it drops below ~24% of baseline mean. This handles gas-dryer ignition gaps without splitting a single cycle.

> **Ignition peak detection:** Uses a dynamic state machine (`IDLE → RISING → FALLING`) with a **prominence threshold** of `max(0.5 A, baseline_current_mean × 0.25)`. Small noise fluctuations (e.g., ±0.03 A) are rejected. Real ignition peaks (typically +0.8–1.2 A above motor baseline) are confirmed. The drop length is dynamic — can be 2, 4, 10+ points.
>
> **Hysteresis & hard floor (2026-05-05 fix):** To prevent over-counting, a peak is only confirmed when:
> - `imotor < _peak_max - 0.1` (hysteresis — must drop 0.1 A from peak)
> - `_peak_max > mean_current + 0.15` (hard floor — peak must exceed mean by at least 0.15 A)
>
> Verified against test data: correctly reports 4 ignitions instead of 5.

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
   - Dryer (SCT-013): factor = 33.0

6. **Baseline timers are appliance-specific:**
   - **HVAC:** `threading.Timer(900)` — 15-minute fixed window.
   - **Dryer:** `threading.Timer(300)` — 5-minute safety timeout. Cancelled on first running data. A second `threading.Timer(60)` — cycle-end watcher — resets on every running reading and fires when the cycle ends.

7. **MQTT callbacks are async.** The `on_mqtt_message` handler spawns DB writes and node commands. Do not block it with synchronous I/O.

8. **The `fix_db_columns.py` script is a one-off utility.** It renames `subtype` → `sub_type` in `app.py` source. It is not a general migration tool.

9. **Sensor nodes determine their own type from backend commands.** The node firmware does not auto-detect HVAC vs Dryer; it waits for `settype:hvac` or `settype:dryer` from the backend.

10. **Current readings are stored with the firmware deductor already applied.** The `icompressor_offset` DB column exists for reference but is not actively used in current calculations. Temperature and humidity readings are calibrated on read via `apply_calibration()` in Python.

11. **The ESP32-C3 has limited RAM.** The offline queue (`MAX_QUEUE_SIZE = 200`) and string operations must not be increased without checking heap availability.

12. **Standard firmware only sends running telemetry.** Idle samples (`current < 0.25 A`) are discarded, not buffered. The Data Auto variant sends both running and idle telemetry once calibrated. The backend gates inserts by `"status"` either way.

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

### 2026-05-14 — v3 Dashboard Fixes (chart6, filter toggle, Delta RH visibility)
- **Chart6 destroy fix:** `initCharts` was destroying charts 1–5 but **not chart6** (Delta RH). On filter toggle, Chart.js threw "Canvas is already in use" because the old chart6 instance was still attached. This aborted `initCharts`, leaving all charts empty. Fixed by adding `chart6` to the destroy and null-reset arrays.
- **Filter toggle cache-busting:** Added `&_cb=${Date.now()}` to history fetch URL to prevent browsers from returning stale cached responses on rapid filtered/unfiltered toggles.
- **setTimeout delay in `onFilterChange`:** Added 50ms async delay before calling `initCharts` to give Chart.js animation frames time to clean up after `destroy()`.
- **Delta RH section visibility:** HVAC `initCharts` restored visibility for `section-chart-5` but not `section-chart-6`. After viewing a dryer (which hides section-chart-6), switching back to HVAC left Delta RH invisible. Added `section-chart-6.style.display = 'block'` in HVAC's `initCharts`.
- **Dryer `pushToCharts` mapping fix:** When `pushToCharts` was expanded from 5 to 6 values, the dryer calls were not updated. They passed `undefined, false/true` which landed in `val6` and `doUpdate` positions, breaking dryer live updates. Fixed by passing explicit `null, null` for val5/val6.
- **Radio button sync on modal open:** `openDeviceDetail` now resets filter radio buttons to "Filtered" to match the `showIdle = false` default, preventing UI/JS state mismatch when reopening the modal.
- **Export modal sync with history range:** When `chartMode === 'history'` and `historyStart`/`historyEnd` are set, `showExportModal()` now pre-fills the export date inputs with the same range and sets the dropdown to "Custom Date Range".
- **"Include idle data" checkbox fix:** The checkbox previously sent no `filtered` param when checked, and the backend defaulted to `filtered=true`. Now explicitly sends `filtered=false` when checked so idle data is actually included in exports.

### 2026-05-15 — Inverter/Non-Inverter Display and Pairing Fix
- **Root cause:** The pairing form sent `name="subtype"` but the backend read `request.form.get('sub_type')`. Field name mismatch caused the backend to always use the default `'noninverter'`, completely ignoring user selection.
- **Fix 1:** Changed form field to `name="sub_type"` and option value to `value="noninverter"` (was `non_inverter`) to align with backend convention.
- **Fix 2:** Card template now only shows `sub_type` for HVAC devices (`{% if a.sub_type and 'HVAC' in a.type %}`), preventing dryers from showing `(NONINVERTER)`.
- **Fix 3:** Updated DB records for three HVAC devices the user had intended as inverter: "AC WS 1" (id 189), "1 - AC01" (id 197), "5 - AC Home 01" (id 202).

### 2026-05-15 — Humidity Calibration Clamp Reverted
- **Change:** Removed `clamp_to=(0, 100)` from all humidity `apply_calibration()` calls and reverted the function to its original 3-parameter signature.
- **Reason:** Calibrated humidity values from linear regression can legitimately exceed 100% when operating conditions fall outside the calibration range. User will consult their advisor before deciding on a final approach (clamp, raw values, or alternative calibration method).
- **Impact:** Dashboard, exports, and SPC calculations now show raw calibrated humidity values as-is from `y = mx + c`.

### 2026-05-12 — Delta RH Chart Added for HVAC (v3 Frontend)
- **New chart6:** Displays `abs(RHreturn - RHsupply)` with pink `#EC4899` line, placed between T_return and T_coil in the 6-chart HVAC layout.
- **Backend:** `api_device_latest` and `api_device_latest_n` return `DeltaRH` field.
- **Frontend:** `pushToCharts` expanded to 6 positional values; history callback recomputes `Math.abs(d.RHreturn - d.RHsupply)`.

### 2026-05-10 — Calendar Date Picker + Auto-Format Time Input (v2 Frontend)
- **Change:** Replaced free-text date/time inputs in History Range and Export Modal with a 3-part picker: calendar `type="date"`, auto-format time text, and AM/PM dropdown.
- **Time auto-format:** Typing digits automatically inserts colons (`092534` → `09:25:34`). Only digits accepted; max 8 chars (`HH:MM:SS`).
- **AM/PM conversion:** Frontend converts 12-hour time + AM/PM selection to 24-hour ISO format (`YYYY-MM-DDTHH:MM:SS`) before sending to backend.
- **Fields updated:** History Range start/end, Export Modal start/end — 4 datetime fields total.
- **Removed:** `normalizeDateTimeInput()` (old free-text parser) and `formatLocalForInput()` (old `toLocaleString()` formatter).

### 2026-05-10 — DHT NaN Corruption Fix + Infinite False Telemetry Fix (v2 Firmware)
- **DHT NaN Problem:** DHT22 intermittently returns NaN (~40% failure rate observed for DHT2). The old code mapped NaN → 0 before adding to the running sum, then divided by `MAX_SAMPLES` regardless of validity. When 2 of 5 samples were NaN, the average was corrupted (e.g., 14.7°C → 8.8°C).
- **DHT Fix:** Added per-metric valid counters (`validDHT1T`, `validDHT2T`, etc.) decoupled from `sampleCount` timing. Averages are computed as `sum / validCount`. If `validCount == 0`, the metric falls back to its last-known-good value (previous window's average). This skips bad samples without corrupting averages or changing publish cadence.
- **Infinite Telemetry Problem:** When the compressor turned off and `currentVal == 0.0` for a full window, `validCurrentA` became 0. The `lastGoodCurrentA` fallback (stale 3.1A from last running window) was used for the average. Since `lastGoodCurrentA` was never updated when `validCurrentA == 0`, this produced false running telemetry **every 10 seconds indefinitely** while the LED showed idle.
- **Telemetry Fix:** Removed `lastGoodCurrentA` fallback for current. Current is the most reliable sensor (ADC). If all 5 samples are 0.0, the true average is **0.0**. Also moved `lastAvgCurrent = currentVal` outside the `if` block so it updates unconditionally, ensuring the LED correctly reflects idle when current is 0.

### 2026-05-12 — HVAC Calibration-Needed Telemetry Leak Fix + Data Auto Variant (v2 Firmware)
- **Problem:** In v2, an HVAC node in `calibration_needed` state could still send telemetry if `avgCurrent >= 0.25 A`. This leaked uncalibrated data to the backend.
- **Fix:** Telemetry gate changed from `if (avgCurrent >= 0.25)` to `if (calibrationAcked && avgCurrent >= 0.25)`. Unpaired and calibration-needed nodes now send **no data at all**.
- **Data Auto variant:** Created `Update_SensorNode_Data_Auto.ino` — a new firmware variant that sends telemetry **continuously** (both running and idle windows) once `calibrationAcked == true`. The `current >= 0.25` gate is removed entirely. The backend's existing `status == "running"` insert gate prevents idle data from flooding the database.
- **Dryer SCT-013 CF:** Corrected from **30.0** → **33.0** across all v2 documentation and the Data Auto firmware.

### 2026-05-10 — Interrupt WDT Timeout Fix + Current Threshold Lowered + Buzzer Hardening (v2 Firmware)
- **Problem:** `Core 0 panic'ed (Interrupt wdt timeout on CPU0)` crash ~10 seconds after telemetry during normal running. Previous `yield()` fixes were insufficient on the single-core ESP32-C3.
- **Root cause:** `loop()` blocked for too long during DS18B20 conversion (750 ms), DHT reads (~4–5 ms each, with disabled interrupts), and 200 ms ADC sampling. ISRs (WiFi, MQTT) were starved, triggering the Interrupt Watchdog (~300 ms timeout).
- **Fix:**
  - Replaced all `yield()` calls with `delay(1)` at critical blocking points. `delay(1)` forces FreeRTOS context switch, guaranteeing ISR servicing. `yield()` does not when the calling task is highest priority.
  - Added `esp_task_wdt_reset()` at the start of `loop()` and inside the 200 ms ADC sampling loop (every 25 reads) and DS18B20 wait loop.
  - DS18B20 changed to non-blocking: `setWaitForConversion(false)` in setup, replaced blocking `requestTemperatures()` with `requestTemperatures()` + yielding 750 ms wait loop.
  - Buffer flush capped at **10 messages per loop** to prevent long blocking during MQTT publish of large offline queues.
- **Current threshold lowered:** 0.4 A → **0.25 A** across all 6 usages in firmware (telemetry gating, LED, status, checkin, BME280 stuck detection).
- **Buzzer UX:** Button 2 5s hold (HVAC calibration request) is now **silent** — no local beep. The only audible feedback is the `startcalibration` ack from the backend (1 short beep).

### 2026-05-07 — Live vs Historical Ignition Count Unification (v2)
- **Problem:** Live auto-update and historical analytics showed **different ignition spike counts** for the same dryer cycle. Live under-counted spikes (e.g., 3 vs 4) due to a stuck FALLING state in the real-time state machine.
- **Root cause:** Two completely different spike detection algorithms. Live used `current - prev > prominence` for entry and `current <= mean + 0.15` for confirmation — the latter never fired when motor baseline (~3.05 A) stayed above the threshold. Historical used `current > prev` for entry and confirmed peaks at cycle end / next rise.
- **Fix (v2 only):** Unified live spike state machine to match historical exactly — entry on any rise, confirm-before-new-rise in FALLING, cycle-end confirmation in `_finalize_dryer_cycle()`. First reading now skipped for spike detection (matching historical). Motor readings collection now gathers all cycle data and filters at cycle end.
- **Result:** Live and historical ignition counts are now identical for all baseline configurations.

### 2026-05-07 — Dryer Ignition Prominence Threshold Lowered to 0.4A (v2)
- **Change:** Prominence threshold changed from dynamic `max(0.35, mean_current * 0.20)` to fixed **0.4A**.
- **Why:** User visually identified 5 spikes but algorithm counted only 3–4. Dynamic formula produced 0.50–0.56A thresholds, filtering borderline bumps.
- **Effect:** Cycle 2 now counts 4 spikes (3.62A bump included). 3.45A bump (0.39A prom) remains just below threshold.
- **Risk:** Very low — motor noise is ±0.03A, 0.4A is >10× above noise floor.

### 2026-05-07 — Cycle-End _confirm_peak() Ordering Bug Fix (v2)
- **Problem:** Live Auto-Update showed **3 ignitions** while History Range showed **4 ignitions** for the same cycle. All algorithm unification and threshold fixes had been applied.
- **Root cause:** In `dryer_analytics()`, the 3 cycle-finalization paths had inconsistent `_confirm_peak()` ordering. Gap and current-drop paths called `_confirm_peak()` **after** `ignition_count` was computed, so pending spikes were confirmed too late. End-of-data path called it **before** — correct.
- **Fix:** Moved `_confirm_peak()` to before `current_spike_avg` and `ignition_count` in both gap and current-drop paths. All 3 paths now confirm pending spikes before computing stats.
- **Result:** Live and history range counts are guaranteed identical.

### 2026-05-07 — BME280 Infinite Reset Loop Fix + Hardening (v2 Firmware)
- **Problem:** BME280 readings froze for ~70 seconds, then produced garbage spikes (e.g., 86.7°C). When a wire became loose, the firmware entered an **infinite reset loop** — out-of-range triggered a soft reset every 2 seconds, but the wire was still loose, so the next reading was also garbage, triggering another reset.
- **Root cause analysis:**
  1. **Initialization:** v2 firmware had no error check on `bme.begin()` and no stabilization delays between `begin()` and `setSampling()`. Sensor could initialize into undefined state.
  2. **I2C bus instability:** Default 400kHz I2C clock + 10ms inter-register delays were marginal under WiFi interrupt load.
  3. **No stuck-value recovery:** Old `bmeStuckCounter` was removed (false positives). But sensor DOES lock up — returning identical cached values for many samples. NaN-based soft reset never triggered because values were not NaN.
  4. **Immediate reset on any bad reading:** Out-of-range and stuck detectors triggered a soft reset on the **first** bad reading, with no cooldown. A loose wire caused an infinite 2-second reset loop.
- **Fix:**
  - **Error check + delays:** Added `if (!bme.begin(...))` + `delay(100)` after begin + `delay(50)` after `setSampling()`.
  - **I2C slowdown:** `Wire.setClock(100000L)` reduces clock from 400kHz → 100kHz for stability.
  - **Inter-register delays:** Increased from 10ms → 50ms between `readTemperature()` / `readHumidity()` / `readPressure()`.
  - **Stuck-value detection:** If temp+hum+pres are identical for 15 consecutive valid samples **while the dryer is running** (`lastAvgCurrent >= 0.25`) → soft reset (with 5s cooldown). Counter resets when idle to avoid false positives in stable ambient conditions.
  - **Out-of-range detection:** If temp > 85°C or pressure < 800hPa → counts up (1/15, 2/15, ...). Requires **15 consecutive** bad readings before reset. Also added lower bounds: temp < -40°C or pressure > 1100 hPa.
  - **Reset cooldown:** All three reset paths (NaN, stuck, out-of-range) now enforce a **minimum 5-second cooldown** between soft resets. This breaks the infinite loop.
  - **Diagnostic logging:** All three paths now log the actual `T=%.1f H=%.1f P=%.1f` values that triggered the condition, making it easy to distinguish I2C timeout (`0.0/0.0/0.0`) from garbage (`86.7/100.0/722.1`) from stuck values.
  - **Unified soft-reset helper:** Extracted reset logic into reusable lambda called by NaN, stuck, and out-of-range paths.
- **Hardware note:** Software mitigations help but cannot fully compensate for missing I2C pull-up resistors (4.7kΩ) or missing decoupling capacitor (100nF). These hardware fixes are strongly recommended.

### 2026-05-07 — LED TX Flash Removed (v2 Firmware)
- **Problem:** When the appliance was running, the LED should be solid ON, but a small super-fast blink was visible every 10 seconds during MQTT telemetry transmission.
- **Root cause:** `publishEventJson()` and `publishTelemetry()` both explicitly toggled the LED OFF → publish → `delay(30)` → LED ON, creating a "TX activity flash." This overrode the LED state machine, which already sets solid ON for running state.
- **Fix:** Removed all `digitalWrite(PINLED, ...)` calls and `delay(30)` from both publish functions. The LED state machine is now the single source of truth for LED behavior.
- **Result:** LED stays perfectly solid when running, with no flicker during transmissions.

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

### 2026-05-07 — Live Chart Buffered Data Refresh
- **Problem:** When offline-buffered data arrived while the dashboard stayed open, the live chart showed gaps (e.g., 3:25:14 → 3:26:14) instead of the buffered points that should fill the gap. After a manual page refresh, the chart rendered correctly.
- **Root cause:** `initCharts()` was called immediately on reconnect, before the backend had finished inserting all buffered MQTT messages. The buffered points that arrived after `initCharts()` completed were dropped by `pushToCharts()` because they were >5 seconds older than `latestChartTimeMs`.
- **Fix:**
  - On reconnect, `initCharts()` now runs after a **1.5-second delay** (`setTimeout`) to let the backend finish inserting buffered rows.
  - Added gap/backward detection in `updateDetailData()`: if a live point is >10 seconds older than `latestChartTimeMs` (late-arriving buffered data) or the gap since the last point is >30 seconds (offline period), `initCharts()` is triggered to reload history.
  - Widened `pushToCharts()` backward guard from 5 seconds → 30 seconds as secondary safety net.

### 2026-05-06 — Idle Badge Delay Fix, Empty Charts Race Condition Fix
- **Idle Badge Delay:** `updateDetailData()` was called before the modal overlay was active, causing its early-exit guard to fire silently. The badge only appeared when the global 5-second interval fired. Fixed by activating the modal **before** calling `updateDetailData()` in `openDeviceDetail()`.
- **Empty Charts Race Condition:** After the badge fix, `updateDetailData()` ran concurrently with `initCharts()`'s async history fetch. Live data pushed to charts set `latestChartTimeMs` to the newest timestamp, causing all subsequent history data points to be skipped (they were older). Added `historyLoading` flag: set `true` when `initCharts()` starts, cleared when history fetch completes. `updateDetailData()` only pushes to charts when `!historyLoading`.

### 2026-05-06 — Energy kWh Integration, HVAC Analytics Daily Report, Date Input Auto-Format, HVAC Fault Alert Refinement
- **Energy kWh:** Replaced raw current sum with proper kWh calculation (`energy_ws = Σ(current × voltage × dt) / 3,600,000`). Added `appliances.voltage` column (default 220V for Indonesia). Added `get_appliance_voltage()` helper.
- **Dryer Analytics:** Per-cycle `energy_kwh` computed using actual time deltas between readings. Table column renamed from `Consumption (A)` to `Energy (kWh)`.
- **HVAC Analytics:** Added daily averages with integrated daily energy consumption. Backend detects cycles per day and sums energy across all cycles within that day. Returns `{"daily_averages": [...]}` only — per-cycle table removed from frontend per user request.
- **Date/Time Inputs:** Changed 4 inputs from `datetime-local` → `text` with `formatDateTimeInput()` (digits-only typing, auto-inserts `-`, `:`, and spaces) and `normalizeDateTimeInput()` (parses `DD-MM-YYYY HH:MM:SS` → ISO).

### 2026-05-07 — History Range Input Copy-Paste Friendly
- **Problem:** Analytics tables display dates as `toLocaleString()` (e.g., `"5/7/2026, 4:26:35 PM"`), but history/export inputs forced `DD-MM-YYYY HH:MM:SS` via a digit-only formatter. Users could not copy-paste dates from the analytics table into the history range fields.
- **Fix:**
  - Removed `oninput="formatDateTimeInput(this)"` from history and export inputs — free-text paste is now allowed.
  - Updated placeholders to `"M/D/YYYY, H:MM:SS AM/PM"`.
  - Extended `normalizeDateTimeInput()` to parse `toLocaleString()` format (`M/D/YYYY, H:MM:SS AM/PM` → ISO) with correct AM/PM conversion (12 PM → 12, 12 AM → 0).
  - Updated `formatLocalForInput()` to use `toLocaleString('en-US', { hour12: true })` so default export dates also match the format.
  - Backward-compatible: existing `DD-MM-YYYY HH:MM:SS`, `DD-MM-YY HH:MM:SS`, and `YYYY-MM-DD HH:MM:SS` entries still work.
- **HVAC Fault Alerts:** Changed from cycle-end evaluation to **3 consecutive readings** in `STABLE_ON` state. Reduced `STABLE_ON` gate from 10 min → **7 min** (`elapsed >= 420`). Added `HVAC_FAULT_COUNTERS` dict with per-reading `_evaluate_hvac_reading()`.
- **Time-Range Query Fix:** Padded `end` parameter by +1 second in `hvac_analytics()` and `dryer_analytics()` to include milliseconds. Fixes issue where a cycle end time copied from the UI (seconds precision) would exclude the actual DB reading (microsecond precision).
- **Frontend DOM Cleanup:** Removed stale `<div>` insertion before `<tbody>` that caused "Daily Averages" ghost header to leak into dryer view. Fixed empty-state checks for HVAC object response.

### 2026-05-05 — HVAC Calibration Progress Fix, BME280 Hardening, Motor Current Fix, Ignition Count Fix
- **Firmware (Calibration):** Fixed HVAC calibration progress dashboard sync. Root cause: during calibration (`CALIBBASELINEWAIT` / `CALIBRUNNING`), normal sensor sampling was skipped, so **no MQTT telemetry was published at all**. Backend had no live data and fell back to stale `hvac_readings` (showing wrong `start_tcoil` like 25.9°C instead of actual 20.00°C) with a frozen progress bar.
  - **Fix:** Firmware now publishes lightweight `calibration_progress` events every ~2.2 s on the existing events topic. Events contain `t3`, `base_t3`, and `delta`.
  - **Backend:** New `calibration_progress` event handler updates `CALIBRATION_TRACKER` with live `start_tcoil` and `current_tcoil`. `api_calibration_progress` reads from tracker first, with `hvac_readings` as fallback for old firmware.
- **Firmware (BME280):** Removed false stuck-detection logic (`bmeStuckCounter`) that triggered after only 3 identical samples (~6 s) during normal operation. Removed `recoverI2C()` from the main loop — bit-banging SCL/SDA was corrupting the active I2C bus by leaving SDA in push-pull OUTPUT mode after `Wire.begin()`.
- **Firmware (BME280):** Simplified BME280 configuration to match the proven `gas_dryer_test` pattern: `MODE_NORMAL, SAMPLING_X2, SAMPLING_X16, SAMPLING_X1, FILTER_X16, STANDBY_MS_62_5`. Removed `Wire.setClock(50000L)` (untested edge case on ESP32-C3).
- **Firmware (BME280):** Added **10 ms delay between BME register reads** (`readTemperature()` → `delay(10)` → `readHumidity()` → `delay(10)` → `readPressure()`) to prevent I2C transaction collision under FreeRTOS task switching / WiFi ISR preemption.
- **Firmware (BME280):** Added **3-attempt retry loop** for NaN readings with 50 ms backoff between attempts. Auto-soft-reset (write `0xB6` to reset register + re-init) triggered on 5 consecutive NaN samples.
- **Firmware (BME280):** Invalid readings now emit **`null`** in JSON instead of `0.0`. Dashboard shows "—" for missing BME data. Added `bmeValidSamples` counter for accurate averaging.
- **Firmware (Setup):** Moved `setupWifi()` before sensor initialization. DHT warm-up loop (up to 20 s) now runs **only for HVAC**; skipped for dryers. BME init simplified to single `bme.begin(0x76, &Wire)`.
- **Backend (Motor current):** Fixed `_motor_readings` only appending when `_peak_state == "IDLE"`. The peak state machine could get stuck in "RISING" because the fallback drop threshold (0.1 A) exceeded actual gas dryer motor fluctuation (±0.03 A). Now collects **ALL readings** into `_motor_readings` and filters ignition spikes at runtime with `filter_threshold = average * 1.15`. Motor baseline median is now stable (~3.1 A) across all time ranges.
- **Backend (Ignition count):** Added hysteresis (`_peak_max - 0.1`) and hard floor (`_peak_max > mean_current + 0.15`) to peak detection. Applied to both v1 (`iot_thesis/app.py`) and v2 (`iot_thesis_v2/app.py`). Verified against `Dryer_Test_20260505_111710.xlsx` — correctly reports 4 ignitions instead of 5.

### 2026-05-05 — Discord Alert System Revision
- **Discord — Fault-Only Alerts:** Raw SPC breach alerts (`spc_ucl_breach`, `spc_lcl_breach`) are **removed from Discord**. They still insert into the `alerts` table and appear on the dashboard, but they no longer spam the Discord channel.
- **Discord — `dryer_humidity_high` Removed:** The legacy end-of-cycle humidity alert (`dryer_humidity_high`) is also **removed from Discord**. The more precise `fault_dryer_incomplete_drying` (SPC-based) remains active and is sent to Discord instead.
- **Discord — Maintenance-Ticket Embeds:** `send_discord_alert()` rewritten with `FAULT_DISCORD_MAP`. Each fault alert now sends a rich embed containing: severity icon + human-readable title, fault description, root cause, and recommended action — formatted like a maintenance work order.
- **Fault Triggering — Immediate:** Removed the 3-consecutive-cycle confirmation delay from `fault_dryer_roller_wear` and `fault_hvac_dirty_filter`. Both now fire **immediately on first detection** at cycle end. The existing 10-minute cooldown per fault type (`_insert_fault_alert()`) prevents spam without delaying actionable maintenance advice.
- **Faults Going to Discord (7 types):** `fault_dryer_incomplete_drying`, `fault_dryer_roller_wear`, `fault_dryer_belt_snapped`, `fault_dryer_lint_blockage`, `fault_hvac_dirty_filter`, `fault_hvac_low_refrigerant`, `fault_hvac_compressor_fault`.

### 2026-05-07 — Offline/Online Status Fix, Debug Print Cleanup, BME280 Threshold Finalization
- **Backend:** Added `ever_connected` flag to `api_device_latest` response. Frontend now distinguishes between a device that was **never connected** (yellow "Awaiting Sensor Data...") and a device that **went offline after previously connecting** (red "Device Offline").
- **Backend:** Increased `offline_threshold_seconds` from **600s → 660s** (11 minutes). Checkin interval is 600s (10 min); the extra 60s prevents idle devices from flickering offline between checkins.
- **Backend:** Removed 7 temporary `[DRYER_ANALYTICS]` debug print statements from `dryer_analytics()`.
- **Firmware (BME280):** Raised stuck-value and out-of-range thresholds from 5 → **15 consecutive readings** before triggering soft reset. Prevents premature resets during normal transient conditions.
- **Firmware (BME280):** Stuck-value detection now only active when running (`lastAvgCurrent >= 0.4`). Counter resets when idle to avoid false positives in stable exhaust duct conditions.

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
| BME280 intermittent NaN during operation | **Mitigated** | Occasionally returns NaN under FreeRTOS task switching / WiFi ISR preemption. Mitigated by 10 ms spacing between register reads, 3-attempt retry, and auto-soft-reset on 5 consecutive NaN. Root cause suspected to be ISR contention with I2C driver. |
| BME280 reads constant values / abnormal pressure | **Mitigated** | Sensor lockup returning identical values or garbage spikes (86.7°C, <800 hPa). Mitigated by I2C slowdown (100kHz), stuck-value detection (5 identical → soft reset), out-of-range detection, and 50ms inter-register delays. Hardware pull-ups (4.7kΩ) + decoupling cap (100nF) still strongly recommended. |
| BME280 reads 182°C / 100% RH / −204 hPa | **Hardware** | Sensor fault — check I2C wiring or replace BME280 |
| Dryer analytics motor current inconsistent across time ranges | **Fixed** | `_motor_readings` was gated by `_peak_state == "IDLE"`, causing state-machine starvation. Now collects all readings and filters spikes at runtime. |
| Dryer ignition over-counting | **Fixed** | Added hysteresis (`_peak_max - 0.1`) and hard floor (`_peak_max > mean_current + 0.15`) to peak detection in both v1 and v2. |
| HVAC calibration progress frozen / wrong start T3 | **Fixed** | Firmware now publishes `calibration_progress` events during calibration. Backend reads from `CALIBRATION_TRACKER` instead of stale `hvac_readings`. |
| `TESTING_GUIDE.md` untracked | **Git** | Exists in working tree but not committed |
| Excel export empty data error | **Fixed** | Returns valid `.xlsx` with "No data available" message |
| Rebaseline button visible before baseline | **Fixed** | Only shown after `baseline_analysis` confirms stats exist |
| Chart tooltip shows wrong timestamp after SPC lines render | **Fixed** | Per-chart `timeLabels` + in-place SPC updates prevent index drift |
| `dryer_readings.time` stored naive local time (browser showed UTC+7 offset) | **Fixed** | Migrated to `TIMESTAMPTZ`; backend now inserts UTC consistently |
| `alerts` table schema may need manual migration | **DB** | Run CREATE TABLE if not already present in local PG / Neon |
| Hardcoded credentials in source code | **Fixed** | Removed all fallback defaults from `os.getenv()`. App now requires `.env` file with `FLASK_SECRET_KEY`, `MQTT_PASS`, and `DB_PASSWORD`. See `iot_thesis_v2/README.md` |

---

## 12. Quick Reference: File Responsibilities

| File | Responsibility |
|------|---------------|
| `iot_thesis/app.py` | v1 backend: HTTP API, MQTT, DB, auth, auto-baseline SPC, Excel export |
| `iot_thesis/templates/dashboard.html` | v1 frontend (Chart.js, real-time cards, detail modal, alert panel) |
| `iot_thesis/Update_SensorNode.ino` | Production ESP32 firmware for both HVAC and Dryer |
| `iot_thesis_v2/app.py` | **v2 backend** — manual SPC baselines, fault alerts, Discord webhooks |
| `iot_thesis_v2/templates/dashboard.html` | **v2 frontend** — inline baseline config, 4-chart layout, severity colors |
| `iot_thesis_v2/AGENTS.md` | v2 agent guidance (manual baseline, fault alerts, Discord) |
| `iot_thesis_v2/FAULTALERT.md` | Deep technical spec for 7 fault types with research citations |
| `gas_dryer_test/esp32dryertest.py` | Testbed Flask dashboard for BME280 validation |
| `gas_dryer_test/esp32dryertest.ino` | Testbed ESP32 firmware (BME280 + SCT-013) |
| `fix_db_columns.py` | One-off source migration: `subtype` → `sub_type` |
| `requirements.txt` | Python dependencies for both backends |
| `.env.example` | Template for all overrideable credentials |
| `TESTING_GUIDE.md` | Step-by-step testing procedures (untracked) |
