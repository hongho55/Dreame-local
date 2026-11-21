"""HA-only backend: removed UI must stay absent while REST remains intact."""
import importlib
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dreame_local" / "app"))
backend = importlib.import_module("web")

class HAOnlyBackendTests(unittest.IsolatedAsyncioTestCase):
    def test_no_bundled_html_css_or_javascript(self):
        source = Path(backend.__file__).read_text()
        for marker in ("<!doctype html>", "<script>", "<style>"):
            self.assertNotIn(marker, source)
        self.assertFalse(hasattr(backend, "PAGE"))
        self.assertFalse(hasattr(backend, "ROOMS_PAGE"))
        self.assertFalse(hasattr(backend, "ingress_middleware"))

    async def test_standalone_pages_and_assets_return_404(self):
        async with TestClient(TestServer(backend.Web(SimpleNamespace()).app)) as client:
            for path in ("/", "/rooms", "/index.html", "/static/app.js", "/static/style.css"):
                with self.subTest(path=path):
                    response = await client.get(path)
                    self.assertEqual(response.status, 404)
                    self.assertNotIn("<script", await response.text())

    async def test_rest_state_is_json_without_robot_calls(self):
        dev = SimpleNamespace(snapshot=lambda: {"online": True, "battery": 100})
        async with TestClient(TestServer(backend.Web(dev, version="synthetic").app)) as client:
            response = await client.get("/api/state")
            self.assertEqual(response.status, 200)
            self.assertEqual(response.content_type, "application/json")
            self.assertEqual(await response.json(), {"online": True, "battery": 100, "version": "synthetic"})

    async def test_empty_map_error_is_korean_without_robot_access(self):
        instance = backend.Web(SimpleNamespace())
        instance._load_map = lambda: None
        async with TestClient(TestServer(instance.app)) as client:
            response = await client.get("/api/rooms")
            self.assertEqual(await response.json(), {"ok": False, "error": "저장된 지도 데이터를 찾을 수 없습니다"})
