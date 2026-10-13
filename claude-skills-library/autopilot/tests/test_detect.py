import os
import tempfile
import time
import unittest
from pathlib import Path

from autopilot.detect import MAX_FILES, profile_project

FIX = Path(__file__).resolve().parent / "fixtures"
REPO = Path(__file__).resolve().parents[3]  # /home/user/mobsurge


class TestDetect(unittest.TestCase):
    def test_contract_keys(self):
        p = profile_project(FIX / "java_maven")
        for k in ("root", "languages", "frameworks", "signals", "summary"):
            self.assertIn(k, p)
        self.assertIsInstance(p["languages"], dict)
        self.assertIsInstance(p["frameworks"], list)
        self.assertIsInstance(p["signals"], list)
        self.assertTrue(p["summary"])

    def test_java_maven(self):
        p = profile_project(FIX / "java_maven")
        self.assertEqual(p["languages"].get("java"), 2)
        self.assertIn("maven", p["frameworks"])
        self.assertIn("kind:code", p["signals"])
        self.assertIn("file:pom.xml", p["signals"])

    def test_node_react(self):
        p = profile_project(FIX / "node_react")
        self.assertIn("react", p["frameworks"])
        self.assertIn("typescript", p["languages"])
        self.assertNotIn("remotion", p["frameworks"])

    def test_python_data(self):
        p = profile_project(FIX / "python_data")
        self.assertIn("pandas", p["frameworks"])
        self.assertIn("kind:data", p["signals"])
        self.assertIn("python", p["languages"])

    def test_content_marketing(self):
        p = profile_project(FIX / "content_marketing")
        self.assertIn("kind:content", p["signals"])
        self.assertIn("topic:marketing", p["signals"])
        self.assertNotIn("kind:code", p["signals"])

    def test_video_remotion(self):
        p = profile_project(FIX / "video_remotion")
        self.assertIn("remotion", p["frameworks"])
        self.assertIn("fw:remotion", p["signals"])
        # En un repo de código, las palabras "reels/tiktok" del README no cuentan.
        self.assertNotIn("topic:social", p["signals"])

    def test_empty(self):
        p = profile_project(FIX / "empty")
        self.assertEqual(p["signals"], [])
        self.assertEqual(p["frameworks"], [])
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(profile_project(d)["signals"], [])

    def test_missing_dir(self):
        p = profile_project("/nonexistent/s2-detector-xyz")
        self.assertEqual(p["signals"], [])

    def test_ignored_dirs(self):
        with tempfile.TemporaryDirectory() as d:
            for ign in ("node_modules", ".git", "venv", ".venv", "build", "dist",
                        "target", "__pycache__"):
                os.makedirs(os.path.join(d, ign))
                Path(d, ign, "x.py").write_text("x=1\n")
                Path(d, ign, "package.json").write_text('{"dependencies":{"remotion":"4"}}')
            p = profile_project(d)
            self.assertEqual(p["languages"], {})
            self.assertNotIn("remotion", p["frameworks"])

    def test_file_limit(self):
        with tempfile.TemporaryDirectory() as d:
            for i in range(MAX_FILES + 50):
                Path(d, f"f{i}.py").touch()
            t = time.monotonic()
            p = profile_project(d)
            self.assertLess(time.monotonic() - t, 1.0)
            self.assertLessEqual(p["files_scanned"], MAX_FILES)
            self.assertIn("scan:truncated", p["signals"])

    @unittest.skipUnless((REPO / "mobsurge" / "pom.xml").exists(), "repo mobsurge no disponible")
    def test_real_repo_fast(self):
        t = time.monotonic()
        p = profile_project(REPO)
        elapsed = time.monotonic() - t
        self.assertLess(elapsed, 1.0)
        self.assertIn("maven", p["frameworks"])
        # los fixtures de este paquete no deben contaminar el perfil
        self.assertNotIn("remotion", p["frameworks"])
        print(f"\n  profile_project({REPO}) = {elapsed*1000:.1f} ms, "
              f"{p['files_scanned']} archivos: {p['summary']}")


if __name__ == "__main__":
    unittest.main()
