"""Lightweight smoke test for telemetry handlers (no DB required)."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Never let tests connect to the shared MQTT broker: each connection leaves a
# 1-hour persistent session behind (~60 wasted session-minutes per test run).
os.environ.setdefault("IOT_DISABLE_MQTT", "1")

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


if __name__ == "__main__":
    test_hvac_telemetry()
    test_dryer_telemetry()
    print("All telemetry smoke tests passed")
