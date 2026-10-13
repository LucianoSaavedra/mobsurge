"""Tests de autopilot/bootstrap.py (hook SessionStart), ejecutándolo de verdad por
stdin. El setup real se sustituye por un cli falso (SKILL_ROUTER_CLI) que solo
deja constancia de que se lanzó."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

LIB = Path(__file__).resolve().parents[2]
BOOT = LIB / "autopilot" / "bootstrap.py"
TMP_BASE = "/tmp/s2-plugin"

FAKE_CLI = """import sys, pathlib, time
args = sys.argv[1:]
proj = pathlib.Path(args[args.index('--project') + 1])
time.sleep(0.2)
(proj / 'fake_cli_ran.txt').write_text(' '.join(args))
print('fake setup ok')
"""


class BootstrapTest(unittest.TestCase):
    def setUp(self):
        os.makedirs(TMP_BASE, exist_ok=True)
        self.tmp = Path(tempfile.mkdtemp(prefix="boot-", dir=TMP_BASE))
        self.proj = self.tmp / "proj"
        self.proj.mkdir()
        self.fake = self.tmp / "fake_cli.py"
        self.fake.write_text(FAKE_CLI)
        self.env = {k: v for k, v in os.environ.items()
                    if k not in ("SKILL_ROUTER_CHILD", "SKILL_ROUTER_AUTOPILOT", "CLAUDE_PROJECT_DIR")}
        self.env["SKILL_ROUTER_CLI"] = str(self.fake)
        self.env["HOME"] = str(self.tmp / "home")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_boot(self, stdin: str, **env):
        e = dict(self.env)
        e.update(env)
        t0 = time.monotonic()
        r = subprocess.run([sys.executable, str(BOOT)], input=stdin, capture_output=True,
                           text=True, env=e, timeout=30)
        return r, time.monotonic() - t0

    def event(self, cwd=None):
        return json.dumps({"session_id": "x", "hook_event_name": "SessionStart", "source": "startup",
                           "cwd": str(cwd or self.proj)})

    def context(self, r):
        self.assertEqual(r.returncode, 0, r.stderr)
        if not r.stdout.strip():
            return None
        data = json.loads(r.stdout)
        hso = data["hookSpecificOutput"]
        self.assertEqual(hso["hookEventName"], "SessionStart")
        return hso["additionalContext"]

    def wait_for(self, path: Path, secs=10.0):
        end = time.monotonic() + secs
        while time.monotonic() < end:
            if path.exists():
                return True
            time.sleep(0.05)
        return False

    def test_new_project_spawns_background_setup(self):
        r, dt = self.run_boot(self.event())
        ctx = self.context(r)
        self.assertIn("segundo plano", ctx)
        self.assertLess(dt, 5)  # no espera al setup
        self.assertFalse((self.proj / "fake_cli_ran.txt").exists() and dt < 0.2)
        self.assertTrue(self.wait_for(self.proj / "fake_cli_ran.txt"))
        args = (self.proj / "fake_cli_ran.txt").read_text()
        self.assertIn("setup --auto --project " + str(self.proj.resolve()), args)
        self.assertTrue(self.wait_for(self.proj / ".claude/skill-router/setup.log"))
        end = time.monotonic() + 5
        while time.monotonic() < end and "fake setup ok" not in \
                (self.proj / ".claude/skill-router/setup.log").read_text():
            time.sleep(0.05)
        self.assertIn("fake setup ok", (self.proj / ".claude/skill-router/setup.log").read_text())

    def test_configured_project_is_fast_and_brief(self):
        sr = self.proj / ".claude/skill-router"
        sr.mkdir(parents=True)
        (sr / "state.json").write_text(json.dumps({"installed": [{"skill": "a"}, {"skill": "b"}]}))
        (sr / "autopilot.json").write_text(json.dumps({"ok": True, "savings": {"saved_tokens_est": 900}}))
        r, dt = self.run_boot(self.event())
        ctx = self.context(r)
        self.assertIn("2 skills", ctx)
        self.assertIn("900", ctx)
        self.assertEqual(len(ctx.splitlines()), 1)
        self.assertFalse(self.wait_for(self.proj / "fake_cli_ran.txt", 0.6))

    def test_configured_without_skills_is_silent(self):
        sr = self.proj / ".claude/skill-router"
        sr.mkdir(parents=True)
        (sr / "autopilot.json").write_text(json.dumps({"ok": True, "installed": []}))
        r, _ = self.run_boot(self.event())
        self.assertIsNone(self.context(r))

    def test_disabled_after_undo_is_silent(self):
        sr = self.proj / ".claude/skill-router"
        sr.mkdir(parents=True)
        (sr / "autopilot.json").write_text(json.dumps({"disabled": True}))
        r, _ = self.run_boot(self.event())
        self.assertIsNone(self.context(r))
        self.assertFalse(self.wait_for(self.proj / "fake_cli_ran.txt", 0.6))

    def test_recent_failure_not_retried(self):
        sr = self.proj / ".claude/skill-router"
        sr.mkdir(parents=True)
        (sr / "autopilot.json").write_text(json.dumps({"ok": False, "failed_at": time.time()}))
        r, _ = self.run_boot(self.event())
        self.assertIsNone(self.context(r))
        self.assertFalse(self.wait_for(self.proj / "fake_cli_ran.txt", 0.6))

    def test_lock_prevents_second_setup(self):
        sr = self.proj / ".claude/skill-router"
        sr.mkdir(parents=True)
        (sr / "setup.lock").write_text(json.dumps({"pid": os.getpid(), "since": time.time()}))
        r, _ = self.run_boot(self.event())
        self.assertIn("en segundo plano", self.context(r))
        self.assertFalse(self.wait_for(self.proj / "fake_cli_ran.txt", 0.6))

    def test_malformed_json(self):
        for bad in ("{no es json", "[1,2]", ""):
            r, _ = self.run_boot(bad)
            self.assertEqual(r.returncode, 0)
            self.assertEqual(r.stdout.strip(), "")
        self.assertFalse((self.proj / ".claude").exists())

    def test_guard_child(self):
        r, _ = self.run_boot(self.event(), SKILL_ROUTER_CHILD="1")
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertFalse((self.proj / ".claude").exists())

    def test_guard_autopilot_off(self):
        r, _ = self.run_boot(self.event(), SKILL_ROUTER_AUTOPILOT="0")
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertFalse((self.proj / ".claude").exists())

    def test_home_and_missing_dir_ignored(self):
        home = self.tmp / "home"
        home.mkdir()
        for cwd in (home, self.tmp / "no-existe"):
            r, _ = self.run_boot(self.event(cwd))
            self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertFalse((home / ".claude").exists())

    def test_never_raises_on_broken_cli(self):
        r, _ = self.run_boot(self.event(), SKILL_ROUTER_CLI=str(self.tmp / "no-existe.py"))
        self.assertEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
