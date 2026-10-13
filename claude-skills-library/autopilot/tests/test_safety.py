"""Tests de autopilot/safety.py (sin red)."""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

LIB = Path(__file__).resolve().parents[2]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from autopilot import safety  # noqa: E402

TMP_BASE = "/tmp/s2-instalador"


def make_skill(base: Path, name: str, desc: str = "Una skill de prueba.") -> Path:
    d = base / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {desc}\n---\n# {name}\n",
                                encoding="utf-8")
    return d


class SafetyTests(unittest.TestCase):
    def setUp(self):
        os.makedirs(TMP_BASE, exist_ok=True)
        self.tmp = Path(tempfile.mkdtemp(prefix="safety-", dir=TMP_BASE))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        try:
            os.rmdir(TMP_BASE)
        except OSError:
            pass

    def test_valid_names(self):
        for ok in ("pdf", "skill-creator", "web_tools", "v2.lint", "A1"):
            self.assertTrue(safety.valid_skill_name(ok), ok)
        for bad in ("", "..", ".hidden", "-x", "a/b", "a..b", "x" * 65, "con espacio",
                    "a\\b", "nombre.", None):
            self.assertFalse(safety.valid_skill_name(bad), repr(bad))

    def test_has_dotdot(self):
        self.assertTrue(safety.has_dotdot("../x"))
        self.assertTrue(safety.has_dotdot("a/../b"))
        self.assertTrue(safety.has_dotdot("/etc/passwd"))
        self.assertFalse(safety.has_dotdot("a/b..c/d"))

    def test_solo_instrucciones(self):
        d = make_skill(self.tmp, "docs")
        (d / "reference").mkdir()
        (d / "reference" / "guia.md").write_text("texto", encoding="utf-8")
        (d / "datos.json").write_text("{}", encoding="utf-8")
        (d / "LICENSE").write_text("MIT", encoding="utf-8")
        r = safety.classify_skill(d)
        self.assertEqual(r["kind"], safety.SOLO_INSTRUCCIONES, r)
        self.assertIn("reference/guia.md", r["files"])

    def test_con_scripts_por_extension(self):
        d = make_skill(self.tmp, "conpy")
        (d / "scripts").mkdir()
        (d / "scripts" / "run.py").write_text("print(1)", encoding="utf-8")
        r = safety.classify_skill(d)
        self.assertEqual(r["kind"], safety.CON_SCRIPTS)
        self.assertIn("scripts/run.py", r["reasons"][0])

    def test_con_scripts_por_bit_de_ejecucion(self):
        d = make_skill(self.tmp, "execbit")
        f = d / "notas.md"
        f.write_text("hola", encoding="utf-8")
        os.chmod(f, 0o755)
        r = safety.classify_skill(d)
        self.assertEqual(r["kind"], safety.CON_SCRIPTS)
        self.assertIn("bit de ejecución", r["reasons"][0])

    def test_con_scripts_por_shebang_y_binario(self):
        d = make_skill(self.tmp, "shebang")
        (d / "tool").write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
        self.assertEqual(safety.classify_skill(d)["kind"], safety.CON_SCRIPTS)
        d2 = make_skill(self.tmp, "binario")
        (d2 / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00")
        r = safety.classify_skill(d2)
        self.assertEqual(r["kind"], safety.CON_SCRIPTS)
        self.assertIn("no textual", r["reasons"][0])

    def test_rechaza_symlink_que_sale(self):
        d = make_skill(self.tmp, "fuga")
        secret = self.tmp / "secreto.txt"
        secret.write_text("x", encoding="utf-8")
        os.symlink(secret, d / "ref.md")
        r = safety.classify_skill(d)
        self.assertEqual(r["kind"], safety.RECHAZADA)
        self.assertIn("sale de la carpeta", r["reasons"][0])

    def test_symlink_interno_a_archivo_permitido(self):
        d = make_skill(self.tmp, "interno")
        (d / "a.md").write_text("x", encoding="utf-8")
        os.symlink("a.md", d / "b.md")
        r = safety.classify_skill(d)
        self.assertEqual(r["kind"], safety.SOLO_INSTRUCCIONES, r)

    def test_rechaza_symlink_a_directorio(self):
        d = make_skill(self.tmp, "dirlink")
        os.symlink(self.tmp, d / "arriba")
        r = safety.classify_skill(d)
        self.assertEqual(r["kind"], safety.RECHAZADA)

    def test_rechaza_nombre_invalido_y_sin_skill_md(self):
        d = make_skill(self.tmp, "bien")
        self.assertEqual(safety.classify_skill(d, name="../mal")["kind"], safety.RECHAZADA)
        vacia = self.tmp / "vacia"
        vacia.mkdir()
        self.assertEqual(safety.classify_skill(vacia)["kind"], safety.RECHAZADA)

    def test_rechaza_skill_md_symlink(self):
        d = self.tmp / "mdlink"
        d.mkdir()
        target = self.tmp / "otro.md"
        target.write_text("---\nname: x\n---\n", encoding="utf-8")
        os.symlink(target, d / "SKILL.md")
        self.assertEqual(safety.classify_skill(d)["kind"], safety.RECHAZADA)


if __name__ == "__main__":
    unittest.main()
