"""Synthetic regression for a robot UID vs legacy empty-UID HACS subscription.

No network, identity, credentials or keys. The real route is verified separately.
"""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dreame_local" / "app"))
spec = importlib.util.find_spec("main")
assert spec is not None and spec.origin is not None
SOURCE = Path(spec.origin)


class StatusTopicAliasTests(unittest.TestCase):
    def setUp(self):
        tree = ast.parse(SOURCE.read_text())
        function = next((n for n in tree.body if isinstance(n, ast.FunctionDef)
                         and n.name == "status_topic_alias"), None)
        self.assertIsNotNone(function, "Missing legacy HACS status-topic compatibility")
        assert function is not None
        namespace = {}
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(SOURCE), "exec"), namespace)
        self.alias = namespace["status_topic_alias"]
        self.canonical = "/status/test-device//dreame.vacuum.test/cn/"
        self.incoming = "/status/test-device/robot-account/dreame.vacuum.test/cn/"

    def test_aliases_only_the_missing_configured_account(self):
        self.assertEqual(self.alias(self.incoming, self.canonical), self.canonical)

    def test_exact_topic_is_not_duplicated(self):
        self.assertIsNone(self.alias(self.canonical, self.canonical))

    def test_configured_nonempty_account_is_not_bypassed(self):
        self.assertIsNone(self.alias(self.incoming,
            self.canonical.replace("//", "/configured-account/")))

    def test_other_devices_models_regions_or_kinds_are_not_aliased(self):
        for original, replacement in (("test-device", "other-device"),
                                      ("dreame.vacuum.test", "other-model"),
                                      ("/cn/", "/eu/"), ("/status/", "/ctrl/")):
            with self.subTest(replacement=replacement):
                self.assertIsNone(self.alias(self.incoming.replace(original, replacement), self.canonical))

    def test_malformed_or_wildcard_topics_are_not_aliased(self):
        for topic in ("", self.incoming.rstrip("/"), self.incoming + "extra",
                      self.incoming.replace("robot-account", "+"),
                      self.incoming.replace("robot-account", "#")):
            with self.subTest(topic=topic):
                self.assertIsNone(self.alias(topic, self.canonical))

    def test_unknown_or_empty_device_is_not_aliased(self):
        for device in ("", "unknown"):
            with self.subTest(device=device):
                self.assertIsNone(self.alias(self.incoming.replace("test-device", device),
                                             self.canonical.replace("test-device", device)))


class StatusForwardingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, Mock
        tree = ast.parse(SOURCE.read_text())
        alias = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                     and n.name == "status_topic_alias")
        callback = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)
                        and n.name == "on_publish")
        self.canonical = "/status/test-device//dreame.vacuum.test/cn/"
        self.incoming = "/status/test-device/robot-account/dreame.vacuum.test/cn/"
        self.device = SimpleNamespace(on_message=AsyncMock(), status_topic=self.canonical, props={"4.1": 1})
        self.broker = SimpleNamespace(publish=AsyncMock(return_value=1),
                                      clients=Mock(return_value=[{"topics": [self.canonical]}]))
        self.api = SimpleNamespace(_live_map_blob=Mock(return_value="synthetic-live-I"))
        self.seeded = set()
        self.transform = Mock(return_value=b"synthetic-transformed-map")
        namespace = {"device": self.device, "broker": self.broker, "api": self.api,
                     "seeded_status_topics": self.seeded, "json": __import__("json"),
                     "map_transform": self.transform, "log": SimpleNamespace(debug=Mock())}
        exec(compile(ast.Module(body=[alias, callback], type_ignores=[]), str(SOURCE), "exec"), namespace)
        self.callback = namespace["on_publish"]

    async def test_push_is_processed_then_transformed_and_forwarded(self):
        payload = b"synthetic-robot-map"
        await self.callback(self.incoming, payload)
        self.device.on_message.assert_awaited_once_with(self.incoming, payload)
        self.transform.assert_called_once_with(self.incoming, payload)
        self.broker.publish.assert_awaited_once_with(self.canonical, b"synthetic-transformed-map")

    async def test_account_discovery_keeps_legacy_subscriber_receiving_status(self):
        import json
        from tempfile import TemporaryDirectory
        from unittest.mock import AsyncMock
        Device = __import__('device').Device
        with TemporaryDirectory() as directory:
            real = Device('test-device', '', 'dreame.vacuum.test', 'cn', AsyncMock(return_value=1), data_dir=Path(directory))
            real.poll_all = AsyncMock()
            before = real.status_topic
            await real._handle_pair(json.dumps({'data': {'method': 'dev_start', 'params': {'uid': 'robot-account'}}}).encode())
            self.assertEqual(real.uid, 'robot-account')
            self.assertIn('/robot-account/', real.recv_topic)
            self.assertEqual(real.status_topic, before)
            self.device.on_message = real.on_message
            self.device.props = real.props
            self.device.status_topic = real.status_topic
            payload = json.dumps({'data': {'method': 'properties_changed', 'params': [
                {'siid': 3, 'piid': 2, 'value': 80}]}}).encode()
            await self.callback(self.incoming, payload)
            self.broker.publish.assert_awaited_once_with(before, b'synthetic-transformed-map')

    async def test_explicit_status_account_is_not_replaced_by_discovered_account(self):
        from tempfile import TemporaryDirectory
        from unittest.mock import AsyncMock
        Device = __import__('device').Device
        with TemporaryDirectory() as directory:
            real = Device('test-device', 'configured-account', 'dreame.vacuum.test', 'cn', AsyncMock(), data_dir=Path(directory))
            before = real.status_topic
            real.uid = 'robot-account'
            self.assertEqual(real.status_topic, before)

    async def test_canonical_push_is_not_looped_or_duplicated(self):
        await self.callback(self.canonical, b"synthetic")
        self.transform.assert_not_called()
        self.broker.publish.assert_not_awaited()

    def map_payload(self):
        import json
        return json.dumps({"data": {"method": "properties_changed", "params": [
            {"siid": 6, "piid": 1, "value": "synthetic-P"}]}}).encode()

    async def test_first_live_map_gets_one_i_base_before_p_frames(self):
        import json
        for _ in range(3):
            await self.callback(self.incoming, self.map_payload())
        calls = self.broker.publish.await_args_list
        self.assertEqual(len(calls), 4)
        self.assertEqual(json.loads(calls[0].args[1])["data"]["params"][0]["value"], "synthetic-live-I")
        self.assertTrue(all(c.args == (self.canonical, b"synthetic-transformed-map") for c in calls[1:]))
        self.api._live_map_blob.assert_called_once()

    async def test_idle_resets_the_base_for_the_next_task(self):
        await self.callback(self.incoming, self.map_payload())
        self.device.props["4.1"] = 0
        await self.callback(self.canonical, b"synthetic-idle")
        self.assertFalse(self.seeded)
        self.device.props["4.1"] = 1
        await self.callback(self.incoming, self.map_payload())
        self.assertEqual(self.api._live_map_blob.call_count, 2)

    async def test_missing_i_is_not_marked_seeded_and_can_retry(self):
        self.api._live_map_blob.return_value = ""
        await self.callback(self.incoming, self.map_payload())
        self.assertFalse(self.seeded)
        self.broker.publish.assert_awaited_once_with(self.canonical, b"synthetic-transformed-map")

    async def test_observer_wildcard_does_not_consume_hacs_bootstrap(self):
        self.broker.clients.return_value = [{"topics": ["/status/#"]}]
        await self.callback(self.incoming, self.map_payload())
        self.api._live_map_blob.assert_not_called()
        self.assertFalse(self.seeded)

    async def test_ordinary_properties_do_not_replace_the_camera_base(self):
        import json
        payload = json.dumps({"data": {"method": "properties_changed", "params": [
            {"siid": 3, "piid": 2, "value": 80}]}}).encode()
        await self.callback(self.incoming, payload)
        self.api._live_map_blob.assert_not_called()
        self.broker.publish.assert_awaited_once()

    async def test_other_device_is_not_forwarded_to_this_subscription(self):
        await self.callback(self.incoming.replace("test-device", "other-device"), b"synthetic")
        self.broker.publish.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
