"""Intercepted-wire guards; real moving-robot timing is verified separately."""
import asyncio
import importlib
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'dreame_local' / 'app'))
device_module = importlib.import_module('device')
Device, PROPS, STATES = device_module.Device, device_module.PROPS, device_module.STATES
live_map_refresh = importlib.import_module('main').live_map_refresh


class LiveMapRequestTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.device = Device('SYNTHETIC_DID', 'SYNTHETIC_UID', 'dreame.vacuum.r2401', 'cn', AsyncMock(), data_dir=Path(self.tmp.name))
        self.device.online = True
        self.device.send = AsyncMock(return_value={'code': 0})
        self.put('STATE', STATES['SWEEPING'])
        self.put('TASK_STATUS', 1)
        self.device.map_path = 'synthetic-existing-path'

    def put(self, prop, value):
        p = PROPS[prop]
        self.device.props[f"{p['siid']}.{p['piid']}"] = value

    async def test_only_read_map_rpc_and_no_path_or_cache_change(self):
        self.device.send.return_value = {'code': 0, 'out': [{'piid': 1, 'value': 'stale-api-reply'}]}
        before = dict(self.device.props)
        self.assertTrue(await self.device.refresh_live_map())
        self.device.send.assert_awaited_once_with('action', {
            'did': 'SYNTHETIC_DID', 'siid': 6, 'aiid': 1,
            'in': [{'piid': 2, 'value': '{"frame_type":"I"}'}],
        }, timeout=2.5)
        self.assertEqual(self.device.props, before)
        self.assertEqual(self.device.map_path, 'synthetic-existing-path')

    async def test_idle_paused_returning_and_station_states_do_not_request(self):
        for state in ('IDLE', 'PAUSED', 'RETURNING', 'CHARGING', 'CHARGING_COMPLETED', 'WASHING_PAUSED'):
            with self.subTest(state=state):
                self.put('STATE', STATES[state])
                self.assertFalse(await self.device.refresh_live_map())
        self.device.send.assert_not_awaited()

    async def test_missing_completed_or_invalid_task_does_not_request(self):
        for task in (None, 0, -1, '1', True):
            self.put('TASK_STATUS', task)
            self.assertFalse(await self.device.refresh_live_map())
        self.device.send.assert_not_awaited()

    async def test_offline_and_unverified_models_do_not_request(self):
        self.device.online = False
        self.assertFalse(await self.device.refresh_live_map())
        self.device.online = True
        self.device.model = 'dreame.vacuum.unverified'
        self.assertFalse(await self.device.refresh_live_map())
        self.device.send.assert_not_awaited()

    async def test_failed_reply_is_reported_without_retry(self):
        self.device.send.return_value = {'code': -1}
        with self.assertRaises(RuntimeError):
            await self.device.refresh_live_map()
        self.assertEqual(self.device.send.await_count, 1)


    async def test_refresh_still_finds_ha_after_robot_account_discovery(self):
        self.device = Device('SYNTHETIC_DID', '', 'dreame.vacuum.r2401', 'cn', AsyncMock(), data_dir=Path(self.tmp.name))
        self.device.online = True
        self.device.send = AsyncMock(return_value={'code': 0})
        self.put('STATE', STATES['SWEEPING'])
        self.put('TASK_STATUS', 1)
        subscription = self.device.status_topic
        self.device.uid = 'SYNTHETIC_DISCOVERED_UID'
        broker = SimpleNamespace(clients=lambda: [{'client_id': 'ha-test', 'topics': [subscription]}])
        with patch('main.asyncio.sleep', new_callable=AsyncMock, side_effect=asyncio.CancelledError):
            with self.assertRaises(asyncio.CancelledError):
                await live_map_refresh(self.device, broker)
        self.device.send.assert_awaited_once()


class LiveMapLoopTests(unittest.IsolatedAsyncioTestCase):
    async def iteration(self, clients, error=None):
        device = SimpleNamespace(status_topic='/status/test//model/cn/', refresh_live_map=AsyncMock(side_effect=error))
        broker = SimpleNamespace(clients=lambda: clients)
        with patch('main.asyncio.sleep', new_callable=AsyncMock, side_effect=asyncio.CancelledError) as sleep:
            with self.assertRaises(asyncio.CancelledError):
                await live_map_refresh(device, broker)
        return device, sleep

    async def test_connected_status_consumer_gets_one_request(self):
        device, sleep = await self.iteration([{'client_id': 'ha-test', 'topics': ['/status/test/+/model/cn/']}])
        device.refresh_live_map.assert_awaited_once_with()
        self.assertGreater(sleep.call_args.args[0], 0.8)
        self.assertLessEqual(sleep.call_args.args[0], 1.0)

    async def test_no_consumer_or_wrong_topic_does_not_request(self):
        for clients in ([], [{'client_id': 'ha-test', 'topics': ['/status/other/+/model/cn/']}], [{'client_id': 'device_test', 'topics': ['#']}]):
            device, _ = await self.iteration(clients)
            device.refresh_live_map.assert_not_awaited()

    async def test_error_backs_off_and_cancel_does_not_retry(self):
        device, sleep = await self.iteration([{'client_id': 'ha-test', 'topics': ['#']}], TimeoutError())
        device.refresh_live_map.assert_awaited_once_with()
        self.assertGreater(sleep.call_args.args[0], 4.8)
        self.assertLessEqual(sleep.call_args.args[0], 5.0)

    async def test_inflight_cancellation_exits_without_sleep(self):
        device = SimpleNamespace(status_topic='/status/test//model/cn/', refresh_live_map=AsyncMock(side_effect=asyncio.CancelledError))
        broker = SimpleNamespace(clients=lambda: [{'client_id': 'ha-test', 'topics': ['#']}])
        with patch('main.asyncio.sleep', new_callable=AsyncMock) as sleep:
            with self.assertRaises(asyncio.CancelledError):
                await live_map_refresh(device, broker)
            sleep.assert_not_awaited()
