# IoT Monitoring System v2 — Manual SPC Baseline

This is the **second-generation** backend and frontend for the IoT-Based Monitoring and Alert System for Split HVAC and Gas Dryers.

## What's New in v2

- **Manual Baseline Input**: Instead of waiting for a 15-minute automatic baseline recording (HVAC) or a full dryer cycle, users now input **UCL** and **LCL** directly based on observed data or manufacturer specs.
- **Immediate Alert Activation**: Once baseline is configured, real-time SPC breach alerts are active within seconds — no waiting period.
- **Inline Baseline Configuration**: UCL/LCL input rows appear directly under each chart in the detail modal, with live mean calculation.
- **4-Chart Layout for Both Device Types**: HVAC now has 4 charts (Delta-T, Coil Temp, Delta-RH, Current) matching the dryer's 4-chart layout (Temp, Humidity, Pressure, Current).
- **Sensor Node Feedback**: When baseline is saved, the backend sends `baseline:set` to the node, which beeps 3 times to give physical confirmation.
- **Delta RH for HVAC**: The dashboard now displays humidity differential (Return RH − Supply RH) alongside temperature delta.
- **Real-Time SPC Alerts**: New alert types `spc_ucl_breach` and `spc_lcl_breach` fire immediately when any running reading exceeds control limits.
- **Fault Alert System** (see `FAULTALERT.md`): Research-backed pattern detection for 7 common appliance faults — 4 dryer faults (roller wear, belt snapped, lint blockage, incomplete drying) and 3 HVAC faults (dirty filter, low refrigerant, compressor fault). Uses per-cycle median for dryer mechanical faults and STABLE_ON state tracking for HVAC thermal faults. Only active after SPC baseline is configured.
- **Dryer SCT-013 CF = 30.0**: Calibrated for the new current sensor with 0.111 deductor.
- **Pressure Precision**: BME280 pressure now reports 2 decimal places for finer exhaust duct monitoring.
- **Instant SPC Line Rendering**: SPC bands appear immediately when opening a device card, not after a 4-second polling delay.
- **Discord Webhook Alerts**: Users can configure a personal Discord webhook URL to receive instant rich embed notifications for every alert (SPC breaches, fault alerts, dryer humidity). Multi-tenant — each user's alerts go to their own Discord channel.

## Architecture

```
iot_thesis_v2/
├── app.py                  # Flask backend (MQTT, DB, auth, SPC, API)
├── templates/
│   ├── dashboard.html      # Main SPA frontend (inline baseline config, 4-chart layout)
│   ├── login.html          # User login
│   └── signup.html         # User registration
└── Update_SensorNode/
    └── Update_SensorNode.ino  # Production ESP32 firmware
```

## Database

v2 uses the same PostgreSQL server but expects a **new or migrated schema**:

### Key Schema Differences from v1

| Table | Change |
|-------|--------|
| `appliances` | Removed all `baseline_*_mean/std` columns. Added `baseline_configured BOOLEAN`. Removed `baselining_since`. |
| `spc_manual_baselines` | **NEW** table. Stores `ucl`, `lcl`, `mean` per appliance per metric. |
| `alerts` | Same schema, now populated with `spc_ucl_breach` / `spc_lcl_breach`, `dryer_humidity_high`, and `fault_*` alert types. |
| `users` | Added `discord_webhook_url TEXT` column for per-user Discord integration. |

### `spc_manual_baselines` Table

```sql
CREATE TABLE spc_manual_baselines (
    id SERIAL PRIMARY KEY,
    appliance_id INT REFERENCES appliances(id) ON DELETE CASCADE,
    metric_name TEXT NOT NULL,          -- e.g., 'deltat', 'current', 'texhaust'
    ucl REAL NOT NULL,
    lcl REAL NOT NULL,
    mean REAL NOT NULL,                 -- computed as (ucl + lcl) / 2
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(appliance_id, metric_name)
);
```

## SPC Metrics

### HVAC
| Metric | Source | Description |
|--------|--------|-------------|
| `deltat` | `\|Treturn − Tsupply\|` | Temperature drop across evaporator |
| `deltarh` | `\|RHreturn − RHsupply\|` | Humidity drop across evaporator |
| `tcoil` | `tcoil` | Evaporator coil temperature |
| `rhreturn` | `rhreturn` | Return air humidity |
| `rhsupply` | `rhsupply` | Supply air humidity |
| `current` | `icompressor` | Compressor current |

### Dryer
| Metric | Source | Description |
|--------|--------|-------------|
| `texhaust` | `texhaust` | Exhaust temperature |
| `rhexhaust` | `rh_exhaust` | Exhaust humidity |
| `pressure` | `pressure` | Exhaust pressure |
| `current` | `imotor` | Motor current |

## Workflow

1. **Pair Device** (same as v1)
2. **Calibrate HVAC** (same as v1 — ice-bath method)
3. **Configure Baseline** (NEW):
   - User opens device detail.
   - Clicks **Configure Baseline** (or **Edit Baseline** if already configured).
   - Inline input rows appear under each chart.
   - Inputs UCL and LCL for each metric; mean is computed live as `(UCL + LCL) / 2`.
   - Clicks **Save**.
   - Node beeps 3 times.
   - SPC lines appear on charts immediately; alerts are now active.
   - Grey "Baseline updated: …" timestamp appears at the bottom.
4. **Discord Setup** (optional):
   - Click "🔔 Discord Alerts" in the sidebar.
   - Paste your Discord webhook URL (create one in Discord: Channel Settings → Integrations → Webhooks).
   - Click **Test** to verify, then **Save**.
   - All alerts for your appliances will now ping your Discord instantly.
5. **Monitor** — real-time charts with SPC bands and instant breach alerts.

## API Endpoints (New/Changed)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/device/<id>/baseline_config` | GET | Fetch current baseline values |
| `/api/device/<id>/baseline_config` | POST | Save UCL/LCL values |
| `/api/device/<id>/spc_limits` | GET | Reads from `spc_manual_baselines` |
| `/api/device/<id>/baseline_analysis` | GET | Returns configured UCL/Mean/LCL + `baseline_set_at` timestamp |
| `/api/user/discord_webhook` | GET | Fetch current Discord webhook URL (masked for security) |
| `/api/user/discord_webhook` | POST | Save or clear Discord webhook URL |
| `/api/user/discord_webhook/test` | POST | Send a test embed to verify the webhook works |

Removed endpoints: `remote_baseline`, `cancel_baseline`, `manual_baseline`.

## Running the Application

```bash
cd iot_thesis_v2
python app.py
```

The Flask server starts on `http://0.0.0.0:5000`.

## Frontend Behavior

### Live Mode (Default)
- Polls `GET /api/device/{id}/latest` for live data.
- Updates mini-cards, charts, and status badges.
- Chart push is guarded: only appends if `timeMs > latestChartTimeMs` to prevent duplicate points.
- SPC limits are fetched immediately on card click so bands render without delay.

### History Mode
- Operator selects a time range from the dropdown.
- Dashboard fetches all readings in range via `GET /api/device/{id}/latest_n?start=...&end=...`.
- Charts render once as static data.
- No live updates are appended while in History mode.

### Inline Baseline Config Panel
- Appears directly under each of the 4 charts when Configure/Edit is clicked.
- Shows UCL input, LCL input, and live-computed Mean.
- Save validates all fields are filled and `UCL > LCL`.
- Cancel hides all input rows and restores the Configure/Edit button.

### Status Badges & Action Bar

| Status | Badge | Action Bar Content |
|--------|-------|-------------------|
| `calibration_needed` | Red "Calib. Needed" | Calibration instructions |
| `calibrating` | Yellow "Calibrating..." | Progress message |
| `normal` | Green "Normal" | Bottom: Configure/Edit Baseline button |

### Alerts Panel
- Fetches from `GET /api/device/{id}/alerts`.
- Shows `spc_ucl_breach`, `spc_lcl_breach`, `dryer_humidity_high`, and all `fault_*` alerts.
- Severity-based colors: red = Critical, orange = Warning, blue = Info.
- Read-only in v2 (no acknowledge/resolve buttons wired).

### Discord Alerts
- Sidebar nav item: "🔔 Discord Alerts" opens a settings modal.
- Input: Discord webhook URL (validated as URL type).
- **Test button**: Sends a green "✅ Test Alert" embed immediately.
- **Save button**: Stores URL in `users.discord_webhook_url`.
- When saved, **fault alerts only** trigger a Discord embed with appliance name, value, threshold, and timestamp.
- **What goes to Discord:** 7 actionable fault types only (`fault_dryer_incomplete_drying`, `fault_dryer_roller_wear`, `fault_dryer_belt_snapped`, `fault_dryer_lint_blockage`, `fault_hvac_dirty_filter`, `fault_hvac_low_refrigerant`, `fault_hvac_compressor_fault`).
- **What stays in DB/dashboard only:** SPC breaches (`spc_ucl_breach`, `spc_lcl_breach`) and legacy `dryer_humidity_high`.
- Fire-and-forget: Discord failures are logged but never block alert DB inserts.

## Firmware Note

The firmware in `Update_SensorNode/` is functionally identical to v1 but removes the old baseline acknowledgment handling. It now responds to `baseline:set` with 3 beeps for user feedback.

## Migration from v1

If you have existing v1 data you want to preserve:
1. Create the new `spc_manual_baselines` table in your existing database.
2. Manually input baseline values for existing appliances via the dashboard.
3. Or start fresh with a new database — pairing is automatic when nodes connect.

## Known Issues / Notes

- **`np.polyfit` import fixed** in `app.py`: `import numpy as np` added. HVAC calibration now works correctly.
- **HVAC threshold UI**: Removed. HVAC alerts now use the SPC baseline UCL/LCL limits configured under each chart. No separate threshold setup is needed.
- **BME280**: Sensor was replaced and is now working correctly. Historical note: previous module failed (no I2C response) — replaced with 3.3V-native module.

## Bug Fixes & Hardening

### 2026-05-05 — HVAC Calibration Progress Fix, BME280 Hardening, Motor Current Fix, Ignition Count Fix
- **Firmware (Calibration):** Fixed HVAC calibration progress dashboard sync. During calibration, normal sampling was skipped so no telemetry reached the backend. `start_tcoil` came from stale DB data and the progress bar was frozen.
  - **Fix:** Firmware publishes `calibration_progress` events every ~2.2 s with `t3`, `base_t3`, `delta`. Backend handles these events to update `CALIBRATION_TRACKER` in real time. `api_calibration_progress` reads from tracker instead of `hvac_readings`.
- **Firmware (BME280):** Removed false stuck-detection logic (`bmeStuckCounter`) that triggered after only 3 identical samples (~6 s) during normal operation. Removed `recoverI2C()` from the main loop — bit-banging SCL/SDA was corrupting the active I2C bus by leaving SDA in push-pull OUTPUT mode after `Wire.begin()`.
- **Firmware (BME280):** Simplified BME280 configuration to match the proven `gas_dryer_test` pattern: `MODE_NORMAL, SAMPLING_X2, SAMPLING_X16, SAMPLING_X1, FILTER_X16, STANDBY_MS_62_5`. Removed `Wire.setClock(50000L)` (untested edge case on ESP32-C3).
- **Firmware (BME280):** Added **10 ms delay between BME register reads** to prevent I2C transaction collision under FreeRTOS task switching / WiFi ISR preemption.
- **Firmware (BME280):** Added **3-attempt retry loop** for NaN readings with 50 ms backoff. Auto-soft-reset (write `0xB6` to reset register + re-init) on 5 consecutive NaN samples.
- **Firmware (BME280):** Invalid readings now emit **`null`** in JSON instead of `0.0`. Dashboard shows "—" for missing BME data. Added `bmeValidSamples` counter for accurate averaging.
- **Firmware (Setup):** Moved `setupWifi()` before sensor initialization. DHT warm-up loop (up to 20 s) now runs **only for HVAC**; skipped for dryers. BME init simplified to single `bme.begin(0x76, &Wire)`.
- **Backend (Motor current):** Fixed `_motor_readings` only appending when `_peak_state == "IDLE"`. The peak state machine could get stuck in "RISING" because the fallback drop threshold (0.1 A) exceeded actual gas dryer motor fluctuation (±0.03 A). Now collects **ALL readings** into `_motor_readings` and filters ignition spikes at runtime with `filter_threshold = average * 1.15`. Motor baseline median is now stable (~3.1 A) across all time ranges.
- **Backend (Ignition count):** Added hysteresis (`_peak_max - 0.1`) and hard floor (`_peak_max > mean_current + 0.15`) to peak detection. Verified against `Dryer_Test_20260505_111710.xlsx` — correctly reports 4 ignitions instead of 5.

### 2026-05-05 — Discord Alert System Revision
- **Discord — Fault-Only Alerts:** Raw SPC breach alerts (`spc_ucl_breach`, `spc_lcl_breach`) are **removed from Discord**. They still insert into the `alerts` table and appear on the dashboard, but they no longer spam the Discord channel.
- **Discord — `dryer_humidity_high` Removed:** The legacy end-of-cycle humidity alert (`dryer_humidity_high`) is also **removed from Discord**. The more precise `fault_dryer_incomplete_drying` (SPC-based) remains active and is sent to Discord instead.
- **Discord — Maintenance-Ticket Embeds:** `send_discord_alert()` rewritten with `FAULT_DISCORD_MAP`. Each fault alert now sends a rich embed containing: severity icon + human-readable title, fault description, root cause, and recommended action — formatted like a maintenance work order.
- **Fault Triggering — Immediate:** Removed the 3-consecutive-cycle confirmation delay from `fault_dryer_roller_wear` and `fault_hvac_dirty_filter`. Both now fire **immediately on first detection** at cycle end. The existing 10-minute cooldown per fault type (`_insert_fault_alert()`) prevents spam without delaying actionable maintenance advice.

### 2026-05-04 — Security & Backend Hardening
- **Credential Hardening:** Removed all hardcoded password defaults from `app.py`. Added `python-dotenv` loading. App now fails fast with a clear `RuntimeError` if `FLASK_SECRET_KEY`, `MQTT_PASS`, or `DB_PASSWORD` is missing. Create a `.env` file in `iot_thesis_v2/` (gitignored) with your credentials.
- **Direction-Aware SPC Cooldown:** `SPC_ALERT_COOLDOWN` key changed from `(appliance_id, metric_name)` to `(appliance_id, metric_name, alert_type)`. UCL and LCL breaches on the same metric are now rate-limited independently (5 min each).
- **In-Memory Tracker Cleanup:** `forget_device()` now purges `appliance_id` from all in-memory dicts (`DRYER_CYCLE_STATS`, `HVAC_CYCLE_TRACKER`, `FAULT_ALERT_TRACKER`, `FAULT_ALERT_COOLDOWN`, `SPC_ALERT_COOLDOWN`, `CYCLE_TRACKER`, `CALIBRATION_TRACKER`) to prevent memory leaks.
- **Motor Baseline Median Helper:** Extracted `_compute_motor_baseline_median()` to eliminate 4x duplicated median computation across `_finalize_dryer_cycle()` and `dryer_analytics()`.
- **Belt Snap Gap Detection:** Added gap-based inference in `_check_dryer_faults()`. If a running cycle ends via >60s gap and `min_current < LCL` (or `belt_snap_start` was set), a `fault_dryer_belt_snapped` alert is triggered. This catches snaps that drop below the firmware's 0.4A gate before the 30s sustained-low timer fires.
- **Frontend `.btn-secondary` CSS:** Added missing `.btn-secondary` rule so Cancel and Test buttons are properly styled.
- **Frontend `.std` Fix:** Removed non-existent `data.deltat.std` / `data.tcoil.std` references from `showThresholdPanel()`. HVAC threshold panel now shows an informational message instead of broken inputs with hardcoded fallbacks.
