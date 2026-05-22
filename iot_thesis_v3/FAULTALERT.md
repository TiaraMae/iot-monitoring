# Fault Alert System — IoT Monitoring v3

> **Document Purpose:** Define the fault detection logic, SPC rule framework, and alert generation strategy for the IoT-Based Monitoring and Alert System. All fault alerts are **gated behind manual SPC baseline configuration** — if UCL/LCL lines are not set, no fault alerts are generated.
>
> **Revision Notes (2026-05-18):** 
> - HVAC fault detection redesigned to use **last-6-reading averaged evaluation** (~1 min of data). Non-inverter evaluates at 1 hour with hourly re-evaluation. Inverter evaluates at 5 minutes when Treturn > 26.5°C. Compressor fault (current > UCL) triggers immediately on any single reading. Severity is two-tier: **Warning** (degraded performance) vs **Critical** (no cooling after 1 hour with Treturn ≥ 27°C).
> - Dryer fault detection now uses **last-6-reading averages** at cycle end. Severity is two-tier: **Warning** (elevated values) vs **Critical** (burning risk >100°C or not drying at all >90% RH).
> - All fault alerts carry a `severity` column (`warning` | `critical`).
>
> **Revision Notes (2026-05-19):**
> - **Belt snap** redesigned to **3 consecutive readings** below LCL. **Roller wear** redesigned to use **motor baseline median** (ignition spikes filtered via 1.15× mean threshold) compared against UCL. Real-time detection runs during the cycle; end-of-cycle backup uses the same median calculation.
> - **Removed** redundant `dryer_humidity_high` alert (replaced by `fault_dryer_incomplete_drying`).
> - **Removed** gap-based belt snap inference that fired on `min_current < LCL` during pause gaps.
> - `motor_readings` only contains running data (`current >= 0.25`), so idle/pause gaps cannot false-trigger consecutive counters.

---

## 1. Research Foundation

### 1.1 SPC-Based HVAC Fault Detection
**Primary Source:** Sun, B., Luh, P.B., Jia, Q.S., O’Neill, Z., Song, F. (2013). *"Building energy doctors: An SPC and Kalman filter-based method for system-level fault detection in HVAC systems."* IEEE Transactions on Automation Science and Engineering, 11(1), 215-229.

Sun et al. demonstrated that **Statistical Process Control (SPC) charts with UCL/LCL boundaries** are effective for detecting system-level HVAC faults. Their method uses X-bar control charts with centerline (mean) and ±3σ control limits, combined with sliding window analysis (5–15 minute windows) to confirm sustained deviations.

Key finding: Deviations in **temperature differential (ΔT)** and **coil temperature** are the strongest discriminators for refrigerant charge faults and airflow restrictions. Current draw deviations, when thermal parameters remain normal, indicate condenser-side faults.

**Supporting Source:** Bonvini, M., Sohn, M.D., Granderson, J., Wetter, M., Piette, M.A. (2014). *"Robust on-line fault detection diagnosis for HVAC components based on nonlinear state estimation techniques."* Applied Energy, 124, 156-166.

Bonvini et al. validated that **current draw anomalies combined with normal thermal readings** indicate condenser-side faults (dirty condenser, restricted airflow), while **thermal anomalies with normal current** indicate refrigerant-side faults (undercharge, leak).

### 1.2 Motor Current Signature Analysis (MCSA)
**Primary Source:** Kia, S.H., Henao, H., Capolino, G.A. (2009). *"Torsional vibration effects on induction machine current and torque signatures in gearbox-based electromechanical systems."* IEEE Transactions on Industrial Electronics, 56(11), 4629-4643.

**Supporting Source:** Bellini, A., Filippetti, F., Tassoni, C., Capolino, G.A. (2008). *"Advances in diagnostic techniques for induction machines."* IEEE Transactions on Industrial Electronics, 55(12), 4109-4126.

MCSA literature establishes that mechanical load changes (bearing wear, misalignment, belt issues) manifest as **sustained changes in the stator current fundamental component**. Key thresholds from industrial practice:
- **±15–25% current deviation** from baseline indicates abnormal mechanical loading.
- **IEEE 841-2001** (severe-duty motors) recommends ±20% as the standard alert band for current anomaly detection.
- A **monotonic increase in RMS current** over multiple operating cycles is the primary signature of progressive mechanical degradation (roller wear, bearing failure).

**Why ±20% for this system?**
- The SCT-013 on the main dryer cable measures a **bimodal signal**: motor baseline (~2.0 A) + gas ignitor spikes (~+1.0 A). Computing σ from this combined signal inflates the standard deviation, making ±3σ limits too wide to detect gradual motor wear.
- A fixed percentage tolerance is simpler, well-documented in MCSA literature, and practical for a retrofit IoT thesis project.
- **UCL = Mean × 1.20** (20% increase → roller wear, bearing degradation, drum imbalance).
- **LCL = Mean × 0.80** (20% decrease → belt slip, broken belt, loss of load).

### 1.3 Gas Dryer Fault Detection
**Primary Source:** Bodily et al. — *"Automating Predictive Maintenance for Energy Efficiency"* (EasyChair). Confirmed that motor current trending upward indicates bearing/roller degradation or belt slippage.

**Supporting Source:** Oak Ridge National Laboratory (ORNL) — *"Residential Clothes Dryer Performance Under Timed and Automatic Termination"* (2014). Established that **end-of-cycle exhaust humidity** is the primary indicator of drying completeness. Higher end-RH than baseline indicates incomplete drying.

**Supporting Source:** U.S. Fire Administration / FEMA (2012). *"Clothes Dryer Fires in Residential Buildings (2008–2010)."* Documented that **lint blockage** is the leading cause of dryer fires, characterized by increased exhaust temperature, reduced airflow, and longer drying cycles.

### 1.4 Combined Motor + Ignitor Current Measurement
When the SCT-013 is clamped on the main power cable (before the appliance), it measures **both motor and ignitor current** as a combined signal. The firmware publishes this combined value. The backend separates the two components in software:

1. **Motor baseline** = median of non-spike readings (for mechanical fault detection: roller wear, belt snap).
2. **Ignition events** = detected spike peaks (for cycle analytics and thermal monitoring).

**Note on data characteristics:** Production telemetry shows the dryer current is not a simple flat baseline with clean spikes. Real data exhibits:
- **Heating-phase baseline**: ~2.03–2.11 A (tight, stable, ±5%).
- **Cool-down/tumbling baseline**: ~1.83–1.90 A (lower, no ignition spikes).
- **Ignition spikes**: ~2.9–3.1 A above the heating baseline.

The ~0.2 A drop during cool-down is **normal cycle behavior** (gas burner off, motor-only tumbling). Fault detection must compare **like-with-like** — heating-phase baseline against heating-phase baseline across cycles.

---

## 2. Key Design Decisions

### 2.1 Why Zone A/B/C Rules Are Removed
The original design adopted Western Electric zone definitions (Zone A = beyond ±2σ, Zone B = ±1σ to ±2σ, Zone C = within ±1σ). These rules assume a **stationary process** — data that fluctuates randomly around a stable mean.

Real appliance data is **cyclical, not stationary**:
- **HVAC ΔT**: Compressor ON → ΔT rises to ~14°C. Compressor OFF → ΔT drops to ~5°C. Repeats every 10–15 minutes. The signal naturally oscillates through all zones.
- **HVAC Coil Temp**: Compressor ON → drops to ~10°C. Compressor OFF → rises to ~20°C.
- **Dryer Current**: Motor baseline ~2.0 A, ignitor spike ~3.0 A, repeats every 30–90 seconds.

Applying "8 consecutive points in Zone C" or "6 consecutive points monotonically increasing" generates false positives on every normal cycle.

**Replacement:** State-aware, cycle-aggregated evaluation.
- For cyclic parameters, we **detect cycles first**, then **compare cycle statistics** (peak, minimum, average, end-value) against baselines.
- Evaluation only occurs during **stable states** (e.g., non-inverter compressor has been ON for ≥1 hour, inverter at ≥5 min high-effort).

### 2.2 State-Aware Evaluation Windows

**HVAC Compressor Window Tracker (v3 — Last-3 Averaged):**

```
IDLE: current < 0.25 A
  ↓ current rises above 0.25 A
RUNNING (non-inverter): buffer last 6 readings
  ↓ current drops below 0.25 A  OR  runtime ≥ 1 hour
EVALUATE average of last 6 readings → continue RUNNING (re-evaluate every hour)

IDLE: current < 0.25 A or T_return ≤ 26.5°C
  ↓ current > 0.25 A AND T_return > 26.5°C
HIGH-EFFORT (inverter): buffer last 6 readings
  ↓ current drops below 0.25 A  OR  runtime ≥ 5 min
EVALUATE average of last 6 readings → return to IDLE
```

**Why last-6 averaged evaluation?**
- A single reading can be noisy or anomalous. Averaging the last 6 readings (~1 minute of data at 10s publish interval) at the evaluation point smooths out sensor jitter while still capturing the true performance at that moment.
- **Non-inverter:** Evaluate at 1 hour of continuous compressor run, then re-evaluate every additional hour if the compressor is still running. If the compressor turns off earlier, evaluate at the last running reading before turn-off.
- **Inverter:** Evaluate at 5 minutes of high-effort run (Treturn > 26.5°C). No maintaining-state detection — the 5-minute window captures the compressor at its hardest-working moment.
- **Compressor fault (current > UCL):** Triggers **immediately** on any single reading, outside the evaluation window. The 10-minute alert cooldown prevents spam.
- **Critical escalation:** If Treturn is still ≥ 27°C after 1 hour of running (non-inverter) or at the 5-minute evaluation (inverter), the AC is effectively not cooling — this is a **critical** alert regardless of the specific fault type.

**Dryer Cycle State Machine:**
```
IDLE: current < 0.4 A
  ↓ current rises above 0.4 A
RUNNING: current ≥ 0.4 A
  ↓ spike detected (using existing ignition state machine)
IGNITION: brief spike event ← EXCLUDED from motor baseline
  ↓ spike ends
RUNNING
  ↓ current < 0.4 A for >60 s
CYCLE_END ← evaluate lint blockage, clothes dryness here
```

### 2.3 Dryer Evaluation — Last-6 Averaging at Cycle End

At cycle end, the system computes **averages from the last 6 running readings** for:
- `end_rh_avg` — average of last 6 RH values
- `end_temp_avg` — average of last 6 exhaust temperature values
- `end_current_avg` — average of last 6 motor current values

**Why last 6?** The dryer publishes every ~10 seconds, so 6 readings = ~1 minute of data at the tail end of the cycle. This smooths out transient RH/temp fluctuations while capturing the true end-of-cycle state.

**Severity logic:**
- `fault_dryer_lint_blockage`: Warning when end RH > UCL AND max temp > UCL. **Critical** when max temp > 100°C (burning risk).
- `fault_dryer_incomplete_drying`: Warning when end RH > UCL. **Critical** when end RH > 90% (not drying at all).

---

### 2.4 Spike Detection + Consecutive Reading Fault Detection

**The Problem:** The SCT-013 on the main cable sees combined motor + ignitor current. We need the motor baseline (floor) to detect roller wear, but ignitor spikes contaminate simple averaging. Additionally, the old per-cycle median approach could not distinguish a single abnormal dip from a genuine belt snap.

**The Solution — Two-Layer Spike Extraction + Fault Detection:**

**Layer 1: Dynamic State Machine** (reuse existing `dryer_analytics()` logic):
```
IDLE → current rises by >prominence_threshold → RISING
RISING → current peaks → FALLING
FALLING → current drops back to baseline → IDLE
```
Each detected spike gives: `spike_start_time`, `spike_peak_value`, `spike_end_time`.

**Prominence threshold:** `max(0.35 A, baseline_current_mean × 0.20)`
- Lowered from 0.50 A to 0.35 A based on data validation (see §2.4.1).
- Catches gradual ramp-up spikes that the old threshold missed.

**Layer 2: Hard Threshold Guard** (safety net):
Any reading > `mean × 1.15` (≈ 2.32 A for a 2.02 A baseline) is excluded from motor baseline calculation regardless of state machine output. This catches:
- Missed spike ramp-up edges (e.g., 2.50 A).
- Startup transients (2.63–3.06 A).
- Any anomalous high readings.

**Layer 3: Fault Detection**
- **Belt snap:** 3 consecutive running readings **below LCL**.
- **Roller wear:** Motor baseline **median** (after filtering out ignition spikes via 1.15× mean threshold) compared against **UCL**.

**Why belt snap uses 3 consecutive readings:**
- A single noisy reading or brief transient cannot trigger a fault.
- Ignition spikes are 1–2 readings wide and reset the consecutive counter when current drops back.
- Pause gaps (current = 0, idle) are excluded because `_check_dryer_faults` only receives running data (`current >= 0.25`).
- `motor_readings` in `DRYER_CYCLE_STATS` only accumulates running data, so end-of-cycle backup checks are also immune to idle gaps.

**Why roller wear uses motor baseline median:**
- Ignition spikes (1–2 readings, ~+50% above baseline) are filtered out by the `1.15× mean` threshold before computing the median.
- The median of the remaining readings represents the **true motor-only current**, unaffected by brief transients.
- Comparing the median against UCL captures sustained mechanical load increase (worn rollers = higher friction = consistently higher motor current across the entire cycle).

**Real-time vs End-of-Cycle:**
- **Real-time** (`_check_dryer_faults`):
  - Belt snap: counter increments on every running reading below LCL. Alert fires when counter reaches 3.
  - Roller wear: motor baseline median is recomputed from `motor_readings` on each running reading (spikes filtered via 1.15× mean threshold). Alert fires when median > UCL.
- **End-of-cycle backup** (`_finalize_dryer_cycle`): If the real-time path didn't fire, the cycle's `motor_readings` array is checked. Belt snap: last 3 readings all < LCL. Roller wear: motor baseline median > UCL.
- Each fault uses a `*_triggered` flag per cycle to prevent duplicate alerts.

**Validation data:**
| Cycle | Duration | Median | Mean | Min | Max |
|-------|----------|--------|------|-----|-----|
| 1 | 49 min | 2.000 A | 2.034 A | 1.960 A | 3.060 A |
| 2 | 45 min | 2.030 A | 2.015 A | 1.830 A | 2.110 A |
| 3 | 7 min | 2.000 A | 2.001 A | 1.990 A | 2.030 A |
| 4 | 59 min | 2.005 A | 2.016 A | 1.970 A | 2.330 A |
| 5 | 56 min | 2.000 A | 2.004 A | 1.840 A | 2.110 A |

Even with startup transients up to 3.06 A, the per-cycle median remains stable at 2.000–2.030 A.

#### 2.4.1 Data Validation — Prominence Threshold
Testing on 1,311 readings over 4 days:

| Threshold | Positive Deltas Triggered | Verified Spikes | False Positives |
|-----------|--------------------------|-----------------|-----------------|
| 0.50 A | 42 | 42 | 0 |
| 0.35 A | 64 | 64 | 0 |

All 22 additional events in the 0.35–0.50 A range are genuine spikes (jumping from ~2.0 A baseline to ~2.4–3.1 A). Lowering to 0.35 A catches them with no false positives.

#### 2.4.2 Roller Wear & Belt Snap Differentiation
| Condition | Detection Method | Interpretation |
|-----------|------------------|----------------|
| Normal | 3 consecutive within LCL–UCL (belt snap); median within LCL–UCL (roller wear) | OK |
| Roller wear (early, +15%) | Motor baseline median > UCL | Warning — sustained high load |
| Roller wear (severe, +25%) | Motor baseline median > UCL | Warning — replace rollers soon |
| Belt snap | 3 consecutive < LCL | Critical — motor lost mechanical load |

### 2.4 Baseline Input UX — Auto-Derived UCL/LCL

For **gas dryer motor current**, the user inputs:
- **Mean**: The steady-state motor current between ignition spikes (e.g., 2.0 A).

The system auto-computes:
- **UCL** = Mean × 1.20
- **LCL** = Mean × 0.80

The user can **override** UCL/LCL if desired, but the defaults are based on MCSA literature.

For **all other parameters**, UCL/LCL remain manual inputs (existing v2 architecture).

---

## 3. Monitored Faults — Gas Dryer

### 3.1 Clothes Not Fully Dried
| Attribute | Detail |
|-----------|--------|
| **Description** | Dryer completes cycle but clothes retain excessive moisture. |
| **Root Cause** | Overloading, worn heating element, or short cycle. |
| **Primary Trigger** | End-of-cycle exhaust RH > `rhexhaust_UCL`. |
| **Confirmatory** | None. |
| **Evaluation Point** | CYCLE_END only (transition from running to idle). |
| **Severity** | Warning (end RH > UCL) / Critical (end RH > 90%) |
| **Alert Type** | `fault_dryer_incomplete_drying` |

**Physics:** Dryer completes cycle but clothes retain excessive moisture due to overloading, worn heating element, or short cycle. The exhaust humidity at cycle end reflects how much moisture was removed from the clothes.

**Research Base:** Oak Ridge National Laboratory (ORNL, 2014) established that **end-of-cycle exhaust humidity** is the primary indicator of drying completeness. Higher end-RH than the normal baseline indicates the drying process was incomplete.

**Why This Check:** End-of-cycle RH exceeding UCL means the exhaust is more humid than normal at cycle end — the clothes didn't dry fully. Evaluated as `elif` after lint blockage so only one humidity alert fires per cycle.

**Trigger Condition:**
- **Warning:** `end_RH > rhexhaust_UCL` at **CYCLE_END**.
- **Critical:** `end_RH > 90.0%` at **CYCLE_END**.

**Code (`_finalize_dryer_cycle()`):**
```python
    if rhexhaust_ucl is not None and end_rh_avg > rhexhaust_ucl:
        if end_rh_avg > 90.0:
            severity = 'critical'
            msg = f"Severely incomplete drying - end RH {end_rh_avg:.1f}% exceeds 90%"
        else:
            severity = 'warning'
            msg = f"Clothes not fully dried - end RH {end_rh_avg:.1f}% exceeds UCL"
        _insert_fault_alert(
            appliance_id, 'fault_dryer_incomplete_drying',
            msg, end_rh_avg, rhexhaust_ucl, severity, now, cur, conn)
```

---

### 3.2 Barrel Roller Worn Out
| Attribute | Detail |
|-----------|--------|
| **Description** | Support rollers under the drum are worn, increasing mechanical friction. |
| **Root Cause** | Normal wear, lack of lubrication, or debris accumulation. |
| **Primary Trigger** | Motor baseline median (spikes filtered via 1.15× mean threshold) > `current_UCL`. |
| **Confirmatory** | None. |
| **Evaluation Point** | During RUNNING state (real-time) or at CYCLE_END (backup). |
| **Severity** | Warning |
| **Alert Type** | `fault_dryer_roller_wear` |

**Physics:** Support rollers under the drum wear down over time, increasing mechanical friction. The motor must draw more current to maintain the same drum RPM.

**Research Base:** Bodily et al. confirmed that motor current trending upward indicates bearing/roller degradation or belt slippage. IEEE 841-2001 recommends ±20% current deviation bands for abnormal mechanical loading detection. MCSA literature (Kia et al. 2009, Bellini et al. 2008) establishes that mechanical load changes manifest as sustained changes in the stator current fundamental component.

**Why This Check:** A single high reading could be an ignition spike or transient. The motor baseline median filters out spikes (via `1.15× mean` threshold) and returns the true motor-only current. If the median exceeds UCL, the motor is consistently drawing more current across the entire cycle — indicating increased mechanical friction from worn rollers. A 10-minute cooldown per fault type prevents alert spam.

**Trigger Condition:** Motor baseline median (after filtering readings > `1.15× mean`) > `current_UCL`.

**Code (`_check_dryer_faults()` — real-time):**
```python
    current_ucl = baselines.get('current', {}).get('ucl')
    if current_ucl is not None and not stats.get('roller_wear_triggered', False):
        motor_readings = stats.get('motor_readings', [])
        if len(motor_readings) >= 3:
            filter_threshold = (sum(motor_readings) / len(motor_readings)) * 1.15
            median_current = _compute_motor_baseline_median(motor_readings, filter_threshold=filter_threshold)
            if median_current > current_ucl:
                _insert_fault_alert(
                    appliance_id, 'fault_dryer_roller_wear',
                    f"Barrel roller worn out - motor baseline median {median_current:.3f}A exceeded UCL {current_ucl:.3f}A",
                    median_current, current_ucl, 'warning', now, cur, conn)
                stats['roller_wear_triggered'] = True
```

**Code (`_finalize_dryer_cycle()` — end-of-cycle backup):**
```python
    current_ucl = baselines.get('current', {}).get('ucl')
    if current_ucl is not None and not stats.get('roller_wear_triggered', False):
        if motor_readings:
            filter_threshold = (sum(motor_readings) / len(motor_readings)) * 1.15
            median_current = _compute_motor_baseline_median(motor_readings, filter_threshold=filter_threshold)
            if median_current > current_ucl:
                _insert_fault_alert(
                    appliance_id, 'fault_dryer_roller_wear',
                    f"Barrel roller worn out - motor baseline median {median_current:.3f}A exceeded UCL {current_ucl:.3f}A during cycle",
                    median_current, current_ucl, 'warning', now, cur, conn)
```

---

### 3.3 Belt Snapped
| Attribute | Detail |
|-----------|--------|
| **Description** | Drive belt connecting motor to drum has broken or slipped off. |
| **Root Cause** | Age, overloading, or misalignment. |
| **Primary Trigger** | 3 consecutive running motor readings < `current_LCL`. |
| **Confirmatory** | Current stays low while the dryer is running (not a brief dip). |
| **Evaluation Point** | During RUNNING state (real-time) or at CYCLE_END (backup). |
| **Severity** | Critical |
| **Alert Type** | `fault_dryer_belt_snapped` |

**Physics:** The drive belt connecting the motor to the drum breaks or slips off. The motor loses all mechanical load and spins freely. Current drops because there is no drum resistance to work against.

**Research Base:** MCSA literature (Kia et al. 2009, Bellini et al. 2008) establishes that loss of mechanical load manifests as a sustained decrease in the stator current fundamental component. A sudden current drop below the normal operating range is the definitive signature of belt failure or loss of load.

**Why This Check:** A single low reading could be a sensor glitch or brief load change. Requiring 3 consecutive readings below LCL confirms the motor has truly lost its mechanical load. When a belt snaps, the dryer may continue trying to run for a few seconds before shutting off, producing multiple low-current readings. If the dryer shuts off immediately, the end-of-cycle backup checks the last 3 running readings of the cycle.

**Trigger Condition:** 3 consecutive running readings with `current < current_LCL`.

**Code (`_check_dryer_faults()` — real-time):**
```python
    current_lcl = baselines.get('current', {}).get('lcl')
    if current_lcl is not None:
        if current < current_lcl:
            stats['consecutive_below_lcl'] = stats.get('consecutive_below_lcl', 0) + 1
        else:
            stats['consecutive_below_lcl'] = 0

        if stats['consecutive_below_lcl'] >= 3 and not stats.get('belt_snap_triggered', False):
            _insert_fault_alert(
                appliance_id, 'fault_dryer_belt_snapped',
                f"Belt snapped - motor current dropped below LCL {current_lcl:.3f}A for 3 consecutive readings (last: {current:.3f}A)",
                current, current_lcl, 'critical', now, cur, conn)
            stats['belt_snap_triggered'] = True
```

**Code (`_finalize_dryer_cycle()` — end-of-cycle backup):**
```python
    current_lcl = baselines.get('current', {}).get('lcl')
    if current_lcl is not None and not stats.get('belt_snap_triggered', False):
        last_3 = motor_readings[-3:] if len(motor_readings) >= 3 else motor_readings
        if len(last_3) >= 3 and all(r < current_lcl for r in last_3):
            _insert_fault_alert(
                appliance_id, 'fault_dryer_belt_snapped',
                f"Belt snapped - last 3 motor readings ({last_3[-3]:.3f}A, {last_3[-2]:.3f}A, {last_3[-1]:.3f}A) all below LCL {current_lcl:.3f}A",
                last_3[-1], current_lcl, 'critical', now, cur, conn)
```

---

### 3.4 Lint Blockage on Exhaust Pipe
| Attribute | Detail |
|-----------|--------|
| **Description** | Lint accumulation in the exhaust duct restricts airflow, causing overheating. |
| **Root Cause** | Failure to clean lint filter or exhaust duct; exterior vent obstruction. |
| **Primary Trigger** | End-of-cycle exhaust RH > `rhexhaust_UCL` **AND** end-of-cycle exhaust temp > `texhaust_UCL`. |
| **Confirmatory** | None (lint blockage is purely end-of-cycle RH + temp). |
| **Evaluation Point** | CYCLE_END only. |
| **Severity** | Warning (temp > UCL) / Critical (temp > 100°C) |
| **Alert Type** | `fault_dryer_lint_blockage` |

**Physics:** Lint accumulation in the exhaust duct restricts airflow. Hot, moist air cannot escape efficiently. At cycle end, clothes remain wet (high exhaust RH) and the exhaust duct overheats (high exhaust temp) because the heat is trapped inside the system.

**Research Base:** U.S. Fire Administration (2012) documented that **lint blockage** is the leading cause of dryer fires, characterized by increased exhaust temperature, reduced airflow, and longer drying cycles. ORNL (2014) linked restricted airflow to longer cycles and incomplete moisture removal.

**Why This Check:** Lint blockage is a **cycle-outcome fault**, not a point-in-time fault. During the cycle, temps may look normal because heat is trapped inside. The definitive signature appears at cycle end: both high RH (wet clothes) AND high exhaust temp (trapped heat). Both conditions must be true to avoid false alarms from overloaded drums (high RH but normal temp).

**Trigger Condition:** `end_RH > rhexhaust_UCL` **AND** `max_temp > texhaust_UCL` at **CYCLE_END**.

**Code (`_finalize_dryer_cycle()`):**
```python
    # --- Lint Blockage (Critical) ---
    rhexhaust_ucl = baselines.get('rhexhaust', {}).get('ucl')
    texhaust_ucl = baselines.get('texhaust', {}).get('ucl')
    if rhexhaust_ucl is not None and texhaust_ucl is not None:
        if end_rh_avg > rhexhaust_ucl and max_temp > texhaust_ucl:
            _insert_fault_alert(
                appliance_id, 'fault_dryer_lint_blockage',
                f"Lint blockage detected - end RH {end_rh_avg:.1f}% > UCL {rhexhaust_ucl:.1f}% and exhaust temp {max_temp:.1f}C > UCL {texhaust_ucl:.1f}C",
                end_rh_avg, rhexhaust_ucl, now, cur, conn)
```

---



## 4. Monitored Faults — Split HVAC

### 4.1 Fault Evaluation Matrix

All HVAC faults are diagnosed from the **average of the last 6 readings at the evaluation point** using three metrics:
- **Delta-T** — temperature split (Return − Supply), larger is better
- **Current** — compressor electrical draw
- **Treturn** — return air temperature (used for severity escalation)

### 4.1 Fault Type Matrix (WHAT is wrong)

| Delta-T_avg vs. LCL | Current_avg vs. Limits | Fault Type |
|---------------------|------------------------|------------|
| ≥ LCL | any | ✅ Good condition |
| < LCL | < LCL | `fault_hvac_low_refrigerant` |
| < LCL | LCL–UCL | `fault_hvac_dirty_filter` |
| < LCL | > UCL | `fault_hvac_compressor_fault` |

### 4.2 Severity Escalation (HOW BAD is it)

| Runtime | Treturn_avg | Severity |
|---------|-------------|----------|
| < 1 hour (non-inverter) / < 5 min (inverter) | any | Warning (if fault detected) |
| ≥ 1 hour (non-inverter) / ≥ 5 min (inverter) | < 27°C | Warning (cooling still happening, but degraded) |
| ≥ 1 hour (non-inverter) / ≥ 5 min (inverter) | ≥ 27°C | **Critical** (room still hot — no effective cooling) |

**Why this two-tier system works:**
- **Low refrigerant:** Less refrigerant mass → evaporator cannot absorb enough heat → temperature split shrinks (Delta-T drops). The compressor senses reduced suction pressure and draws **less** current (offloads).
- **Dirty filter:** Restricted airflow → less total heat transfer across the coil → temperature split shrinks (Delta-T drops). The compressor still tries to pump against the restriction, so current stays **normal**.
- **Outdoor problem (capacitor/condenser):** Condenser cannot reject heat effectively → high-side pressure rises → compressor works harder → less indoor cooling → Delta-T drops **AND** current rises.

**Research Base:** Sun et al. (2013) identified temperature differential (ΔT) as the strongest discriminator for refrigerant charge and airflow faults. Bonvini et al. (2014) validated that **current draw anomalies combined with normal thermal readings** indicate condenser-side faults, while **thermal anomalies with normal current** indicate refrigerant-side faults. The Delta-T + current matrix directly implements this finding.

---

### 4.2 Dirty Indoor Filter
| Attribute | Detail |
|-----------|--------|
| **Description** | Air filter is clogged, restricting airflow across the evaporator coil. |
| **Root Cause** | Neglected filter replacement; high dust environments. |
| **Primary Trigger** | Delta-T_avg < LCL **AND** current_avg within LCL–UCL. |
| **Confirmatory** | None. |
| **Evaluation Point** | Non-inverter: 1 hour, then every additional hour. Inverter: 5 min high-effort. |
| **Severity** | Warning (Treturn < 27°C) / **Critical** (Treturn ≥ 27°C after 1 hr non-inverter, or at 5 min inverter) |
| **Alert Type** | `fault_hvac_dirty_filter` |

**Physics:** A clogged indoor filter restricts airflow across the evaporator coil. With less warm air passing over the coil, the refrigerant cannot absorb its design heat load. The supply air temperature rises because the coil cannot chill it sufficiently. The compressor continues to draw normal current because the mechanical load hasn't changed — only the heat exchange has degraded.

**Why This Check:** Restricted airflow reduces total heat transfer → smaller temperature split (Delta-T drops). The compressor electrical load is unchanged because the mechanical resistance hasn't changed. This is the only cell in the matrix where Delta-T is low but current is normal.

**Trigger Condition:** `deltat_avg < deltat_lcl` **AND** `current_lcl ≤ current_avg ≤ current_ucl` at evaluation.

**Code (`_evaluate_hvac_window()`):**
```python
    deltat_avg = sum(r['deltat'] for r in reading_buffer) / len(reading_buffer)
    current_avg = sum(r['current'] for r in reading_buffer) / len(reading_buffer)
    treturn_avg = sum(r['treturn'] for r in reading_buffer) / len(reading_buffer)
    current = snapshot.get('current', 0.0)
    deltat_lcl = baselines.get('deltat', {}).get('lcl')
    current_lcl = baselines.get('current', {}).get('lcl')
    current_ucl = baselines.get('current', {}).get('ucl')

    if deltat_avg >= deltat_lcl:
        return  # Good condition

    severity = 'critical' if (runtime >= 600 and treturn_avg >= 27.0) else 'warning'

    if current_avg < current_lcl:
        # Low refrigerant (see §4.3)
        _insert_fault_alert(
            appliance_id, 'fault_hvac_low_refrigerant',
            f"Low refrigerant - Delta-T {deltat_avg:.1f}C below LCL {deltat_lcl:.1f}C with low current {current_avg:.2f}A",
            deltat_avg, deltat_lcl, severity, now, cur, conn)
    elif current_avg > current_ucl:
        # Outdoor problem (see §4.4)
        _insert_fault_alert(
            appliance_id, 'fault_hvac_compressor_fault',
            f"Outdoor problem - Delta-T {deltat_avg:.1f}C below LCL {deltat_lcl:.1f}C with high current {current_avg:.2f}A",
            current_avg, current_ucl, severity, now, cur, conn)
    else:
        # Dirty filter — Delta-T low, current normal
        _insert_fault_alert(
            appliance_id, 'fault_hvac_dirty_filter',
            f"Dirty indoor filter - Delta-T {deltat_avg:.1f}C below LCL {deltat_lcl:.1f}C with normal current {current_avg:.2f}A",
            deltat_avg, deltat_lcl, severity, now, cur, conn)
```

---

### 4.3 Low Refrigerant
| Attribute | Detail |
|-----------|--------|
| **Description** | Refrigerant charge is below specification due to leak or improper installation. |
| **Root Cause** | Micro-leaks in coil or lines; improper initial charge; Schrader valve leaks. |
| **Primary Trigger** | Snapshot T_supply > `tsupply_UCL` **AND** snapshot current < `current_LCL`. |
| **Confirmatory** | None. |
| **Evaluation Point** | Non-inverter: cycle end. Inverter: high-effort window end. |
| **Severity** | Warning (temp > UCL) / Critical (temp > 100°C) |
| **Alert Type** | `fault_hvac_low_refrigerant` |

**Physics:** Low refrigerant charge means less refrigerant mass in the evaporator coil. The refrigerant evaporates too early in the coil path. By the time it reaches the end of the coil, it's all vapor and absorbing little heat. Result:
- **Delta-T drops**: The coil cannot chill the air enough → temperature split shrinks.
- **Current drops**: The compressor senses reduced suction pressure (less refrigerant to compress) and draws less current. The motor offloads because there is simply less mass to pump.

**Research Base:** Sun et al. (2013) demonstrated that deviations in temperature differential (ΔT) are the strongest discriminators for refrigerant charge faults. Bonvini et al. (2014) validated that **thermal anomalies with normal current** indicate refrigerant-side faults. In the snapshot matrix, "normal current" is replaced by "low current" because reduced refrigerant mass directly reduces compressor load.

**Why This Check:** Low refrigerant is the only condition that produces **both** small temperature split **and** low compressor current. The compressor has less work to do because there is less refrigerant to pump, while the reduced cooling capacity shrinks the Delta-T.

**Trigger Condition:** `deltat_avg < deltat_lcl` **AND** `current_avg < current_lcl` at evaluation.

**Code (`_evaluate_hvac_window()`):**
```python
    if current_avg < current_lcl:
        severity = 'critical' if (runtime >= 600 and treturn_avg >= 27.0) else 'warning'
        _insert_fault_alert(
            appliance_id, 'fault_hvac_low_refrigerant',
            f"Low refrigerant - Delta-T {deltat_avg:.1f}C below LCL {deltat_lcl:.1f}C with low current {current_avg:.2f}A",
            deltat_avg, deltat_lcl, severity, now, cur, conn)
```

---

### 4.4 Outdoor Problem (Capacitor / Condenser)
| Attribute | Detail |
|-----------|--------|
| **Description** | Compressor or condenser is struggling due to electrical or mechanical fault. |
| **Root Cause** | Failing run capacitor, dirty condenser, refrigerant overcharge, failing compressor bearings. |
| **Primary Trigger** | Snapshot T_supply > `tsupply_UCL` **AND** snapshot current > `current_UCL`. |
| **Confirmatory** | None. |
| **Evaluation Point** | Non-inverter: cycle end. Inverter: high-effort window end. |
| **Severity** | Warning (temp > UCL) / Critical (temp > 100°C) |
| **Alert Type** | `fault_hvac_compressor_fault` |

**Physics:** When the condenser cannot reject heat effectively (dirty coils, failing fan, bad capacitor), or when the compressor has internal mechanical friction (worn bearings), the high-side pressure rises. The compressor must work harder to push refrigerant against this elevated pressure. Result:
- **Delta-T drops**: Less heat is rejected outdoors, so the system cannot absorb as much heat indoors → smaller temperature split.
- **Current rises**: The compressor motor draws more power to overcome the increased mechanical load.

**Research Base:** Sun et al. (2013) found that current draw deviations with normal thermal readings indicate condenser-side faults. Bonvini et al. (2014) validated that current anomalies combined with normal thermal parameters point to mechanical or electrical compressor issues. In the snapshot matrix, "normal thermal" is replaced by "low Delta-T" because both thermal and electrical performance degrade simultaneously.

**Why This Check:** An outdoor problem is the only condition that produces **both** small temperature split **and** high compressor current. The compressor is working harder but achieving less cooling — the hallmark of a condenser-side or compressor-side fault.

**Trigger Condition:** `deltat_avg < deltat_lcl` **AND** `current_avg > current_ucl` at evaluation.

**Code (`_evaluate_hvac_window()`):**
```python
    elif current_avg > current_ucl:
        severity = 'critical' if (runtime >= 600 and treturn_avg >= 27.0) else 'warning'
        _insert_fault_alert(
            appliance_id, 'fault_hvac_compressor_fault',
            f"Outdoor problem - Delta-T {deltat_avg:.1f}C below LCL {deltat_lcl:.1f}C with high current {current_avg:.2f}A",
            current_avg, current_ucl, severity, now, cur, conn)
```

---

## 5. SPC Rule Framework Summary (Revised)

### 5.1 Why Zone Definitions Are Removed
The original Zone A/B/C framework is **not applicable** to cyclic appliance data. Instead, we use three rule types tailored to state-aware evaluation:

| Rule Name | Condition | Use Case |
|-----------|-----------|----------|
| **Immediate Breach** | SPC point breach during running state | SPC ucl/lcl breach |
| **Cycle Sustained** | Cycle statistic (peak, min, median, avg) beyond limit at cycle end | Roller wear, refrigerant leak, dirty filter, belt snap |
| **End-of-Cycle** | End-of-cycle value beyond limit at CYCLE_END transition | Lint blockage, incomplete drying |

### 5.2 Cycle Statistic Definitions

| Appliance | Parameter | Cycle Statistic | Why This Statistic |
|-----------|-----------|-----------------|-------------------|
| **Dryer** | Motor current | Belt snap: 3 consecutive readings < LCL. Roller wear: motor baseline median (spikes filtered via 1.15× mean threshold) vs UCL. | Belt snap: consecutive readings confirm sustained loss of load. Roller wear: median captures true motor current after removing ignition spikes. |
| **Dryer** | Exhaust RH | End-of-cycle value (last 2 min average) | ORNL finding: end-RH is definitive dryness indicator |
| **Dryer** | Exhaust temp | End-of-cycle value (last 2 min average) | Trapped heat at cycle end indicates blockage |
| **HVAC** | ΔT | Average of **last 6 readings** at evaluation point | Smooths sensor noise while capturing true performance |
| **HVAC** | Current | Average of **last 6 readings** at evaluation point | Used together with Delta-T_avg in the fault matrix |
| **HVAC** | T_return | Average of **last 6 readings** at evaluation point | Used for **severity escalation** (critical if ≥ 27°C after 1 hr non-inverter, or at 5 min inverter) |
| **HVAC** | T_return | Kept on chart for **visual context only** | No longer used for fault detection |
| **HVAC** | T_coil | Kept on chart for **visual context only** | No longer used for fault detection |

### 5.3 Cooldown & Resolution
- **Alert Cooldown:** 10 minutes per fault type per appliance. Prevents alert spam.
- **Auto-Resolution:** If normal readings persist for 2 consecutive cycles (or 10 minutes for continuous faults), the fault is considered resolved.

---

## 6. Baseline Requirement (Critical Invariant)

**All fault alerts are gated behind `baseline_configured = TRUE`.**

If the user has not manually configured SPC baselines, fault detection is **completely skipped**.

| Fault | Required Baseline Metrics |
|-------|--------------------------|
| Clothes Not Fully Dried | `rhexhaust` |
| Barrel Roller Worn Out | `current` |
| Belt Snapped | `current` |
| Lint Blockage | `texhaust`, `rhexhaust` |
| Dirty Indoor Filter | `deltat`, `current` |
| Low Refrigerant | `deltat`, `current` |
| Outdoor Problem (Capacitor/Condenser) | `deltat`, `current` |

---

## 7. Baseline Setting Instructions for Users

When the user configures SPC baselines in the dashboard, display these instructions per parameter:

### Gas Dryer
| Parameter | Instruction |
|-----------|-------------|
| **Motor Current** | *"Enter the steady-state motor current measured between gas ignition spikes (e.g., 2.0 A). Do not include spike peaks. UCL/LCL will be auto-derived (±20%)."* |
| **Exhaust Temperature** | *"Record the exhaust temperature during the last 2 minutes of a normal drying cycle. Do not record during warm-up."* |
| **Exhaust Humidity** | *"Record the exhaust humidity during the last 2 minutes of a normal drying cycle. This is the definitive dryness indicator."* |

### Split HVAC
| Parameter | Instruction |
|-----------|-------------|
| **ΔT (Return − Supply)** | *"Set UCL to the highest and LCL to the lowest temperature split observed during normal cooling. Delta-T LCL is the critical threshold — any cycle with maximum Delta-T below this value triggers fault evaluation."* |
| **Compressor Current** | *"Set UCL to the highest current and LCL to the lowest current observed during normal operation. Used together with Delta-T in the fault matrix: low current + low Delta-T = low refrigerant; high current + low Delta-T = outdoor problem."* |

---

## 8. Implementation Notes

### 8.1 Backend Integration (`app.py`)
1. New function `check_fault_alerts(appliance_id, reading_data, dev_type)` is called in `on_mqtt_message()` for every running reading.
2. Function fetches `spc_manual_baselines` for the appliance.
3. If `baseline_configured` is false or required metrics are missing, return immediately.
4. Maintain in-memory trackers:
   - `FAULT_ALERT_TRACKER = {appliance_id: {fault_type: {cycle_count, last_trigger, active}}}`
   - `HVAC_CYCLE_TRACKER = {appliance_id: {state, start_time, reading_buffer, last_evaluation_runtime}}`
     - `state`: `IDLE` or `RUNNING`
     - `reading_buffer`: list of last 6 readings (dict with `deltat`, `current`, `treturn`)
     - `last_evaluation_runtime`: seconds since start when last evaluation occurred (0 = never evaluated this cycle)
   - `DRYER_CYCLE_STATS = {appliance_id: {cycle_start, spike_peaks[], motor_readings[]}}` (accumulates per-cycle data)
5. On trigger, insert into `alerts` table with `alert_type = 'fault_*'` and 10-minute cooldown.

### 8.2 Frontend Integration (`dashboard.html`)
1. Fault alerts appear in the Alerts Panel. No other alert types are shown (SPC breach alerts removed).
2. Critical faults: red left border (`#EF4444`) + `#FEF2F2` background.
3. Warning/Info faults: orange/blue left border + matching background.
4. No acknowledge button required (v2 alerts panel is read-only).

### 8.3 Data Flow
```
ESP32 publishes running telemetry
    ↓
Backend inserts into dryer_readings / hvac_readings
    ↓
check_fault_alerts() → Pattern-based fault alerts (baseline-gated)
    ↓
Frontend polls /api/device/<id>/alerts every 5s
```

### 8.4 Dryer Analytics Enhancement
The existing `dryer_analytics()` per-cycle table should include:

| Column | Definition |
|--------|-----------|
| **Spike Avg** | Average amplitude of detected ignition spike peaks within the cycle (existing). |
| **Motor Current Avg** | Average of all non-spike current readings within the cycle (readings below the spike detection threshold). This represents the true motor load, excluding ignitor transients. |
| **Motor Baseline Median** | Median of all non-spike current readings within the cycle. This is the value compared against UCL/LCL for roller-wear fault detection. |

**Rationale:** Showing all three metrics (Spike Avg, Motor Current Avg, Motor Baseline Median) helps users understand the separation between motor baseline and ignitor spikes. The Motor Baseline Median is the official value used for roller-wear fault detection.

---

## 9. Discord Alert Behavior

### What Goes to Discord
Only **actionable fault alerts** are sent to Discord. Raw data-point alerts are suppressed.

| Alert Type | Discord? | Reason |
|------------|----------|--------|
| `fault_dryer_incomplete_drying` | ✅ Yes | Actionable maintenance advice |
| `fault_dryer_roller_wear` | ✅ Yes | Actionable maintenance advice |
| `fault_dryer_belt_snapped` | ✅ Yes | Critical — immediate action required |
| `fault_dryer_lint_blockage` | ✅ Yes | Critical — immediate action required |
| `fault_hvac_dirty_filter` | ✅ Yes | Actionable maintenance advice |
| `fault_hvac_low_refrigerant` | ✅ Yes | Critical — immediate action required |
| `fault_hvac_compressor_fault` | ✅ Yes | Critical — immediate action required |
| `dryer_humidity_high` | ❌ No | **Removed** — replaced by `fault_dryer_incomplete_drying` (SPC-based, more precise) |

### Discord Embed Format
Fault alerts use a **maintenance-ticket style** embed instead of raw data dumps:

```
🔴 Belt Snapped
Belt snapped — motor baseline 1.20A below LCL 1.60A
━━━━━━━━━━━━━━━━━━━━
📍 Appliance: Dryer Test
🔍 Cause: Age, overloading, or misalignment.
🔧 Recommended Action: Replace drive belt immediately.
```

The embed includes:
- **Severity icon + human-readable title** (e.g., "🔴 Belt Snapped" instead of "🚨 Fault Dryer Belt Snapped")
- **Fault message** with measured value and threshold
- **Cause** explaining the physics/root cause
- **Recommended Action** telling the user what to do

---

## 10. Alert Severity, Titles & Recommended Actions

| Alert Type | Severity | Discord Title | Cause | Recommended Action |
|------------|----------|---------------|-------|-------------------|
| `fault_dryer_incomplete_drying` | 🔵 Info | Clothes Not Fully Dried | Overloading, worn heating element, or short cycle | Reduce load size and run another cycle |
| `fault_dryer_roller_wear` | 🟠 Warning | Barrel Roller Worn Out | Support rollers under the drum are worn, increasing mechanical friction | Inspect and replace drum support rollers |
| `fault_dryer_belt_snapped` | 🔴 Critical | Belt Snapped | Age, overloading, or misalignment | Replace drive belt immediately |
| `fault_dryer_lint_blockage` | 🔴 Critical | Lint Blockage Detected | Failure to clean lint filter or exhaust duct; exterior vent obstruction | Clean lint filter and inspect exhaust duct |
| `fault_hvac_dirty_filter` | 🟠 Warning | Dirty Indoor Filter | Neglected filter replacement; high dust environments | Replace or clean the indoor air filter |
| `fault_hvac_low_refrigerant` | 🔴 Critical | Low Refrigerant | Micro-leaks in coil or lines; improper initial charge; Schrader valve leaks | Contact HVAC technician to check for leaks and recharge |
| `fault_hvac_compressor_fault` | 🔴 Critical | Compressor Electrical Fault | Failing compressor bearings, refrigerant overcharge, condenser blockage, or starter relay failure | Contact HVAC technician for compressor inspection |

---

## 11. References

1. Sun, B., Luh, P.B., Jia, Q.S., O’Neill, Z., Song, F. (2013). *Building energy doctors: An SPC and Kalman filter-based method for system-level fault detection in HVAC systems.* IEEE TASE, 11(1), 215-229.
2. Bonvini, M., Sohn, M.D., Granderson, J., Wetter, M., Piette, M.A. (2014). *Robust on-line fault detection diagnosis for HVAC components based on nonlinear state estimation techniques.* Applied Energy, 124, 156-166.
3. Kia, S.H., Henao, H., Capolino, G.A. (2009). *Torsional vibration effects on induction machine current and torque signatures in gearbox-based electromechanical systems.* IEEE T-IE, 56(11), 4629-4643.
4. Bellini, A., Filippetti, F., Tassoni, C., Capolino, G.A. (2008). *Advances in diagnostic techniques for induction machines.* IEEE T-IE, 55(12), 4109-4126.
5. IEEE 841-2001. *IEEE Standard for Petroleum and Chemical Industry — Severe Duty Totally Enclosed Fan-Cooled (TEFC) Squirrel Cage Induction Motors — Up to and Including 370 kW (500 hp).*
6. Western Electric Company (1956). *Statistical Quality Control Handbook.*
7. Nelson, L.S. (1984). *The Shewhart Control Chart — Tests for Special Causes.* JQT, 16(4), 237-239.
8. Bodily et al. *Automating Predictive Maintenance for Energy Efficiency.* EasyChair.
9. Oak Ridge National Laboratory (2014). *Residential Clothes Dryer Performance Under Timed and Automatic Termination.* ORNL/TM-2014/431.
10. U.S. Fire Administration (2012). *Clothes Dryer Fires in Residential Buildings (2008–2010).* Topical Fire Report Series, Vol. 13, Issue 7.
11. Rossi, T.M., Braun, J.E. (1997). *A statistical, rule-based fault detection and diagnostic method for vapor compression air conditioners.* Int. J. HVAC&R Research, 3(1), 19-37.
12. Breuker, M.S., Braun, J.E. (1998). *Common faults and their impacts for rooftop air conditioners.* HVAC&R Research, 4(3), 293-318.
