# IoT-Based Monitoring and Alert System for Split HVAC and Gas Dryers

> **Thesis Project** — Mechanical Engineering  
> **Author:** Tiara Mae Muljana  
> **Institution:** Swiss German University

This repository contains two distinct systems developed for an IoT-based predictive maintenance thesis:

1. **`iot_thesis/`** — The **full production system** for residential split-type HVAC units and commercial gas dryers. It collects real-time sensor telemetry, establishes Statistical Process Control (SPC) baselines, runs calibration workflows, and triggers maintenance alerts.
2. **`gas_dryer_test/`** — A **standalone PCB validation testbed** used to verify that the custom sensor node PCB can successfully read BME280 and SCT-013 data, transmit via MQTT, and display it on a local Flask dashboard. This is a simplified prototype and is **not** part of the main production architecture.

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Tech Stack](#tech-stack)
- [Repository Structure](#repository-structure)
- [Getting Started](#getting-started)
  - [Prerequisites](#prerequisites)
  - [Installation](#installation)
  - [Environment Configuration](#environment-configuration)
  - [Running the Application](#running-the-application)
- [Firmware](#firmware)
- [Security Notice](#security-notice)
- [Future Work](#future-work)
- [License](#license)

---

## Overview

The project addresses the lack of accessible, retrofit-friendly monitoring solutions for two common appliance categories:

1. **Split-Type HVAC Systems** — Monitors return/supply air temperature & humidity, coil temperature, and compressor current to detect refrigerant leaks, filter clogs, and compressor degradation.
2. **Commercial Gas Dryers** — Monitors exhaust temperature, humidity, pressure, and motor current to detect vent blockages, burner inefficiency, and bearing wear.

Each appliance is paired with a custom ESP32-C3 sensor node that transmits data via MQTT to a cloud-hosted backend. The Flask-based web dashboard provides real-time visualization, device pairing, calibration workflows, baseline training, and Excel data export.

---

## Architecture

### Production System (`iot_thesis/`)

```
┌─────────────────┐     WiFi + TLS      ┌─────────────────┐
│  ESP32 Sensor   │ ───────────────────▶│   HiveMQ Cloud  │
│    Node         │      MQTT 8883      │  MQTT Broker    │
└─────────────────┘                     └────────┬────────┘
                                                  │
                       ┌──────────────────────────┘
                       │
              ┌────────▼────────┐
              │  Flask Backend  │
              │  (iot_thesis)   │
              │  ├─ PostgreSQL  │
              │  ├─ MQTT Client │
              │  └─ SPC Logic   │
              └────────┬────────┘
                       │
              ┌────────▼────────┐
              │  Web Dashboard  │
              │  (Browser)      │
              └─────────────────┘
```

> **Note:** `gas_dryer_test/` is a separate, standalone test system for early PCB validation and is not shown in this architecture diagram.

### Sensor Node Hardware
- **MCU:** ESP32-C3 Super Mini
- **HVAC Sensors:** 2× DHT22 (return & supply), DS18B20 (coil), ZHT103C (compressor current) — *cf=11.0, deductor=0.033*
- **Dryer Sensors:** BME280 (exhaust temp/humidity/pressure), SCT-013 (motor current) — *cf=37.0, deductor=0.111*
- **Connectivity:** Wi-Fi + MQTT over TLS
- **Power:** USB-C / 5V adapter

---

## Tech Stack

| Layer | Technology |
|-------|------------|
| **Firmware** | C++ (Arduino framework), ESP32-C3 |
| **Backend** | Python 3, Flask, Flask-Login |
| **Database** | PostgreSQL (local) / Neon (cloud testing) |
| **Messaging** | MQTT (HiveMQ Cloud) |
| **Frontend** | HTML5, Vanilla JS, Chart.js |
| **Data Export** | openpyxl, pandas |
| **Analytics** | NumPy, Statistics (SPC limits) |

---

## Repository Structure

```
iot-monitoring/
├── .env.example              # Template for environment variables
├── .gitignore                # Git ignore rules
├── README.md                 # This file
├── requirements.txt          # Python dependencies
│
├── gas_dryer_test/           # Standalone PCB validation testbed
│   ├── esp32dryertest.py     # Simple Flask backend for BME + SCT testing
│   └── esp32dryertest/       # Arduino firmware (BME280 + SCT)
│       └── esp32dryertest.ino
│
└── iot_thesis/               # Full production application
    ├── app.py                # Flask backend (MQTT, DB, API, auth, SPC)
    ├── fix_db_columns.py     # DB migration utility
    ├── templates/            # Jinja2 HTML templates
    │   ├── dashboard.html    # Main monitoring dashboard
    │   ├── login.html        # User login
    │   └── signup.html       # User registration
    └── Update_SensorNode/    # Production ESP32 firmware
        └── Update_SensorNode.ino
```

> **Note:** PCB design files, full documentation, and additional hardware assets will be added to this repository in future updates.

---

## Getting Started

### Prerequisites

- Python 3.10+
- PostgreSQL 14+ (local instance)
- HiveMQ Cloud account (or any MQTT broker)
- Arduino IDE 2.x (for firmware compilation)

### Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/TiaraMae/iot-monitoring.git
   cd iot-monitoring
   ```

2. **Create a virtual environment**
   ```bash
   python -m venv venv
   # Windows
   venv\Scripts\activate
   # macOS/Linux
   source venv/bin/activate
   ```

3. **Install Python dependencies**
   ```bash
   pip install -r requirements.txt
   ```

### Environment Configuration

1. Copy the environment template:
   ```bash
   cp .env.example .env
   ```

2. Edit `.env` and replace the placeholder values with your actual credentials:
   - `FLASK_SECRET_KEY` — Generate a random string
   - `MQTT_*` — Your HiveMQ Cloud broker credentials
   - `DB_*` — Your local PostgreSQL credentials
   - `NEON_DATABASE_URL` — (Optional) For the dryer test backend only

3. The application uses built-in fallback values if `.env` is not present, **but this is not recommended for production or public deployments**.

### Running the Application

**Main Backend (iot_thesis):**
```bash
cd iot_thesis
python app.py
```
The Flask server will start on `http://0.0.0.0:5000`.

**Dryer Test Backend (gas_dryer_test):**
```bash
cd gas_dryer_test
python esp32dryertest.py
```

### Database Setup

The application expects a PostgreSQL database named `iot_db` with the following tables. Schema initialization scripts will be added in a future update.

#### `users`
| Column | Type | Notes |
|--------|------|-------|
| `id` | SERIAL PK | |
| `email` | TEXT UNIQUE | Login identifier |
| `password_hash` | TEXT | bcrypt hash |
| `name` | TEXT | Display name |
| `created_at` | TIMESTAMPTZ | Auto |

#### `appliances` (52 columns)
| Column | Type | Used By | Notes |
|--------|------|---------|-------|
| `id` | SERIAL PK | Both | |
| `user_id` | INT FK | Both | → users |
| `name` | TEXT | Both | Device display name |
| `type` | TEXT | Both | `Split HVAC` or `Gas Dryer` |
| `brand` | TEXT | Both | e.g. `Generic` |
| `location` | TEXT | Both | e.g. `Home` |
| `created_at` | TIMESTAMPTZ | Both | |
| `operational_status` | TEXT | Both | `calibration_needed` → `calibrating` → `baselining` → `normal` |
| `sub_type` | TEXT | HVAC | `inverter` or `noninverter` |
| `baselining_since` | TIMESTAMPTZ | Both | Timestamp when baseline recording started |
| `calibration_started_at` | TIMESTAMPTZ | HVAC | Reserved |
| **Calibration** | | | Slope/intercept from ice-bath calibration (HVAC only) |
| `treturn_slope` | REAL | HVAC | DHT1 temp correction |
| `treturn_intercept` | REAL | HVAC | |
| `rhreturn_slope` | REAL | HVAC | DHT1 humidity correction |
| `rhreturn_intercept` | REAL | HVAC | |
| `tsupply_slope` | REAL | HVAC | DHT2 temp correction |
| `tsupply_intercept` | REAL | HVAC | |
| `rhsupply_slope` | REAL | HVAC | DHT2 humidity correction |
| `rhsupply_intercept` | REAL | HVAC | |
| `tcoil_slope` | REAL | HVAC | DS18B20 correction (currently 1.0) |
| `tcoil_offset` | REAL | HVAC | DS18B20 offset (currently 0.0) |
| `icompressor_offset` | REAL | Both | Current sensor deductor (HVAC: -0.033, Dryer: -0.111) |
| **Reserved offsets** | | | *Future use — not populated by current code* |
| `treturn_offset` … `imotor_offset` | REAL | — | 10 placeholder offset columns |
| **HVAC baselines** | | | Statistical values from 10-min baseline |
| `baseline_deltat_mean` | REAL | HVAC | Mean ΔT (return − supply) |
| `baseline_deltat_std` | REAL | HVAC | |
| `baseline_tcoil_mean` | REAL | HVAC | Mean coil temperature |
| `baseline_tcoil_std` | REAL | HVAC | |
| `baseline_rhreturn_mean` | REAL | HVAC | Mean return RH |
| `baseline_rhreturn_std` | REAL | HVAC | |
| `baseline_rhsupply_mean` | REAL | HVAC | Mean supply RH |
| `baseline_rhsupply_std` | REAL | HVAC | |
| `baseline_current_mean` | REAL | HVAC | Mean compressor current |
| `baseline_current_std` | REAL | HVAC | |
| **Dryer baselines** | | | Statistical values from 10-min baseline |
| `baseline_heat_rise_mean` | REAL | Dryer | Mean exhaust temp rise |
| `baseline_heat_rise_std` | REAL | Dryer | |
| `baseline_rhexhaust_mean` | REAL | Dryer | Reserved for future use |
| `baseline_rhexhaust_std` | REAL | Dryer | |
| `baseline_rhambient_mean` | REAL | Dryer | Reserved for future use |
| `baseline_rhambient_std` | REAL | Dryer | |
| `baseline_pressure_mean` | REAL | Dryer | Mean exhaust pressure |
| `baseline_pressure_std` | REAL | Dryer | |
| **Thresholds** | | | SPC control limits |
| `threshold_current_min` | REAL | Both | Current lower bound (non-inverter / dryer) |
| `threshold_current_max` | REAL | Both | Current upper bound |
| `threshold_texhaust_max` | REAL | Dryer | Reserved |
| `alert_rhexhaust_threshold` | REAL | Dryer | Exhaust humidity alert threshold (default 40.0%) |
| `alert_enabled` | BOOLEAN | Both | Master alert enable switch (default TRUE) |

#### `alerts`

| Column | Type | Notes |
|--------|------|-------|
| `id` | SERIAL PK | |
| `appliance_id` | INT FK | → appliances |
| `alert_type` | TEXT | e.g. `high_rh_exhaust` |
| `message` | TEXT | Human-readable description |
| `value` | REAL | Measured value that triggered alert |
| `threshold` | REAL | Configured threshold at time of alert |
| `cycle_start_time` | TIMESTAMPTZ | Start of the cycle that triggered this alert |
| `cycle_end_time` | TIMESTAMPTZ | End of the triggering cycle |
| `created_at` | TIMESTAMPTZ | Alert creation time |
| `resolved_at` | TIMESTAMPTZ | NULL until manually resolved |
| `acknowledged` | BOOLEAN | FALSE by default |

#### `sensor_nodes`
| Column | Type | Notes |
|--------|------|-------|
| `id` | SERIAL PK | |
| `mac_address` | TEXT | ESP32 Wi-Fi MAC |
| `status` | TEXT | `unpaired` or `paired` |
| `appliance_id` | INT FK | NULL until paired |
| `created_at` | TIMESTAMPTZ | |
| `last_seen` | TIMESTAMPTZ | Updated on every telemetry receipt |

#### `hvac_readings`
| Column | Type | Notes |
|--------|------|-------|
| `id` | SERIAL PK | |
| `sensor_node_id` | INT FK | → sensor_nodes |
| `time` | TIMESTAMPTZ | Actual sample time (adjusted for `ago_ms`) |
| `treturn` | REAL | Return air temperature (°C) |
| `rhreturn` | REAL | Return air RH (%) |
| `tsupply` | REAL | Supply air temperature (°C) |
| `rhsupply` | REAL | Supply air RH (%) |
| `tcoil` | REAL | Evaporator coil temperature (°C) |
| `icompressor` | REAL | Compressor current (A) |

#### `dryer_readings`
| Column | Type | Notes |
|--------|------|-------|
| `id` | SERIAL PK | |
| `sensor_node_id` | INT FK | → sensor_nodes |
| `time` | TIMESTAMPTZ | Actual sample time |
| `texhaust` | REAL | Exhaust temperature (°C) |
| `rh_exhaust` | REAL | Exhaust RH (%) |
| `tambient` | REAL | *Reserved — not populated* |
| `rh_ambient` | REAL | *Reserved — not populated* |
| `imotor` | REAL | Motor current (A) |
| `pressure` | REAL | Exhaust pressure (hPa) |

#### `sensor_events`
| Column | Type | Notes |
|--------|------|-------|
| `id` | SERIAL PK | |
| `sensor_node_mac` | TEXT | MAC address of originating node |
| `event_type` | TEXT | e.g. `maintenance` |
| `timestamp` | TIMESTAMPTZ | Event time |

#### Adding missing columns
If your `appliances` table was created before the dryer baseline feature, run this once:

```sql
ALTER TABLE appliances
    ADD COLUMN IF NOT EXISTS baseline_heat_rise_mean REAL,
    ADD COLUMN IF NOT EXISTS baseline_heat_rise_std REAL;
```

These columns are required for Gas Dryer SPC temperature monitoring.

#### Database migration (run once)

If upgrading an existing database, execute:

```sql
-- New alert configuration columns
ALTER TABLE appliances
    ADD COLUMN IF NOT EXISTS alert_rhexhaust_threshold REAL DEFAULT 40.0,
    ADD COLUMN IF NOT EXISTS alert_enabled BOOLEAN DEFAULT TRUE;

-- Alerts table
CREATE TABLE IF NOT EXISTS alerts (
    id SERIAL PRIMARY KEY,
    appliance_id INT REFERENCES appliances(id) ON DELETE CASCADE,
    alert_type TEXT NOT NULL,
    message TEXT,
    value REAL,
    threshold REAL,
    cycle_start_time TIMESTAMPTZ,
    cycle_end_time TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    resolved_at TIMESTAMPTZ,
    acknowledged BOOLEAN DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_alerts_appliance ON alerts(appliance_id);
CREATE INDEX IF NOT EXISTS idx_alerts_created ON alerts(created_at);
```

---

## Firmware

### Production Sensor Node (`iot_thesis/Update_SensorNode/`)

Supports both HVAC and Dryer modes. Appliance type is set by the backend during pairing (`settype:HVAC` or `settype:DRYER`).

**Key Features:**
- Multi-sensor averaging (5 samples @ 2-second intervals = 10-second telemetry cadence)
- Offline message buffering (up to 200 readings, FIFO eviction)
- Two-button physical interface:
  - **Button 1 (hold 2s):** Maintenance request
  - **Button 2 (hold 5s):** HVAC calibration only (dryer: disabled)
- Calibration state machine for HVAC units
- Automatic Wi-Fi and MQTT reconnection with LED status indication
- Buzzer feedback for user actions
- **Telemetry gating:** Only transmits when appliance current ≥ 0.4 A (running). Idle samples are discarded, not buffered.

### PCB Validation Test Node (`gas_dryer_test/esp32dryertest/`)

Standalone test firmware used to validate that the custom PCB can read BME280 and SCT-013 sensors and transmit data over MQTT. This is **not** the production dryer node used by `iot_thesis`.

**Key Features:**
- Current-based run detection (appliance ON/OFF state)
- Idle ping heartbeat to maintain backend connectivity
- 10-sample SCT moving average for noise reduction
- RAM-based offline buffering (up to 100 readings)

### Required Arduino Libraries

- `WiFi` (ESP32 core)
- `PubSubClient` or `ArduinoMqttClient`
- `DHT sensor library` (by Adafruit)
- `DallasTemperature`
- `Adafruit BME280`
- `Adafruit Unified Sensor`

---

## System Workflow Overview

This section describes the complete end-to-end data flow from physical sensors to the web dashboard.

```
┌─────────────────┐     ┌──────────────┐     ┌─────────────┐
│  Sensor Node    │────▶│  HiveMQ Cloud │────▶│   Flask     │
│  (ESP32-C3)     │ MQTT│   (TLS:8883)  │     │  Backend    │
└─────────────────┘     └──────────────┘     └──────┬──────┘
       │                                            │
       │ 1. Sample sensors every 2s                 │ 3. Parse JSON
       │ 2. Build payload (running only)            │ 4. Gate insert
       │    with status:"running"/"idle"            │ 5. Cycle tracker
       │                                            │ 6. Baseline timer
       ▼                                            ▼
┌─────────────────┐                         ┌─────────────┐
│   BME280 /      │                         │  PostgreSQL │
│   DHT22 /       │                         │  (iot_db)   │
│   DS18B20 /     │                         └──────┬──────┘
│   SCT-013       │                                │
└─────────────────┘                                │
                                                   ▼
                                            ┌─────────────┐
                                            │  Dashboard  │
                                            │ (Chart.js)  │
                                            └─────────────┘
```

**Data Flow Summary:**
1. **Sensor Node** reads sensors every 2 seconds. After 5 samples (10 seconds), it computes averages.
2. If average current ≥ 0.4 A, the node builds a JSON telemetry payload including `"status":"running"` and publishes via MQTT. If current < 0.4 A, samples are discarded (idle state).
3. If Wi-Fi or MQTT is down, running samples are queued in a 200-slot FIFO buffer and flushed on reconnect.
4. **HiveMQ Cloud** relays the message to the Flask backend via TLS on port 8883.
5. **Backend** (`on_mqtt_message`) parses the JSON, corrects timestamps via `ago_ms`, deduplicates rapid messages, and gates database inserts: only `"running"` data is inserted; idle data only updates `last_seen`.
6. **Database** stores running readings in `hvac_readings` or `dryer_readings`.
7. **Dashboard** polls the backend every 5 seconds for live data, and renders real-time charts with deduplication guards.

---

## Sensor Node Implementation Procedure

### Hardware Assembly — HVAC Node

| Sensor | Pin | Purpose |
|--------|-----|---------|
| DHT1 | GPIO 4 | Return air temp/RH |
| DHT2 | GPIO 6 | Supply air temp/RH |
| DS18B20 | GPIO 5 | Evaporator coil temp |
| ZHT103C | GPIO 1 (ADC) | Compressor current |
| LED | GPIO 10 | Status indicator |
| Buzzer | GPIO 20 | Audio feedback |
| Button 1 | GPIO 9 | Maintenance request |
| Button 2 | GPIO 8 | Calibration trigger |

### Hardware Assembly — Dryer Node

| Sensor | Pin | Purpose |
|--------|-----|---------|
| BME280 (I2C) | SDA=GPIO 7, SCL=GPIO 2 | Exhaust temp/RH/pressure |
| SCT-013 | GPIO 1 (ADC) | Motor current |
| LED | GPIO 10 | Status indicator |
| Buzzer | GPIO 20 | Audio feedback |
| Button 1 | GPIO 9 | Maintenance request |
| Button 2 | GPIO 8 | *(Disabled — no function)* |

### Firmware Upload

1. Open `iot_thesis/Update_SensorNode/Update_SensorNode.ino` in Arduino IDE.
2. Select **Board:** ESP32C3 Dev Module.
3. Set `WIFI_SSID` and `WIFI_PASSWORD` in the firmware (or use a separate `secrets.h`).
4. Set `MQTT_BROKER`, `MQTT_PORT` (8883), `MQTT_USER`, and `MQTT_PASS` for HiveMQ Cloud.
5. Compile and upload.

### Initial Boot Sequence

On first power-up, the node performs this sequence:

1. **Wi-Fi connection** — LED fast blink (200 ms) until connected.
2. **MQTT connection** — LED medium blink (500 ms) until connected to HiveMQ.
3. **Config request** — Publishes `{"event":"requestconfig"}` to `nodes/mac_address`.
4. **Wait for backend** — The backend responds with either:
   - `settype:HVAC` or `settype:DRYER` → node beeps, stores type, LED goes idle.
   - No response (unpaired) → LED shows idle blink every 10 s.

At this point the node is online and ready for pairing.

---

## Pairing & Onboarding Procedure

### Detecting Unpaired Nodes

1. In the dashboard sidebar, click **Scan for New Devices**.
2. The backend queries the `sensor_nodes` table for rows with `status = 'unpaired'`.
3. Unpaired MAC addresses appear in a modal.

### Pairing Flow

1. Select a MAC address from the scan list.
2. Enter a **Device Name** (e.g., "Living Room AC").
3. Select **Type:** `Split HVAC` or `Gas Dryer`.
4. Fill optional fields: Brand, Location.
5. Click **Pair Device**.

### Backend Actions During Pairing

1. Inserts a new row into `appliances`:
   - HVAC → `operational_status = 'calibration_needed'`
   - Dryer → `operational_status = 'normal'`
2. Updates `sensor_nodes`:
   - Sets `status = 'paired'`
   - Sets `appliance_id = <new_appliance_id>`
3. Publishes `settype:HVAC` or `settype:DRYER` to the node's `nodes/mac_address` topic.

### Firmware Response

- Receives `settype:*` via MQTT callback.
- Beeps to acknowledge.
- Sets internal `applianceType` variable.
- HVAC nodes will show `calibration_needed` state. Dryer nodes immediately enter `normal` state.

---

## HVAC Calibration Procedure

### Why Calibration Is Needed

HVAC nodes use two DHT22 sensors and one DS18B20. During calibration, all three probes are physically placed together at the AC supply air mouth so they read the same environmental conditions. The backend then computes linear regression slopes and intercepts to correct for sensor-to-sensor bias.

### Physical Process

1. Place DHT1, DHT2, and DS18B20 probes together at the supply air outlet.
2. Turn on the AC unit.
3. On the sensor node, **hold Button 2 for 5 seconds**.
4. The node enters calibration mode (LED slow blink, 2 s period).
5. The node sends `event:calibration_success_request` to the backend, including the initial coil temperature (`tcoil_start`).
6. Wait for the coil temperature to drop by at least 8 °C (this indicates the compressor is running and the coil is cold).
7. Once ΔT ≥ 8 °C, the node sends `event:calibration_success_request` again with final readings.

### Backend Processing

1. On first calibration event: records `calibration_started_at` and `tcoil_start`.
2. On second calibration event (ΔT ≥ 8 °C):
   - Validates that coil temp dropped ≥ 8 °C.
   - Computes linear regression for each sensor pair:
     - `treturn_slope`, `treturn_intercept` (DHT1 correction)
     - `tsupply_slope`, `tsupply_intercept` (DHT2 correction)
     - `rhreturn_slope`, `rhreturn_intercept`
     - `rhsupply_slope`, `rhsupply_intercept`
   - Sets `operational_status = 'normal'`.
   - Sends `calibrationsuccessack` to node (3 beeps).

### If Calibration Fails

If the coil temperature does not drop by 8 °C within a reasonable time, the operator can cancel calibration via the web UI. The node receives `calibrationfailack` (2 long beeps).

---

## Baseline Recording Procedure

### Important: Web-Triggered Only

Baseline recording is initiated **exclusively from the web dashboard**. Physical Button 2 no longer starts baseline recording.

### Starting Baseline

1. Ensure the appliance is **running** (current ≥ 0.4 A). The backend checks this before allowing baseline start.
2. In the dashboard, click **Start Baseline Recording**.
3. Backend sends `baselinestartack` to the node.
4. Node beeps twice and enters baselining mode (LED alternating fast/slow blink).
5. Backend sets `operational_status = 'baselining'` and records `baselining_since`.

### Recording Duration

- **Both HVAC and Dryer:** 15-minute fixed recording window.
- Backend starts a `threading.Timer(900.0, _complete_baseline)`.
- If the appliance stops (current < 0.4 A) during the window, the timer continues; the backend will still compute baselines from the data collected so far.

### Completion

After 15 minutes, `_complete_baseline()` runs:

1. Queries all readings since `baselining_since`.
2. Calls `do_set_baseline_calculated()` to compute:
   - **HVAC:** mean and std for ΔT, coil temp, return RH, supply RH, current.
   - **Dryer:** mean and std for exhaust temp, exhaust RH, pressure, current.
3. Populates `baseline_*_mean` and `baseline_*_std` columns in `appliances`.
4. Sets `operational_status = 'normal'`.
5. Sends `baselinesuccessack` to node (3 beeps).
6. Dashboard auto-shows the baseline results panel.

### Cancellation

Operators can click **Cancel Baseline** in the dashboard. The backend:
1. Cancels the running timer.
2. Sends `baselinefailack` to node (2 long beeps).
3. Sets status back to `normal`.

---

## Firmware Data Collection & Transmission

### Sampling Loop

Every 2 seconds, `loop()` calls `readSensors()`:
- Reads all configured sensors based on `applianceType`.
- Accumulates values into running sums.
- Increments `sampleCount`.

When `sampleCount >= MAX_SAMPLES` (5):
- Computes `lastAvgCurrent = sumCurrentA / MAX_SAMPLES`.
- **If `lastAvgCurrent >= 0.4 A`:** builds telemetry payload and publishes.
- **If `lastAvgCurrent < 0.4 A`:** calls `resetAverages()` and discards all samples.

### Current Measurement

```cpp
float readCurrentIrms() {
    uint32_t maxVal = 0;
    for (int i = 0; i < 1000; i++) {
        uint32_t val = analogRead(PIN_CURRENT);
        if (val > maxVal) maxVal = val;
    }
    float trueVoltageMv = (maxVal / 4095.0) * 3300.0;
    // Deductor subtracted in firmware
    return max(0.0, (trueVoltageMv / 1000.0) * cf - deductor);
}
```

- **HVAC (ZHT103C):** `cf = 11.0`, `deductor = 0.033`
- **Dryer (SCT-013):** `cf = 37.0`, `deductor = 0.111`

### Telemetry Payload Format

```json
{
  "treturn": 24.5,
  "rhreturn": 55.2,
  "tsupply": 18.3,
  "rhsupply": 72.1,
  "tcoil": 12.5,
  "CurrentA": 3.412,
  "status": "running",
  "agoms": 0
}
```

The `status` field is always included and is determined by `avgCurrent >= 0.4`.

### Offline Buffering

If MQTT is disconnected:
- Running payloads are serialized and appended to a 200-slot ring buffer.
- On reconnect, the buffer is flushed: each message is published with a 120 ms delay to avoid flooding.
- If the buffer exceeds 200 entries, oldest entries are overwritten (FIFO eviction).
- Buffer state is logged to Serial on flush (`Buffer flushed: N messages`).

---

## Backend Data Processing

### MQTT Message Handling (`on_mqtt_message`)

1. **Parse JSON** — Extracts sensor values and metadata.
2. **Deduplication** — Skips messages received < 1 second after the previous message from the same node.
3. **Timestamp correction** — If `agoms` or `ago_ms` is present, subtracts from server time to get actual sample time.
4. **MAC lookup** — Resolves `sensor_nodes.id` from MAC address.
5. **Appliance lookup** — Resolves `appliances.id` and reads current operational status.

### Data Insert Gating

```python
status_field = data.get("status", "running")
if status_field == "running":
    # INSERT into hvac_readings or dryer_readings
else:
    # Only UPDATE sensor_nodes.last_seen and appliances.last_seen
```

Idle data never enters the readings tables. This reduces DB noise and simplifies SPC analysis.

### HVAC Calibration Event Processing

When `event_type == "calibration_success_request"`:
- First event: records start time and initial coil temp.
- Second event (ΔT ≥ 8 °C): computes regression, sets `normal`, sends success ACK.

### Dryer Cycle Tracker

The backend maintains `CYCLE_TRACKER = {}` (appliance_id → `{start_time, last_time}`):
- On running data: if not in tracker, sets `start_time = now`.
- On idle transition (running → idle):
  1. Queries the last 2 minutes of `rh_exhaust` readings.
  2. Computes average exhaust humidity.
  3. Compares against `alert_rhexhaust_threshold`.
  4. If above threshold and `alert_enabled == True`, inserts an alert into the `alerts` table.
  5. Removes appliance from `CYCLE_TRACKER`.

### Baseline Timer Management

`BASELINE_TIMER_TRACKER = {}` maps `appliance_id → threading.Timer`:
- `remote_baseline()` creates a 900-second timer and stores it.
- `_complete_baseline()` fires, computes stats, updates DB, sends ACK.
- `cancel_baseline()` calls `.cancel()` on the timer and cleans up.

---

## Frontend Dashboard Behavior

### Live Mode (Default)

- Polls `GET /api/device/{id}/latest` every 5 seconds.
- Updates mini-cards, charts, and status badges.
- Chart push is guarded: only appends if `timeMs > latestChartTimeMs` to prevent duplicate points.
- When switching from History back to Live, `latestChartTimeMs` resets to 0 and the 1080-point rolling window reloads.

### History Mode

- Operator selects a time range.
- Dashboard fetches all readings in range via `GET /api/readings`.
- Charts are rendered once as static data.
- No live updates are appended while in History mode.

### Status Badges & Action Bar

| Status | Badge | Action Bar Content |
|--------|-------|-------------------|
| `calibration_needed` | Red "Calib. Needed" | Calibration instructions, "Start Calibration" button |
| `calibrating` | Yellow "Calibrating..." | Progress message |
| `normal` | Green "Normal" | "Start Baseline Recording" button |
| `baselining` | Blue "Recording Baseline..." | Recording message (15 min), "Cancel" button |

### Uncalibrated HVAC Warning

If `calibrated === false` for an HVAC device:
- Numeric displays show "—".
- Charts are hidden.
- A warning banner instructs the operator to perform calibration.

### Baseline Results Panel

Auto-appears when baseline completes. Displays:
- Computed mean and standard deviation for each baseline parameter.
- An **Edit Alert Thresholds** button that opens the threshold config panel.

### Threshold Config Panel

Collapsible panel with:
- Max exhaust humidity threshold input (dryer).
- Alert enable/disable toggle.
- Values are persisted via `POST /api/device/{id}/thresholds`.

### Alerts Panel

- Fetches from `GET /api/device/{id}/alerts`.
- Unresolved alerts: red background.
- Resolved alerts: gray background.
- Shows alert type, message, measured value, threshold, and cycle timestamps.

---

## LED & Buzzer Feedback Reference

### LED Patterns (Priority Order)

| Priority | State | LED Pattern |
|----------|-------|-------------|
| 1 | Calibration active | Slow blink, 2 s period |
| 2 | Baselining active | Alternating fast (200 ms) / slow (1000 ms) blink |
| 3 | Wi-Fi disconnected | Fast blink, 200 ms period |
| 4 | MQTT disconnected | Medium blink, 500 ms period |
| 5 | Running (current ≥ 0.4 A) | Solid ON |
| 6 | Idle (current < 0.4 A) | Brief blink every 10 seconds |

### Buzzer Sounds

| Event | Sound |
|-------|-------|
| `baselinestartack` received | 2 short beeps |
| `baselinesuccessack` received | 3 short beeps |
| `baselinefailack` received | 2 long beeps |
| `calibrationsuccessack` received | 3 short beeps |
| Connection down (Wi-Fi or MQTT) | 1 beep every 10 seconds |

---

## Testing Procedures

### 1. Current Sensor Verification

**Objective:** Confirm `readCurrentIrms()` reports accurate values.

**Steps:**
1. Connect a known load (e.g., 60 W lamp ≈ 0.27 A @ 220 V).
2. Flash firmware with `deductor = 0` temporarily.
3. Open Serial Monitor.
4. Verify reported current is within ±10% of expected.
5. Restore correct `deductor` value.

### 2. Pairing Test

**Objective:** Verify unpaired nodes appear and pair correctly.

**Steps:**
1. Factory-reset a node (erase Flash).
2. Power on. Confirm LED shows idle blink (unpaired).
3. In dashboard, click **Scan for New Devices**.
4. Confirm MAC appears in modal.
5. Pair as HVAC. Confirm backend sets `calibration_needed`.
6. Pair as Dryer. Confirm backend sets `normal`.

### 3. HVAC Calibration Test

**Objective:** Verify calibration computes slopes/intercepts.

**Steps:**
1. Place all three probes together at supply mouth.
2. Turn on AC. Hold Button 2 for 5 s.
3. Confirm LED slow blink.
4. Use ice pack or cold spray on coil to accelerate ΔT ≥ 8 °C.
5. Wait for 3 beeps (success).
6. Query DB: confirm `treturn_slope`, `tsupply_slope`, etc. are populated and not 1.0/0.0.

### 4. Baseline Test

**Objective:** Verify 15-minute baseline completes successfully.

**Steps:**
1. Start appliance (HVAC or dryer). Confirm current ≥ 0.4 A.
2. In dashboard, click **Start Baseline Recording**.
3. Confirm node beeps twice and LED alternates fast/slow.
4. Wait 15 minutes.
5. Confirm node beeps 3 times and dashboard shows baseline results panel.
6. Query `appliances` table: confirm `baseline_*_mean` and `baseline_*_std` are populated.

### 5. Data Gating Test

**Objective:** Confirm idle data is not inserted.

**Steps:**
1. Start appliance, confirm readings appear in DB.
2. Stop appliance, wait 30 seconds.
3. Check DB: no new rows in readings table for this device.
4. Check `sensor_nodes.last_seen`: should still be updating.

### 6. Offline Buffer Test

**Objective:** Verify FIFO buffer flushes on reconnect.

**Steps:**
1. Start appliance running.
2. Disconnect Wi-Fi router or power-cycle it.
3. Wait 2–3 minutes.
4. Reconnect Wi-Fi.
5. Check Serial Monitor for `Buffer flushed: N messages`.
6. Confirm DB has rows for the disconnected period.

### 7. Alert Test (Dryer)

**Objective:** Verify end-of-cycle humidity alert fires.

**Steps:**
1. Set `alert_rhexhaust_threshold = 20.0` (artificially low).
2. Set `alert_enabled = TRUE`.
3. Run a dryer cycle.
4. Stop cycle (current drops below 0.4 A).
5. Check `alerts` table: should have a `high_rh_exhaust` alert.
6. Check dashboard alerts panel: alert appears in red.
7. Restore normal threshold (e.g., 40.0).

### 8. History Mode Test

**Objective:** Verify static chart display.

**Steps:**
1. Let appliance run for 10+ minutes in Live mode.
2. Switch to History, select last 30 minutes.
3. Confirm charts render all data points.
4. Wait 1 minute: confirm no new points are appended.
5. Switch back to Live: confirm rolling window resumes.

---

## Security Notice

> **⚠️ Credential Rotation Required**
>
> During initial development, sensitive credentials (MQTT passwords, database passwords, API keys) were temporarily hardcoded in source files. These have been refactored to use environment variables with safe fallback defaults.
>
> **If this repository is or will become public, you MUST rotate the following credentials immediately:**
> - HiveMQ Cloud MQTT password
> - PostgreSQL `postgres` user password
> - Neon Cloud database password (if used)
> - Flask secret key
>
> Rotate these values in your respective cloud dashboards and update your local `.env` file accordingly. The fallback values in the code should be treated as compromised.

---

## Future Work / Roadmap

| # | Task | Priority | Notes |
|---|------|----------|-------|
| 1 | ~~Fault Logic & Alert System~~ | ✅ Done | Dryer end-of-cycle humidity alerts implemented |
| 2 | **SPC Limit Enforcement** | 🔴 High | Trigger alerts when UCL/LCL breached during operation |
| 3 | **Discord Integration** | 🔴 High | Webhook alerts for maintenance reminders and fault notifications |
| 4 | **Multi-Device Dashboard Stress Test** | 🟡 Medium | Verify UI performance with 5+ simultaneous devices |
| 5 | **Unit Tests** | 🟡 Medium | pytest suite for SPC math, calibration regression, cycle detection |
| 6 | **Docker Deployment** | 🟡 Medium | Containerize Flask app + PostgreSQL for easy cloud deployment |
| 7 | **Mobile Responsive Polish** | 🟡 Medium | Test and fix dashboard on phone/tablet screens |
| 8 | **PCB Design Files** | 🟢 Low | KiCad schematics and Gerber files |
| 9 | **3D Enclosure** | 🟢 Low | STL files for sensor node housing |
| 10 | **DB Migration Scripts** | 🟢 Low | Formalize schema creation and versioned migrations |
| 11 | **CI/CD** | 🟢 Low | GitHub Actions for linting and basic tests |

### Completed Recently
- ✅ Web-triggered baseline only (removed physical button baseline)
- ✅ Data gating: only running data inserted; idle updates `last_seen` only
- ✅ Dryer end-of-cycle humidity alert system with configurable thresholds
- ✅ Alerts table and panel in dashboard
- ✅ HVAC calibration flow: `calibration_needed` → `calibrating` → `normal`
- ✅ Baseline results panel and threshold configuration UI
- ✅ Chart deduplication guards and history mode static display
- ✅ Full DB schema reference documentation

---

## License

This project was developed as part of an academic thesis. All rights reserved by the author until a formal open-source license is chosen.

---

**Author:** Tiara Mae Muljana  
**Contact:** tiara.muljana@student.sgu.ac.id  
**Repository:** [github.com/TiaraMae/iot-monitoring](https://github.com/TiaraMae/iot-monitoring)
