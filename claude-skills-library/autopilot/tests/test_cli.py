"""Tests de autopilot/cli.py (C4). detect/plan/install se simulan: los escriben
otros agentes y aquí solo se comprueba la orquestación contra los contratos C1-C3."""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

LIB = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(LIB))

from autopilot import cli  # noqa: E402

TMP_BASE = "/tmp/s2-plugin"

PROFILE = {"root": "", "languages": {"python": 3}, "frameworks": [], "signals": ["python"],
           "summary": "Proyecto Python"}
PLAN = {"scenarios": ["dev"], "repos": [{"name": "r1", "url": "https://github.com/x/r1",
                                         "scenario": "dev", "trust": "oficial", "reason": "señales: python"}],
        "skipped": [], "token_budget_note": "nota"}


def fake_apply(plan, project_root, allow_scripts=False, dry_run=False, sources_dir=None, max_skills=12):
    root = Path(project_root)
    res = {"installed": [{"skill": "a", "repo": "r1", "path": str(root / ".claude/skills/a")}],
           "skipped": [{"skill": "b", "repo": "r1", "reason": "contiene scripts ejecutables"}],
           "dedup_removed": [{"skill": "c", "kept": "a", "similarity": 0.7}],
           "token_estimate": {"before_chars": 4000, "after_chars": 400, "before_tokens_est": 1000,
                              "after_tokens_est": 100, "before_skills": 10, "after_skills": 1,
                              "method": "chars/4"},
           "state_path": str(root / ".claude/skill-router/state.json"), "dry_run": dry_run}
    if not dry_run:
        (root / ".claude/skills/a").mkdir(parents=True, exist_ok=True)
        (root / ".claude/skill-router/state.json").write_text(json.dumps({"installed": [{"skill": "a"}]}))
    return res


def fake_undo(project_root, force=False):
    root = Path(project_root)
    shutil.rmtree(root / ".claude/skills", ignore_errors=True)
    try:
        (root / ".claude/skill-router/state.json").unlink()
    except OSError:
        pass
    return {"project": str(root), "removed": ["a"], "kept": [], "state_removed": True}


class CliTest(unittest.TestCase):
    def setUp(self):
        os.makedirs(TMP_BASE, exist_ok=True)
        self.proj = Path(tempfile.mkdtemp(prefix="cli-", dir=TMP_BASE))
        self.patches = [
            mock.patch.object(cli.detect, "profile_project", side_effect=lambda r: {**PROFILE, "root": str(r)}),
            mock.patch.object(cli.planmod, "make_plan", return_value=PLAN),
            mock.patch.object(cli.install, "apply_plan", side_effect=fake_apply),
            mock.patch.object(cli.install, "undo", side_effect=fake_undo),
            mock.patch.object(cli.install, "status", side_effect=lambda p: {
                "project": str(p), "active": True, "count": 1,
                "installed": [{"skill": "a", "repo": "r1", "present": True, "modified": False}]}),
        ]
        self.mocks = [p.start() for p in self.patches]

    def tearDown(self):
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.proj, ignore_errors=True)

    def run_cli(self, *args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli.main(list(args))
        return code, buf.getvalue()

    def test_plan_json(self):
        code, out = self.run_cli("plan", "--project", str(self.proj), "--json")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["plan"]["repos"][0]["name"], "r1")
        self.mocks[2].assert_not_called()  # plan no instala

    def test_setup_auto_writes_marker_and_savings(self):
        code, out = self.run_cli("setup", "--auto", "--project", str(self.proj), "--json")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["savings"]["saved_tokens_est"], 900)
        self.assertEqual(data["savings"]["saved_pct"], 90)
        m = cli.read_marker(self.proj)
        self.assertTrue(m["ok"])
        self.assertEqual(m["installed"], ["a"])
        self.assertIn(".claude", m["created_dirs"])
        self.assertFalse((self.proj / ".claude/skill-router/setup.lock").exists())
        kw = self.mocks[2].call_args.kwargs
        self.assertFalse(kw["allow_scripts"])
        self.assertEqual(kw["max_skills"], 12)

    def test_setup_text_in_spanish(self):
        code, out = self.run_cli("setup", "--auto", "--project", str(self.proj))
        self.assertEqual(code, 0)
        self.assertIn("Skills instaladas", out)
        self.assertIn("ahorro ≈ 900 (90%)", out)

    def test_setup_dry_run_no_marker_no_lock(self):
        code, _ = self.run_cli("setup", "--dry-run", "--project", str(self.proj), "--json")
        self.assertEqual(code, 0)
        self.assertTrue(self.mocks[2].call_args.kwargs["dry_run"])
        self.assertIsNone(cli.read_marker(self.proj))

    def test_allow_scripts_passed(self):
        self.run_cli("setup", "--auto", "--allow-scripts", "--project", str(self.proj), "--json")
        self.assertTrue(self.mocks[2].call_args.kwargs["allow_scripts"])

    def test_lock_busy(self):
        d = cli.sr_dir(self.proj)
        d.mkdir(parents=True)
        (d / cli.LOCK_FILE).write_text(json.dumps({"pid": os.getpid(), "since": 0}))
        code, out = self.run_cli("setup", "--auto", "--project", str(self.proj), "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["reason"], "otro setup en curso")
        self.mocks[2].assert_not_called()
        code, _ = self.run_cli("setup", "--yes", "--project", str(self.proj), "--json")
        self.assertEqual(code, 3)

    def test_stale_lock_is_taken_over(self):
        d = cli.sr_dir(self.proj)
        d.mkdir(parents=True)
        (d / cli.LOCK_FILE).write_text(json.dumps({"pid": 999999999, "since": 0}))
        code, _ = self.run_cli("setup", "--auto", "--project", str(self.proj), "--json")
        self.assertEqual(code, 0)
        self.mocks[2].assert_called_once()

    def test_undo_disables_autopilot_and_manual_setup_reenables(self):
        self.run_cli("setup", "--auto", "--project", str(self.proj), "--json")
        code, out = self.run_cli("undo", "--project", str(self.proj), "--json")
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)["autopilot_disabled"])
        self.assertTrue(cli.read_marker(self.proj)["disabled"])
        # --auto respeta la desactivación
        self.mocks[2].reset_mock()
        code, out = self.run_cli("setup", "--auto", "--project", str(self.proj), "--json")
        self.assertTrue(json.loads(out)["skipped"])
        self.mocks[2].assert_not_called()
        # setup manual la levanta
        code, _ = self.run_cli("setup", "--yes", "--project", str(self.proj), "--json")
        self.assertEqual(code, 0)
        self.assertFalse(cli.read_marker(self.proj)["disabled"])

    def test_undo_purge_leaves_project_clean(self):
        self.run_cli("setup", "--auto", "--project", str(self.proj), "--json")
        code, _ = self.run_cli("undo", "--purge", "--project", str(self.proj), "--json")
        self.assertEqual(code, 0)
        self.assertEqual(list(self.proj.iterdir()), [])

    def test_status_includes_marker(self):
        self.run_cli("setup", "--auto", "--project", str(self.proj), "--json")
        code, out = self.run_cli("status", "--project", str(self.proj), "--json")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["autopilot"]["savings"]["saved_tokens_est"], 900)
        self.assertFalse(data["setup_running"])

    def test_install_error_recorded_exit_1(self):
        self.mocks[2].side_effect = RuntimeError("sin red")
        code, out = self.run_cli("setup", "--auto", "--project", str(self.proj), "--json")
        self.assertEqual(code, 1)
        self.assertIn("sin red", json.loads(out)["error"])
        m = cli.read_marker(self.proj)
        self.assertFalse(m["ok"])
        self.assertIn("failed_at", m)
        self.assertFalse((self.proj / ".claude/skill-router/setup.lock").exists())

    def _fetch_fail_apply(self, reason):
        def apply(plan, project_root, allow_scripts=False, dry_run=False, sources_dir=None, max_skills=12):
            return {"installed": [], "skipped": [{"skill": "*", "repo": "r1", "reason": reason}],
                    "dedup_removed": [], "removed": [],
                    "token_estimate": {"before_tokens_est": 0, "after_tokens_est": 0,
                                       "before_skills": 0, "after_skills": 0},
                    "dry_run": dry_run}
        return apply

    def test_fetch_failure_marks_retry(self):
        self.mocks[2].side_effect = self._fetch_fail_apply("git clone falló: fatal: could not read from remote")
        code, out = self.run_cli("setup", "--auto", "--project", str(self.proj))
        self.assertEqual(code, 1)
        self.assertIn("se reintentará automáticamente pasadas 24 h", out)
        m = cli.read_marker(self.proj)
        self.assertIs(m["ok"], False)
        self.assertIsInstance(m["failed_at"], float)
        self.assertIn("could not read from remote", m["error"])
        self.assertFalse((self.proj / ".claude/skill-router/setup.lock").exists())
        # El arranque siguiente no lo da por configurado: queda en la ventana de reintento.
        from autopilot import bootstrap
        buf = io.StringIO()
        with mock.patch.object(bootstrap, "_spawn_setup") as spawn:
            self.assertIsNone(bootstrap.run(json.dumps({"cwd": str(self.proj)}), out=buf))
            spawn.assert_not_called()
            m["failed_at"] = 0.0  # pasadas 24 h: se reintenta
            cli.write_marker(self.proj, m)
            with mock.patch.object(bootstrap, "_plan_repos", return_value=[{"name": "r1"}]):
                self.assertIn("segundo plano", bootstrap.run(json.dumps({"cwd": str(self.proj)}), out=buf))
            spawn.assert_called_once()

    def test_fetch_failure_json_exit_1(self):
        self.mocks[2].side_effect = self._fetch_fail_apply("no se pudo clonar: sin red")
        code, out = self.run_cli("setup", "--auto", "--project", str(self.proj), "--json")
        self.assertEqual(code, 1)
        self.assertTrue(json.loads(out)["result"]["fetch_failed"])

    def test_not_in_catalog_is_not_a_fetch_failure(self):
        self.mocks[2].side_effect = self._fetch_fail_apply(
            "el repo no está en el catálogo curado (o su URL no coincide)")
        code, _ = self.run_cli("setup", "--auto", "--project", str(self.proj), "--json")
        self.assertEqual(code, 0)
        self.assertIs(cli.read_marker(self.proj)["ok"], True)

    def test_partial_fetch_failure_with_installs_is_ok(self):
        def apply(*a, **k):
            r = fake_apply(*a, **k)
            r["skipped"].append({"skill": "*", "repo": "r1", "reason": "git clone falló: x"})
            return r
        self.mocks[2].side_effect = apply
        code, _ = self.run_cli("setup", "--auto", "--project", str(self.proj), "--json")
        self.assertEqual(code, 0)
        self.assertIs(cli.read_marker(self.proj)["ok"], True)

    def test_setup_writes_gitignore(self):
        self.run_cli("setup", "--auto", "--project", str(self.proj), "--json")
        self.assertEqual((self.proj / ".claude/skill-router/.gitignore").read_text(), "*\n")

    def test_setup_text_net_cost_and_skip_order(self):
        def apply(*a, **k):
            r = fake_apply(*a, **k)
            r["skipped"] = ([{"skill": f"s{i}", "repo": "r1", "reason": "tiene scripts"} for i in range(11)]
                            + [{"skill": "mia", "repo": "r1", "reason": "modificada a mano; se conserva"},
                               {"skill": "mala", "repo": "r1", "reason": "rechazada por seguridad: x"}])
            return r
        self.mocks[2].side_effect = apply
        code, out = self.run_cli("setup", "--auto", "--project", str(self.proj))
        self.assertEqual(code, 0)
        self.assertIn("Coste neto añadido frente a no instalar nada: ≈ 100 tokens/turno (1 skills)", out)
        lines = out.splitlines()
        i = lines.index("Saltadas: 13")
        self.assertIn("mia", lines[i + 1])
        self.assertIn("mala", lines[i + 2])

    def test_status_text_shows_cost(self):
        self.run_cli("setup", "--auto", "--project", str(self.proj), "--json")
        code, out = self.run_cli("status", "--project", str(self.proj))
        self.assertEqual(code, 0)
        self.assertIn("Coste por turno de las skills instaladas: ≈ 100 tokens (1 skills)", out)
        self.assertIn("Ahorro estimado por turno: ≈ 900", out)

    def test_runs_by_path_and_as_module(self):
        for cmd in ([sys.executable, str(LIB / "autopilot" / "cli.py"), "--help"],
                    [sys.executable, "-m", "autopilot.cli", "--help"]):
            r = subprocess.run(cmd, cwd=str(LIB), capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr)
            for sub in ("setup", "status", "undo", "plan"):
                self.assertIn(sub, r.stdout)


class PluginLayoutTest(unittest.TestCase):
    """JSON válidos y rutas existentes en la estructura del plugin y el marketplace."""

    def test_manifest_and_hooks(self):
        manifest = json.loads((LIB / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["name"], "skill-router")
        for k in ("version", "description", "author"):
            self.assertIn(k, manifest)
        hooks = json.loads((LIB / "hooks/hooks.json").read_text(encoding="utf-8"))["hooks"]
        ss = hooks["SessionStart"][0]
        self.assertEqual(ss["matcher"], "startup")
        self.assertEqual(ss["hooks"][0]["args"], ["${CLAUDE_PLUGIN_ROOT}/autopilot/bootstrap.py"])
        ups = hooks["UserPromptSubmit"][0]["hooks"][0]
        # Forma exec (sin shell): nada de VAR=valor ni ${VAR:-...}, que fallan en PowerShell.
        self.assertEqual(ups["args"], ["${CLAUDE_PLUGIN_ROOT}/autopilot/bootstrap.py", "route"])
        self.assertEqual(ups["timeout"], 30)
        for h in (ss["hooks"][0], ups):
            self.assertEqual(h["type"], "command")
            self.assertEqual(h["command"], "python3")
            self.assertNotIn("=", h["command"])
            rel = h["args"][0].split("${CLAUDE_PLUGIN_ROOT}/", 1)[1]
            self.assertTrue((LIB / rel).is_file(), rel)
        boot = (LIB / "autopilot/bootstrap.py").read_text(encoding="utf-8")
        self.assertIn('"llm"', boot)
        self.assertTrue((LIB / "skill-router/integration/hooks/route_prompt.py").is_file())

    def test_commands_and_skill(self):
        for name in ("setup", "status", "undo", "plan"):
            text = (LIB / "commands" / f"{name}.md").read_text(encoding="utf-8")
            self.assertTrue(text.startswith("---\n"))
            self.assertIn("${CLAUDE_PLUGIN_ROOT}/autopilot/cli.py", text)
        skill = (LIB / "skills/skill-router/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("name: skill-router", skill)

    def test_marketplace(self):
        repo = LIB.parent
        mk = json.loads((repo / ".claude-plugin/marketplace.json").read_text(encoding="utf-8"))
        entry = mk["plugins"][0]
        self.assertEqual(entry["name"], "skill-router")
        self.assertNotIn("..", entry["source"])
        self.assertTrue((repo / entry["source"] / ".claude-plugin/plugin.json").is_file())


if __name__ == "__main__":
    unittest.main()
