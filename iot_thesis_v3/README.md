# IoT Monitoring System v3 — Backend-Driven CF/Deductor

This is the **third-generation** backend and frontend for the IoT-Based Monitoring and Alert System for Split HVAC and Gas Dryers.

## What's New in v3

- **Backend Owns CF/Deductor:** The backend stores calibration factor (CF) and deductor per appliance in the `appliances` table. When a sensor node boots or reconnects, the backend sends `setcf:*` and `setdeductor:*` via MQTT control topic.
- **Sensor Node Computes Current:** The ESP32-C3 receives CF and deductor from the backend, persists them in NVS flash memory, and computes `CurrentA = max(0, (rawMv/1000)*cf - deductor)` locally. This eliminates hardcoded sensor constants in firmware — new appliance types can be added in the backend without reflashing.
- **All Telemetry Stored:** Every 10-second telemetry window (running or idle) is stored in PostgreSQL. The old "running-only insert gate" is removed.
- **Filtered/Unfiltered Data Toggle:** A radio toggle in the dashboard lets users switch between filtered (running only, default) and unfiltered (all data including idle gaps). Idle points are styled as small gray dots on charts.
- **Export with Idle Option:** The Excel export modal has an "Include idle data" checkbox.
- **Threshold Standardization:** All current thresholds across backend and firmware are consistently **0.25 A**.
- **Removed Data Auto Variant:** The separate `Update_SensorNode_Data_Auto.ino` firmware is no longer needed; the standard firmware sends continuous data natively.

## Architecture

```
iot_thesis_v3/
├── app.py                  # Flask backend (MQTT, DB, auth, SPC, API)
├── templates/
│   ├── dashboard.html      # Main SPA frontend (filtered toggle, idle styling)
│   ├── login.html          # User login
│   └── signup.html         # User registration
└── Update_SensorNode/
    └── Update_SensorNode.ino  # Firmware: receives CF/deductor, computes CurrentA
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

Because v3 stores **all** telemetry (not just running), database size will grow approximately **3–5×** faster than v2. Consider implementing a retention policy for long-term production use.

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

### 2026-05-26 — Credential Sanitization for GitHub
- **Real firmware excluded:** All `Update_SensorNode/` and `esp32dryertest/` folders with hardcoded WiFi/MQTT credentials added to `.gitignore`.
- **Clean versions pushed:** `_Clean` folders created for all 6 firmware variants with placeholder credentials (`YOUR_WIFI_SSID`, `YOUR_WIFI_PASSWORD`, `YOUR_MQTT_BROKER`, `YOUR_MQTT_USERNAME`, `YOUR_MQTT_PASSWORD`).
- **v4 backend sanitized:** Hardcoded MQTT defaults (`d57bf828...`, `esp32user`) removed from `app.py`. All MQTT values now require environment variables.

### 2026-05-26 — Atmospheric Pressure Simplification
- **EMA removed:** Atmospheric pressure baseline no longer uses exponential moving average.
- **Simple update:** Updates to latest raw idle reading every 2 minutes with 15-second settling guard.
- **Null-safe:** Guards against `None` raw_pressure. Hard reset on first idle after cycle end.
- **DB persistence:** Baseline stored in `appliances.atmospheric_pressure` and survives restarts.

### 2026-05-26 — Dryer Incomplete Drying Alert Fix
- **Root cause:** With "running only" firmware, the ESP32 stops sending when current drops below ~0.25A. The old code only called `check_fault_alerts()` on running messages, so the `current < 0.15` cycle-end check was never reached. If the cycle was the last of the day, no subsequent message arrived to trigger gap-based finalization, and the alert was never evaluated.
- **Fix (3 parts):**
  1. **Global timeout check** — After every MQTT message, ALL in-cycle dryers are scanned. Idle >120s triggers `_finalize_dryer_cycle()`.
  2. **Fault checks on all dryer messages** — `check_fault_alerts()` runs on both running and idle messages. Prepares for "keep on sending data" firmware.
  3. **Guard real-time checks during idle** — Spike state machine and belt snap detection only run when `current >= 0.25`. Idle messages reset fault trackers to prevent false positives.

### 2026-05-26 — Session Security & Rate Limiting
- **Flask-Limiter** added: login 30/min, signup 5/min, default 200/min.
- **Session hardening:** Secure, HttpOnly, SameSite=Lax cookies; 12-hour session lifetime.
- **Null-safe sensor handling:** Missing DHT/BME readings emit `null`; backend handles gracefully.

### 2026-05-26 — Gauge Pressure UI Layout
- Dryer latest-data grid changed to 3-column layout. Gauge pressure and absolute pressure now wrap neatly below exhaust temp / RH / current.

### 2026-05-26 — Excel Export Enhancement
- **New column:** "Raw Absolute Pressure (hPa)" added to dryer exports.
- **Renamed:** "Pressure" → "Gauge Pressure" for clarity.

### 2026-05-17
- **Baseline removal feature** — New ` Remove Baseline` button appears when baseline exists. `DELETE /api/device/<id>/baseline_config` clears all baseline rows and sets `baseline_configured = FALSE`. Frontend wipes SPC lines from charts and refreshes UI state.
- **Discord alert testing documented** — Fault alerts can be tested by setting tight UCL/LCL baselines (e.g., Current UCL = 2.01 for a 2.0 A motor baseline) so normal running data immediately crosses thresholds. This fires real Discord alerts with the maintenance-ticket embed format. See AGENTS.md §11 for examples, limitations (10-min cooldown, DB pollution), and cleanup instructions.
- **Idle point styling removed** — `updateChart` no longer applies gray tiny-dot styling to idle points. Idle and running points now render identically. The filtered/unfiltered toggle still controls visibility.
- **Dryer cycle RH refined** — `start_rh` computed from first 6 RH readings (was single point). `end_rh_avg` computed from last 6 RH readings (was last 10). Both live fault detection and historical analytics updated.

### 2026-05-10
- **DHT22 stuck detection** in firmware — per-window (10s) stuck-value detection with `dht.begin()` re-init after 3 consecutive stuck windows.
