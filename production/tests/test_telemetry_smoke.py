"""Lightweight smoke test for telemetry handlers (no DB required)."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Never let tests connect to the shared MQTT broker: each connection leaves a
# 1-hour persistent session behind (~60 wasted session-minutes per test run).
os.environ.setdefault("IOT_DISABLE_MQTT", "1")

from datetime import datetime, timedelta
from unittest.mock import patch
from app import telemetry


def test_hvac_telemetry():
    node = {
        "id": 1,
        "mac_address": "AA:BB:CC:DD:EE:FF",
        "status": "paired",
        "appliance_id": 10,
    }
    appliance = {
        "id": 10,
        "type": "HVAC",
        "current_sensor": "ZHT103C",
        "treturn_offset": 0.0,
        "tsupply_offset": 0.0,
        "delta_t_lcl": 5.0,
        "delta_t_delay_minutes": 2,
        "baseline_configured": True,
        "alert_enabled": True,
    }
    with patch("app.telemetry.models.get_sensor_node_by_mac", return_value=node), \
         patch("app.telemetry.models.update_node_last_seen", return_value=True), \
         patch("app.telemetry.models.get_appliance", return_value=appliance), \
         patch("app.telemetry.models.insert_hvac_reading", return_value=1) as ins, \
         patch("app.telemetry.models.get_spc_baselines", return_value={}), \
         patch("app.telemetry.alerts.check_hvac_delta_t_alert") as chk, \
         patch("app.telemetry.alerts.sweep_dryer_cycles"):
        telemetry.handle_telemetry(
            "AA:BB:CC:DD:EE:FF",
            '{"DS1Temp":22.5,"DS2Temp":18.2,"CurrentA":2.1}',
        )
        assert ins.called, "HVAC reading not inserted"
        assert chk.called, "delta-T alert check not called"
    print("HVAC telemetry smoke OK")


def test_dryer_telemetry():
    node = {
        "id": 2,
        "mac_address": "11:22:33:44:55:66",
        "status": "paired",
        "appliance_id": 11,
    }
    appliance = {
        "id": 11,
        "type": "Gas Dryer",
        "current_sensor": "SCT013-015",
        "atmospheric_pressure": 1013.25,
        "baseline_configured": True,
        "alert_enabled": True,
    }
    with patch("app.telemetry.models.get_sensor_node_by_mac", return_value=node), \
         patch("app.telemetry.models.update_node_last_seen", return_value=True), \
         patch("app.telemetry.models.get_appliance", return_value=appliance), \
         patch("app.telemetry.models.insert_dryer_reading", return_value=1) as ins, \
         patch("app.telemetry.models.get_spc_baselines", return_value={}), \
         patch("app.telemetry.alerts.check_dryer_faults") as chk, \
         patch("app.telemetry.alerts.sweep_dryer_cycles"):
        telemetry.handle_telemetry(
            "11:22:33:44:55:66",
            '{"BME280Temp":55.0,"BME280Hum":45.0,"BME280Pres":1015.0,"CurrentA":3.5}',
        )
        assert ins.called, "Dryer reading not inserted"
        _, _, _, _, gauge, abs_p, current = ins.call_args[0]
        assert abs_p == 1015.0
        assert round(gauge, 2) == round(1015.0 - 1013.25, 2)
        assert chk.called, "dryer fault check not called"
    print("Dryer telemetry smoke OK")


def test_compute_actual_time():
    """Firmware has no RTC: sample time is reconstructed as
    server_now(WIB) - age. Pin the agoms/ago_ms/ago handling and the
    negative-age clamp (a node clock hiccup must never shift readings
    into the future)."""
    now = datetime(2026, 10, 30, 22, 0, 0)
    with patch("app.telemetry.config.now_wib", return_value=now):
        assert telemetry._compute_actual_time({"agoms": 2500}) == now - timedelta(milliseconds=2500)
        assert telemetry._compute_actual_time({"agoms": 0}) == now
        assert telemetry._compute_actual_time({"ago_ms": 10000}) == now - timedelta(seconds=10)
        assert telemetry._compute_actual_time({"ago": 7}) == now - timedelta(seconds=7)
        assert telemetry._compute_actual_time({}) == now
        # negative ages clamp to 0 (max(0, ...)) — never future timestamps
        assert telemetry._compute_actual_time({"agoms": -60000}) == now
        assert telemetry._compute_actual_time({"ago": -5}) == now
    print("compute_actual_time smoke OK")


def test_dedupe_window():
    """Reconnect bursts (broker-queued agoms:0 messages arriving together)
    are collapsed by the 1.0 s dedupe window: arrivals < 1 s apart are dropped,
    arrivals >= 1 s apart are kept — that is exactly why stored rows can sit
    1-2 s apart after a backend outage. Dedupe is per-MAC."""
    mac = "DE:DU:PE:00:00:01"
    mac2 = "DE:DU:PE:00:00:02"
    telemetry.DEDUPE_CACHE.clear()
    t0 = datetime(2026, 10, 30, 22, 0, 0)
    times = iter([t0, t0,                        # msg 1: kept
                  t0 + timedelta(seconds=0.5), t0 + timedelta(seconds=0.5),  # msg 2: dropped (< 1 s)
                  t0 + timedelta(seconds=1.5), t0 + timedelta(seconds=1.5),  # msg 3: kept (>= 1 s)
                  t0, t0])                      # msg 4 (other MAC): kept
    with patch("app.telemetry.config.now_wib", side_effect=lambda: next(times)), \
         patch("app.telemetry.models.get_sensor_node_by_mac", return_value=None), \
         patch("app.telemetry.models.register_unpaired_node", return_value=1), \
         patch("app.telemetry.models.get_node_by_id", return_value=None):
        telemetry.handle_telemetry(mac, '{"agoms":0}')
        assert telemetry.DEDUPE_CACHE.get(mac) == t0, "msg 1 should seed the dedupe cache"
        telemetry.handle_telemetry(mac, '{"agoms":0}')
        assert telemetry.DEDUPE_CACHE.get(mac) == t0, "msg 2 (< 1 s) must be dropped"
        telemetry.handle_telemetry(mac, '{"agoms":0}')
        assert telemetry.DEDUPE_CACHE.get(mac) == t0 + timedelta(seconds=1.5), "msg 3 (>= 1 s) must be kept"
        assert mac2 not in telemetry.DEDUPE_CACHE, "other MAC must be independent before its first message"
        telemetry.handle_telemetry(mac2, '{"agoms":0}')
        assert telemetry.DEDUPE_CACHE.get(mac2) == t0, "per-MAC independence: msg 4 kept"
    telemetry.DEDUPE_CACHE.clear()
    print("dedupe window smoke OK")


if __name__ == "__main__":
    test_hvac_telemetry()
    test_dryer_telemetry()
    test_compute_actual_time()
    test_dedupe_window()
    print("All telemetry smoke tests passed")
