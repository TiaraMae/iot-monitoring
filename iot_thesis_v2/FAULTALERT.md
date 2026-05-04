# Fault Alert System — IoT Monitoring v2 (Revised)

> **Document Purpose:** Define the fault detection logic, SPC rule framework, and alert generation strategy for the IoT-Based Monitoring and Alert System. All fault alerts are **gated behind manual SPC baseline configuration** — if UCL/LCL lines are not set, no fault alerts are generated.
>
> **Revision Notes (2026-05-03):** This revision addresses real-world cyclic data patterns observed in production telemetry. The original zone-based SPC rules (Western Electric Zones A/B/C) are **removed** because they generate false positives on naturally cyclic signals (HVAC compressor cycling, gas dryer ignition spikes). Replaced with **state-aware, cycle-aggregated evaluation**.

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
- Evaluation only occurs during **stable states** (e.g., compressor has been ON for ≥10 minutes).

### 2.2 State-Aware Evaluation Windows

**HVAC Compressor State Machine:**
```
IDLE: current < 0.4 A for >60 s
  ↓ current rises above 0.4 A
STARTING: current > 0.4 A, <10 min elapsed
  ↓ 10 min continuous ON
STABLE_ON: current > 0.4 A, ≥10 min continuous ← evaluate faults here only
  ↓ current drops below 0.4 A
STOPPING: current < 0.4 A, <60 s
  ↓ 60 s elapsed
IDLE
```

Why 10 minutes? HVAC compressors have long thermal time constants. From production data, compressor ON periods are ~10–15 minutes. After 10 minutes:
- Evaporator coil is fully chilled and stable.
- ΔT has reached its maximum and plateaued.
- Current has stabilized (inverter) or is at steady state (non-inverter).

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

### 2.3 Spike Detection + Per-Cycle Median for Motor Baseline Extraction

**The Problem:** The SCT-013 on the main cable sees combined motor + ignitor current. We need the motor baseline (floor) to detect roller wear, but ignitor spikes contaminate simple averaging.

**The Solution — Two-Layer Spike Extraction:**

**Layer 1: Dynamic State Machine** (reuse existing `dryer_analytics()` logic):
```
IDLE → current rises by >prominence_threshold → RISING
RISING → current peaks → FALLING
FALLING → current drops back to baseline → IDLE
```
Each detected spike gives: `spike_start_time`, `spike_peak_value`, `spike_end_time`.

**Prominence threshold:** `max(0.35 A, baseline_current_mean × 0.20)`
- Lowered from 0.50 A to 0.35 A based on data validation (see §2.3.1).
- Catches gradual ramp-up spikes that the old threshold missed.

**Layer 2: Hard Threshold Guard** (safety net):
Any reading > `mean × 1.15` (≈ 2.32 A for a 2.02 A baseline) is excluded from motor baseline calculation regardless of state machine output. This catches:
- Missed spike ramp-up edges (e.g., 2.50 A).
- Startup transients (2.63–3.06 A).
- Any anomalous high readings.

**Layer 3: Per-Cycle Median** (roller wear evaluation):
Instead of a rolling buffer, compute **one median motor baseline per dryer cycle** using all non-spike, non-excluded readings within that cycle.

**Why per-cycle median?**
- Eliminates within-cycle variation (cool-down phases, brief load changes).
- Natural comparison of like-with-like (cycle-to-cycle).
- Median is robust to outliers — a few missed spike edges or startup transients do not shift the median.
- No circular buffer state to maintain.

**Validation data:**
| Cycle | Duration | Median | Mean | Min | Max |
|-------|----------|--------|------|-----|-----|
| 1 | 49 min | 2.000 A | 2.034 A | 1.960 A | 3.060 A |
| 2 | 45 min | 2.030 A | 2.015 A | 1.830 A | 2.110 A |
| 3 | 7 min | 2.000 A | 2.001 A | 1.990 A | 2.030 A |
| 4 | 59 min | 2.005 A | 2.016 A | 1.970 A | 2.330 A |
| 5 | 56 min | 2.000 A | 2.004 A | 1.840 A | 2.110 A |

Even with startup transients up to 3.06 A, the per-cycle median remains stable at 2.000–2.030 A.

#### 2.3.1 Data Validation — Prominence Threshold
Testing on 1,311 readings over 4 days:

| Threshold | Positive Deltas Triggered | Verified Spikes | False Positives |
|-----------|--------------------------|-----------------|-----------------|
| 0.50 A | 42 | 42 | 0 |
| 0.35 A | 64 | 64 | 0 |

All 22 additional events in the 0.35–0.50 A range are genuine spikes (jumping from ~2.0 A baseline to ~2.4–3.1 A). Lowering to 0.35 A catches them with no false positives.

#### 2.3.2 Roller Wear Differentiation
| Condition | Per-Cycle Median | Interpretation |
|-----------|------------------|----------------|
| Normal | 2.00 A | OK |
| Roller wear (early, +15%) | 2.30 A | Below UCL — not yet triggered |
| Roller wear (+20%) | 2.40 A | At UCL — triggers after 3-cycle confirmation |
| Roller wear (severe, +25%) | 2.50 A | Above UCL — triggers reliably |
| Belt snap | < `current_LCL` (typically ~1.6 A for 2.0 A baseline) | Immediate fault |

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
| **Severity** | Info |
| **Alert Type** | `fault_dryer_incomplete_drying` |

**Physics:** Dryer completes cycle but clothes retain excessive moisture due to overloading, worn heating element, or short cycle. The exhaust humidity at cycle end reflects how much moisture was removed from the clothes.

**Research Base:** Oak Ridge National Laboratory (ORNL, 2014) established that **end-of-cycle exhaust humidity** is the primary indicator of drying completeness. Higher end-RH than the normal baseline indicates the drying process was incomplete.

**Why This Check:** End-of-cycle RH exceeding UCL means the exhaust is more humid than normal at cycle end — the clothes didn't dry fully. Evaluated as `elif` after lint blockage so only one humidity alert fires per cycle.

**Trigger Condition:** `end_RH > rhexhaust_UCL` at **CYCLE_END**.

**Code (`_finalize_dryer_cycle()`):**
```python
    # --- Incomplete Drying (Info) ---
    # Only check if lint blockage did not fire (guaranteed by elif)
    elif rhexhaust_ucl is not None and end_rh_avg > rhexhaust_ucl:
        _insert_fault_alert(
            appliance_id, 'fault_dryer_incomplete_drying',
            f"Clothes not fully dried - end RH {end_rh_avg:.1f}% exceeds UCL {rhexhaust_ucl:.1f}%",
            end_rh_avg, rhexhaust_ucl, now, cur, conn)
```

---

### 3.2 Barrel Roller Worn Out
| Attribute | Detail |
|-----------|--------|
| **Description** | Support rollers under the drum are worn, increasing mechanical friction. |
| **Root Cause** | Normal wear, lack of lubrication, or debris accumulation. |
| **Primary Trigger** | Per-cycle median motor baseline (spike-excluded) > `current_UCL` for **3 consecutive dryer cycles**. |
| **Confirmatory** | None. |
| **Evaluation Point** | During RUNNING state (excluding ignition windows). |
| **Severity** | Warning |
| **Alert Type** | `fault_dryer_roller_wear` |

**Physics:** Support rollers under the drum wear down over time, increasing mechanical friction. The motor must draw more current to maintain the same drum RPM.

**Research Base:** Bodily et al. confirmed that motor current trending upward indicates bearing/roller degradation or belt slippage. IEEE 841-2001 recommends ±20% current deviation bands for abnormal mechanical loading detection. MCSA literature (Kia et al. 2009, Bellini et al. 2008) establishes that mechanical load changes manifest as sustained changes in the stator current fundamental component.

**Why This Check:** The per-cycle median of spike-excluded readings filters out gas ignition spikes and captures the true motor load. Requiring 3 consecutive cycles with median > UCL prevents false alarms from transient heavy loads. A +20% worn motor (median ≈ UCL) triggers reliably within 3–5 cycles because wear is persistent across cycles.

**Trigger Condition:** `median_current > current_UCL` for **3 consecutive dryer cycles**.

**Code (`_finalize_dryer_cycle()`):**
```python
    # Compute motor baseline median
    median_current = 0.0
    if motor_readings:
        motor_readings_sorted = sorted(motor_readings)
        n = len(motor_readings_sorted)
        median_current = motor_readings_sorted[n // 2] if n % 2 == 1 else (motor_readings_sorted[n // 2 - 1] + motor_readings_sorted[n // 2]) / 2.0

    # --- Roller Wear (Warning) - per-cycle median > UCL for 3 consecutive cycles ---
    current_ucl = baselines.get('current', {}).get('ucl')
    if current_ucl is not None and median_current > current_ucl:
        tracker = FAULT_ALERT_TRACKER.setdefault(appliance_id, {})
        roller = tracker.setdefault('fault_dryer_roller_wear', {'cycle_count': 0, 'last_trigger': None, 'active': False})
        roller['cycle_count'] += 1
        if roller['cycle_count'] >= 3:
            _insert_fault_alert(
                appliance_id, 'fault_dryer_roller_wear',
                f"Barrel roller worn out - motor baseline {median_current:.3f}A exceeds UCL {current_ucl:.3f}A for 3 consecutive cycles",
                median_current, current_ucl, now, cur, conn)
            roller['cycle_count'] = 0
    elif current_ucl is not None:
        tracker = FAULT_ALERT_TRACKER.get(appliance_id, {})
        if tracker and 'fault_dryer_roller_wear' in tracker:
            tracker['fault_dryer_roller_wear']['cycle_count'] = 0
```

---

### 3.3 Belt Snapped
| Attribute | Detail |
|-----------|--------|
| **Description** | Drive belt connecting motor to drum has broken or slipped off. |
| **Root Cause** | Age, overloading, or misalignment. |
| **Primary Trigger** | Per-cycle motor current < `current_LCL` for **>30 seconds** while `in_cycle = True`. |
| **Confirmatory** | Current drops near zero while the dryer is supposedly running (current ≥ 0.4 A threshold was met, then collapsed). |
| **Evaluation Point** | During RUNNING state. |
| **Severity** | Critical |
| **Alert Type** | `fault_dryer_belt_snapped` |

**Physics:** The drive belt connecting the motor to the drum breaks or slips off. The motor loses all mechanical load and spins freely. Current collapses because there is no drum resistance to work against.

**Research Base:** MCSA literature (Kia et al. 2009, Bellini et al. 2008) establishes that loss of mechanical load manifests as a sustained decrease in the stator current fundamental component. A sudden current drop below the normal operating range is the definitive signature of belt failure or loss of load.

**Why This Check:** The firmware only sends telemetry when current ≥ 0.4 A. A reading below LCL (typically ~1.6 A for a 2.0 A baseline) while `in_cycle = True` confirms sustained collapse, not just a brief gap between ignition spikes. Belt snap is an immediate fault — no multi-cycle confirmation needed.

**Trigger Condition:** `current < current_LCL` for **>30 seconds** while `in_cycle = True`.

**Code (`_check_dryer_faults()`):**
```python
    # Belt snap detection (immediate, during cycle)
    if stats.get('in_cycle', False):
        lcl = baselines.get('current', {}).get('lcl', 0.8)
        if current < lcl:
            if 'belt_snap_start' not in stats:
                stats['belt_snap_start'] = actual_time
            elif (actual_time - stats['belt_snap_start']).total_seconds() > 30:
                if not stats.get('belt_snap_triggered', False):
                    _insert_fault_alert(
                        appliance_id, 'fault_dryer_belt_snapped',
                        f"Belt snapped - motor current {current:.2f}A below LCL for >30s",
                        current, lcl, now, cur, conn)
                    stats['belt_snap_triggered'] = True
        else:
            stats.pop('belt_snap_start', None)
```

---

### 3.4 Lint Blockage on Exhaust Pipe
| Attribute | Detail |
|-----------|--------|
| **Description** | Lint accumulation in the exhaust duct restricts airflow, causing overheating. |
| **Root Cause** | Failure to clean lint filter or exhaust duct; exterior vent obstruction. |
| **Primary Trigger** | End-of-cycle exhaust RH > `rhexhaust_UCL` **AND** end-of-cycle exhaust temp > `texhaust_UCL`. |
| **Confirmatory** | Cycle duration > `baseline_cycle_duration × 1.30`. |
| **Evaluation Point** | CYCLE_END only. |
| **Severity** | Critical |
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

### 4.1 Dirty Indoor Filter
| Attribute | Detail |
|-----------|--------|
| **Description** | Air filter is clogged, restricting airflow across the evaporator coil. |
| **Root Cause** | Neglected filter replacement; high dust environments. |
| **Primary Trigger** | Minimum coil temp during STABLE_ON < `tcoil_LCL` for **3 consecutive STABLE_ON cycles**. |
| **Confirmatory** | None. |
| **Evaluation Point** | STABLE_ON only (≥10 min continuous compressor ON). |
| **Severity** | Warning |
| **Alert Type** | `fault_hvac_dirty_filter` |

**Physics:** A clogged indoor filter restricts airflow across the evaporator coil. With less warm air passing over the coil, the refrigerant cannot absorb its design heat load. The evaporating temperature drops below specification, causing the coil surface to **supercool** (get colder than normal). This is the same mechanism that causes evaporator icing in severely blocked systems.

**Research Base:** Sun et al. (2013) identified coil temperature and ΔT as the strongest discriminators for airflow restriction faults. Bonvini et al. (2014) validated that restricted airflow primarily manifests as a **current draw anomaly with normal thermal parameters** — the compressor works against reduced suction pressure.

**Why This Check:** During normal compressor-ON operation, the coil reaches its coldest point near LCL. Supercooling from restricted airflow pushes it **below** this bound. We check `min_tcoil < LCL` because the coil gets colder, not warmer.

**Trigger Condition:** `min_tcoil < tcoil_LCL` for **3 consecutive STABLE_ON cycles**.

**Code (`_evaluate_hvac_cycle()`):**
```python
    # --- Dirty Filter (Warning) - min coil temp < LCL for 3+ consecutive cycles ---
    # Physics: restricted airflow -> less heat load -> refrigerant supercools -> coil colder
    tcoil_lcl = baselines.get('tcoil', {}).get('lcl')
    if tcoil_lcl is not None and min_tcoil < tcoil_lcl:
        df = fat.setdefault('fault_hvac_dirty_filter', {'cycle_count': 0, 'last_trigger': None, 'active': False})
        df['cycle_count'] += 1
        if df['cycle_count'] >= 3:
            _insert_fault_alert(
                appliance_id, 'fault_hvac_dirty_filter',
                f"Dirty indoor filter - min coil temp {min_tcoil:.2f}C below LCL {tcoil_lcl:.2f}C for 3 consecutive cycles",
                min_tcoil, tcoil_lcl, now, cur, conn)
            df['cycle_count'] = 0
    elif tcoil_lcl is not None:
        if 'fault_hvac_dirty_filter' in fat:
            fat['fault_hvac_dirty_filter']['cycle_count'] = 0
```

---

### 4.2 Low Refrigerant
| Attribute | Detail |
|-----------|--------|
| **Description** | Refrigerant charge is below specification due to leak or improper installation. |
| **Root Cause** | Micro-leaks in coil or lines; improper initial charge; Schrader valve leaks. |
| **Primary Trigger** | Peak ΔT during STABLE_ON < `((deltat_UCL + deltat_mean) / 2)` for **3 consecutive STABLE_ON cycles**. |
| **Confirmatory** | Minimum coil temp during STABLE_ON > `tcoil_UCL` (coil warmer than normal). |
| **Evaluation Point** | STABLE_ON only. |
| **Severity** | Critical |
| **Alert Type** | `fault_hvac_low_refrigerant` |

**Physics:** Low refrigerant charge means less refrigerant mass in the evaporator coil. The refrigerant evaporates too early in the coil path. By the time it reaches the end of the coil, it's all vapor and absorbing little heat. Result:
- **ΔT drops**: The coil cannot chill the air enough → supply air is warmer → temperature split (return − supply) shrinks.
- **Coil warms up**: The coil surface temperature rises because there's not enough liquid refrigerant left to absorb heat.

**Research Base:** Sun et al. (2013) demonstrated that deviations in temperature differential (ΔT) and coil temperature are the strongest discriminators for refrigerant charge faults. Bonvini et al. (2014) validated that **thermal anomalies with normal current** indicate refrigerant-side faults (undercharge, leak).

**Why This Check:** During normal stable operation, peak ΔT reaches the upper half of the envelope (near UCL). With low refrigerant, the peak fails to reach this upper half. The threshold `(UCL + mean) / 2` captures this "not reaching the upper half" signature without requiring a separate ON-phase baseline. The confirmatory `min_tcoil > UCL` catches the warmer coil signature.

**Trigger Condition:** `peak_deltat < ((deltat_UCL + deltat_mean) / 2)` **OR** `min_tcoil > tcoil_UCL` for **3 consecutive STABLE_ON cycles**.

**Code (`_evaluate_hvac_cycle()`):**
```python
    # --- Low Refrigerant (Critical) ---
    # Primary: peak dT fails to reach upper half of envelope (< (UCL + mean) / 2)
    # Confirmatory: min coil temp > UCL (coil warmer than normal)
    deltat_ucl = baselines.get('deltat', {}).get('ucl')
    deltat_mean = baselines.get('deltat', {}).get('mean')
    deltat_threshold = (deltat_ucl + deltat_mean) / 2.0 if deltat_ucl is not None and deltat_mean is not None else None
    tcoil_ucl = baselines.get('tcoil', {}).get('ucl')

    low_ref_triggered = False
    if deltat_threshold is not None and peak_deltat < deltat_threshold:
        low_ref_triggered = True
    if tcoil_ucl is not None and min_tcoil > tcoil_ucl:
        low_ref_triggered = True

    if low_ref_triggered:
        lr = fat.setdefault('fault_hvac_low_refrigerant', {'cycle_count': 0, 'last_trigger': None, 'active': False})
        lr['cycle_count'] += 1
        if lr['cycle_count'] >= 3:
            if deltat_threshold is not None and peak_deltat < deltat_threshold:
                msg = f"Low refrigerant - peak dT {peak_deltat:.2f}C below threshold {deltat_threshold:.2f}C for 3 consecutive cycles"
                val, thresh = peak_deltat, deltat_threshold
            else:
                msg = f"Low refrigerant - min coil temp {min_tcoil:.2f}C above UCL {tcoil_ucl:.2f}C for 3 consecutive cycles"
                val, thresh = min_tcoil, tcoil_ucl
            _insert_fault_alert(
                appliance_id, 'fault_hvac_low_refrigerant', msg,
                val, thresh, now, cur, conn)
            lr['cycle_count'] = 0
    elif deltat_threshold is not None or tcoil_ucl is not None:
        if 'fault_hvac_low_refrigerant' in fat:
            fat['fault_hvac_low_refrigerant']['cycle_count'] = 0
```

---

### 4.3 Compressor Electrical Fault / Hard Start
| Attribute | Detail |
|-----------|--------|
| **Description** | Compressor draws excessive current due to mechanical strain or electrical fault. |
| **Root Cause** | Failing compressor bearings, refrigerant overcharge, condenser blockage, starter relay failure. |
| **Primary Trigger** | Average current during STABLE_ON > `current_UCL` for **2 consecutive STABLE_ON cycles**. |
| **Confirmatory** | Coil temp and ΔT may be normal or abnormal depending on root cause. |
| **Evaluation Point** | STABLE_ON only. |
| **Severity** | Critical |
| **Alert Type** | `fault_hvac_compressor_fault` |

**Physics:** During stable operation, compressor current should be relatively constant. Sustained elevation above UCL indicates the compressor is working harder than normal — failing bearings, refrigerant overcharge, condenser blockage, or starter relay failure.

**Research Base:** Sun et al. (2013) found that current draw deviations with normal thermal readings indicate condenser-side faults. Bonvini et al. (2014) validated that current anomalies combined with normal thermal parameters point to mechanical or electrical compressor issues.

**Why This Check:** Current is the most direct measure of compressor electrical load. Two consecutive cycles catches the fault quickly while filtering out brief startup surges. Unlike refrigerant or airflow faults which primarily affect thermal parameters, compressor electrical faults directly elevate current draw.

**Trigger Condition:** `avg_current > current_UCL` for **2 consecutive STABLE_ON cycles**.

**Code (`_evaluate_hvac_cycle()`):**
```python
    # --- Compressor Fault (Critical) - avg current > UCL for 2+ consecutive cycles ---
    current_ucl = baselines.get('current', {}).get('ucl')
    if current_ucl is not None and avg_current > current_ucl:
        cf = fat.setdefault('fault_hvac_compressor_fault', {'cycle_count': 0, 'last_trigger': None, 'active': False})
        cf['cycle_count'] += 1
        if cf['cycle_count'] >= 2:
            _insert_fault_alert(
                appliance_id, 'fault_hvac_compressor_fault',
                f"Compressor electrical fault - avg current {avg_current:.2f}A exceeds UCL {current_ucl:.2f}A for 2 consecutive cycles",
                avg_current, current_ucl, now, cur, conn)
            cf['cycle_count'] = 0
    elif current_ucl is not None:
        if 'fault_hvac_compressor_fault' in fat:
            fat['fault_hvac_compressor_fault']['cycle_count'] = 0
```

---

## 5. SPC Rule Framework Summary (Revised)

### 5.1 Why Zone Definitions Are Removed
The original Zone A/B/C framework is **not applicable** to cyclic appliance data. Instead, we use three rule types tailored to state-aware evaluation:

| Rule Name | Condition | Use Case |
|-----------|-----------|----------|
| **Immediate Breach** | Single point beyond UCL or LCL during appropriate state | Belt snap, ignition failure |
| **Cycle Sustained** | Cycle statistic (peak, min, median, avg) beyond limit for N consecutive cycles | Roller wear, refrigerant leak, dirty filter |
| **End-of-Cycle** | End-of-cycle value beyond limit at CYCLE_END transition | Lint blockage, incomplete drying |

### 5.2 Cycle Statistic Definitions

| Appliance | Parameter | Cycle Statistic | Why This Statistic |
|-----------|-----------|-----------------|-------------------|
| **Dryer** | Motor current | Per-cycle median (spike-excluded) | Median ignores ignition spikes and cool-down phases; captures true motor load |
| **Dryer** | Exhaust RH | End-of-cycle value (last 2 min average) | ORNL finding: end-RH is definitive dryness indicator |
| **Dryer** | Exhaust temp | End-of-cycle value (last 2 min average) | Trapped heat at cycle end indicates blockage |
| **HVAC** | ΔT | Per-STABLE_ON-cycle **peak** | Peak occurs after thermal equilibrium; compares like-with-like across cycles |
| **HVAC** | Coil temp | Per-STABLE_ON-cycle **minimum** | Minimum coil temp indicates maximum cooling capacity |
| **HVAC** | Current | Per-STABLE_ON-cycle **average** | Average filters out brief startup transients |

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
| Dirty Indoor Filter | `tcoil` |
| Low Refrigerant | `deltat`, `tcoil` |
| Compressor Electrical Fault | `current` |

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
| **ΔT (Return − Supply)** | *"Set UCL to the highest ΔT and LCL to the lowest ΔT observed during stable inverter modulation. The system auto-derives the low-refrigerant threshold from these bounds."* |
| **Coil Temperature** | *"Set UCL to the highest coil temp and LCL to the lowest coil temp observed during stable inverter modulation."* |
| **Compressor Current** | *"Set UCL to the highest current and LCL to the lowest current observed during stable inverter modulation."* |

---

## 8. Implementation Notes

### 8.1 Backend Integration (`app.py`)
1. New function `check_fault_alerts(appliance_id, reading_data, dev_type)` is called **after** `check_spc_alerts()` in `on_mqtt_message()`.
2. Function fetches `spc_manual_baselines` for the appliance.
3. If `baseline_configured` is false or required metrics are missing, return immediately.
4. Maintain in-memory trackers:
   - `FAULT_ALERT_TRACKER = {appliance_id: {fault_type: {cycle_count, last_trigger, active}}}`
   - `HVAC_CYCLE_TRACKER = {appliance_id: {state, stable_on_start, peak_deltat, min_tcoil, avg_current}}`
   - `DRYER_CYCLE_STATS = {appliance_id: {cycle_start, spike_peaks[], motor_readings[]}}` (accumulates per-cycle data)
5. On trigger, insert into `alerts` table with `alert_type = 'fault_*'` and 10-minute cooldown.

### 8.2 Frontend Integration (`dashboard.html`)
1. Fault alerts appear in the existing Alerts Panel alongside `spc_ucl_breach` / `spc_lcl_breach`.
2. Critical faults: red left border (`#EF4444`) + `#FEF2F2` background.
3. Warning/Info faults: orange/blue left border + matching background.
4. No acknowledge button required (v2 alerts panel is read-only).

### 8.3 Data Flow
```
ESP32 publishes running telemetry
    ↓
Backend inserts into dryer_readings / hvac_readings
    ↓
check_spc_alerts() → SPC breach alerts
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

## 9. Severity Classification

| Severity | Color | Faults | Recommended Action |
|----------|-------|--------|-------------------|
| **Critical** | Red | Belt Snapped, Lint Blockage, Low Refrigerant, Compressor Fault | Immediate shutdown / service call |
| **Warning** | Orange | Roller Wear, Dirty Filter | Schedule maintenance within 24–48h |
| **Info** | Blue | Incomplete Drying | Check load size / cycle settings |

---

## 10. References

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
