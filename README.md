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
| `operational_status` | TEXT | Both | `calibration_needed` → `calibrating` → `pending_baseline` → `baselining` → `normal` |
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

---

## Firmware

### Production Sensor Node (`iot_thesis/Update_SensorNode/`)

Supports both HVAC and Dryer modes with automatic appliance type detection via backend command.

**Key Features:**
- Multi-sensor averaging (5 samples @ 2-second intervals)
- Offline message buffering (up to 200 readings)
- Two-button physical interface:
  - **Button 1:** Maintenance request
  - **Button 2:** Calibration / Baseline start
- Calibration state machine for HVAC units (ice-bath reference method)
- Automatic Wi-Fi and MQTT reconnection with LED status indication
- Buzzer feedback for user actions

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

## Future Work

The following components are planned for inclusion in subsequent repository updates:

- [ ] **PCB Design** — KiCad schematics and Gerber files for the ESP32-C3 sensor node
- [ ] **Database Schema** — Official SQL migration scripts and ER diagram
- [ ] **3D Enclosure** — STL files for sensor node housing
- [ ] **Calibration Guide** — Step-by-step HVAC sensor calibration procedure
- [ ] **Deployment Guide** — Docker/containerization and cloud deployment instructions
- [ ] **Unit Tests** — Backend API and SPC logic test suite
- [ ] **CI/CD** — GitHub Actions for linting and dependency checks

---

## License

This project was developed as part of an academic thesis. All rights reserved by the author until a formal open-source license is chosen.

---

**Author:** Tiara Mae Muljana  
**Contact:** tiara.muljana@student.sgu.ac.id  
**Repository:** [github.com/TiaraMae/iot-monitoring](https://github.com/TiaraMae/iot-monitoring)
