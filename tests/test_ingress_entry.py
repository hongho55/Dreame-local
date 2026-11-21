"""Exercise the configured entry with Supervisor's literal URL join."""
import ast
import importlib
import importlib.util
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
    def test_runtime_version_matches_addon_metadata(self):
        path = Path(os.environ.get("DREAME_CONFIG", ROOT / "dreame_local" / "config.yaml"))
        match = re.search(r'^version:\s*["\']?([^\s"\']+)', path.read_text(), re.MULTILINE)
        assert match is not None, "version missing from add-on metadata"
        expected = match.group(1)
        # Parse source without importing main (no startup, config or TLS reads).
        spec = importlib.util.find_spec("main")
        assert spec is not None and spec.origin is not None
        source = Path(spec.origin).read_text()
        tree = ast.parse(source)
        version = next(ast.literal_eval(node.value) for node in tree.body
                       if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "VERSION" for t in node.targets))
        self.assertEqual(version, expected)

    def test_addon_has_no_ingress_or_sidebar_registration(self):
        path = Path(os.environ.get("DREAME_CONFIG", ROOT / "dreame_local" / "config.yaml"))
        source = path.read_text()
        self.assertRegex(source, r"(?m)^ingress: false$")
        self.assertIsNone(re.search(r"(?m)^(panel_icon|panel_title|panel_admin|ingress_port|ingress_entry):", source))

    async def test_old_ingress_prefix_is_not_an_alternate_ui(self):
        async with TestClient(TestServer(Web(SimpleNamespace()).app)) as client:
            for path in ("/api/hassio_ingress/SYNTHETIC/", "/api/hassio_ingress/SYNTHETIC/rooms"):
                self.assertEqual((await client.get(path)).status, 404)


if __name__ == "__main__":
    unittest.main()
