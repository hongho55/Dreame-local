"""Read-only map parsing fixtures, without robot identity or real map data."""
import base64
import importlib
import json
from pathlib import Path
import sys
import unittest
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dreame_local" / "app"))
Web = importlib.import_module("web").Web


def blob():
    raw = bytearray(27)
    raw[19:21] = (4).to_bytes(2, "little")
    raw[21:23] = (3).to_bytes(2, "little")
    raw += bytes(range(12))
    raw += json.dumps({"timestamp_ms": 1000, "seg_inf": {}}).encode()
    return base64.b64encode(zlib.compress(raw)).decode()


class WebMapTests(unittest.TestCase):
    def test_existing_raw_frame(self):
        decoded = Web._decode_map_text(blob())
        self.assertEqual(decoded[1:3], (4, 3))
        self.assertEqual(decoded[0][27:39], bytes(range(12)))

    def test_map_list_wrapper_matches_raw_frame(self):
        self.assertEqual(Web._decode_map_text(json.dumps({"mapstr": [{"map": blob()}]})),
                         Web._decode_map_text(blob()))

    def test_wrapper_skips_invalid_candidate(self):
        wrapped = {"mapstr": [None, {"map": "broken"}, {"map": blob()}]}
        self.assertEqual(Web._decode_map_text(json.dumps(wrapped)), Web._decode_map_text(blob()))

    def test_invalid_wrappers_fail_closed(self):
        for text in ("{", "{}", '{"mapstr":null}', '{"mapstr":"invalid"}',
                     '{"mapstr":[{"map":null}]}', '{"mapstr":[{"map":"{}"}]}'):
            with self.subTest(text=text):
                self.assertIsNone(Web._decode_map_text(text))

    def test_empty_and_truncated_frame_fail_closed(self):
        for text in ("", "not a frame", base64.b64encode(zlib.compress(b"short")).decode()):
            with self.subTest(text=text):
                self.assertIsNone(Web._decode_map_text(text))

    def test_existing_urlsafe_and_frame_suffix(self):
        for text in (blob().replace("/", "_").replace("+", "-").rstrip("="), blob() + ",synthetic"):
            with self.subTest(text=text):
                self.assertEqual(Web._decode_map_text(text), Web._decode_map_text(blob()))


if __name__ == "__main__":
    unittest.main()
