# IoT-Based Monitoring and Alert System for Split HVAC and Gas Dryers

> **Thesis Project** — Mechanical Engineering  
> **Author:** Tiara Mae Muljana  
> **Institution:** Swiss German University

This repository contains the embedded firmware, backend services, and web dashboard for an IoT-based predictive maintenance system targeting residential split-type HVAC units and commercial gas dryers. The system collects real-time sensor telemetry, establishes Statistical Process Control (SPC) baselines, and triggers maintenance alerts when operational parameters deviate from normal behavior.

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

### Sensor Node Hardware
- **MCU:** ESP32-C3 Super Mini
- **HVAC Sensors:** 2× DHT22 (return & supply), DS18B20 (coil), SCT-013 (compressor current)
- **Dryer Sensors:** BME280 (exhaust temp/humidity/pressure), SCT-013 (motor current)
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
├── gas_dryer_test/           # Dryer-specific testbed & prototype
│   ├── esp32dryertest.py     # Flask backend for BME dryer testing
│   └── esp32dryertest/       # Arduino firmware (BME280 + SCT)
│       └── esp32dryertest.ino
│
└── iot_thesis/               # Main production application
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

The application expects a PostgreSQL database with the following core tables (schema initialization scripts will be added in a future update):

- `users` — Authentication and profile data
- `appliances` — Registered HVAC / Dryer devices
- `sensor_nodes` — ESP32 node registry and pairing status
- `hvac_readings` — Time-series data for HVAC units
- `dryer_readings` — Time-series data for dryers
- `sensor_events` — Maintenance and calibration event log

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

### Dryer Test Node (`gas_dryer_test/esp32dryertest/`)

Prototype firmware focused on BME280 + SCT-013 validation.

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
