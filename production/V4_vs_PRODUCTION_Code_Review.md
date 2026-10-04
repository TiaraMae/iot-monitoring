# v4 → Production: Code Diff, End-to-End System Walkthrough & Gap Analysis

> **STATUS (2026-09-23, FINAL):** **All gaps are now fixed** — GAP-1/2/3/6/7/13/14/15/16/17 closed in round 1, **GAP-4/5/8/9/10/11/12 closed in round 2** (this same day). GAP-11/12 were delete-only; firmware fixes from round 1 still require **re-flashing the ESP32-C3 nodes**; the GAP-10 service install must be run once on the mini-PC (Administrator). Test suites: pytest 2/2 + 6 script suites all green.

Goal: compare `iot_thesis_v4` (base) vs `production` (current), trace the full path **firmware power-on → LED/buzzer → pairing → calibration → telemetry/buffering → SPC → Discord → alert**, and list every gap worth fixing. Line numbers verified against files on disk (2026-09-23).

---

# PART A — CODE DIFFERENCES (v4 base → production)

## A1. Firmware (`Update_SensorNode_Clean.ino` 1357 lines → `Update_SensorNode_Production.ino` 1158 lines)

*This table describes the state as originally reviewed. The firmware deltas the team approved (F3, F4, F5) were **fixed 2026-09-23** — see the status marks in Part C. The firmware changes require re-flashing the nodes.*

| # | Area | v4 (base) | production | Impact |
|---|------|-----------|------------|--------|
| F1 | **HVAC temp/humidity sensors** | 2× DHT22 (pins 5,6) + 1× DS18B20 coil probe (pin 7); telemetry `DHT1Temp/DHT1Hum/DHT2Temp/DHT2Hum/DS18B20Temp` | 2× DS18B20 (pins 5,6); telemetry `DS1Temp/DS2Temp` only — **no humidity, no coil probe** | HVAC RH charting/faults (delta-RH, RH baselines) gone; humidity-based analytics impossible |
| F2 | **DHT health machinery** | DHT stuck-value detection (3 windows) + soft re-init + `lastGood*` fallback poisoning protection (~120 lines) | Removed entirely | N/A — sensors no longer exist |
| F3 | **Telemetry publish gate** | `if (calibrationAcked)` only — paired-but-uncalibrated HVAC **discards samples**, streams nothing | `if (calibrationAcked \|\| isPaired)` — streams whenever paired (comment: backend shows live calibration drop) | Production streams uncalibrated HVAC data (offsets are 0, harmless but uncalibrated temps are stored/shown) |
| F4 | **Calibration LED state** | Top priority: slow blink 2 s during `CALIBBASELINEWAIT/CALIBRUNNING` | **Removed** — LED machine is `running > WiFi-down > MQTT-down > idle` only | **GAP: no visual "calibrating" indication** (see Part C) |
| F5 | **Connection-down buzzer during calibration** | Suppressed while calibrating (`calibState == CALIBIDLE` guard) | **Not suppressed** — beeps every 10 s during calibration | Noisy/ambiguous feedback during calibration |
| F6 | **Button-2 while busy** | 700 ms deny beep when `calibrationSavePending/maintenanceRequestPending/calibState != CALIBIDLE` | Silently ignored (no feedback) | Operator gets no feedback (minor) |
| F7 | Calibration event/control names | `event_button2_calibration_request`; acks `calibrationsuccessack`/`calibrationfailack` | `event_button2_offset_calibration_request`; acks `offsetcalibrationsuccessack`/`offsetcalibrationfailack` | Renamed consistently both sides — verified matching |
| F8 | Calibration math target | Same protocol: signed 8.0 °C drop, 10 min timeout, 10 s approval window, baseline→final capture | Identical (adapted to DS1/DS2) | Principle preserved |
| F9 | Calibration success payload | `{elapsedms, deltaT, base{t1,h1,t2,h2,t3}, final{...}}` | `{elapsedms, deltaT, base{ds1,ds2}, final{ds1,ds2}}` | Matches new sensors |
| F10 | WiFi/MQTT/PWM/TLS/offline buffer | 30×500 ms STA connect, no AP fallback; 10 s WiFi / 5 s MQTT retry; RAM FIFO 200 drop-oldest; flush ≤10/loop, 120 ms gaps, stop-on-failure, `agoms` age injection | **Byte-for-byte identical logic** (topic prefix changed) | Preserved |
| F11 | Buzzer pattern table | 9 patterns (pair=1 short, unpair=1×1500 ms, deny=700 ms, maintenance=1×1000 ms, busy=900 ms, conn-down=1 short/10 s, calib timeout=2×long, success=3 short, baseline:set=3 short) | Identical | Preserved |
| F12 | LED patterns (non-calib) | running=solid, WiFi=200 ms, MQTT=500 ms, idle=50 ms/10 s | Identical | Preserved |
| F13 | Credentials | Hardcoded in .ino (real + `_Clean` sanitized copy; two variants drifted in `beepLongFailTwice` timing) | `secrets.h` gitignored + `.example` — single source, no drift | Improved |
| F14 | Running threshold | `isRunningByCurrent` ≥ 0.25 A | Identical | Preserved |
| F15 | Dead code | Duplicate unreachable `isRunningByCurrent` branch in LED machine (lines 597–599 vs 614–616); comment mentions nonexistent "baselining" state | Duplicate branch removed; comment cleaned | Fixed in production |

## A2. Backend architecture

| # | Area | v4 | production |
|---|------|----|------------|
| B1 | Structure | Monolithic `app.py` 3190 lines | 11 modules: `__init__` (factory), `config`, `db`, `mqtt`, `telemetry`, `devices`, `calibration`, `alerts`, `analytics`, `models`, `api` |
| B2 | MQTT topic namespace | `iot/nodes/<mac>/{events,telemetry,control}` | `iot/production/nodes/<mac>/{...}` (`MQTT_TOPIC_PREFIX` in config.py) |
| B3 | MQTT start | Import time, outside `__main__` | Import side-effect inside `create_app()` — note: every `create_app()` call makes a new client |
| B4 | Device types | `Air Conditioner` / `Dryer` (legacy strings) | `HVAC` / `Gas Dryer`; startup migration rewrites old rows |
| B5 | Current-sensor config | CF/deductor keyed off `sub_type` (inverter/non-inverter) | **Sensor profiles** `CURRENT_SENSOR_PROFILES = {SCT013-015: 33.0/0.111, ZHT103C: 11.0/0.033}`; `current_sensor` column chosen at pairing (required field), fixed thereafter; `sub_type` kept but unused; startup backfill `cf=33.0→SCT013-015 else ZHT103C` |
| B6 | HVAC storage/calibration model | Slopes/intercepts (`treturn_slope/intercept`, `tsupply_*`, humidity forced 1.0/0.0, coil 1.0/0.0); two-point `np.polyfit` vs coil probe; `hvac_readings` has treturn/rhreturn/tsupply/rhsupply/tcoil/icompressor | **Offsets**: `treturn_offset`, `tsupply_offset`; `ref=(base_ds1+base_ds2)/2; offset=ref−base`; offsets applied **before storing**; `hvac_readings` = treturn/tsupply/icompressor only |
| B7 | HVAC alert engine | Runtime-window matrix: non-inverter 1 h eval, inverter 10 min single eval (gated by treturn ≥ 26.5 at start), current-vs-UCL/LCL/warn matrix → `fault_hvac_low_refrigerant`, `fault_hvac_compressor_fault`, `fault_hvac_compressor_degradation`, `fault_hvac_dirty_filter`; CRITICAL escalation at treturn ≥ 26.5; immediate CRITICAL on any single reading > current UCL | **Replaced** by continuous low-delta-T timer: while current ≥ 0.25, `delta_t = abs(treturn−tsupply) < delta_t_lcl` accumulates; fires `fault_hvac_low_delta_t` (warning only) after `delta_t_delay_minutes` (default 5); resets on delta_t ≥ LCL or current < 0.25 for > 30 s; 600 s cooldown. **No current-based HVAC faults anymore** |
| B8 | Calibration backend gates | Reject if t3_delta < 7.5 or t2/t1 delta < 2.5 → polyfit | Reject if `final_ds2 >= base_ds2` (signed) or supply drop < 7.5 or return drop < 2.5 → offsets; restart-healing `_begin_session` if tracker lost while `calibrating` |
| B9 | SPC → alert plumbing | Alert engine reads `spc_manual_baselines` directly | Baseline editor **mirrors** deltat LCL + delay into `appliances.delta_t_lcl` / `delta_t_delay_minutes` (alert engine reads those); DELETE clears them |
| B10 | Dryer fault engine | 7 fault types; roller wear = median>UCL at finalize; belt snap real-time 3-consecutive<LCL | Ported nearly 1:1; **adds** real-time roller wear (3 consecutive > UCL); renamed `fault_dryer_belt_snap`→`fault_dryer_belt_snapped`; prominence 0.40 / mean+0.15 spike logic, 120 s gap/idle boundaries, sweep preserved |
| B11 | Offline detection (`/api/device/<id>/latest`) | `is_offline` = last_seen > **660 s** | **120 s** (`OFFLINE_TIMEOUT_SECONDS`, naive-local compare) — matches the 10 s telemetry cadence; v4's 660 s assumed checkin-only idle nodes |
| B12 | New endpoints | — | `GET/POST /api/device/<id>/settings` (alert_enabled, delta_t_lcl, delay, voltage, atmospheric_pressure); `GET /api/device/<id>/calibration_status`; `POST /api/device/<id>/map_position` (dead floor-plan); `POST /api/send_command` passthrough |
| B13 | Removed endpoints | — | `/api/device/<id>/sensor_config` (owner-only CF editor), `/api/device/<id>/table_data` |
| B14 | `latest_n` cap | 1080 | **1000** (frontend still requests up to 1080) |
| B15 | Discord | Per-user webhook; 9 fault templates; sync 5 s POST on MQTT thread; colors: critical red, incomplete-drying blue 0x3B82F6, other warning amber; test embed green | Per-user webhook; 6 templates; same sync POST; **all warnings amber 0xF59E0B**; test embed blue 0x2563EB; generic gray fallback for unknown types |
| B16 | Schema extras | `alerts.severity`, `alerts.acknowledged`, `spc_manual_baselines.lcl` nullable, `dryer_readings.abs_pressure`, `appliances.atmospheric_pressure` | Same + `appliances.is_inverter`, `treturn_offset`, `tsupply_offset`, `delta_t_lcl`, `delta_t_delay_minutes`, `current_sensor`, `map_*`, `on_map`; `sub_type` retained unused |
| B17 | Time model | Reading `time` = tz-aware UTC (back-corrected); `alerts.created_at`, `last_seen`, `calibration_started_at` = naive **local** `NOW()` | **Identical (inherited inconsistency)** |
| B18 | Tests | None | 7 pytest files (telemetry smoke, baseline mirror sync, delta-T alert, offline status, offset calibration ×24 checks, sensor profiles ×18, dashboard detail) |
| B19 | Frontend | dashboard.html 2753 lines; 6 HVAC charts incl. RH + coil; floor-plan UI active | dashboard.html 2510 lines; **2 HVAC charts (delta-T, current)** + wiring-guide sensor picker; floor-plan JS dead but retained |
| B20 | Deployment | Full Ubuntu + Windows suites (systemd, nginx, UFW, NSSM, SSL PS1 scripts) all present | Windows README only; **setup-ssl/nssm/firewall scripts TODO, `deployment/ubuntu/` empty**; waitress in requirements but not invoked (dev server runs) |
| B21 | Docs | README/AGENTS/LEARN/MIGRATION/RERUN | README + AGENTS (changelog through 2026-09-15) |

**Unchanged principles (verified identical):** pairing flow (backend-driven `settype`/`setcf`/`setdeductor`/`restore:*`, NVS persist, 10 s unpaired re-request), offline buffering (200 FIFO, 10/loop flush, 120 ms gaps, stop-on-failure, `agoms` back-correction), 0.25 A running threshold, 600 s per-(appliance,fault-type) cooldown, SPC storage & auto rules (dryer current mean±20%, pressure mean+UCL no LCL), dryer spike prominence 0.40 / mean+0.15, 120 s cycle boundaries, energy trapezoid over ≤120 s gaps, event dedupe 5 s, telemetry dedupe 1 s, per-user Discord webhook with masked display + test endpoint.

---

# PART B — END-TO-END WALKTHROUGH (production code path)

## Stage 0 — Power-on (`setup()`, firmware lines 869–948)

1. `Serial.begin(115200)`, 3 s hold, prints "Booting Sensor Node…".
2. **NVS restore** (`nodecfg`): `paired`, `type`, `cf`, `deductor`. If paired → `isPaired=true`; `calibrationAcked = (type=="Dryer")` (line 885) — note a previously-calibrated HVAC temporarily boots as "needs calibration" until the backend re-pushes `restore:normal` over MQTT. Restores `nodeCf`/`nodeDeductor` if valid.
3. GPIO setup: buttons INPUT_PULLUP, LED/buzzer outputs, both off → **buzzer silent, LED off** at power-on.
4. ADC: 12-bit, 11 dB attenuation on GPIO 0, `esp_adc_cal_characterize(…, 1100, …)`.
5. `setupWifi()`: WiFi OFF → 100 ms → STA → TX power 8.5 dBm → begin; up to 30 × 500 ms blocking attempts, **LED toggles fast each 500 ms** (fast blink). On failure: logs, continues — background retry every 10 s in `loop()`. **No AP fallback** — credentials are compile-time (`secrets.h`); wrong WiFi = forever-blinking node.
6. Sensors: DS18B20 ×2 `begin()` + async conversion; I2C + BME280 `begin(0x76)` with sampling config; dummy conversion + 800 ms prime, reads discarded.
7. TLS: DigiCert Global Root G2 `setCACert` (EMQX Cloud; v4 still uses ISRG Root X1 with HiveMQ), 20 s timeout. MQTT: EMQX Cloud Serverless 8883, buffer 1024, keepalive 15. Prints "System Ready."

## Stage 1 — Connectivity loop (`checkConnection()`, lines 619–699)

Priority machine each loop pass:
- **current ≥ 0.25 A → LED solid ON** (highest)
- WiFi down → LED 200 ms fast blink; retry every 10 s (full mode-reset cycle); **buzzer 1 short beep every 10 s**
- WiFi ok, MQTT down → LED 500 ms blink; connect retry every 5 s (`ESP32-<mac>` client id); on connect → subscribe `iot/production/nodes/<mac>/control`, LED ON flash, **immediately `requestBackendConfig()`**
- idle → 50 ms flash every 10 s
- Unpaired → re-send `event_request_config` every 10 s.

## Stage 2 — Pairing (backend-driven; no device UI)

1. Node publishes `{"mac","event":"event_request_config"}` to `…/events`.
2. Backend (`devices.handle_node_event`): unknown MAC → auto-register `sensor_nodes` row (status `unpaired`) → replies `settype:unpaired`. Unpaired telemetry never arrives (firmware streams only once calibrated) — the old `UNPAIRED_CACHE` preview for this was **removed 2026-09-23 (GAP-3)**; unpaired nodes are visible via the scan list (`last_seen` from checkins/events).
3. User: dashboard → Add Device → `scanNodes()` → `GET /api/unpaired_nodes` (last_seen ≤ 30 s) → picks MAC, name, type (`HVAC`/`Gas Dryer`), inverter flag, **current sensor** (SCT013-015 or ZHT103C — drives CF/deductor) → `POST /devices/pair`.
4. Backend creates appliance (HVAC → `offset_calibration_needed`; Dryer → `normal`), links node, pushes in order: `settype:hvac|dryer`, `setcf:<cf>`, `setdeductor:<d>`, `restore:offsetcalibrationneeded|normal`. (v4 only sent `settype`+`restore` at pair; CF arrived on the next config request — production fixed this ordering gap.)
5. Firmware `applyApplianceType()`: persists `paired`/`type` in NVS, resets runtime flow, Dryer ⇒ `calibrationAcked=true`, **1 short beep = paired**. `setcf:`/`setdeductor:` write NVS. `restore:normal` → `calibrationAcked=true` + immediate checkin.

## Stage 3 — Calibration (HVAC only; dryer auto-ready)

Constants both sides: signed drop **8.0 °C**, **10 min** total timeout, **10 s** approval window, baseline capture with **AC OFF**.

1. Hold the **hidden button for 5 s** → denied (1 long 700 ms beep) if unpaired or Dryer; silently ignored if busy (deliberate — see GAP-6). Else → `CALIBWAITAPPROVAL`, publish `event_button2_offset_calibration_request`, `beepRequest` (1 short). 10 s approval window.
2. Backend (status `offset_calibration_needed`): zero offsets, status→`calibrating`, open `CALIBRATION_TRACKER`, reply `startcalibration`. (If backend restarted mid-session, next request re-opens the tracker — restart healing.) Approval timeout → firmware 2 long beeps + `calibration_fail_request{reason:"approval_timeout"}`.
3. Firmware → `CALIBBASELINEWAIT`: captures baseline `{ds1, ds2}` at next valid window → `CALIBRUNNING`. Streams `calibration_progress {ds2, base_ds2, delta}` every 2 s (exempt from the 5 s event dedupe).
4. User turns AC on; supply probe must drop **≥ 8.0 °C** within 10 min → `calibration_success_request {elapsedms, deltaT, base{ds1,ds2}, final{ds1,ds2}}`, state `calib-saving`.
5. Backend gates: reject if `final_ds2 >= base_ds2` (no cooling) or supply drop < 7.5 or return drop < 2.5 → status back to `offset_calibration_needed` + `offsetcalibrationfailack` (firmware: 2 long beeps, state IDLE). Pass → `treturn_offset = ref−base_ds1`, `tsupply_offset = ref−base_ds2`, `ref=(base_ds1+base_ds2)/2`, status→`normal`, `offsetcalibrationsuccessack` → firmware **3 short beeps**.
6. Dashboard shows live drop via `GET /api/device/<id>/calibration_progress`.
7. **LED during calibration: slow 1000 ms blink (top priority) — restored 2026-09-23 (GAP-1/GAP-15)**; the connection-down buzzer is **muted during calibration (GAP-2)**. With AC on (≥0.25 A) the LED shows solid "running", which overrides the calibration blink.

## Stage 4 — Telemetry & offline buffering

- Every **2 s**: sensor sample (DS18B20 ×2 or BME280 with NaN/stuck/out-of-range soft-reset machinery) + 200 ms ADC RMS window → `amps = (mV/1000)×cf − deductor` (floored at 0). Every **5 samples (≈10 s)**: build payload `{mac, type, calstate, DS1Temp/DS2Temp or BME280*, CurrentA, status}` and publish to `…/telemetry`. Gate: `calibrationAcked` only (**v4 behavior, restored 2026-09-23 per F3 decision** — a paired-but-uncalibrated HVAC streams nothing).
- Publish fail / MQTT down → push to RAM FIFO (max 200, drop-oldest ≈ 33 min). Reconnect → flush ≤10/loop, 120 ms apart, stop-on-first-failure, each resent with `"agoms":<ageMs>`.
- Server (`telemetry.handle_telemetry`): `nan→null`, backdate by `agoms>ago_ms>ago` (clamp future >1 min), dedupe <1 s, auto-register unknown MAC, unpaired → `UNPAIRED_CACHE`; paired → `last_seen` update, **offsets applied to DS temps before storing**, `delta_t = abs(treturn−tsupply)`, store every reading (running + idle), then dryer faults or HVAC delta-T check, then global `sweep_dryer_cycles` (finalizes any dryer cycle silent >120 s).

## Stage 5 — SPC baseline setting (`analytics.save_spc_baselines`)

- User: device detail → baseline editor → per-metric UCL/LCL/mean. Rules: dryer current mean-only ⇒ auto UCL/LCL = mean×1.2 / ×0.8; dryer pressure = mean+UCL, no LCL; HVAC deltat may be LCL-only, plus **alert delay minutes** (default 5); UCL must be > LCL otherwise rejected.
- Upsert into `spc_manual_baselines`; `appliances.baseline_configured=TRUE`; deltat LCL + delay **mirrored** into `appliances.delta_t_lcl`/`delta_t_delay_minutes` (what the alert engine actually reads; DELETE clears them); node receives `baseline:set` → **3 short beeps**.
- Gauge pressure = `BME280Pres − appliances.atmospheric_pressure` (fallback: SPC `pressure.mean`); raw preserved in `abs_pressure`.

## Stage 6 — Alert generation → Discord

Gating (both engines): `baseline_configured AND alert_enabled`.

**HVAC** (`check_hvac_delta_t_alert`, called only when current ≥ 0.25 and delta_t valid):
- delta_t ≥ LCL → healthy, timer cleared. delta_t < LCL → start/continue timer. Current < 0.25 for > 30 s → timer cleared (compressor stopped).
- elapsed ≥ delay_minutes → `INSERT alerts (fault_hvac_low_delta_t, warning)` + `send_discord_alert`, then 600 s cooldown (`DELTA_T_TRACKER.last_alert_time`).

**Dryer** (`check_dryer_faults` + `_finalize_dryer_cycle` + sweep):
- Cycle = I ≥ 0.25; ends on gap >120 s, idle ≥120 s in-cycle, or sweep.
- Real-time: **belt snapped** (3 consecutive < current LCL → CRITICAL, latched per cycle); **roller wear** (3 consecutive > UCL → WARNING).
- At finalize: exhaust ventilation blockage (end-RH avg > RH UCL AND max temp > temp UCL → WARNING, **CRITICAL** if max gauge pressure > pressure UCL); incomplete drying (end-RH avg > RH UCL → WARNING, >90% → CRITICAL); belt backup (last 3 < LCL → CRITICAL); roller wear (median of motor readings, filtered ≤ mean×1.15, > UCL → WARNING).

**Discord** (`send_discord_alert`): per-user webhook from `users.discord_webhook_url` (NULL ⇒ silent skip); rich embed from `FAULT_DISCORD_MAP` (title/description/cause/action); color red 0xEF4444 critical / amber 0xF59E0B warning; **synchronous `requests.post(timeout=5)` on the paho MQTT thread** (GAP-4). Only rate limit = the 600 s cooldowns. Webhook configured via dashboard modal (masked `url[:6]+"..."+url[-12:]`), test endpoint sends blue embed.

## Stage 7 — Maintenance & daily operation

- Yellow button (2 s) → `maintenance_request` → allowed for dryer or status `normal` → `sensor_events` insert + `maintenanceack` (1 long beep); otherwise `maintenancedenied`. Logs shown in dashboard; exported in Excel.
- Dashboard polls `/api/device/<id>/latest` every 5 s → cards show running/idle, **offline if last_seen > 120 s**, max unresolved alert severity; detail overlay charts with SPC lines, alerts panel with Resolve, energy doughnut, Excel export (filtered/date-range).

---

# PART C — GAPS FOUND (ranked)

*Status marks: ✅ fixed 2026-09-23 · ⏸ open (deferred) · ℹ️ decided by design / no change needed.*

## Critical (break user-visible behavior)

- ✅ **FIXED 2026-09-23 — GAP-1 — No "calibrating" LED indication in production firmware.** v4's LED machine had calibration as top priority (2 s slow blink). Production's machine (`running > WiFi > MQTT > idle`) gave **no visual state during calibration**. Fixed: calibration branch restored as top priority, slow blink **1000 ms** (per GAP-15 decision). *Pending node reflash.*
- ✅ **FIXED 2026-09-23 — GAP-2 — Connection-down buzzer not muted during calibration.** v4 guarded the 10 s connection beep with `calibState == CALIBIDLE`; production removed the guard → beeps throughout calibration. Fixed: guard restored. *Pending node reflash.*
- ✅ **FIXED 2026-09-23 — GAP-3 — Unpaired-node preview can never show live data.** Backend kept `UNPAIRED_CACHE` and `/api/node/<id>/latest` for the Add-Device preview, but the firmware gate meant **unpaired nodes never publish telemetry** — the preview was always empty. Fixed: dead code removed (`UNPAIRED_CACHE`, `/api/node/<id>/latest`; the frontend never called it). Unpaired scan list (`/api/unpaired_nodes`) kept for pairing.
- ✅ **FIXED 2026-09-23 (round 2) — GAP-4 — Synchronous Discord POST on the MQTT thread (both versions).** `send_discord_alert` did a blocking `requests.post(timeout=5)` inside the paho callback; a slow/dead webhook stalled **all** ingestion. Fixed: alerts are enqueued on a module-level queue and delivered by a single daemon worker thread (ordered, cap 50, synchronous fallback if full). The webhook *test* endpoint stays synchronous. Verified with a mocked delivery test (embed + WIB timestamp intact, non-blocking).

## Medium (correctness / robustness)

- ✅ **FIXED 2026-09-23 (round 2) — GAP-5 — In-memory state lost on backend restart.** `DRYER_CYCLE_STATS`, `DELTA_T_TRACKER`, `FAULT_ALERT_TRACKER` are RAM-only; a restart mid-dryer-cycle discarded the cycle and its end-of-cycle faults were never evaluated. Fixed: `alerts.rehydrate_dryer_cycles()` runs at startup — re-arms cooldowns from the alerts table (24 h), replays the trailing contiguous readings (gap ≤ 120 s) through the normal engine: orphaned finished cycles are finalized and evaluated immediately; running cycles are rebuilt in memory. New test `tests/test_dryer_rehydrate.py` (6 checks, all pass). HVAC delta-T tracker restart remains benign (accepted).
- ℹ️ **NO CHANGE (verified + decided) — GAP-6 — hidden button silently ignored while busy.** v4 gave a 700 ms deny beep; production ignores silently. Verified production firmware's busy branch is a true no-op (no beep, no event published) — **kept silent deliberately** so operators aren't alarmed by a deny beep.
- ✅ **FIXED 2026-09-23 — GAP-7 — Mixed time model.** Reading `time` was tz-aware UTC while `alerts.created_at` / `last_seen` / `calibration_started_at` were naive local `NOW()`. Fixed: **all times are now WIB** — `config.TIMEZONE = Asia/Jakarta` with `now_wib()` / `to_wib()` helpers; reading times, dedupe, delta-T/dryer/calibration trackers normalized to naive WIB; Discord embeds use aware WIB ISO-8601; startup logs the timezone. **Requirement: the server host clock must be set to Jakarta time (UTC+7).**
- ✅ **FIXED 2026-09-23 (round 2) — GAP-8 — `delta_t = abs(treturn − tsupply)`** masked swapped DS18B20 probes. Fixed: signed delta-T everywhere (telemetry ingest, `/latest`, 2 dashboard fallbacks) — positive = healthy cooling; swapped probes read strongly negative and trip the existing low-delta-T alert with the negative value in the message. Correctly wired systems behave identically.
- ✅ **FIXED 2026-09-23 (round 2) — GAP-9 — `latest_n` cap 1000 vs frontend 1080.** Fixed: `config.MAX_CHART_POINTS = 1080` is the single backend source of truth; `latest_n` caps at the constant.
- ✅ **FIXED 2026-09-23 (round 2) — GAP-10 — Production deployment scripts missing.** Ported v4's Windows suite to `deployment/windows/`: NSSM service (Waitress serving `run:app`, auto-start + restart-on-failure, log rotation), both nginx configs (static alias → production path), Let's Encrypt SSL script with auto-renewal task, firewall rules, and a complete README runbook. `run.py`/`python -m app` now serve via Waitress when `FLASK_ENV=production`. **Run once on the mini-PC with Administrator rights to go live.** v4 untouched.

## Minor / cleanup / doc drift

- ✅ **FIXED 2026-09-23 (round 2, delete-only) — GAP-11 — Silent no-op / dead endpoints:** deleted `POST /api/device/<id>/thresholds` (validated-but-never-persisted RH threshold; zero frontend callers), `GET /api/device/<id>/history` (dead), `POST /api/device/<id>/map_position` + `models.update_appliance_map` (floor-plan), all floor-plan CSS/JS and the orphaned `.fp-dropdown` listener, and the `updateCards` alias. `get_recent_dryer_readings` kept (GAP-5 reuses it).
- ✅ **FIXED 2026-09-23 (round 2, delete-only) — GAP-12 — Dead files:** deleted `dashboard.html.bak`, `dashboard.html.prod-backup`, 8 root `_*.py` scratch scripts, and `tests/test_dashboard_detail.py` (broken on reset DB).
- ✅ **FIXED 2026-09-23 — GAP-13 — 12 hardcoded 0.25 literals** bypassing `config.RUNNING_CURRENT_THRESHOLD`. Fixed: all 11 backend literals (`analytics.py`, `devices.py` incl. SQL filters) now use the constant. Value unchanged — **0.25 A is the minimum working current** (team decision). Firmware keeps its own 0.25 A `isRunningByCurrent` threshold.
- ℹ️ **BY DESIGN — GAP-14 — HVAC single-reading current spike fault removed** with the v4→production alert rewrite (dryer still has real-time belt/roller checks). **Confirmed intentional by the team (2026-09-23): HVAC alerting is delta-T only** — we only want to detect when the AC is not performing well; no compressor over-current logic.
- ✅ **RESOLVED 2026-09-23 — GAP-15 — Calibration LED blink duration.** Docs said 1000 ms, v4 code used 2000 ms. **Team ruling: 1000 ms is correct.** The restored calibration LED state (GAP-1) blinks at 1000 ms. (v4 base repo left as-is — it is the frozen reference.)
- ✅ **FIXED 2026-09-23 — GAP-16 — MQTT client connected at import time / duplicate backends.** Importing `app.mqtt` used to connect immediately, so any second server process (or side script) became a ghost duplicate backend → double readings/alerts/Discord posts, only stopped later by the port bind. Fixed: connect moved to `mqtt.start_mqtt()` (called once from `create_app()`); new `app/singleinstance.py` PID lockfile (`backend.lock`) denies a second process **before** it touches MQTT/DB, with a proper Windows liveness check. Validated: acquire/release, live-second-process denial, stale-lock takeover all pass.
- ℹ️ **DECIDED — KEEP AS-IS — GAP-17 — No AP/provisioning fallback in firmware (both).** WiFi creds are compile-time; every network change = edit `secrets.h` + reflash. Accepted for this fixed deployment. **Future option (approved in principle, not scheduled):** WiFiManager-style hotspot provisioning — on failed connect the node opens its own setup AP and credentials are entered from a phone.

## Verified-OK items (checked so you don't have to)

- Firmware ↔ backend control vocabulary matches exactly (`settype:*`, `setcf:`, `setdeductor:`, `restore:offsetcalibrationneeded|normal`, `startcalibration`, `offsetcalibrationsuccessack`, `offsetcalibrationfailack`, `maintenanceack/denied`, `actiondenied:busy`, `baseline:set`).
- Offline buffering logic is byte-equivalent (200 FIFO, 10/loop, 120 ms, stop-on-failure, `agoms`); server back-correction + 1 s dedupe + future-clamp intact.
- Dryer fault engine constants identical (0.25 A, 120 s, 0.40 prominence, mean+0.15, 1.15 filter, 600 s cooldown).
- Calibration protocol constants identical (8.0 °C signed, 7.5/2.5 backend gates, 10 min, 10 s approval).
- Pair ordering fixed in production (setcf/setdeductor now sent at pair time, not on the next config request).

---

# PROPOSED NEXT STEPS

**Updated 2026-09-23 — round 1 COMPLETE.** The following was implemented and validated (pytest 2/2 + 5 script suites, lockfile behavior verified): firmware GAP-1/2/15 (v4-parity LED, buzzer mute, 1000 ms blink — **nodes need reflash**), backend GAP-3/7/13/16 (dead preview removal, WIB time model, threshold centralization, single-process guard), docs (this report + AGENTS.md changelog + `.gitignore`).

**Remaining for a future round (priority order):**
*All code gaps are fixed — the only remaining items are operational:*
1. **Re-flash the ESP32-C3 nodes** with the round-1 firmware (calibration LED 1000 ms, buzzer mute, v4 telemetry gate).
2. **Run the GAP-10 install once on the mini-PC** (Administrator): `setup-nssm-service.ps1` → service auto-start; then SSL + firewall + nginx per `deployment/windows/README.md`.
3. *(Optional, future)* GAP-17 WiFiManager hotspot provisioning, if the deployment ever needs network changes without reflash.

---

# REVIEW DECISIONS (2026-09-23) — what was changed and what was not

Decisions made after reviewing this report with the team, and their status:

| Item | Decision | Action taken |
|---|---|---|
| F3 | Telemetry shows data **only when paired and calibrated** (v4 behavior) | ✅ Firmware gate changed back to `if (calibrationAcked)` — HVAC paired-but-uncalibrated streams nothing. Dryer unaffected. |
| F4 | Do as v4 | ✅ Calibration LED state restored as top priority, slow blink **1000 ms** (see GAP-15). |
| F5 | Do as v4 | ✅ Connection-down buzzer muted again while `calibState != CALIBIDLE`. |
| F6 | Silent **and** no-op | ✅ Verified production firmware already does exactly this (empty branch, no beep, no event) — no change. |
| F7–F12, F14 | OK | No change. |
| F13 | OK | No change. |
| GAP-3 | Dead unpaired preview | ✅ Removed `UNPAIRED_CACHE` + `/api/node/<id>/latest` route (frontend never called it). Unpaired scan list kept. |
| GAP-6 | Silent no-op | No change (already correct). |
| GAP-7 | All times **WIB** | ✅ `config.TIMEZONE` (Asia/Jakarta) + `now_wib()`/`to_wib()` helpers; reading times, trackers, dedupe all naive WIB; server host must be set to Jakarta time (startup log added). |
| GAP-13 | 0.25 A is the **minimum working current** | ✅ Value unchanged; all 11 backend literals centralized to `config.RUNNING_CURRENT_THRESHOLD`. |
| GAP-14 | Delta-T-only HVAC alerting is **intentional** | No change — no compressor over-current logic, by design. |
| GAP-15 | Calibration LED blink **should be 1000 ms** | ✅ Applied in the restored LED state (v4's 2000 ms code was the bug; LEARN.md was right). |
| GAP-16 | Prevent duplicate backends | ✅ MQTT connect moved to `start_mqtt()` (import no longer connects) + PID lockfile `backend.lock` denies a second process before it touches MQTT/DB. |
| GAP-17 | Keep compile-time WiFi for now | Documented (change WiFi = edit `secrets.h` + reflash). **Future option:** WiFiManager hotspot provisioning. |

## Still open (not in this round — say the word to schedule)

*None — all 17 gaps are closed as of 2026-09-23 (round 2). Only operational actions remain: re-flash the nodes (round-1 firmware fixes) and run `deployment/windows/setup-nssm-service.ps1` once on the mini-PC (GAP-10 go-live).*
- Firmware note: the three firmware changes require re-flashing the ESP32-C3 nodes.
