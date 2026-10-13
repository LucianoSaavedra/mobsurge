"""Tests de autopilot/bootstrap.py (hook SessionStart), ejecutándolo de verdad por
stdin. El setup real se sustituye por un cli falso (SKILL_ROUTER_CLI) que solo
deja constancia de que se lanzó."""
from __future__ import annotations

import hashlib
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
                    if k not in ("SKILL_ROUTER_CHILD", "SKILL_ROUTER_AUTOPILOT", "CLAUDE_PROJECT_DIR",
                                 "CLAUDE_PLUGIN_DATA", "XDG_CACHE_HOME", "SKILL_ROUTER_CATALOG",
                                 "SKILL_ROUTER_HOOK_MODE", "PYTHONIOENCODING")}
        self.env["SKILL_ROUTER_CLI"] = str(self.fake)
        self.env["HOME"] = str(self.tmp / "home")
        self.data = self.tmp / "plugin-data"
        self.env["CLAUDE_PLUGIN_DATA"] = str(self.data)

    def make_python_project(self, d=None):
        """Proyecto con señales claras (Python + pandas): el plan real no sale vacío."""
        d = d or self.proj
        (d / "pyproject.toml").write_text('[project]\nname = "a"\ndependencies = ["pandas"]\n')
        (d / "a.py").write_text("import pandas\n")

    def ext_marker(self, base=None):
        h = hashlib.sha1(str(self.proj.resolve()).encode()).hexdigest()
        return Path(base or self.data) / "projects" / f"{h}.json"

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
        self.make_python_project()
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
        # Nada de .claude/skill-router (rutas absolutas, logs) debe acabar en un commit.
        self.assertEqual((self.proj / ".claude/skill-router/.gitignore").read_text(), "*\n")
        self.assertFalse(self.ext_marker().exists())

    def test_empty_git_repo_leaves_no_trace(self):
        subprocess.run(["git", "init", "-q", str(self.proj)], check=True)
        (self.proj / "notas.txt").write_text("hola\n")
        r, dt = self.run_boot(self.event())
        self.assertIsNone(self.context(r))
        self.assertEqual(r.stdout, "")
        self.assertFalse((self.proj / ".claude").exists())
        self.assertFalse(self.wait_for(self.proj / "fake_cli_ran.txt", 0.6))
        st = subprocess.run(["git", "-C", str(self.proj), "status", "--porcelain"],
                            capture_output=True, text=True).stdout
        self.assertEqual(st.strip(), "?? notas.txt")
        m = json.loads(self.ext_marker().read_text())
        self.assertTrue(m["empty"])
        self.assertEqual(m["root"], str(self.proj.resolve()))
        # Segunda sesión: sigue sin rastro y sin contexto.
        r, _ = self.run_boot(self.event())
        self.assertEqual(r.stdout, "")
        self.assertFalse((self.proj / ".claude").exists())

    def test_empty_plan_marker_fallback_without_plugin_data(self):
        env = dict(self.env)
        env.pop("CLAUDE_PLUGIN_DATA")
        self.env = env
        (self.proj / "notas.txt").write_text("hola\n")
        r, _ = self.run_boot(self.event())
        self.assertEqual(r.stdout, "")
        self.assertFalse((self.proj / ".claude").exists())
        self.assertTrue(self.ext_marker(self.tmp / "home/.cache/skill-router").is_file())

    def test_stale_empty_marker_is_rechecked(self):
        mk = self.ext_marker()
        mk.parent.mkdir(parents=True)
        mk.write_text(json.dumps({"empty": True, "checked_at": time.time() - 25 * 3600}))
        self.make_python_project()
        r, _ = self.run_boot(self.event())
        self.assertIn("segundo plano", self.context(r))
        self.assertTrue(self.wait_for(self.proj / "fake_cli_ran.txt"))

    def test_fresh_empty_marker_skips_plan(self):
        mk = self.ext_marker()
        mk.parent.mkdir(parents=True)
        mk.write_text(json.dumps({"empty": True, "checked_at": time.time()}))
        self.make_python_project()  # aun con señales: la marca reciente manda (sin coste)
        r, _ = self.run_boot(self.event())
        self.assertEqual(r.stdout, "")
        self.assertFalse((self.proj / ".claude").exists())

    def test_context_survives_cp1252_stdout_and_shows_cost(self):
        sr = self.proj / ".claude/skill-router"
        sr.mkdir(parents=True)
        (sr / "state.json").write_text(json.dumps({"installed": [{"skill": "a"}, {"skill": "b"}]}))
        (sr / "autopilot.json").write_text(json.dumps({"ok": True, "savings": {
            "saved_tokens_est": 900, "after_tokens_est": 160, "after_skills": 2}}))
        r, _ = self.run_boot(self.event(), PYTHONIOENCODING="cp1252")
        self.assertEqual(r.stderr, "")
        self.assertTrue(r.stdout.isascii())
        ctx = self.context(r)
        self.assertIn("≈", ctx)
        self.assertIn("coste: ≈160 tokens/turno (2 skills)", ctx)
        self.assertIn("900", ctx)

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


FAKE_ROUTE = """import os, sys, pathlib
pathlib.Path(sys.argv[0]).with_suffix('.out').write_text(os.environ.get('SKILL_ROUTER_HOOK_MODE', '<unset>'))
if os.environ.get('FAKE_ROUTE_FAIL'):
    raise RuntimeError('boom')
sys.exit(0)
"""


class RouteWrapperTest(unittest.TestCase):
    """bootstrap.py route: envoltorio del hook UserPromptSubmit sin sintaxis de shell."""

    def setUp(self):
        os.makedirs(TMP_BASE, exist_ok=True)
        self.tmp = Path(tempfile.mkdtemp(prefix="route-", dir=TMP_BASE))
        self.fake = self.tmp / "fake_route.py"
        self.fake.write_text(FAKE_ROUTE)
        self.env = {k: v for k, v in os.environ.items()
                    if k not in ("SKILL_ROUTER_HOOK_MODE", "SKILL_ROUTER_CHILD", "SKILL_ROUTER_HOOK_OFF")}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_fake(self, **env):
        drv = ("import sys, pathlib; sys.path.insert(0, sys.argv[1]); import bootstrap; "
               "bootstrap.ROUTE_PROMPT = pathlib.Path(sys.argv[2]); sys.argv = ['bootstrap.py', 'route']; "
               "sys.exit(bootstrap.main())")
        e = dict(self.env)
        e.update(env)
        r = subprocess.run([sys.executable, "-c", drv, str(BOOT.parent), str(self.fake)],
                           input="{}", capture_output=True, text=True, env=e, timeout=30)
        return r, self.fake.with_suffix(".out").read_text()

    def test_default_mode_is_llm(self):
        r, mode = self.run_fake()
        self.assertEqual((r.returncode, mode), (0, "llm"))

    def test_empty_mode_becomes_llm(self):
        self.assertEqual(self.run_fake(SKILL_ROUTER_HOOK_MODE="")[1], "llm")

    def test_explicit_mode_is_kept(self):
        self.assertEqual(self.run_fake(SKILL_ROUTER_HOOK_MODE="lexical")[1], "lexical")

    def test_errors_never_block(self):
        r, _ = self.run_fake(FAKE_ROUTE_FAIL="1")
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_real_route_prompt_runs(self):
        """Exec form real, sin shell: route_prompt.py en léxico con las skills de demo."""
        e = dict(self.env, SKILL_ROUTER_HOOK_MODE="lexical", SKILL_ROUTER_DEMO="1", SKILL_ROUTER_HOOK_DEBUG="1")
        ev = json.dumps({"hook_event_name": "UserPromptSubmit", "prompt": "rellena este formulario PDF",
                         "cwd": str(self.tmp)})
        r = subprocess.run([sys.executable, str(BOOT), "route"], input=ev, capture_output=True,
                           text=True, env=e, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("6 skills (skills de demo); modo lexical", r.stderr)
        if r.stdout.strip():
            out = json.loads(r.stdout)
            self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit")
        r = subprocess.run([sys.executable, str(BOOT), "route"], input=ev, capture_output=True,
                           text=True, env=dict(e, SKILL_ROUTER_HOOK_OFF="1"), timeout=30)
        self.assertEqual((r.returncode, r.stdout), (0, ""))


if __name__ == "__main__":
    unittest.main()
