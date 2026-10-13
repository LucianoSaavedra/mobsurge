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
        self.assertEqual(self.names(p), ["remotion-skills"])
        self.assertEqual(p["repos"][0]["trust"], "oficial-remotion")
        self.assertIn("remotion-video-skill", [s["name"] for s in p["skipped"]])

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
        prof = {"languages": {}, "frameworks": ["remotion"],
                "signals": ["kind:code", "kind:data", "kind:documents",
                            "kind:skills-dev", "topic:marketing", "topic:social",
                            "topic:study", "fw:remotion"]}
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

    def test_catalog_without_signals(self):
        with tempfile.TemporaryDirectory() as d:
            cat = Path(d, "c.json")
            cat.write_text(json.dumps({"scenarios": {"x": {"title": "X", "repos": [
                {"name": "a", "url": "https://github.com/a/a", "trust": "oficial"}]}}}))
            p = make_plan({"signals": ["kind:code"]}, cat)
            self.assertEqual(p["repos"], [])


if __name__ == "__main__":
    unittest.main()
