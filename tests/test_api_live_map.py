"""Synthetic live-I vs docked-restoration regression; no robot connection."""
import base64
import json
import unittest
import zlib
from types import SimpleNamespace
from test_api_map_angle import frame, decode
from api import DreameApi


class LiveMapBaseTests(unittest.TestCase):
    def api(self, live):
        api = DreameApi.__new__(DreameApi)
        api.dev = SimpleNamespace(props={"6.1": live} if live else {}, map_path="")
        return api

    def live(self, *, kind=ord("P"), task: object = 1):
        raw = bytearray(decode(frame(90, frame_id=300)))
        raw[4] = kind
        api = self.api(base64.b64encode(zlib.compress(raw)).decode())
        api.dev.props["4.1"] = task
        return api

    def restored_flag(self, api):
        raw = decode(api._as_current_map(frame(-90)))
        return json.loads(raw[39:])["rpur"]

    def test_active_live_p_can_be_a_continuing_frame_base(self):
        self.assertEqual(self.restored_flag(self.live()), 0)

    def test_active_live_i_can_be_a_continuing_frame_base(self):
        self.assertEqual(self.restored_flag(self.live(kind=ord("I"))), 0)

    def test_docked_old_live_frame_remains_a_restoration(self):
        self.assertEqual(self.restored_flag(self.live(task=0)), 1)

    def test_unknown_task_or_frame_type_remains_a_restoration(self):
        for task in (None, "1", True, -1):
            with self.subTest(task=task):
                self.assertEqual(self.restored_flag(self.live(task=task)), 1)
        self.assertEqual(self.restored_flag(self.live(kind=0)), 1)

    def test_missing_live_header_remains_a_restoration(self):
        api = self.api(None)
        api.dev.props["4.1"] = 1
        self.assertEqual(self.restored_flag(api), 1)


if __name__ == "__main__":
    unittest.main()
