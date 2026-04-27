-- Migration: Add dryer heat-rise baseline columns to appliances table
-- Required by do_set_baseline_calculated() in app.py for Gas Dryer SPC logic

ALTER TABLE appliances
    ADD COLUMN IF NOT EXISTS baseline_heat_rise_mean REAL,
    ADD COLUMN IF NOT EXISTS baseline_heat_rise_std REAL;
