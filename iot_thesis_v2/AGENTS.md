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

### Key Firmware Changes
- `baselinestartack`, `baselinesuccessack`, `baselinefailack` commands are **removed**.
- **New command:** `baseline:set` → node beeps 3 short beeps to confirm baseline configuration.

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
- `SPC_ALERT_COOLDOWN` — rate-limit dict to prevent alert spam (5-minute cooldown per metric).
- `api_baseline_config` endpoint (GET/POST).
- `baseline_configured` column on `appliances`.

### Alert System Expansion
In addition to the existing `dryer_humidity_high` end-of-cycle alert:
- `spc_ucl_breach` — reading exceeded UCL.
- `spc_lcl_breach` — reading fell below LCL.

These are inserted in `on_mqtt_message()` immediately after a running reading is inserted.

### Data Flow
1. Node publishes running telemetry.
2. Backend inserts into `hvac_readings` / `dryer_readings`.
3. Backend calls `check_spc_alerts()` against `spc_manual_baselines`.
4. If breached and rate-limit allows, inserts into `alerts`.

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
| `GET` | `/api/device/<id>/hvac_analytics` | Daily averages (last 30 days, compressor running) |
| `GET` | `/api/device/<id>/dryer_analytics` | Cycle detection + ignition peak stats |

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
| `checkBaselineResults()` | Fetches `/baseline_analysis`; toggles Configure/Edit buttons; shows `baseline-timestamp` from `MAX(updated_at)`. |
| `buildStatusPolling(id, type)` | Polls `/spc_limits` every 4s. **Fires immediately on card click** (not after delay). Updates card badge, modal action bar, and SPC lines. |
| `updateDataTable()` | Fetches `/hvac_analytics` or `/dryer_analytics`; rebuilds `<thead>` and `<tbody>`. Only populates when `status === 'normal'`. |

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
- Populated by `check_spc_alerts()` (SPC breaches) and `_process_cycle_end()` (dryer humidity alerts).
- `api_alerts` is **read-only** in v2 — no acknowledge/resolve endpoint.

---

## 7. Common Pitfalls for Agents

1. **Do not reference v1 auto-baseline code.** All timer-based baseline logic is gone.
2. **UCL must be > LCL.** The backend validates this and rejects otherwise.
3. **Mean is computed, not stored by user.** The DB stores UCL, LCL, and computed Mean.
4. **Delta RH is HVAC-only.** The Delta-RH row in the readings grid is hidden for dryers via `detail-deltarh` parent display toggle.
5. **Alert rate limiting:** `SPC_ALERT_COOLDOWN` prevents one alert per metric per 5 minutes. Do not remove this unless explicitly asked.
6. **`baseline:set` is the only baseline-related MQTT command.** Do not send `baselinestartack`, `baselinesuccessack`, etc.
7. **The `deltarh` metric uses `abs(h1c - h2c)`** (absolute difference), same as `deltat`.
8. **SPC lines are drawn in-place** (`applySPCLines()` uses push/pop/assign). Do not replace entire arrays — it causes Chart.js metadata invalidation.
9. **Each chart owns its own `timeLabels` array.** Never use a global `chartTimeLabels` — it causes cross-chart timestamp contamination.
10. **`buildStatusPolling()` fires immediately** — the first poll runs on card click, not after 4 seconds.
11. **`initCharts()` fetches `/spc_limits` immediately** — SPC lines appear as soon as limits arrive, not after polling delay.
12. **Excel export fix:** Dryer exports show `Type: {dev_type}` (no `sub_type` leak). HVAC shows `Type: {dev_type} ({sub_type})`.
13. **`np.polyfit` is NOT imported** in v2 `app.py`. The calibration success handler references it but will crash at runtime if calibration is triggered. Add `import numpy as np` if calibration is needed.

---

## 8. Changelog

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
