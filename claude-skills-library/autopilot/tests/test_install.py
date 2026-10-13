"""Tests de autopilot/install.py.

Sin red: los repos son git locales servidos con file:// en /tmp/s2-instalador.
La prueba con red (anthropics/skills) solo corre con SKILL_ROUTER_NET_TEST=1.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

LIB = Path(__file__).resolve().parents[2]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from autopilot import install  # noqa: E402

TMP_BASE = "/tmp/s2-instalador"


def git(*args, cwd=None):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c",
                    "init.defaultBranch=main", "-c", "commit.gpgsign=false", *args],
                   cwd=cwd, check=True, capture_output=True)


def write_skill(repo: Path, rel: str, name: str, desc: str, extra: dict | None = None):
    d = repo / rel
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {desc}\n---\n# {name}\n",
                                encoding="utf-8")
    for fname, content in (extra or {}).items():
        p = d / fname
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


def make_repo(path: Path, skills: list) -> str:
    path.mkdir(parents=True)
    for s in skills:
        write_skill(path, *s)
    git("init", "-q", cwd=path)
    git("add", "-A", cwd=path)
    git("commit", "-q", "-m", "init", cwd=path)
    return "file://" + str(path)


def commit_all(path: Path):
    git("add", "-A", cwd=path)
    git("commit", "-q", "-m", "update", cwd=path)


class InstallTests(unittest.TestCase):
    def setUp(self):
        os.makedirs(TMP_BASE, exist_ok=True)
        self.tmp = Path(tempfile.mkdtemp(prefix="install-", dir=TMP_BASE))
        self.sources = self.tmp / "sources"
        self.globals = self.tmp / "home" / ".claude" / "skills"
        self.globals.mkdir(parents=True)
        self.project = self.tmp / "proyecto"
        self.project.mkdir()
        env = mock.patch.dict(os.environ, {"SKILL_ROUTER_GLOBAL_SKILLS": str(self.globals)})
        env.start()
        self.addCleanup(env.stop)

        self.url_of = make_repo(self.tmp / "oficial", [
            ("skills/pdf", "pdf", "Read, extract and fill PDF forms; merge and split PDF documents."),
            ("skills/docx", "docx", "Create and edit Word documents with tracked changes."),
            ("skills/runner", "runner", "Runs helper scripts.", {"scripts/run.py": "print(1)\n"}),
            ("skills/frontend-design", "frontend-design",
             "Design distinctive frontend interfaces with React and CSS."),
        ])
        self.url_com = make_repo(self.tmp / "comunidad", [
            ("pdf", "pdf", "PDF tools."),
            ("pdf-forms", "pdf-forms",
             "Read, extract and fill PDF forms; merge and split PDF documents quickly."),
            ("testing", "testing", "Write unit tests with pytest and coverage reports."),
        ])
        self.url_fuera = make_repo(self.tmp / "fuera", [("evil", "evil", "No curada.")])
        self.catalog = self.tmp / "skills.json"
        self.catalog.write_text(json.dumps({"version": 1, "scenarios": {
            "base": {"title": "Base", "repos": [
                {"name": "oficial", "url": self.url_of, "trust": "oficial", "about": "x"},
                {"name": "comunidad", "url": self.url_com + ".git", "trust": "comunidad", "about": "y"},
            ]}}}), encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        try:
            os.rmdir(TMP_BASE)
        except OSError:
            pass

    def plan(self, *repos):
        rows = {"oficial": (self.url_of, "oficial"), "comunidad": (self.url_com, "comunidad"),
                "fuera": (self.url_fuera, "oficial")}
        return {"scenarios": ["base"],
                "repos": [{"name": r, "url": rows[r][0], "scenario": "base", "trust": rows[r][1],
                           "reason": "test"} for r in repos],
                "skipped": [], "token_budget_note": ""}

    def apply(self, plan, **kw):
        kw.setdefault("sources_dir", self.sources)
        kw.setdefault("catalog_path", self.catalog)
        return install.apply_plan(plan, self.project, **kw)

    def names(self, res):
        return sorted(e["skill"] for e in res["installed"])

    # --- básicos -------------------------------------------------------------

    def test_instala_copia_y_salta_scripts(self):
        res = self.apply(self.plan("oficial"))
        self.assertEqual(self.names(res), ["docx", "frontend-design", "pdf"])
        sk = self.project / ".claude" / "skills"
        self.assertTrue((sk / "pdf" / "SKILL.md").is_file())
        self.assertFalse((sk / "pdf").is_symlink())
        self.assertFalse((sk / "runner").exists())
        reasons = {s["skill"]: s["reason"] for s in res["skipped"]}
        self.assertIn("scripts", reasons["runner"])
        self.assertTrue(Path(res["state_path"]).is_file())
        te = res["token_estimate"]
        self.assertEqual(te["before_skills"], 4)
        self.assertEqual(te["after_skills"], 3)
        self.assertLess(te["after_tokens_est"], te["before_tokens_est"])
        self.assertIn("chars/4", te["method"])

    def test_allow_scripts(self):
        res = self.apply(self.plan("oficial"), allow_scripts=True)
        self.assertIn("runner", self.names(res))
        run = self.project / ".claude" / "skills" / "runner" / "scripts" / "run.py"
        self.assertTrue(run.is_file())
        self.assertFalse(os.access(run, os.X_OK))  # se copia sin bit de ejecución

    def test_dry_run_no_escribe(self):
        res = self.apply(self.plan("oficial"), dry_run=True)
        self.assertEqual(self.names(res), ["docx", "frontend-design", "pdf"])
        self.assertTrue(res["dry_run"])
        self.assertFalse((self.project / ".claude").exists())

    def test_solo_catalogo(self):
        res = self.apply(self.plan("fuera"))
        self.assertEqual(res["installed"], [])
        self.assertIn("catálogo", res["skipped"][0]["reason"])
        self.assertFalse((self.project / ".claude" / "skills" / "evil").exists())

    def test_sin_catalogo_no_instala(self):
        res = self.apply(self.plan("oficial"), catalog_path=self.tmp / "no-existe.json")
        self.assertEqual(res["installed"], [])

    def test_url_no_permitida(self):
        path, _, err = install.fetch_repo("x", "ext::sh -c touch% /tmp/pwn", self.sources)
        self.assertIsNone(path)
        self.assertIn("no permitida", err)

    # --- deduplicación y límite ----------------------------------------------

    def test_dedup_prefiere_trust_oficial(self):
        res = self.apply(self.plan("comunidad", "oficial"))
        installed = {e["skill"]: e["repo"] for e in res["installed"]}
        self.assertEqual(installed["pdf"], "oficial")
        removed = {d["skill"]: d for d in res["dedup_removed"]}
        self.assertEqual(removed["pdf"]["kept"], "pdf")
        self.assertEqual(removed["pdf"]["similarity"], 1.0)
        # pdf-forms casi idéntica a pdf (oficial): se quita por similitud
        self.assertIn("pdf-forms", removed)
        self.assertEqual(removed["pdf-forms"]["kept"], "pdf")
        self.assertGreaterEqual(removed["pdf-forms"]["similarity"], install.DEDUP_THRESHOLD)
        self.assertIn("testing", installed)

    def test_dedup_a_igual_trust_descripcion_mas_completa(self):
        cat = json.loads(self.catalog.read_text())
        cat["scenarios"]["base"]["repos"][1]["trust"] = "oficial"
        self.catalog.write_text(json.dumps(cat))
        res = self.apply(self.plan("comunidad", "oficial"))
        # mismo trust: gana la descripción más completa (pdf-forms, de comunidad),
        # y el "pdf" de comunidad ("PDF tools.") pierde ante el de oficial.
        installed = {e["skill"]: e["repo"] for e in res["installed"]}
        self.assertEqual(installed.get("pdf-forms"), "comunidad")
        removed = {(d["skill"], d["repo"]): d for d in res["dedup_removed"]}
        self.assertEqual(removed[("pdf", "oficial")]["kept"], "pdf-forms")
        self.assertIn(("pdf", "comunidad"), removed)
        self.assertNotIn("pdf", installed)

    def test_max_skills(self):
        res = self.apply(self.plan("oficial", "comunidad"), max_skills=2)
        self.assertEqual(len(res["installed"]), 2)
        self.assertTrue(any("max_skills" in s["reason"] for s in res["skipped"]))
        self.assertEqual(len(list((self.project / ".claude" / "skills").iterdir())), 2)

    # --- protecciones ---------------------------------------------------------

    def test_no_toca_skill_existente_del_usuario(self):
        mine = self.project / ".claude" / "skills" / "pdf"
        mine.mkdir(parents=True)
        (mine / "SKILL.md").write_text("mía", encoding="utf-8")
        res = self.apply(self.plan("oficial"))
        self.assertNotIn("pdf", self.names(res))
        self.assertEqual((mine / "SKILL.md").read_text(), "mía")
        install.undo(self.project)
        self.assertEqual((mine / "SKILL.md").read_text(), "mía")

    def test_no_duplica_skill_global_y_no_la_toca(self):
        g = self.globals / "docx"
        g.mkdir()
        (g / "SKILL.md").write_text("global", encoding="utf-8")
        res = self.apply(self.plan("oficial"))
        self.assertNotIn("docx", self.names(res))
        install.undo(self.project)
        self.assertEqual((g / "SKILL.md").read_text(), "global")

    def test_rechaza_proyecto_en_home(self):
        home = self.tmp / "home"
        with self.assertRaises(ValueError):
            install.apply_plan(self.plan("oficial"), home, sources_dir=self.sources,
                               catalog_path=self.catalog)
        self.assertEqual(list(self.globals.iterdir()), [])

    def test_rechaza_skills_dir_symlink(self):
        (self.project / ".claude").mkdir()
        os.symlink(self.globals, self.project / ".claude" / "skills")
        with self.assertRaises(ValueError):
            self.apply(self.plan("oficial"))

    def test_rechaza_symlink_que_sale_del_repo(self):
        repo = self.tmp / "oficial"
        os.symlink("/etc/hostname", repo / "skills" / "docx" / "ref.md")
        commit_all(repo)
        shutil.rmtree(self.sources, ignore_errors=True)
        res = self.apply(self.plan("oficial"))
        self.assertNotIn("docx", self.names(res))
        reason = next(s["reason"] for s in res["skipped"] if s["skill"] == "docx")
        self.assertIn("seguridad", reason)

    # --- idempotencia, status y undo -------------------------------------------

    def test_idempotente(self):
        r1 = self.apply(self.plan("oficial"))
        state1 = json.loads(Path(r1["state_path"]).read_text())
        r2 = self.apply(self.plan("oficial"))
        state2 = json.loads(Path(r2["state_path"]).read_text())
        self.assertEqual(self.names(r1), self.names(r2))
        self.assertTrue(all(e["unchanged"] for e in r2["installed"]))
        strip = lambda s: {k: v for k, v in s.items() if k != "updated"}  # noqa: E731
        self.assertEqual(strip(state1), strip(state2))

    def test_cambio_de_plan_retira_lo_suyo(self):
        self.apply(self.plan("oficial", "comunidad"))
        self.assertTrue((self.project / ".claude" / "skills" / "testing").exists())
        res = self.apply(self.plan("oficial"))
        self.assertIn("testing", [r["skill"] for r in res["removed"]])
        self.assertFalse((self.project / ".claude" / "skills" / "testing").exists())

    def test_no_sobrescribe_copia_editada(self):
        self.apply(self.plan("oficial"))
        f = self.project / ".claude" / "skills" / "pdf" / "SKILL.md"
        f.write_text("editada", encoding="utf-8")
        res = self.apply(self.plan("oficial"))
        self.assertTrue(any("modificó" in s["reason"] for s in res["skipped"]))
        self.assertEqual(f.read_text(), "editada")
        st = install.status(self.project)
        self.assertTrue(next(e for e in st["installed"] if e["skill"] == "pdf")["modified"])
        u = install.undo(self.project)
        self.assertEqual([k["skill"] for k in u["kept"]], ["pdf"])
        self.assertEqual(f.read_text(), "editada")
        u2 = install.undo(self.project, force=True)
        self.assertIn("pdf", u2["removed"])

    def test_status_y_undo(self):
        self.assertFalse(install.status(self.project)["active"])
        self.apply(self.plan("oficial"))
        st = install.status(self.project)
        self.assertTrue(st["active"])
        self.assertEqual(st["count"], 3)
        self.assertTrue(all(e["present"] and not e["modified"] for e in st["installed"]))
        (self.project / "src").mkdir()
        u = install.undo(self.project)
        self.assertEqual(sorted(u["removed"]), ["docx", "frontend-design", "pdf"])
        self.assertTrue(u["state_removed"])
        # .claude la creó skill-router y quedó vacía: también se quita
        self.assertFalse((self.project / ".claude").exists())
        self.assertTrue((self.project / "src").exists())
        self.assertIn("nada", install.undo(self.project)["message"])

    def test_undo_conserva_claude_preexistente(self):
        (self.project / ".claude").mkdir()
        (self.project / ".claude" / "settings.json").write_text("{}")
        self.apply(self.plan("oficial"))
        install.undo(self.project)
        self.assertTrue((self.project / ".claude" / "settings.json").is_file())
        self.assertFalse((self.project / ".claude" / "skills").exists())

    def test_undo_ignora_entradas_manipuladas(self):
        self.apply(self.plan("oficial"))
        victim = self.tmp / "victima"
        victim.mkdir()
        sp = self.project / install.STATE_REL
        st = json.loads(sp.read_text())
        st["installed"].append({"skill": "../../victima", "path": str(victim), "hash": ""})
        sp.write_text(json.dumps(st))
        install.undo(self.project)
        self.assertTrue(victim.is_dir())

    def test_reutiliza_fuente_clonada(self):
        self.apply(self.plan("oficial"), dry_run=True)
        clones = list(self.sources.iterdir())
        self.assertEqual(len(clones), 1)
        with mock.patch.object(install, "_git", wraps=install._git) as g:
            self.apply(self.plan("oficial"), dry_run=True)
            self.assertFalse(any(c.args[0][0] == "clone" for c in g.call_args_list))

    def test_sources_env(self):
        alt = self.tmp / "alt-sources"
        with mock.patch.dict(os.environ, {"SKILL_ROUTER_SOURCES": str(alt)}):
            install.apply_plan(self.plan("oficial"), self.project, dry_run=True,
                               catalog_path=self.catalog)
        self.assertTrue(alt.is_dir() and any(alt.iterdir()))


@unittest.skipUnless(os.environ.get("SKILL_ROUTER_NET_TEST") == "1",
                     "prueba con red: exporta SKILL_ROUTER_NET_TEST=1")
class NetworkTest(unittest.TestCase):
    def test_anthropics_skills_dry_run(self):
        os.makedirs(TMP_BASE, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(prefix="net-", dir=TMP_BASE))
        try:
            proj = tmp / "p"
            proj.mkdir()
            plan = {"scenarios": ["base"], "repos": [{
                "name": "anthropics-skills", "url": "https://github.com/anthropics/skills",
                "scenario": "base", "trust": "oficial", "reason": "red"}],
                "skipped": [], "token_budget_note": ""}
            with mock.patch.dict(os.environ, {"SKILL_ROUTER_GLOBAL_SKILLS": str(tmp / "g")}):
                res = install.apply_plan(plan, proj, dry_run=True, sources_dir=tmp / "src")
            print("\n" + json.dumps({k: res[k] for k in ("installed", "dedup_removed", "token_estimate")},
                                    ensure_ascii=False, indent=1))
            print("skipped:", json.dumps(res["skipped"], ensure_ascii=False, indent=1))
            self.assertFalse(any("clone" in s["reason"] for s in res["skipped"]), res["skipped"])
            self.assertFalse((proj / ".claude").exists())
            self.assertGreater(res["token_estimate"]["before_skills"], 0)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
