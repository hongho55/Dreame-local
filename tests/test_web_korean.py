"""Korean display labels must not change robot command values."""
import importlib
from html.parser import HTMLParser
from pathlib import Path
import re
import sys
from types import SimpleNamespace
import unittest

from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dreame_local" / "app"))
ui = importlib.import_module("web")


class Controls(HTMLParser):
    def __init__(self):
        super().__init__()
        self.language = None
        self.commands = []
        self.options = {}
        self.select = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "html":
            self.language = attrs.get("lang")
        if tag == "button" and "data-cmd" in attrs:
            self.commands.append(attrs["data-cmd"])
        if tag == "select":
            self.select = attrs["id"]
            self.options[self.select] = []
        if tag == "option" and self.select:
            # Values MUST be explicit: translated option text is not a protocol value.
            self.options[self.select].append(attrs.get("value"))

    def handle_endtag(self, tag):
        if tag == "select":
            self.select = None


class KoreanUITests(unittest.IsolatedAsyncioTestCase):
    async def test_both_routes_serve_korean_html(self):
        async with TestClient(TestServer(ui.Web(SimpleNamespace()).app)) as client:
            for path, heading in (("/", "드리미 로컬"), ("/rooms", "지도·방 관리")):
                with self.subTest(path=path):
                    response = await client.get(path)
                    self.assertEqual(response.status, 200)
                    html = await response.text()
                    parser = Controls()
                    parser.feed(html)
                    self.assertEqual(parser.language, "ko")
                    self.assertIn(heading, html)

    async def test_command_names_and_option_values_preserved(self):
        parser = Controls()
        parser.feed(ui.PAGE)
        self.assertEqual(parser.commands, ["start", "pause", "stop", "return_to_base", "locate"])
        self.assertEqual(parser.options, {
            "suction": ["", "quiet", "standard", "strong", "turbo"],
            "water": ["", "low", "medium", "high"],
            "mode": ["", "sweeping", "mopping", "sweeping_and_mopping", "mopping_after_sweeping"],
        })

    async def test_german_display_strings_removed(self):
        german = r"Räume|Raum |Karte|Sperr|Wisch|Virtuell|Reinig|Teilen|Zonen|Fehler|keine|nicht|Schnell|Aktualisieren|Umbenennen|Zurück"
        for html in (ui.PAGE, ui.ROOMS_PAGE):
            self.assertIsNone(re.search(german, html))
        for text in ("방 이름 변경", "방 합치기", "방 나누기", "진입 금지 구역", "물걸레 금지 구역", "가상 벽", "지도 백업"):
            self.assertIn(text, ui.ROOMS_PAGE)

    async def test_confirmations_and_status_labels_are_korean(self):
        for text in ("모든 구역 설정을 삭제할까요?", "지금 청소할까요?", "로봇을 이동시켜 빠른 지도 작성을 시작할까요?"):
            self.assertIn(text, ui.ROOMS_PAGE)
        self.assertIn("charging_completed:'충전 완료'", ui.PAGE)
        self.assertIn("label(s.state)", ui.PAGE)
        self.assertIn("document.getElementById('mode').value = s.mode", ui.PAGE)

    async def test_empty_map_error_is_korean_without_robot_access(self):
        instance = ui.Web(SimpleNamespace())
        instance._load_map = lambda: None
        async with TestClient(TestServer(instance.app)) as client:
            response = await client.get("/api/rooms")
            self.assertEqual(await response.json(), {"ok": False, "error": "저장된 지도 데이터를 찾을 수 없습니다"})


if __name__ == "__main__":
    unittest.main()
