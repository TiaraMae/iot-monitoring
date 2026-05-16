# AGENTS.md — IoT Monitoring v2 (Manual SPC Baseline)

> **If you are an AI agent working on this folder (`iot_thesis_v2/`), read this file first.**

---

## 1. Project Identity

This is the **v2 iteration** of the IoT Monitoring & Predictive Maintenance thesis project.

- **Title:** IoT-Based Monitoring and Alert System for Split HVAC and Commercial Gas Dryers
- **Author:** Tiara Mae Muljana
- **Institution:** Swiss German University (SGU)

### Core Change from v1
**Automatic baseline recording is replaced by manual user-input baselines.** Users enter UCL (Upper Control Limit) and LCL (Lower Control Limit) per metric. The mean is computed as `(UCL + LCL) / 2`. This eliminates the 15-minute HVAC baseline wait and the cycle-based dryer baseline wait.

---

## 2. Repository Layout

```
iot_thesis_v2/
├── app.py                   # Flask backend
├── templates/
│   ├── dashboard.html       # Main SPA (inline baseline config, 4-chart layout)
│   ├── login.html           # Unchanged from v1
│   └── signup.html          # Unchanged from v1
└── Update_SensorNode/
    └── Update_SensorNode.ino  # Cleaned firmware
```

**Do not mix with `iot_thesis/` (v1).** They are independent.

---

## 3. Hardware Specifications

Identical to v1. See root `AGENTS.md` for full pin map and sensor specs.

### v2-Specific Hardware Notes
- **Dryer SCT-013 Calibration Factor:** Updated to **33.0** (was 37.0 in v1). Deductor remains **0.111**.
- **Pressure Precision:** BME280 pressure readings now report **2 decimal places** (was 1 decimal) for finer exhaust duct monitoring.

### Key Firmware Changes
- `baselinestartack`, `baselinesuccessack`, `baselinefailack` commands are **removed**.
- **New command:** `baseline:set` → node beeps 3 short beeps to confirm baseline configuration.
- **Current threshold lowered:** 0.4 A → **0.25 A** across all firmware usages (telemetry gating, LED, status, checkin, BME280 stuck detection).
- **Watchdog hardening:** `esp_task_wdt_reset()` + `delay(1)` at critical blocking points (ADC sampling loop, DHT reads, DS18B20 conversion wait). Replaces `yield()` which did not guarantee ISR servicing on single-core ESP32-C3.
- **DS18B20 non-blocking:** `setWaitForConversion(false)` in setup + yielding 750 ms wait loop instead of blocking `requestTemperatures()`.
- **Buffer flush cap:** Maximum **10** offline messages published per `loop()` iteration to prevent long MQTT blocking.
- **Buzzer UX:** Button 2 5s hold (HVAC calibration request) is now **silent** — no local beep. `startcalibration` backend ack = 1 short beep.

---

## 4. Backend Architecture (`app.py`)

### Removed from v1
- `BASELINE_TIMER_TRACKER` and all `threading.Timer` baseline logic.
- `BASELINE_DRYER_TRACKER` and cycle-end watcher logic.
- `do_set_baseline_calculated()`, `_complete_baseline()`, `_dryer_baseline_safety_timeout()`, `_start_dryer_baseline_cycle_timer()`, `_dryer_baseline_cycle_timeout()`.
- `remote_baseline`, `cancel_baseline`, `manual_baseline` endpoints.
- `baseline_calc_thread()` daemon thread.
- `baselining_since` column.
- All `baseline_*_mean` and `baseline_*_std` columns from `appliances`.

### Added in v2
- `spc_manual_baselines` table.
- `get_spc_baselines(appliance_id)` — fetches from new table.
- `save_spc_baselines(appliance_id, baselines)` — upserts UCL/LCL/Mean.
- `check_spc_alerts(appliance_id, reading_data, dev_type)` — real-time SPC breach checker on every running telemetry insert.
- `notify_node_baseline_set(appliance_id)` — publishes `baseline:set` to control topic.
- `SPC_ALERT_COOLDOWN` — rate-limit dict to prevent alert spam (5-minute cooldown per metric **per direction**). Key is `(appliance_id, metric_name, alert_type)` so UCL and LCL breaches are independently throttled.
- `api_baseline_config` endpoint (GET/POST).
- `baseline_configured` column on `appliances`.

### Alert System Expansion
In addition to the existing `dryer_humidity_high` end-of-cycle alert:
- `spc_ucl_breach` — reading exceeded UCL.
- `spc_lcl_breach` — reading fell below LCL.

These are inserted in `on_mqtt_message()` immediately after a running reading is inserted.

### Fault Alert System (NEW)
Pattern-based fault detection gated behind `baseline_configured = TRUE`. All fault alerts use a **10-minute cooldown** per fault type.

**Dryer Faults:**
| Fault | Trigger | Severity |
|-------|---------|----------|
| `fault_dryer_roller_wear` | Per-cycle motor baseline median > UCL at cycle end (fires immediately) | Warning |
| `fault_dryer_belt_snapped` | Per-cycle motor baseline median < LCL at cycle end **OR** cycle aborts via >60s gap with `min_current < LCL` | Critical |
| `fault_dryer_lint_blockage` | End-of-cycle RH > UCL AND max exhaust temp > UCL | Critical |
| `fault_dryer_incomplete_drying` | End-of-cycle RH > UCL | Info |

**HVAC Faults:**
| Fault | Trigger | Severity |
|-------|---------|----------|
| `fault_hvac_dirty_filter` | Min coil temp < LCL during STABLE_ON at cycle end (fires immediately) | Warning |
| `fault_hvac_low_refrigerant` | Min coil temp > UCL (primary) OR peak ΔT < ((UCL + mean) / 2) (confirmatory) during STABLE_ON at cycle end | Critical |
| `fault_hvac_compressor_fault` | Avg current > UCL during STABLE_ON at cycle end | Critical |

**Motor Baseline Extraction (Dryer):**
- Prominence threshold: `max(0.35A, baseline_mean × 0.20)` (lowered from 0.50A).
- Hard threshold guard: readings > `mean × 1.15` are excluded.
- Per-cycle median of non-spike, non-excluded readings = motor baseline.
- Refactored into `_compute_motor_baseline_median(motor_readings, filter_threshold=None)` helper to eliminate 4x duplicated code in `_finalize_dryer_cycle()` and `dryer_analytics()`.

**Auto-Derived UCL/LCL (Dryer Current):**
- If user enters only `mean` for dryer `current`, system auto-derives:
  - UCL = `mean × 1.20`
  - LCL = `mean × 0.80`
- User can still override by entering explicit UCL/LCL values.

### Data Flow
1. Node publishes running telemetry.
2. Backend inserts into `hvac_readings` / `dryer_readings`.
3. Backend calls `check_spc_alerts()` against `spc_manual_baselines`.
4. If breached and rate-limit allows, inserts into `alerts`.
5. If fault conditions are met, `check_fault_alerts()` evaluates cycle-aggregated patterns and inserts fault alerts.
6. If a Discord webhook URL is configured for the user, `send_discord_alert()` fires a rich embed (fire-and-forget).

### Discord Webhook Alerts (NEW)
Each user can configure a personal Discord webhook URL. When a **fault alert** fires, a rich color-coded embed is sent to that user's Discord channel instantly.

**Alert types that trigger Discord (7 fault types only):**
- `fault_dryer_incomplete_drying`
- `fault_dryer_roller_wear`
- `fault_dryer_belt_snapped`
- `fault_dryer_lint_blockage`
- `fault_hvac_dirty_filter`
- `fault_hvac_low_refrigerant`
- `fault_hvac_compressor_fault`

**Alert types that do NOT trigger Discord (DB/dashboard only):**
- SPC breaches (`spc_ucl_breach`, `spc_lcl_breach`) — raw data point spam, not actionable
- Legacy humidity alert (`dryer_humidity_high`) — superseded by `fault_dryer_incomplete_drying`

**Embed format — Maintenance-ticket style:**
Each Discord embed includes a severity icon + human-readable title, fault description, root cause, and recommended action. Example:
```
🔴 Belt Snapped
Belt snapped — motor baseline 1.20A below LCL 1.60A
━━━━━━━━━━━━━━━━━━━━
📍 Appliance: Dryer Test
🔍 Cause: Age, overloading, or misalignment.
🔧 Recommended Action: Replace drive belt immediately.
```

**Embed colors by severity:**
| Severity | Color | Hex | Alert Types |
|----------|-------|-----|-------------|
| Critical | 🔴 Red | `#EF4444` | Belt snapped, lint blockage, low refrigerant, compressor fault |
| Warning | 🟠 Orange | `#F59E0B` | Roller wear, dirty filter |
| Info | 🔵 Blue | `#3B82F6` | Incomplete drying |

**Implementation:** `send_discord_alert()` is fire-and-forget. It fetches the user's webhook URL via `get_user_webhook(appliance_id)`, builds the embed, and POSTs via `requests`. Discord failures are logged but never block the DB alert insert.

### API Endpoints

| Method | Route | Purpose |
|--------|-------|---------|
| `GET` | `/dashboard` | Main UI |
| `POST` | `/devices/pair` | Pair node → appliance |
| `POST` | `/devices/<id>/forget` | Unpair & delete appliance |
| `GET` | `/api/unpaired_nodes` | List unpaired nodes seen in last 30s |
| `GET` | `/api/node/<id>/latest` | Live unpaired node readings |
| `GET` | `/api/device/<id>/latest` | Latest reading + `is_offline`/`has_data`/`baseline_configured` |
| `GET` | `/api/device/<id>/latest_n` | Last N readings for charts (supports `?start=&end=` history mode) |
| `GET` | `/api/device/<id>/table_data` | Last 120 readings for tabular view |
| `GET` | `/api/device/<id>/maintenance_logs` | Distinct maintenance timestamps |
| `GET` | `/api/device/<id>/export_excel` | Excel export (optional `?start_date=&end_date=`) |
| `GET` | `/api/device/<id>/calibration_progress` | HVAC ice-bath progress (% drop, current tcoil) |
| `GET` | `/api/device/<id>/spc_limits` | SPC UCL/Mean/LCL (reads `spc_manual_baselines`) |
| `GET` | `/api/device/<id>/baseline_analysis` | Baseline metadata + per-metric stats + `baseline_set_at` |
| `GET/POST` | `/api/device/<id>/baseline_config` | Get/save manual SPC baselines |
| `GET/POST` | `/api/device/<id>/thresholds` | Get/set `alert_rhexhaust_threshold` and `alert_enabled` |
| `GET` | `/api/device/<id>/alerts` | List alerts (resolved/unresolved) — read-only |
| `GET` | `/api/device/<id>/hvac_analytics` | Daily averages + daily energy kWh (last 30 days, compressor running). Optional `?start=&end=` time-range filter. |
| `GET` | `/api/device/<id>/dryer_analytics` | Cycle detection + ignition peak stats + per-cycle energy kWh. Optional `?start=&end=` time-range filter. |
| `GET` | `/api/user/discord_webhook` | Fetch current Discord webhook URL (masked) |
| `POST` | `/api/user/discord_webhook` | Save/update Discord webhook URL |
| `POST` | `/api/user/discord_webhook/test` | Send test embed to verify webhook |

---

## 5. Frontend (`dashboard.html`)

### Chart Layout (4 Charts for Both HVAC and Dryer)

| Chart # | HVAC | Dryer |
|---------|------|-------|
| **1** | Delta-T (Return − Supply) | Exhaust Temperature |
| **2** | Coil Temperature (Evaporator) | Exhaust Humidity |
| **3** | Delta-RH (Return − Supply) | Exhaust Pressure |
| **4** | Compressor Current | Motor Current (Gas Ignition Spikes) |

Each chart has 4 datasets: value line + UCL dashed + Mean dashed + LCL dashed.

### Inline Baseline Config UI Flow

1. **Buttons** (bottom of detail modal):
   - `btn-configure-baseline` — shown if `!currentDeviceHasBaseline`
   - `btn-edit-baseline` — shown if `currentDeviceHasBaseline`
   - `btn-save-baseline` / `btn-cancel-baseline` — hidden until config mode opens

2. **Open config:** `showBaselineConfigPanel()` fetches `/baseline_config` and injects an input row **directly under each chart** (`baseline-inputs-chart-1 … 4`):
   - UCL number input
   - LCL number input
   - Live-computed Mean display `(UCL + LCL) / 2`

3. **Validation:** `saveBaselineConfig()` verifies no empty fields, POSTs `{metrics: {deltat: {ucl, lcl}, ...}}`.

4. **Backend:** `api_baseline_config` validates `ucl > lcl`, upserts into `spc_manual_baselines`, sets `baseline_configured = TRUE`, and sends MQTT `baseline:set` to the node.

5. **Close:** `hideBaselineInputs()` collapses all input rows and restores the Configure/Edit button. On success, `checkBaselineResults()` is called to refresh the timestamp.

### Key Frontend Functions

| Function | What it does |
|----------|--------------|
| `openDeviceDetail(el)` | Resets stale UI (table, charts, baseline inputs); fetches baseline status; shows/hides grid rows based on `isHVAC` (including Delta-RH row); calls `buildActionBar`, `buildStatusPolling`, `initCharts`, `updateDataTable`, etc. |
| `updateDetailData()` | Polls `/api/device/{id}/latest`. Updates reading grid values, run/idle badge, offline badge. If `chartMode === 'live'`, calls `pushToCharts(...)`. |
| `initCharts(id, isHVAC)` | Destroys old charts; creates 4 Chart.js instances with per-chart `timeLabels`; fetches SPC limits **immediately** + history; calls `applySPCLines()` when limits arrive. |
| `pushToCharts(time, v1, v2, v3, v4, doUpdate, isHVAC)` | Deduplicates by `timeMs`; pushes to each chart's `data.labels`/`timeLabels`; trims to `MAX_CHART_POINTS = 1080` in live mode. |
| `applySPCLines()` | Mutates SPC datasets **in-place** (`push`/`pop`/`assign`) to match data label count — avoids Chart.js metadata invalidation. |
| `saveBaselineConfig()` | Collects UCL/LCL inputs, validates, POSTs to `/baseline_config`. On success: hides inputs, calls `checkBaselineResults()`, fetches fresh `/spc_limits`, redraws lines. |
| `formatDateTimeInput(el)` | Auto-formats digits-only input to `DD-MM-YYYY HH:MM:SS` as the user types. Used on all 4 date/time inputs. |
| `normalizeDateTimeInput(raw)` | Parses `DD-MM-YYYY HH:MM:SS` (or 2-digit year / ISO-like variants) into `YYYY-MM-DDTHH:MM:SS` for backend queries. |
| `checkBaselineResults()` | Fetches `/baseline_analysis`; toggles Configure/Edit buttons; shows `baseline-timestamp` from `MAX(updated_at)`. |
| `buildStatusPolling(id, type)` | Polls `/spc_limits` every 4s. **Fires immediately on card click** (not after delay). Updates card badge, modal action bar, and SPC lines. |
| `updateDataTable()` | Fetches `/hvac_analytics` or `/dryer_analytics`; rebuilds `<thead>` and `<tbody>`. Only populates when `status === 'normal'`. Includes DOM cleanup for stale header divs when switching between HVAC and dryer. |

### Readings Grid Visibility

`openDeviceDetail()` hides/shows grid rows based on device type:
- `row-tcoil` — hidden for dryers
- `lbl-t2` parent (Supply Temp) — hidden for dryers
- `lbl-h2` parent (RH Supply) — hidden for dryers
- `detail-deltarh` parent (Delta RH) — **hidden for dryers**
- `lbl-delta` label changes: HVAC = "Delta-T (Ret-Sup)", Dryer = "Pressure (hPa)"

### Removed UI Components
- "Start Baseline Recording" / "Cancel Baseline" buttons and timer UI.
- SPC Summary panel (intermediate table between config and charts).
- `btn-rebaseline` — replaced by bottom Configure/Edit flow.
- HVAC threshold inputs — replaced with an informational message that HVAC uses SPC baseline limits.

### Added UI Components
- `.btn-secondary` CSS rule for Cancel and Test buttons.
- HVAC threshold panel now displays: *"HVAC alerts use the SPC baseline UCL/LCL limits configured under each chart."*

---

## 6. Database Schema

### `appliances` (simplified)
```sql
id | user_id | name | type | brand | location | created_at
| operational_status | sub_type
| treturn_slope | treturn_intercept | ... (calibration columns same as v1)
| icompressor_offset
| alert_enabled | baseline_configured
```

### `spc_manual_baselines` (NEW)
```sql
id | appliance_id | metric_name | ucl | lcl | mean | created_at | updated_at
```

**Metrics:**
- HVAC: `deltat`, `deltarh`, `tcoil`, `rhreturn`, `rhsupply`, `current`
- Dryer: `texhaust`, `rhexhaust`, `pressure`, `current`

### `alerts`
```sql
id | appliance_id | alert_type | message | value | threshold
| created_at | resolved_at | acknowledged | cycle_start_time | cycle_end_time
```
- Populated by `check_spc_alerts()` (SPC breaches), `_insert_fault_alert()` (fault alerts), and `_process_cycle_end()` (dryer humidity alerts).
- `api_alerts` is **read-only** in v2 — no acknowledge/resolve endpoint.

### `users`
```sql
id | email | password_hash | name | created_at | discord_webhook_url
```
- `discord_webhook_url` stores the user's personal Discord webhook (optional, `TEXT` column).

---

## 7. Common Pitfalls for Agents

1. **Do not reference v1 auto-baseline code.** All timer-based baseline logic is gone.
2. **UCL must be > LCL.** The backend validates this and rejects otherwise.
3. **Mean is computed, not stored by user.** The DB stores UCL, LCL, and computed Mean.
4. **Delta RH is HVAC-only.** The Delta-RH row in the readings grid is hidden for dryers via `detail-deltarh` parent display toggle.
5. **Alert rate limiting:** `SPC_ALERT_COOLDOWN` prevents one alert per metric **per direction** per 5 minutes. UCL and LCL breaches are independently throttled. Do not remove this unless explicitly asked.
6. **`baseline:set` is the only baseline-related MQTT command.** Do not send `baselinestartack`, `baselinesuccessack`, etc.
7. **The `deltarh` metric uses `abs(h1c - h2c)`** (absolute difference), same as `deltat`.
8. **SPC lines are drawn in-place** (`applySPCLines()` uses push/pop/assign). Do not replace entire arrays — it causes Chart.js metadata invalidation.
9. **Each chart owns its own `timeLabels` array.** Never use a global `chartTimeLabels` — it causes cross-chart timestamp contamination.
10. **`buildStatusPolling()` fires immediately** — the first poll runs on card click, not after 4 seconds.
11. **`initCharts()` fetches `/spc_limits` immediately** — SPC lines appear as soon as limits arrive, not after polling delay.
12. **Excel export fix:** Dryer exports show `Type: {dev_type}` (no `sub_type` leak). HVAC shows `Type: {dev_type} ({sub_type})`.
13. **`import numpy as np` is now present** in v2 `app.py`. Calibration works correctly.
14. **Discord webhook is per-user, not per-appliance.** The URL is stored on the `users` table. All alerts for all appliances owned by that user go to the same Discord channel.
15. **`requests` library is required** for Discord webhook POSTs. Listed in `requirements.txt`.

---

## 8. Changelog

### 2026-05-10 — Calendar Date Picker + Auto-Format Time Input (v2 Frontend)
- **Change:** Replaced free-text date/time inputs with 3-part picker: calendar `type="date"`, auto-format time, AM/PM dropdown.
- **Time auto-format:** Typing digits auto-inserts colons (`092534` → `09:25:34`).
- **AM/PM conversion:** Frontend converts 12h + AM/PM to 24h ISO before backend send.
- **Fields:** History Range and Export Modal start/end (4 fields).

### 2026-05-10 — DHT NaN Corruption Fix + Infinite False Telemetry Fix (v2 Firmware)
- **DHT NaN Problem:** DHT22 intermittently returns NaN (~40% failure rate observed for DHT2). Old code mapped NaN → 0 before adding to the running sum, then divided by `MAX_SAMPLES` (5) regardless of validity. When 2 of 5 samples were NaN, the average was corrupted (e.g., 14.7°C → 8.8°C).
- **DHT Fix:** Added per-metric valid counters decoupled from `sampleCount` timing. Averages are `sum / validCount`. If `validCount == 0`, metric falls back to last-known-good value (previous window's average). Skips bad samples without corrupting averages or changing publish cadence.
- **Infinite Telemetry Problem:** When compressor turned off and `currentVal == 0.0` for a full window, `validCurrentA` became 0. The `lastGoodCurrentA` fallback (stale 3.1A) was used for the average. Since `lastGoodCurrentA` was never updated when `validCurrentA == 0`, this produced false running telemetry **every 10 seconds indefinitely** while LED showed idle.
- **Telemetry Fix:** Removed `lastGoodCurrentA` fallback for current. If all 5 samples are 0.0, average is **0.0**. Moved `lastAvgCurrent = currentVal` outside the `if` block so it updates unconditionally.

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

### 2026-05-07 — Live vs Historical Ignition Count Unification
- **Problem:** Live auto-update and historical analytics showed **different ignition spike counts** for the same dryer cycle. Live under-counted spikes (e.g., 3 vs 4), and historical over-counted borderline bumps in some cases.
- **Root cause:** Two completely different spike detection algorithms existed:
  - **Historical (`dryer_analytics()`):** Entry on any `current > prev` rise; confirms pending peak when next rise starts while in FALLING state; confirms at cycle end.
  - **Live (`_check_dryer_faults()`):** Entry only when `current - prev > prominence`; no FALLING→RISING transition handler; confirmation gated by broken `current <= mean + 0.15` check. When motor baseline (~3.05 A) never dropped below `mean + 0.15` (e.g., 2.65 A), the state machine got stuck in FALLING and lost all subsequent spikes.
- **Fix:**
  - **Unified state machine:** Live now uses the exact same 3-state logic as historical — entry on any `current > prev`, confirm previous peak before starting new rise when in FALLING, fall on `current < peak_max - 0.1`.
  - **Removed broken confirmation gate:** Replaced `current <= mean + 0.15` with cycle-end confirmation in `_finalize_dryer_cycle()` (same `_confirm_peak()` logic as historical).
  - **First-reading skip:** Live cycle start now sets `prev_current = current` and skips spike processing for the first reading, matching historical behavior.
  - **Motor readings collection:** Live now collects **all** cycle readings into `motor_readings` and applies the same `filter_threshold = average × 1.15` at cycle end, matching historical.
- **Result:** Live and historical ignition counts are now **identical** for all mean values (verified against exported `Gas_Test_Lab_20260508_111231_10907e.xlsx`).

### 2026-05-07 — Dryer Ignition Prominence Threshold Lowered to 0.4A
- **Change:** Prominence threshold changed from dynamic `max(0.35, mean_current * 0.20)` to fixed **0.4A** in both live (`_check_dryer_faults`) and historical (`dryer_analytics`) algorithms.
- **Why:** User visually identified 5 spikes in the current chart but the algorithm only counted 3–4. The dynamic formula produced thresholds of 0.50–0.56A (with mean 2.5–2.8A), filtering out the 3.45A bump (prominence 0.39A) and sometimes the 3.62A bump (prominence 0.55A).
- **Effect with 0.4A:** Cycle 2 now counts **4 spikes** (4.26A, 3.87A, 3.62A, 4.30A). The 3.45A bump (0.39A prominence) remains just below threshold.
- **Risk:** Very low — motor baseline fluctuation is only ±0.03A, so 0.4A is >10× above noise floor.

### 2026-05-07 — Live vs History Range Analytics Cache Fix
- **Problem:** After backend restart, Live Auto-Update showed **3 ignitions** while History Range showed **4 ignitions** for the same cycle. Both should use the same `dryer_analytics()` backend code.
- **Root cause (frontend):** Two bugs in `dashboard.html`:
  1. **Browser caching:** `fetch(endpoint)` had no cache-busting. The old `dryer_analytics` response (pre-restart) was cached for the URL without query params. History Range used a different URL (`?start=...&end=...`), bypassing the cache.
  2. **Stale table on mode switch:** `onChartModeChange()` called `initCharts()` when switching to Live mode but **never called `updateDataTable()`**. The analytics table stayed on whatever was last rendered (History Range data).
- **Fix:**
  - Added cache-busting timestamp: `fetch(endpoint + (endpoint.includes('?') ? '&' : '?') + '_t=' + Date.now())`
  - Added `updateDataTable()` call in `onChartModeChange()` when switching to Live mode.

### 2026-05-07 — Cycle-End _confirm_peak() Ordering Bug Fix
- **Problem:** After all previous fixes, Live Auto-Update still showed **3 ignitions** while History Range showed **4 ignitions** for the same cycle 2. Debug logs showed the 4.299A spike was present in `end_of_data` path but missing in `gap` path.
- **Root cause:** In `dryer_analytics()`, the 3 cycle-finalization paths had inconsistent `_confirm_peak()` ordering:
  - **Gap path** (used by live full query): `_confirm_peak()` was called **after** `ignition_count` and `current_spike_avg` were computed. The pending spike was confirmed too late — added to `_peak_values` after the count was already saved to the cycle object.
  - **Current-drop path** (same bug): `_confirm_peak()` also called after cycle stats computation.
  - **End-of-data path** (used by history range): `_confirm_peak()` called **before** cycle stats — correct.
- **Fix:** Moved `_confirm_peak()` to **before** `current_spike_avg` and `ignition_count` in both gap and current-drop paths. All 3 paths now confirm pending spikes before computing stats.
- **Result:** Live and history range counts are now guaranteed identical regardless of how a cycle ends (gap, current drop, or end of data).

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

### 2026-05-07 — Live Chart Buffered Data Refresh
- **Problem:** When offline-buffered data arrived while the dashboard stayed open, the live chart showed gaps instead of the buffered points that should fill the gap. After a manual page refresh, the chart rendered correctly.
- **Root cause:** `initCharts()` was called immediately on reconnect, before the backend had finished inserting all buffered MQTT messages. Buffered points that arrived after `initCharts()` completed were dropped by `pushToCharts()` because they were >5 seconds older than `latestChartTimeMs`.
- **Fix:**
  - On reconnect, `initCharts()` now runs after a **1.5-second delay** (`setTimeout`) to let the backend finish inserting buffered rows.
  - Added gap/backward detection in `updateDetailData()`: if a live point is >10 seconds older than `latestChartTimeMs` (late-arriving buffered data) or the gap since the last point is >30 seconds (offline period), `initCharts()` is triggered to reload history.
  - Widened `pushToCharts()` backward guard from 5 seconds → 30 seconds as secondary safety net.

### 2026-05-06 — Idle Badge Delay Fix, Empty Charts Race Condition Fix
- **Idle Badge Delay:** `updateDetailData()` was called before the modal overlay was active, causing its early-exit guard to fire silently. The badge only appeared when the global 5-second interval fired. Fixed by activating the modal **before** calling `updateDetailData()` in `openDeviceDetail()`.
- **Empty Charts Race Condition:** After the badge fix, `updateDetailData()` ran concurrently with `initCharts()`'s async history fetch. Live data pushed to charts set `latestChartTimeMs` to the newest timestamp, causing all subsequent history data points to be skipped (they were older). Added `historyLoading` flag: set `true` when `initCharts()` starts, cleared when history fetch completes. `updateDetailData()` only pushes to charts when `!historyLoading`.

### 2026-05-06 — Energy kWh Integration, HVAC Analytics Daily Report, Date Input Auto-Format, HVAC Fault Alert Refinement
- **Energy kWh:** Replaced raw current sum with proper kWh calculation (`energy_ws = Σ(current × voltage × dt) / 3,600,000`). Added `appliances.voltage` column (default 220V). Added `get_appliance_voltage()` helper.
- **Dryer Analytics:** Per-cycle `energy_kwh` computed using actual time deltas between readings. Table column renamed from `Consumption (A)` to `Energy (kWh)`.
- **HVAC Analytics:** Added daily averages with integrated daily energy consumption. Backend detects cycles per day and sums energy across all cycles within that day. Returns `{"daily_averages": [...]}` only — per-cycle table removed from frontend.
- **Date/Time Inputs:** Changed 4 inputs from `datetime-local` → `text` with `formatDateTimeInput()` (digits-only typing, auto-inserts `-`, `:`, and spaces) and `normalizeDateTimeInput()` (parses `DD-MM-YYYY HH:MM:SS` → ISO).

### 2026-05-07 — Offline/Online Status Fix, Debug Print Cleanup, BME280 Threshold Finalization
- **Backend:** Added `ever_connected` flag to `api_device_latest` response. Frontend now distinguishes between a device that was **never connected** (yellow "Awaiting Sensor Data...") and a device that **went offline after previously connecting** (red "Device Offline").
- **Backend:** Increased `offline_threshold_seconds` from **600s → 660s** (11 minutes). Checkin interval is 600s (10 min); the extra 60s prevents idle devices from flickering offline between checkins.
- **Backend:** Removed 7 temporary `[DRYER_ANALYTICS]` debug print statements from `dryer_analytics()`.
- **Firmware (BME280):** Raised stuck-value and out-of-range thresholds from 5 → **15 consecutive readings** before triggering soft reset. Prevents premature resets during normal transient conditions.
- **Firmware (BME280):** Stuck-value detection now only active when running (`lastAvgCurrent >= 0.4`). Counter resets when idle to avoid false positives in stable exhaust duct conditions.

### 2026-05-07 — History Range Input Copy-Paste Friendly
- **Problem:** Analytics tables display dates as `toLocaleString()` (e.g., `"5/7/2026, 4:26:35 PM"`), but history/export inputs forced `DD-MM-YYYY HH:MM:SS` via a digit-only formatter. Users could not copy-paste dates from the analytics table into the history range fields.
- **Fix:**
  - Removed `oninput="formatDateTimeInput(this)"` from history and export inputs — free-text paste is now allowed.
  - Updated placeholders to `"M/D/YYYY, H:MM:SS AM/PM"`.
  - Extended `normalizeDateTimeInput()` to parse `toLocaleString()` format (`M/D/YYYY, H:MM:SS AM/PM` → ISO) with correct AM/PM conversion.
  - Updated `formatLocalForInput()` to use `toLocaleString('en-US', { hour12: true })` so default export dates also match the format.
  - Backward-compatible: existing `DD-MM-YYYY HH:MM:SS`, `DD-MM-YY HH:MM:SS`, and `YYYY-MM-DD HH:MM:SS` entries still work.
- **HVAC Fault Alerts:** Changed from cycle-end evaluation to **3 consecutive readings** in `STABLE_ON` state. Reduced `STABLE_ON` gate from 10 min → **7 min** (`elapsed >= 420`). Added `HVAC_FAULT_COUNTERS` dict with per-reading `_evaluate_hvac_reading()`.
- **Time-Range Query Fix:** Padded `end` parameter by +1 second in `hvac_analytics()` and `dryer_analytics()` to include milliseconds. Fixes issue where a cycle end time copied from the UI (seconds precision) would exclude the actual DB reading (microsecond precision).
- **Frontend DOM Cleanup:** Removed stale `<div>` insertion before `<tbody>` that caused "Daily Averages" ghost header to leak into dryer view. Fixed empty-state checks for HVAC object response.

### 2026-05-05 — HVAC Calibration Progress Fix, BME280 Hardening, Motor Current Fix, Ignition Count Fix
- **Firmware (Calibration):** Fixed HVAC calibration progress dashboard sync. During calibration (`CALIBBASELINEWAIT` / `CALIBRUNNING`), normal sampling was skipped so no telemetry was published. Backend had no live data and used stale DB readings, causing frozen progress and wrong `start_tcoil`.
  - **Fix:** Firmware publishes `calibration_progress` events every ~2.2 s containing `t3`, `base_t3`, and `delta`.
  - **Backend:** New event handler updates `CALIBRATION_TRACKER` with live values. `api_calibration_progress` reads from tracker first.
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
- **Faults Going to Discord (7 types):** `fault_dryer_incomplete_drying`, `fault_dryer_roller_wear`, `fault_dryer_belt_snapped`, `fault_dryer_lint_blockage`, `fault_hvac_dirty_filter`, `fault_hvac_low_refrigerant`, `fault_hvac_compressor_fault`.

### 2026-05-04 — Bug Fixes & Security Hardening
- **Security:** Removed hardcoded credential defaults from `app.py`. Added `python-dotenv` loading. App raises `RuntimeError` on startup if `FLASK_SECRET_KEY`, `MQTT_PASS`, or `DB_PASSWORD` is missing. Local `.env` file (gitignored) is now required.
- **Backend:** Direction-aware SPC cooldown — key changed to `(appliance_id, metric_name, alert_type)` so UCL and LCL breaches are independently rate-limited.
- **Backend:** `forget_device()` now cleans up all in-memory trackers (`DRYER_CYCLE_STATS`, `HVAC_CYCLE_TRACKER`, `FAULT_ALERT_TRACKER`, `FAULT_ALERT_COOLDOWN`, `SPC_ALERT_COOLDOWN`, `CYCLE_TRACKER`, `CALIBRATION_TRACKER`) to prevent memory leaks.
- **Backend:** Extracted `_compute_motor_baseline_median()` helper to eliminate duplicated median logic across `_finalize_dryer_cycle()` and `dryer_analytics()`.
- **Backend:** Belt snap gap-detection — if a cycle aborts via >60s gap and `min_current < LCL` (or `belt_snap_start` was recorded), `fault_dryer_belt_snapped` is fired immediately.
- **Frontend:** Added missing `.btn-secondary` CSS rule.
- **Frontend:** Removed broken HVAC threshold inputs (which referenced non-existent `.std` properties). HVAC threshold panel now shows an informational message.

### 2026-05-03 — Fault Alert System Implementation
- **Backend:** Added `check_fault_alerts()`, `_check_dryer_faults()`, `_check_hvac_faults()` with per-cycle median tracking.
- **Backend:** Lowered dryer spike prominence threshold from 0.50A to 0.35A based on data validation.
- **Backend:** Added hard threshold guard (`mean × 1.15`) for motor baseline extraction.
- **Backend:** Auto-derived UCL/LCL for dryer current when user provides only mean.
- **Backend:** `dryer_analytics()` now computes per-cycle `motor_baseline_median`.
- **Frontend:** Alerts panel renders fault alerts with severity-based colors (red=critical, orange=warning, blue=info).
- **Frontend:** Dryer analytics table shows "Motor Current" column (median of non-spike readings).

### 2026-05-03 — Discord Webhook Alert Integration
- **Backend:** Added `send_discord_alert()`, `get_user_webhook()`, `get_appliance_name()` helpers.
- **Backend:** Hooked Discord alerts into `check_spc_alerts()`, `_insert_fault_alert()`, and `_process_cycle_end()`.
- **Backend:** Added `GET/POST /api/user/discord_webhook` and `POST /api/user/discord_webhook/test` endpoints.
- **Frontend:** Added "🔔 Discord Alerts" sidebar nav item + settings modal with URL input, Save, and Test buttons.
- **Deps:** Added `requests>=2.28.0` to `requirements.txt`.
- **DB:** Added `discord_webhook_url TEXT` column to `users` table.

### 2026-05-01 — v2 UI Polish & Delay Fixes
- **Frontend:** Fixed Delta-RH row leaking onto dryer device cards (added `detail-deltarh` parent hide toggle in `openDeviceDetail()`).
- **Frontend:** Baseline timestamp now refreshes immediately after Save Baseline (`checkBaselineResults()` called in `saveBaselineConfig()` success handler).
- **Frontend:** SPC lines now render immediately on card click. `initCharts()` fetches `/spc_limits` right after chart creation and calls `applySPCLines()` when limits arrive.
- **Frontend:** `buildStatusPolling()` now fires its first poll immediately (extracted `doPoll()` function) instead of waiting 4 seconds for the first `setInterval` tick.

### 2026-05-01 — Inline Baseline Config & 4-Chart Layout
- **Frontend:** Inline UCL/LCL input rows appear directly under each of the 4 charts with live mean calculation.
- **Frontend:** Bottom button flow: Configure Baseline → Edit Baseline → Save/Cancel. Removed from action bar.
- **Frontend:** HVAC now has 4 charts (Delta-T, Coil Temp, Delta-RH, Current) matching dryer's 4-chart layout.
- **Frontend:** Chart title updated: "T Return minus Supply Temperature" → "Delta T (Return − Supply)".
- **Frontend:** SPC Summary panel removed; save immediately hides inputs and shows Edit Baseline button.
- **Frontend:** Stale table fix — `openDeviceDetail()` clears `#table-headers`, `#table-body`, `#table-title` before rendering.
- **Frontend:** Per-chart `timeLabels` arrays prevent cross-chart timestamp contamination.
- **Frontend:** SPC line in-place mutation avoids Chart.js metadata invalidation.

### 2026-04-30 — Backend Hardening
- **Backend:** `api_baseline_analysis()` returns `baseline_set_at` from `MAX(updated_at)`; uses `"texhaust"` / `"rhexhaust"` keys for dryer (not `"temp"` / `"humidity"`).
- **Backend:** Dryer cycle detection: gap threshold 600s → 60s, `cycle_start` fixed at 0.4A, noise filter 3.0 min → 1.0 min.
- **Backend:** Excel export fix — dryer no longer leaks `sub_type` into sheet header.
- **Backend:** `dryer_readings.time` migrated to `TIMESTAMPTZ`; backend uses `datetime.now(timezone.utc)` for timestamp correction.
- **Backend:** Future-timestamp guard clamps readings to `now + 1 min`.

### v2 Initial Release — Manual SPC Baseline System
- **Backend:** `spc_manual_baselines` table replaces automatic baseline recording.
- **Backend:** Real-time `check_spc_alerts()` with 5-minute rate limiting.
- **Backend:** Removed all baseline timer logic (`BASELINE_TIMER_TRACKER`, `BASELINE_DRYER_TRACKER`, etc.).
- **Firmware:** Responds to `baseline:set` with 3 beeps (replaces old baseline ACK commands).
