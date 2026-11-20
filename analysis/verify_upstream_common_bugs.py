"""Check shared regressions on a public upstream snapshot, without certs/data.

Run with the same aiohttp test environment as unittest. A failing upstream suite
is expected: assertions describe fixed behavior. No robot/network commands occur;
a loopback aiohttp TestServer is the only networking in the regression suite.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
FILES = ("api.py", "device.py", "events.py", "web.py", "mapping.json")
TESTS = ("test_api_map_angle.py", "test_state_sync.py", "test_web_map.py", "test_common_web_controls.py")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--revision", default="upstream/main")
    parser.add_argument("--angle-fix-only", action="store_true", help="Apply only the two-byte fix to the temporary upstream snapshot and run angle regressions")
    args = parser.parse_args()
    revision = subprocess.check_output(["git", "rev-parse", args.revision], cwd=ROOT, text=True).strip()
    with tempfile.TemporaryDirectory(prefix="dreame-common-repro-", dir=os.environ.get("TMPDIR")) as directory:
        stage = Path(directory)
        app = stage / "dreame_local" / "app"
        app.mkdir(parents=True)
        tests = stage / "tests"
        tests.mkdir()
        evidence = {}
        for name in FILES:
            data = subprocess.check_output(["git", "show", f"{revision}:dreame_local/app/{name}"], cwd=ROOT)
            (app / name).write_bytes(data)
            lines = data.decode().splitlines()
            terms = ("raw[9] =", "return_to_base", "cleaning_time", 'get("SUCTION_LEVEL") or 2', '"ok": True, "result": res', 'text.startswith("{")', 'f"ali_dreame/{self.dev.uid}/{self.dev.did}"')
            evidence[name] = [{"line": i, "text": line.strip()} for i, line in enumerate(lines, 1) if any(term in line for term in terms)]
        if args.angle_fix_only:
            path = app / "api.py"
            text = path.read_text()
            old = "raw[9] = lraw[9]"
            assert text.count(old) == 1
            path.write_text(text.replace(old, "raw[9:11] = lraw[9:11]"))
        for name in TESTS[:1] if args.angle_fix_only else TESTS:
            shutil.copyfile(ROOT / "tests" / name, tests / name)
        runner = '''import io,json,sys,unittest
suite=unittest.defaultTestLoader.discover("tests")
r=unittest.TextTestRunner(stream=io.StringIO(),verbosity=0).run(suite)
print(json.dumps({"tests_run":r.testsRun,"failures":[{"test":str(t),"reason":tb.splitlines()[-1]} for t,tb in r.failures],"errors":[{"test":str(t),"reason":tb.splitlines()[-1]} for t,tb in r.errors],"skipped":len(r.skipped)}))
'''
        run = subprocess.run([sys.executable, "-c", runner], cwd=stage, text=True, capture_output=True, check=True)
        result = json.loads(run.stdout)
        if result["errors"] or (args.angle_fix_only and result["failures"]):
            raise RuntimeError(json.dumps(result))
        print(json.dumps({"upstream_revision": revision, "angle_fix_applied": args.angle_fix_only, "source_evidence": evidence, "regressions": result}, indent=2))


if __name__ == "__main__":
    main()
