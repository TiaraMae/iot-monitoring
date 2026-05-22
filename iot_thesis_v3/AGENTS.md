# AGENTS.md — IoT Monitoring v3 (Backend-Driven CF/Deductor)

> **If you are an AI agent working on this folder (`iot_thesis_v3/`), read this file first.**

---

## 1. Project Identity

This is the **v3 iteration** of the IoT Monitoring & Predictive Maintenance thesis project.

- **Title:** IoT-Based Monitoring and Alert System for Split HVAC and Commercial Gas Dryers
- **Author:** Tiara Mae Muljana
- **Institution:** Swiss German University (SGU)

### Core Architecture
**The backend owns the calibration factor (CF) and deductor values.** When a sensor node boots or reconnects, the backend sends its CF and deductor via MQTT. The node computes current locally and sends the calculated `CurrentA` in telemetry. This makes the system extensible: new appliance types with different current sensors can be added in the backend without reflashing firmware.

---

## 2. Repository Layout

```
iot_thesis_v3/
├── app.py                   # Flask backend (MQTT, DB, auth, SPC, API)
├── templates/
│   ├── dashboard.html       # Main SPA (filtered/unfiltered toggle, idle styling)
│   ├── login.html           # Unchanged from v2
│   └── signup.html          # Unchanged from v2
└── Update_SensorNode/
    └── Update_SensorNode.ino  # Firmware: receives CF/deductor, computes CurrentA
```

**Do not mix with `iot_thesis/` (v1) or `iot_thesis_v2/` (v2).** They are independent systems.

---

## 3. Hardware Specifications

Identical to v2. See root `AGENTS.md` for full pin map and sensor specs.

### v3-Specific Hardware Notes
- **Current sensor:** ZHT103C (HVAC) or SCT-013 (Dryer) → ADC → RMS → `esp_adc_cal_raw_to_voltage()` → raw mV.
- **Firmware does NOT hardcode CF/deductor.** It receives them from the backend via MQTT control topic (`setcf:*`, `setdeductor:*`).
- **CF and deductor are persisted in ESP32 NVS** (`nodecfg` namespace, keys `cf` and `deductor`) across reboots.
- **LED running threshold:** Computed current `>= 0.25 A` (universal, not appliance-specific raw mV).

### Firmware Key Changes (v3)
- `readCurrentIrms()` — Samples ADC, computes RMS mV, then applies `nodeCf` and `nodeDeductor` to calculate amperage.
- Telemetry payload sends `"CurrentA"` (float, 3 decimal places) instead of raw mV.
- **Telemetry gate removed:** Once `calibrationAcked == true`, ALL data is published (running + idle). No more idle discard.
- **Offline buffering buffers all telemetry**, not just running windows.
- `status` field computed as `"running"` / `"idle"` based on `CurrentA >= 0.25`.
- `Update_SensorNode_Data_Auto.ino` variant **removed** — standard firmware sends continuous data natively.

---

## 4. Backend Architecture (`app.py`)

### Current Flow (v3)

```
Node boots / reconnects
  → sends event_request_config
  → Backend queries appliances table for type, operational_status, cf, deductor
  → Backend replies:
      settype:hvac or settype:dryer
      setcf:11.0 or setcf:33.0
      setdeductor:0.033 or setdeductor:0.111
      restore:normal or restore:calibrationneeded
  → Node stores cf/deductor in NVS
  → Node computes CurrentA = max(0, (rawMv/1000)*cf - deductor)
  → Telemetry sends "CurrentA"
  → Backend stores CurrentA directly (no computation)
```

### Database — `appliances` Table

**Columns added:**
```sql
ALTER TABLE appliances ADD COLUMN cf REAL;
ALTER TABLE appliances ADD COLUMN deductor REAL;
```

**Default values set on INSERT (`pair_device`):**
- Dryer: `cf = 33.0`, `deductor = 0.111`
- HVAC: `cf = 11.0`, `deductor = 0.033`

Existing rows must be backfilled with a one-time migration.

### Config Request Handler (`handle_node_events`)

When `event_request_config` is received from a **paired** node:
1. Query `appliances` for `type`, `operational_status`, `cf`, `deductor`.
2. Fallback to type-based defaults if `cf`/`deductor` are NULL (pre-migration safety).
3. Send commands in this order:
   - `settype:hvac` / `settype:dryer`
   - `setcf:{value}`
   - `setdeductor:{value}`
   - `restore:normal` / `restore:calibrationneeded`

CF/deductor are sent **on every reconnect**, not just first pairing, so the node always has current values.

### Telemetry Ingestion (`on_mqtt_message`)

```python
final_amps = max(0.0, float(data.get("CurrentA", 0.0) or 0.0))
```

No backend computation. The node has already applied CF and deductor.

### All Readings Stored

- **Every** telemetry message results in an INSERT into `hvac_readings` or `dryer_readings`, regardless of current.
- SPC alert checking and fault alert checking are gated to `final_amps >= 0.25` — idle data is stored but not evaluated.

### Filtered API Parameter

Data endpoints accept `filtered` query parameter (default `true`):
- `true` → only `icompressor >= 0.25` / `imotor >= 0.25`
- `false` → all readings including idle

### Sensor Config API (Optional)

`GET/POST /api/device/<id>/sensor_config`
- Returns or updates `cf` and `deductor` for an appliance.
- Used for future appliance types or sensor recalibration.
- Not exposed in the frontend UI yet.

### Threshold Standardization

All current thresholds are **0.25 A** consistently:
- `_compute_daily_energy()` cycle split
- `api_device_latest()` running status badges
- `_check_dryer_faults()` cycle start
- `_check_hvac_faults()` compressor ON threshold (0.25 A), snapshot tracker evaluation (max deltat)
- `hvac_analytics()` SQL filter
- `dryer_analytics()` cycle detection

---

## 5. Frontend (`dashboard.html`)

### Filtered/Unfiltered Toggle
- Radio buttons: `[● Filtered] [○ Unfiltered]`
- Default: **Filtered** (only running data).
- Changing re-fetches chart history and table data.

### Idle Data Visualization
When **Unfiltered**:
- Idle points (`current < 0.25 A`) appear as small gray dots (`#94a3b8`, radius 1).
- Running points appear as colored dots (dataset color, radius 3).
- Implemented via per-point `pointBackgroundColor` and `pointRadius` arrays in Chart.js.

### Export Modal
- Checkbox: **"Include idle data"**
- Unchecked (default): exports with `filtered=true`
- Checked: exports with `filtered=false`

---

## 6. Data Flow Summary

```
ESP32-C3
  ├── Samples ADC → RMS → esp_adc_cal_raw_to_voltage() → raw mV
  ├── Applies nodeCf + nodeDeductor → CurrentA
  ├── Sends telemetry: { "CurrentA": 3.125, "status": "running", ... }
  └── Publishes EVERY 10s window (running + idle) after calibration

Flask Backend
  ├── Receives MQTT telemetry
  ├── Stores CurrentA directly into DB (no computation)
  ├── If CurrentA >= 0.25:
  │     └── check_fault_alerts()
  └── API endpoints serve data with optional ?filtered= param

Frontend
  ├── Default: ?filtered=true (running only)
  ├── Toggle to ?filtered=false (all data)
  └── Idle points styled as gray/small on charts
```

---

## 7. Common Pitfalls for Agents

1. **Firmware computes current, backend stores it directly.** Do not add CF/deductor math to `on_mqtt_message()`.
2. **Firmware must receive CF/deductor before computing current.** If `nodeCf <= 0`, `readCurrentIrms()` returns 0.0 and logs a warning.
3. **Always send setcf + setdeductor on config request.** Even on reconnect. The node may have been reflashed (NVS wiped).
4. **Always INSERT in backend.** Do not gate INSERTs on `is_running`. Gate only the **alert checking**.
5. **Cycle end thresholds are still lower than start.** Dryer cycle end uses `baseline_current_mean * 0.3` (or 0.15 A fallback). HVAC stopping transition uses 0.25 A with 60 s timeout.
6. **Default view is filtered.** The frontend and API both default to `filtered=true`.
7. **No DB schema changes required for telemetry.** `icompressor` and `imotor` columns already store floats. Idle vs running is inferred from value.
8. **Data growth concern:** Continuous telemetry increases DB size ~3–5×. Consider retention policy for production.
9. **NVS keys for CF/deductor:** `cf` (float) and `deductor` (float) in `nodecfg` namespace. Clear them on `settype:unpaired`.
10. **Existing appliances need migration:** Run the SQL `ALTER TABLE` + `UPDATE` statements before deploying.

---

## 8. HVAC Fault Alert Redesign (v3)

The HVAC fault detection system was **redesigned in v3** around a **peak-performance snapshot** approach:

### Evaluation Matrix
| Delta-T | Current | Result |
|---------|---------|--------|
| ≥ LCL | any | ✅ Good condition |
| < LCL | < LCL | 🔴 Low refrigerant |
| < LCL | LCL–UCL | 🟠 Dirty air filter |
| < LCL | > UCL | 🔴 Outdoor problem (capacitor/condenser) |

### Snapshot Capture
- **Non-inverter:** Reading with **maximum Delta-T** during the ON cycle, evaluated at cycle end (current < 0.25 A).
- **Inverter:** Reading with **maximum Delta-T** during high-effort window (T_return > 26.5°C), evaluated when maintaining phase detected (current < 80% of peak for > 2 min) or compressor turns off.

### Key Changes from v2
- **Removed:** 10-minute STABLE_ON window, T_coil-based evaluation, consecutive counters.
- **Added:** Single-reading snapshot per cycle/window, Delta-T + current matrix.
- **Charts:** Delta-T, T_supply, T_return, T_coil, Current (5 charts). Only Delta-T and Current have SPC lines/baseline config.
- **Baselines:** Only `deltat` and `current` are configured for HVAC fault detection.

See `FAULTALERT.md` for full details.

---

## 9. Retained from v2 (Unchanged in v3)

- Manual SPC baseline input (`spc_manual_baselines` table)
- Dryer fault alert system (4 fault types, unchanged)
- Discord webhook integration
- HVAC calibration (ice-bath method)
- Pairing/unpairing workflows
- LED state machine and buzzer signals

---

## 10. Changelog

### 2026-05-17 — Baseline Removal Feature
- **New capability:** Users can now remove/clear a previously saved baseline via a `🗑️ Remove Baseline` button next to `✏️ Edit Baseline`.
- **Backend:** Extended `api_baseline_config` to accept `DELETE`. Deletes all `spc_manual_baselines` rows for the appliance and sets `appliances.baseline_configured = FALSE`.
- **Frontend:** `removeBaselineConfig()` calls DELETE, then clears SPC line datasets (1–3) from all charts, resets `currentDeviceHasBaseline`, hides inputs, and rebuilds the action bar. No firmware impact.

### 2026-05-17 — Idle Point Styling Removed + Dryer Cycle RH Refinement
- **Idle point styling removed:** `updateChart` no longer applies per-point `pointBackgroundColor`/`pointRadius` arrays for idle vs running distinction. Idle data points now render with the same color and radius as running points. The `!showIdle && idle` guard in live updates still controls whether idle points are pushed at all.
- **Dryer cycle begin RH:** Historical `dryer_analytics` defers `start_rh` computation to cycle finalization, averaging the **first 6** `valid_rh` entries (was single first reading at cycle start). Live `_finalize_dryer_cycle` also computes `begin_rh_avg` from first 6 `rh_history` entries.
- **Dryer cycle end RH:** Both live and historical paths now compute `end_rh_avg` from the **last 6** RH readings (was last 10). Consistent ~1-minute smoothing window at both cycle start and end.

### 2026-05-17 — SPC Breach Alerts Completely Removed
- **Rationale:** `spc_ucl_breach` / `spc_lcl_breach` fired on every individual reading crossing a threshold (e.g., dryer ignition spike crossing Current UCL). This was raw data-point spam, not actionable maintenance advice. The 7 fault alerts already evaluate at cycle/window end and provide diagnostics.
- **Backend:** Deleted `SPC_ALERT_COOLDOWN` global, `check_spc_alerts()` function, and its call in `on_mqtt_message()`. Removed from tracker cleanup in `forget_device()`.
- **Frontend:** Removed `isSPCBreach` styling branch from `loadAlerts()`. Alerts panel now only shows the 7 actionable fault types.
- **SPC baselines preserved:** UCL/LCL/Mean values continue to drive chart lines and gate the 7 fault alerts. Only the per-reading breach alerts are gone.

### 2026-05-18 — Dryer Cycle Gap Threshold Increased to 2 Minutes
- **Change:** Gap threshold that differentiates separate dryer cycles increased from **60 seconds → 120 seconds (2 minutes)**.
- **Files changed:** `app.py` — 8 substitutions across `_compute_daily_energy`, `_compute_energy_kwh`, `_check_dryer_faults`, `on_mqtt_message` CYCLE_TRACKER, and `dryer_analytics`.
- **Rationale:** 1-minute gaps were too aggressive — brief pauses (e.g., ignition gaps) within a single cycle were incorrectly splitting it into multiple cycles. A 2-minute gap better represents a true "cycle ended" condition.
- **Energy computation:** Also updated to use 120s gap breaks for consistency with cycle detection.

### 2026-05-18 — Two-Tier Severity System (Warning + Critical) + Rolling-Window Evaluation
- **HVAC evaluation redesigned:**
  - Non-inverter: evaluates at **10 min of running** OR before compressor turns off (whichever comes first).
  - Inverter: evaluates **before maintaining state** (before current drops) OR at 10 min.
  - Uses **average of last 3 readings** at the evaluation point (instead of single snapshot).
  - **Severity:** Warning when Treturn < 27°C. **Critical** when Treturn ≥ 27°C after 10 min (no effective cooling).
- **Dryer evaluation redesigned:**
  - Uses **average of last 6 readings** at cycle end for end-RH, end-temp, and end-current.
  - `fault_dryer_lint_blockage`: Warning when end RH > UCL + temp > UCL. **Critical** when max temp > 100°C (burning risk).
  - `fault_dryer_incomplete_drying`: Warning when end RH > UCL. **Critical** when end RH > 90% (not drying at all).
- **Backend:** Added `severity` column to `alerts` table. Updated `_insert_fault_alert()`, `send_discord_alert()`, `_check_hvac_faults()`, `_evaluate_hvac_window()`, `_finalize_dryer_cycle()`, `api_alerts()`.
- **Frontend:** `loadAlerts()` now shows severity badges (red CRITICAL pill / amber WARNING pill) using the `severity` field from API.
- **Docs:** Updated `FAULTALERT.md` with new evaluation timing, severity matrices, and code snippets.

## 11. Testing Discord Alerts via Tight Baselines (No Code Changes)

You can test Discord fault alerts without any code changes by setting very tight baselines that normal running data will immediately cross.

### Quick Examples
| Fault | Appliance | Baseline Settings | Trigger Condition |
|-------|-----------|-------------------|-------------------|
| `fault_dryer_roller_wear` | Dryer | Current UCL = 2.01 (baseline ~2.0 A) | Current > 2.01 A |
| `fault_dryer_belt_snapped` | Dryer | Current LCL = 1.99 (baseline ~2.0 A) | Current < 1.99 A |
| `fault_hvac_dirty_filter` | HVAC | Delta-T LCL = 9.5 (normal ~10°C) | Delta-T < 9.5°C with normal current |
| `fault_hvac_low_refrigerant` | HVAC | Delta-T LCL = 9.5, Current LCL = 2.5 | Delta-T < 9.5°C with current < 2.5 A |
| `fault_hvac_compressor_fault` | HVAC | Delta-T LCL = 9.5, Current UCL = 4.0 | Delta-T < 9.5°C with current > 4.0 A |

### Limitations
- **10-minute cooldown** per fault type per appliance (`_insert_fault_alert` cooldown). Restart backend to reset.
- **DB pollution** — test alerts are real rows in `alerts`. Clean up with `DELETE FROM alerts WHERE appliance_id = X;` after testing.
- **Lint blockage / incomplete drying** require cycle-end conditions (both RH + temp thresholds). Harder to trigger naturally.

---

### 2026-05-14 — Dashboard Fixes
- **Chart6 destroy fix:** `initCharts` was destroying charts 1–5 but **not chart6** (Delta RH). On filter toggle, Chart.js threw "Canvas is already in use" because the old chart6 instance was still attached. This aborted `initCharts`, leaving all charts empty. Fixed by adding `chart6` to the destroy and null-reset arrays.
- **Filter toggle cache-busting:** Added `&_cb=${Date.now()}` to history fetch URL to prevent browsers from returning stale cached responses on rapid filtered/unfiltered toggles.
- **setTimeout delay in `onFilterChange`:** Added 50ms async delay before calling `initCharts` to give Chart.js animation frames time to clean up after `destroy()`.
- **Delta RH section visibility:** HVAC `initCharts` restored visibility for `section-chart-5` but not `section-chart-6`. After viewing a dryer (which hides section-chart-6), switching back to HVAC left Delta RH invisible. Added `section-chart-6.style.display = 'block'` in HVAC's `initCharts`.
- **Dryer `pushToCharts` mapping fix:** When `pushToCharts` was expanded from 5 to 6 values, the dryer calls were not updated. They passed `undefined, false/true` which landed in `val6` and `doUpdate` positions, breaking dryer live updates. Fixed by passing explicit `null, null` for val5/val6.
- **Radio button sync on modal open:** `openDeviceDetail` now resets filter radio buttons to "Filtered" to match the `showIdle = false` default, preventing UI/JS state mismatch when reopening the modal.
- **Export modal sync with history range:** When `chartMode === 'history'`, `showExportModal()` pre-fills export dates from `historyStart`/`historyEnd`.
- **"Include idle data" checkbox fix:** Explicitly sends `filtered=false` when checked (backend defaulted to `filtered=true` when param was missing).

### 2026-05-15 — Inverter/Non-Inverter Pairing Fix
- **Root cause:** Pairing form sent `name="subtype"` but backend read `request.form.get('sub_type')`. Mismatch caused every device to default to `'noninverter'` regardless of user selection.
- **Fix:** Form field changed to `name="sub_type"` and option value to `value="noninverter"`.
- **Card display:** Template now only shows `sub_type` for HVAC (`'HVAC' in a.type`), hiding it for dryers.
- **DB update:** Set `sub_type = 'inverter'` for "AC WS 1" (189), "1 - AC01" (197), "5 - AC Home 01" (202).

### 2026-05-15 — Humidity Calibration Clamp Reverted
- **Change:** Removed `clamp_to=(0, 100)` from all humidity `apply_calibration()` calls and reverted the function to its original 3-parameter signature.
- **Reason:** Calibrated humidity values from linear regression can legitimately exceed 100% when operating conditions fall outside the calibration range. User will consult their advisor before deciding on a final approach (clamp, raw values, or alternative calibration method).
- **Impact:** Dashboard, exports, and SPC calculations now show raw calibrated humidity values as-is from `y = mx + c`.

### 2026-05-19 — Belt Snap & Roller Wear: 3-Consecutive-Reading Fix
- **Root cause of false belt snap on Dryer 2:** The gap >120s check in `_check_dryer_faults` inferred belt snap from `min_current < LCL` — a single lowest point in the entire cycle. This falsely triggered on normal ignition valleys or brief dips.
- **Removed redundant logic:**
  - Deleted `CYCLE_TRACKER` / `_process_cycle_end` / `dryer_humidity_high` alert (redundant with `fault_dryer_incomplete_drying`).
  - Deleted gap-based belt snap inference (`min_current < LCL` on gap >120s).
  - Deleted median-based belt snap and roller wear checks from `_finalize_dryer_cycle`.
- **New real-time detection (`_check_dryer_faults`):**
  - Tracks `consecutive_below_lcl` and `consecutive_above_ucl` counters during active cycles.
  - Only counts **running readings** (`current >= 0.25`) because `_check_dryer_faults` is gated by `final_amps >= 0.25`.
  - Belt snap: 3 consecutive readings < LCL → critical alert.
  - Roller wear: 3 consecutive readings > UCL → warning alert.
  - `belt_snap_triggered` / `roller_wear_triggered` flags prevent duplicates per cycle.
- **New end-of-cycle backup (`_finalize_dryer_cycle`):**
  - Added missing `baseline_configured` + `alert_enabled` gate at top of function.
  - Belt snap: last 3 `motor_readings` all < LCL (catches immediate-shutdown cases).
  - Roller wear: any 3 consecutive `motor_readings` all > UCL.
  - `motor_readings` only contains running data, so idle/pause gaps cannot false-trigger.
- **Why pause gaps are safe:** `_check_dryer_faults` only receives running data; idle readings (current = 0 during 1-2 min gas dryer pauses) are inserted into DB but never passed to fault detection. `DRYER_CYCLE_STATS['motor_readings']` is only appended inside `_check_dryer_faults`, so it exclusively contains running readings.
- **Docs:** Updated `FAULTALERT.md` §2.4, §3.2, §3.3, §5.2, §8.4, §9 with new detection logic and removed `dryer_humidity_high`.

### 2026-05-17 — Alert-Based Device Badge + Resolve Button
- **Backend:**
  - Added `get_appliance_alert_status(appliance_id)` helper → queries `MAX(severity)` of unresolved alerts, returns `'normal'`, `'warning'`, or `'critical'`.
  - `api_device_latest` now returns `alert_status` in all response paths.
  - `api_spc_limits` now returns `alert_status` for frontend polling.
  - `get_appliances_for_user` includes `alert_status` per appliance.
  - `POST /api/alert/<int:alert_id>/resolve` endpoint added — sets `resolved_at = NOW()`, verifies ownership via `appliances.user_id` join.
- **Frontend:**
  - Card badge now reflects `alert_status` instead of `operational_status`: Normal (green) = no unresolved alerts; Warning (amber); Critical (red). Calibration states (`calibration_needed`, `calibrating`) still take precedence.
  - `data-alert-status` attribute added to card divs.
  - `loadAlerts()` renders a **Resolve** button for each unresolved alert; resolved alerts show their resolution timestamp and are dimmed (`opacity: 0.7`).
  - `resolveAlert(alertId)` function POSTs to `/api/alert/${alertId}/resolve`, then refreshes `loadAlerts()` and `updateMiniCards()`.
  - `updateMiniCards()` polling now updates the main status badge dynamically when `alert_status` changes.
  - `buildStatusPolling()` `doPoll` updates badge from `alert_status` independently of `operational_status`.

### 2026-05-16 — Monthly Energy Consumption Pie Chart
- **New feature:** One pie chart at the top of the dashboard showing monthly energy consumption grouped by appliance type (HVAC = blue, Dryer = orange).
- **Backend:** Added `_compute_energy_kwh()` unified energy integral, `/api/energy_summary`, `/api/energy_summary/export`, and `/api/energy_months`.
- **Frontend:** Month selector (only shows months with actual data), pie chart, right-side summary panel with total kWh + per-type breakdown + per-appliance list, Excel export button.
- **Polling:** Updates every 5 seconds.
- **Forgotten devices:** Excluded — only current appliances are queried.

### 2026-05-12 — Delta RH Chart Added for HVAC
- **New chart6:** Displays `abs(RHreturn - RHsupply)` with pink `#EC4899` line, placed between T_return and T_coil in the 6-chart HVAC layout.
- **Backend:** `api_device_latest` and `api_device_latest_n` return `DeltaRH` field.
- **Frontend:** `pushToCharts` expanded to 6 positional values; history callback recomputes `Math.abs(d.RHreturn - d.RHsupply)`.

### 2026-05-10 — DHT22 Stuck Detection (Firmware)
- **Per-window detection:** Checks averaged telemetry values every 10s instead of per-sample. Threshold: 3 consecutive stuck windows = 30s total.
- **DHT re-init:** Calls `dht.begin()` to recover from stuck state.
- **Poisoning protection:** `lastGoodDHT*` fallbacks only updated when sensor is not stuck, preventing corrupted values from poisoning future windows.

### 2026-05-07 — Gap Detection Fix
- Wrapped gap detection in `if (data.running_status === 'running')` to prevent `initCharts` destroy/recreate every 5 seconds when device is idle in filtered mode.

### 2026-05-07 — Idle Data Leak Fix
- Added `!showIdle && idle` guard around `pushToCharts` in live updates so filtered charts stay clean and do not accumulate idle points.

### 2026-05-05 — Dryer SPC Lines Fix
- Updated `applySPCLines` to use `texhaust`/`rhexhaust` keys instead of old `temp`/`humidity`, fixing missing SPC lines on dryer charts.
- Offline buffering (200 messages max)
