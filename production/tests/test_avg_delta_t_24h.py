"""Unit tests for the 24 h running-average delta-T on the HVAC device card.

models.compute_avg_running_delta_t averages the SIGNED delta-T
(treturn - tsupply, no abs) over readings where the compressor is running
(icompressor >= threshold), skipping the first `warmup_minutes` of each
idle->running segment so starting transients do not skew the value.

Pure-function tests — no DB or MQTT broker required.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Never let tests connect to the shared MQTT broker: each connection leaves a
# 1-hour persistent session behind (~60 wasted session-minutes per test run).
os.environ.setdefault("IOT_DISABLE_MQTT", "1")

from datetime import datetime, timedelta

from app import models

THRESHOLD = 0.25
WARMUP_MIN = 5
T0 = datetime(2026, 9, 26, 8, 0, 0)


def row(minute, treturn, tsupply, icomp):
    return {
        "time": T0 + timedelta(minutes=minute),
        "treturn": treturn,
        "tsupply": tsupply,
        "icompressor": icomp,
    }


def run_rows(start_min, count, step_min=1, delta=8.0, icomp=2.0):
    """`count` consecutive running readings with a constant delta-T."""
    base_supply = 20.0
    return [
        row(start_min + i * step_min, base_supply + delta, base_supply, icomp)
        for i in range(count)
    ]


def check(name, cond, failures):
    print(('PASS' if cond else 'FAIL') + ': ' + name)
    if not cond:
        failures.append(name)


def main():
    failures = []

    # 1. All idle -> None.
    rows = [row(i, 25.0, 20.0, 0.1) for i in range(30)]
    check('all idle -> None', models.compute_avg_running_delta_t(rows, THRESHOLD, WARMUP_MIN) is None, failures)

    # 2. One 10-minute run, 1-min spacing: the first 5 readings (minutes 0-4)
    #    are warmup, minutes 5-9 (5 readings, delta 8.0) are averaged.
    rows = run_rows(0, 10, step_min=1, delta=8.0)
    check('single run averages post-warmup only', models.compute_avg_running_delta_t(rows, THRESHOLD, WARMUP_MIN) == 8.0, failures)

    # 3. Warmup boundary: the reading exactly at +5 min IS included.
    rows = run_rows(0, 6, step_min=1, delta=8.0)  # minutes 0..5
    check('reading at exactly +5 min is included', models.compute_avg_running_delta_t(rows, THRESHOLD, WARMUP_MIN) == 8.0, failures)
    rows_short = run_rows(0, 5, step_min=1, delta=8.0)  # minutes 0..4 -> all warmup
    check('run shorter than warmup -> None', models.compute_avg_running_delta_t(rows_short, THRESHOLD, WARMUP_MIN) is None, failures)

    # 4. Two separate runs: warmup applies per segment. First run gives 8.0
    #    (minutes 5-9), second run gives 12.0 (minutes 15-19). Idle in between.
    rows = run_rows(0, 10, step_min=1, delta=8.0)
    rows += [row(10 + i, 25.0, 20.0, 0.0) for i in range(4)]  # idle minutes 10-13
    rows += run_rows(14, 10, step_min=1, delta=12.0)  # second run minutes 14-23
    check('warmup per segment (avg of 8.0 and 12.0)',
          models.compute_avg_running_delta_t(rows, THRESHOLD, WARMUP_MIN) == 10.0, failures)

    # 5. Window starting mid-run: the first visible row is treated as the
    #    segment start, so its own warmup runs from there.
    rows = run_rows(0, 10, step_min=1, delta=8.0)
    rows_windowed = rows[3:]  # starts at minute 3 -> warmup until minute 8
    check('window starting mid-run treats first row as segment start',
          models.compute_avg_running_delta_t(rows_windowed, THRESHOLD, WARMUP_MIN) == 8.0, failures)

    # 6. Signed values: negative delta-T averaged as-is (no abs).
    rows = run_rows(0, 10, step_min=1, delta=-6.0)
    check('negative delta-T kept signed', models.compute_avg_running_delta_t(rows, THRESHOLD, WARMUP_MIN) == -6.0, failures)

    # 7. Rows with NULL temperatures are skipped.
    rows = run_rows(0, 10, step_min=1, delta=8.0)
    rows[6]["treturn"] = None
    rows[7]["tsupply"] = None
    # minutes 5-9 originally 5 readings; 2 dropped -> 3 left, still all 8.0
    check('NULL temperature rows skipped', models.compute_avg_running_delta_t(rows, THRESHOLD, WARMUP_MIN) == 8.0, failures)

    # 8. Mixed deltas average correctly: minutes 0-4 warmup (8.0), qualifying
    #    minutes 5-9 -> 8,9,10,11,12 -> avg 10.0.
    rows = run_rows(0, 6, step_min=1, delta=8.0)
    for i, d in enumerate((9.0, 10.0, 11.0, 12.0)):
        rows.append(row(6 + i, 20.0 + d, 20.0, 2.0))
    check('mixed deltas average (10.0)', models.compute_avg_running_delta_t(rows, THRESHOLD, WARMUP_MIN) == 10.0, failures)

    # 9. None current counts as idle (segment reset).
    rows = run_rows(0, 10, step_min=1, delta=8.0)
    rows += [row(10 + i, 25.0, 20.0, None) for i in range(4)]  # current unknown = idle
    rows += run_rows(14, 10, step_min=1, delta=12.0)
    check('NULL current resets segment', models.compute_avg_running_delta_t(rows, THRESHOLD, WARMUP_MIN) == 10.0, failures)

    # --- compute_daily_running_averages (HVAC Daily Averages table) ---
    def row_at(dt, treturn, tsupply, icomp):
        return {"time": dt, "treturn": treturn, "tsupply": tsupply, "icompressor": icomp}

    DAY1 = datetime(2026, 9, 26, 8, 0, 0)

    # D1. Warmup excluded: minutes 0-4 are transient (supply 30), minutes 5-9
    #     steady (supply 20). If warmup were included avg_return would be 31.5.
    rows = [row_at(DAY1 + timedelta(minutes=m), 35.0, 30.0, 2.0) for m in range(5)]
    rows += [row_at(DAY1 + timedelta(minutes=m), 28.0, 20.0, 2.0) for m in range(5, 10)]
    check('daily: warmup excluded (post-warmup only)',
          models.compute_daily_running_averages(rows, THRESHOLD, WARMUP_MIN)
          == [{"date": "2026-09-26", "avg_return": 28.0, "avg_supply": 20.0}], failures)

    # D2. Per-day bucketing, newest first; idle reading resets the segment.
    rows = run_rows(0, 10, step_min=1, delta=8.0)          # day 1 run, steady supply 20
    rows += [row(10, 25.0, 20.0, 0.0)]                     # idle
    rows += [row(1440 + m, 30.0, 22.0, 2.0) for m in range(10)]  # day 2 run, supply 22
    check('daily: per-day buckets newest-first',
          models.compute_daily_running_averages(rows, THRESHOLD, WARMUP_MIN)
          == [
              {"date": "2026-09-27", "avg_return": 30.0, "avg_supply": 22.0},
              {"date": "2026-09-26", "avg_return": 28.0, "avg_supply": 20.0},
          ], failures)

    # D3. Run spanning midnight: warmup is measured from the REAL run start
    #     (23:57), NOT reset at the day boundary. 00:00/00:01 are still warmup,
    #     00:02 (+5 min exactly) and 00:03 qualify. Day 1 ends up with no
    #     qualifying readings at all -> absent from the result.
    start = datetime(2026, 9, 26, 23, 57, 0)
    rows = []
    for i, hhmm in enumerate([(23, 57), (23, 58), (23, 59), (0, 0), (0, 1)]):
        d = datetime(2026, 9, 26 + (1 if hhmm[0] < 12 else 0), hhmm[0], hhmm[1])
        rows.append(row_at(d, 35.0, 30.0, 2.0))            # warmup transient
    for hhmm in [(0, 2), (0, 3)]:
        d = datetime(2026, 9, 27, hhmm[0], hhmm[1])
        rows.append(row_at(d, 28.0, 20.0, 2.0))            # post-warmup
    check('daily: midnight run keeps warmup from real start',
          models.compute_daily_running_averages(rows, THRESHOLD, WARMUP_MIN)
          == [{"date": "2026-09-27", "avg_return": 28.0, "avg_supply": 20.0}], failures)

    # D4. Two runs in one day: warmup applies per segment.
    rows = [row_at(DAY1 + timedelta(minutes=m), 28.0, 20.0, 2.0) for m in range(10)]
    rows += [row_at(DAY1 + timedelta(minutes=10 + i), 25.0, 20.0, 0.0) for i in range(2)]  # idle
    rows += [row_at(DAY1 + timedelta(minutes=12 + m), 32.0, 24.0, 2.0) for m in range(10)]
    check('daily: per-segment warmup within a day',
          models.compute_daily_running_averages(rows, THRESHOLD, WARMUP_MIN)
          == [{"date": "2026-09-26", "avg_return": 30.0, "avg_supply": 22.0}], failures)

    # D5. Limit: only the `limit` newest days with data.
    rows = []
    for d in range(3):
        day = DAY1 + timedelta(days=d)
        rows += [row_at(day + timedelta(minutes=m), 28.0 + d, 20.0 + d, 2.0) for m in range(10)]
    limited = models.compute_daily_running_averages(rows, THRESHOLD, WARMUP_MIN, limit=2)
    check('daily: limit keeps newest days',
          [x["date"] for x in limited] == ["2026-09-28", "2026-09-27"], failures)

    # D6. All idle -> no days.
    rows = [row(i, 25.0, 20.0, 0.1) for i in range(30)]
    check('daily: all idle -> empty', models.compute_daily_running_averages(rows, THRESHOLD, WARMUP_MIN) == [], failures)

    if failures:
        print(f'\n{len(failures)} check(s) FAILED')
        sys.exit(1)
    print('\nAll avg-delta-T checks passed.')


if __name__ == '__main__':
    main()
