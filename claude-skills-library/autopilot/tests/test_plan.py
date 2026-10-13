import json
import tempfile
import unittest
from pathlib import Path

from autopilot.detect import profile_project
from autopilot.plan import make_plan

FIX = Path(__file__).resolve().parent / "fixtures"
CATALOG = Path(__file__).resolve().parents[2] / "skills.json"


def plan_for(name, **kw):
    return make_plan(profile_project(FIX / name), CATALOG, **kw)


def catalog_urls():
    data = json.loads(CATALOG.read_text(encoding="utf-8"))
    return {r["url"] for sc in data["scenarios"].values() for r in sc.get("repos", [])}


def catalog_scenario(key):
    return json.loads(CATALOG.read_text(encoding="utf-8"))["scenarios"][key]


class TestPlan(unittest.TestCase):
    def check_contract(self, plan, max_repos=3):
        for k in ("scenarios", "repos", "skipped", "token_budget_note"):
            self.assertIn(k, plan)
        self.assertLessEqual(len(plan["repos"]), max_repos)
        urls = [r["url"] for r in plan["repos"]]
        self.assertEqual(len(urls), len(set(urls)), "URLs duplicadas")
        for r in plan["repos"]:
            for k in ("name", "url", "scenario", "trust", "reason"):
                self.assertIn(k, r)
            self.assertIn(r["url"], catalog_urls(), "solo catálogo curado")
        for s in plan["skipped"]:
            self.assertEqual(set(s), {"name", "reason"})

    def names(self, plan):
        return [r["name"] for r in plan["repos"]]

    def test_java_maven(self):
        p = plan_for("java_maven")
        self.check_contract(p)
        self.assertEqual(self.names(p), ["superpowers"])
        self.assertIn("everything-claude-code", [s["name"] for s in p["skipped"]])

    def test_node_react(self):
        p = plan_for("node_react")
        self.check_contract(p)
        self.assertEqual(p["scenarios"], ["ingenieria"])

    def test_python_data(self):
        p = plan_for("python_data")
        self.check_contract(p)
        self.assertIn("anthropics-skills", self.names(p))
        for big in ("composio-awesome", "behisecc-awesome"):
            self.assertNotIn(big, self.names(p))

    def test_content_marketing(self):
        p = plan_for("content_marketing")
        self.check_contract(p)
        self.assertEqual(self.names(p), ["marketingskills"])

    def test_video_remotion_prefers_official_and_fewer(self):
        p = plan_for("video_remotion")
        self.check_contract(p)
        # Todas las skills de Remotion traen scripts: el catálogo deja sus
        # listas vacías y el plan NO instala esos repos enteros.
        self.assertEqual(self.names(p), [])
        self.assertEqual(p["scenarios"], ["video_viral"])
        sk = {s["name"]: s["reason"] for s in p["skipped"]}
        for name in ("remotion-skills", "remotion-video-skill"):
            self.assertIn(name, sk)
            self.assertIn("no se instala el repo entero", sk[name])
        # el oficial se evalúa antes que el comunitario
        order = [s["name"] for s in p["skipped"]]
        self.assertLess(order.index("remotion-skills"), order.index("remotion-video-skill"))
        self.assertIn("no se instala nada", p["token_budget_note"])

    def test_empty_gets_empty_plan(self):
        p = plan_for("empty")
        self.check_contract(p)
        self.assertEqual(p["repos"], [])
        self.assertEqual(p["scenarios"], [])
        self.assertTrue(p["token_budget_note"])

    def test_deterministic(self):
        for name in ("java_maven", "python_data", "video_remotion"):
            self.assertEqual(plan_for(name), plan_for(name))

    def test_max_repos(self):
        # base(3), marketing(3), ingenieria(2): tres repos instalables distintos
        prof = {"languages": {}, "frameworks": [],
                "signals": ["kind:code", "kind:documents", "topic:marketing"]}
        for m in (1, 2, 3):
            p = make_plan(prof, CATALOG, max_repos=m)
            self.check_contract(p, max_repos=m)
            self.assertEqual(len(p["repos"]), m)
        p = make_plan(prof, CATALOG, max_repos=1)
        self.assertTrue(any("límite" in s["reason"] for s in p["skipped"]))

    def test_dedup_same_url(self):
        # skill-creator y anthropics-skills son el mismo repo
        prof = {"signals": ["kind:skills-dev", "kind:documents"]}
        p = make_plan(prof, CATALOG, max_repos=3)
        self.check_contract(p)
        self.assertEqual(sum(r["url"] == "https://github.com/anthropics/skills"
                             for r in p["repos"]), 1)

    def test_java_maven_has_no_foreign_skills(self):
        p = plan_for("java_maven")
        allowed = set()
        for r in catalog_scenario("ingenieria")["repos"]:
            allowed.update(r.get("skills", []))
        self.assertTrue(p["repos"])
        foreign = {"academy-guide", "discernment-nudge", "internal-comms", "brand-guidelines",
                   "frontend-design", "template", "doc-coauthoring", "copywriting"}
        for r in p["repos"]:
            self.assertIn("skills", r, "el plan debe acotar las skills del repo")
            self.assertTrue(r["skills"])
            self.assertLessEqual(len(r["skills"]), 4)
            self.assertLessEqual(set(r["skills"]), allowed)
            self.assertFalse(set(r["skills"]) & foreign)
        self.assertEqual(p["repos"][0]["skills"],
                         [r for r in catalog_scenario("ingenieria")["repos"]
                          if r["name"] == "superpowers"][0]["skills"])

    def test_every_planned_repo_has_skill_list(self):
        for name in ("java_maven", "node_react", "python_data", "content_marketing",
                     "video_remotion", "empty"):
            for r in plan_for(name)["repos"]:
                self.assertIsInstance(r.get("skills"), list, f"{name}: {r['name']}")
                self.assertTrue(r["skills"])

    def test_catalog_skill_lists_documented(self):
        data = json.loads(CATALOG.read_text(encoding="utf-8"))
        self.assertIn("2026-10-13", data.get("skills_note", ""))
        for key, sc in data["scenarios"].items():
            for r in sc.get("repos", []):
                self.assertIsInstance(r.get("skills"), list, f"{key}/{r['name']}")
                self.assertLessEqual(len(r["skills"]), 4)
                self.assertTrue(all(isinstance(x, str) and x for x in r["skills"]))
                self.assertNotIn("template", r["skills"])

    def test_empty_skill_list_not_installed(self):
        with tempfile.TemporaryDirectory() as d:
            cat = Path(d, "c.json")
            cat.write_text(json.dumps({"scenarios": {"x": {"title": "X", "signals": {"kind:code": 3},
                "repos": [
                    {"name": "vacio", "url": "https://github.com/a/vacio", "trust": "oficial", "skills": []},
                    {"name": "bueno", "url": "https://github.com/a/bueno", "trust": "comunidad", "skills": ["s1", "s1", ""]},
                ]}}}))
            p = make_plan({"signals": ["kind:code"]}, cat)
            self.assertEqual([r["name"] for r in p["repos"]], ["bueno"])
            self.assertEqual(p["repos"][0]["skills"], ["s1"])
            sk = {s["name"]: s["reason"] for s in p["skipped"]}
            self.assertIn("vacio", sk)
            self.assertIn("vacía", sk["vacio"])
            # solo el repo vacío: nada se instala y se informa
            cat.write_text(json.dumps({"scenarios": {"x": {"title": "X", "signals": {"kind:code": 3},
                "repos": [{"name": "vacio", "url": "https://github.com/a/vacio", "trust": "oficial", "skills": []}]}}}))
            p = make_plan({"signals": ["kind:code"]}, cat)
            self.assertEqual(p["repos"], [])
            self.assertEqual([s["name"] for s in p["skipped"]], ["vacio"])

    def test_same_repo_merges_skill_lists(self):
        prof = {"signals": ["topic:marketing", "topic:social"]}
        p = make_plan(prof, CATALOG)
        self.assertEqual(self.names(p), ["marketingskills"])
        skills = p["repos"][0]["skills"]
        self.assertIn("copywriting", skills)
        self.assertIn("social-content", skills)
        self.assertEqual(len(skills), len(set(skills)))

    def test_deterministic_with_skills(self):
        for name in ("java_maven", "python_data", "content_marketing", "video_remotion"):
            a, b = plan_for(name), plan_for(name)
            self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))

    def test_catalog_without_signals(self):
        with tempfile.TemporaryDirectory() as d:
            cat = Path(d, "c.json")
            cat.write_text(json.dumps({"scenarios": {"x": {"title": "X", "repos": [
                {"name": "a", "url": "https://github.com/a/a", "trust": "oficial"}]}}}))
            p = make_plan({"signals": ["kind:code"]}, cat)
            self.assertEqual(p["repos"], [])


if __name__ == "__main__":
    unittest.main()
