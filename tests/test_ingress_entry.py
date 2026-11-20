"""Exercise the configured entry with Supervisor's literal URL join."""
import importlib
import os
from pathlib import Path
import re
import sys
from types import SimpleNamespace
import unittest
from urllib.parse import urljoin

from aiohttp.test_utils import TestClient, TestServer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dreame_local" / "app"))
Web = importlib.import_module("web").Web


class IngressEntryTests(unittest.IsolatedAsyncioTestCase):
    async def test_supervisor_generated_entry_reaches_state_controls(self):
        path = Path(os.environ.get("DREAME_CONFIG", ROOT / "dreame_local" / "config.yaml"))
        match = re.search(r"^ingress_entry:\s*([^\n]+)", path.read_text(), re.MULTILINE)
        if match is None:
            self.fail("ingress_entry missing from add-on configuration")
        entry = match.group(1).strip().strip("\"'")
        prefix = "/api/hassio_ingress/SYNTHETIC/"
        # Supervisor app.ingress_url appends the configured entry literally.
        ingress_url = prefix + entry
        upstream_path = "/" + ingress_url.removeprefix(prefix)
        ui = Web(SimpleNamespace())
        async with TestClient(TestServer(ui.app)) as client:
            response = await client.session.get(f"http://127.0.0.1:{client.server.port}{upstream_path}")
            self.assertEqual(response.status, 200, f"generated upstream path: {upstream_path}")
            self.assertIn("상태·제어", await response.text())
            self.assertEqual(upstream_path, "/")
            rooms = await client.get("/rooms")
            self.assertEqual(rooms.status, 200)
            self.assertIn("api/map.png", await rooms.text())
        self.assertEqual(urljoin(ingress_url, "api/rooms"), prefix + "api/rooms")
        self.assertEqual(urljoin(ingress_url, "api/map.png"), prefix + "api/map.png")
        self.assertEqual(urljoin(ingress_url, "rooms"), prefix + "rooms")


if __name__ == "__main__":
    unittest.main()
