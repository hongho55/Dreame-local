"""X40 web controls: real handlers, synthetic device and intercepted wire only."""
import importlib
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock

from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dreame_local" / "app"))
Device = importlib.import_module("device").Device
PROPS = importlib.import_module("device").PROPS
Web = importlib.import_module("web").Web


class X40WebControlTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dev = Device("SYNTHETIC_DID", "SYNTHETIC_UID", "dreame.vacuum.r2401", "cn", AsyncMock(), data_dir=Path(self.tmp.name))
        self.dev.send = AsyncMock(return_value={"code": 0})
        self.put("CLEANING_MODE", 5120)
        self.put("TASK_STATUS", 0)
        self.put("STATE", 13)

    def put(self, prop, value):
        p = PROPS[prop]
        self.dev.props[f"{p['siid']}.{p['piid']}"] = value

    async def post(self, body):
        async with TestClient(TestServer(Web(self.dev).app)) as client:
            response = await client.post("/api/command", json=body)
            return response.status, await response.json()

    def assert_wire_property(self, prop, value):
        p = PROPS[prop]
        self.dev.send.assert_awaited_once_with("set_properties", [{"did": "SYNTHETIC_DID", "siid": p["siid"], "piid": p["piid"], "value": value}])

    async def test_sequential_mode_uses_packed_value_not_bare_three(self):
        status, result = await self.post({"set": "CLEANING_MODE", "value": "mopping_after_sweeping"})
        self.assertEqual(status, 200)
        self.assertTrue(result["ok"])
        self.assert_wire_property("CLEANING_MODE", 5123)
        self.assertEqual(self.dev.get("CLEANING_MODE"), 5120, "No optimistic cache overwrite")

    async def test_all_modes_preserve_independent_packed_settings(self):
        for raw in (5120, 5121, 5122, 5123, (2 << 16) | (25 << 8)):
            for logical, bits in ((0, 2), (1, 1), (2, 0), (3, 3)):
                with self.subTest(raw=raw, logical=logical):
                    self.put("CLEANING_MODE", raw)
                    self.dev.send.reset_mock()
                    await self.dev.set_cleaning_mode(logical)
                    encoded = (raw & ~3) | bits
                    self.assert_wire_property("CLEANING_MODE", encoded)
                    self.assertEqual(encoded & ~3, raw & ~3)
                    self.put("CLEANING_MODE", encoded)
                    self.assertEqual(self.dev.cleaning_mode(), logical)

    async def test_snapshot_decodes_current_mode_and_fine_water(self):
        self.put("WETNESS_LEVEL", 23)
        snap = self.dev.snapshot()
        self.assertEqual(snap["mode"], "sweeping_and_mopping")
        self.assertEqual(snap["wetness_level"], 23)
        self.assertEqual(snap["water"], "medium")
        self.assertTrue(snap["wetness_supported"])
        for value, coarse in ((5, "low"), (16, "medium"), (27, "high")):
            self.put("WETNESS_LEVEL", value)
            self.assertEqual(self.dev.snapshot()["water"], coarse)
        self.dev.send.assert_not_awaited()

    async def test_missing_packed_state_never_writes_defaults(self):
        for value in (None, -1, "5120", True):
            self.put("CLEANING_MODE", value)
            status, result = await self.post({"set": "CLEANING_MODE", "value": "mopping_after_sweeping"})
            self.assertFalse(result["ok"])
            self.assertEqual(status, 500)
        self.dev.send.assert_not_awaited()

    async def test_running_task_cannot_enter_sequential_mode(self):
        self.put("STATE", 1)
        self.put("TASK_STATUS", 1)
        _, result = await self.post({"set": "CLEANING_MODE", "value": "mopping_after_sweeping"})
        self.assertFalse(result["ok"])
        self.dev.send.assert_not_awaited()

    async def test_fine_water_writes_correct_x40_property(self):
        _, result = await self.post({"set": "WETNESS_LEVEL", "value": 23})
        self.assertTrue(result["ok"])
        self.assert_wire_property("WETNESS_LEVEL", 23)

    async def test_coarse_water_uses_hacs_x40_wetness_translation(self):
        for name, value in (("low", 5), ("medium", 16), ("high", 27)):
            self.dev.send.reset_mock()
            _, result = await self.post({"set": "WATER_VOLUME", "value": name})
            self.assertTrue(result["ok"])
            self.assert_wire_property("WETNESS_LEVEL", value)

    async def test_wetness_rejects_invalid_or_secondary_setting_changes(self):
        for value in (0, 33, 1.5, True, "23", None):
            _, result = await self.post({"set": "WETNESS_LEVEL", "value": value})
            self.assertFalse(result["ok"])
        self.put("CLEANING_MODE", 25 << 8)
        _, result = await self.post({"set": "WETNESS_LEVEL", "value": 27})
        self.assertFalse(result["ok"])
        self.dev.send.assert_not_awaited()

    async def test_return_button_resolves_to_actual_charge_action(self):
        _, result = await self.post({"action": "return_to_base"})
        self.assertTrue(result["ok"])
        self.dev.send.assert_awaited_once_with("action", {"did": "SYNTHETIC_DID", "siid": 3, "aiid": 1, "in": []})

    async def test_robot_rejection_is_not_reported_as_ok(self):
        for response in ({"code": -1}, [{"code": -4004}]):
            self.dev.send.return_value = response
            _, result = await self.post({"set": "SUCTION_LEVEL", "value": "standard"})
            self.assertFalse(result["ok"])
            self.assertEqual(result["result"], response)
            self.assertIn("거절", result["error"])

    async def test_raw_hacs_command_is_not_double_encoded(self):
        raw = {"did": "SYNTHETIC_DID", "siid": 4, "piid": 23, "value": 5123}
        await self.post({"method": "set_properties", "params": [raw]})
        self.dev.send.assert_awaited_once_with("set_properties", [raw])

    async def test_non_x40_legacy_mode_and_water_are_unchanged(self):
        self.dev.model = "SYNTHETIC_OTHER_MODEL"
        self.put("CLEANING_MODE", 1)
        self.assertEqual(self.dev.snapshot()["mode"], "mopping")
        await self.dev.set_cleaning_mode(2)
        self.assert_wire_property("CLEANING_MODE", 2)
        self.dev.send.reset_mock()
        await self.dev.set_water_volume(3)
        self.assert_wire_property("WATER_VOLUME", 3)


if __name__ == "__main__":
    unittest.main()
