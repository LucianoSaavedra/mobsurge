#!/usr/bin/env python3
"""
Pruebas del hook UserPromptSubmit y del lanzador de la skill (solo stdlib).

    python3 integration/tests/test_route_prompt.py -v

Cada prueba ejecuta el hook como lo haría Claude Code: un proceso aparte que
recibe el JSON del evento por stdin. HOME apunta a una carpeta temporal para no
depender de las skills instaladas en la máquina.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

INTEGRATION = Path(__file__).resolve().parents[1]
ROUTER = INTEGRATION.parent
HOOK = INTEGRATION / "hooks" / "route_prompt.py"
RUNNER = INTEGRATION / "skill-router" / "run_router.py"


def write_skill(root: Path, folder: str, name: str, description: str) -> None:
    d = root / folder
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n", encoding="utf-8")


class HookTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="skill-router-test-")
        self.tmp = Path(self._tmp.name)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith("SKILL_ROUTER_") and k != "CLAUDE_PROJECT_DIR"}
        self.env["HOME"] = str(self.home)
        self.env["PYTHONDONTWRITEBYTECODE"] = "1"

    def tearDown(self):
        self._tmp.cleanup()

    def run_hook(self, stdin: str, args: list[str] | None = None, **env) -> subprocess.CompletedProcess:
        e = dict(self.env)
        e.update(env)
        return subprocess.run([sys.executable, str(HOOK), *(args or [])], input=stdin.encode("utf-8"),
                              capture_output=True, env=e, timeout=60)

    @staticmethod
    def event(prompt, **extra) -> str:
        ev = {"session_id": "test", "transcript_path": "/tmp/t.jsonl", "cwd": "/tmp",
              "permission_mode": "default", "hook_event_name": "UserPromptSubmit", "prompt": prompt}
        ev.update(extra)
        return json.dumps(ev, ensure_ascii=False)

    def context_of(self, proc: subprocess.CompletedProcess) -> str:
        self.assertEqual(proc.returncode, 0)
        out = proc.stdout.decode("utf-8")
        self.assertTrue(out.strip(), f"se esperaba salida; stderr={proc.stderr!r}")
        data = json.loads(out)  # stdout debe ser UN objeto JSON y nada más
        self.assertEqual(set(data), {"hookSpecificOutput"})
        hso = data["hookSpecificOutput"]
        self.assertEqual(hso["hookEventName"], "UserPromptSubmit")
        self.assertNotIn("decision", data)
        ctx = hso["additionalContext"]
        self.assertIsInstance(ctx, str)
        self.assertLessEqual(len(ctx), 10_000)
        return ctx

    def assertSilent(self, proc: subprocess.CompletedProcess):
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, b"")


class TestHookContract(HookTestCase):
    def test_estudio_directo(self):
        ctx = self.context_of(self.run_hook(
            self.event("quiero preparar mi examen de cálculo con flashcards y un simulacro"),
            SKILL_ROUTER_DEMO="1"))
        first = ctx.split("Skills que encajan con este mensaje:\n", 1)[1].splitlines()[0]
        self.assertTrue(first.startswith("- estudio-a-medida"), first)
        self.assertIn("Ruido para este turno", ctx)

    def test_video(self):
        ctx = self.context_of(self.run_hook(
            self.event("escríbeme un guion para un reel de tiktok con buen gancho"),
            SKILL_ROUTER_DEMO="1"))
        first = ctx.split("Skills que encajan con este mensaje:\n", 1)[1].splitlines()[0]
        self.assertTrue(first.startswith("- guiones-video"), first)

    def test_negativo_hola(self):
        self.assertSilent(self.run_hook(self.event("hola"), SKILL_ROUTER_DEMO="1"))

    def test_json_malformado(self):
        proc = self.run_hook('{"prompt": "examen de calculo", ', SKILL_ROUTER_DEMO="1")
        self.assertSilent(proc)
        self.assertIn(b"JSONDecodeError", proc.stderr)

    def test_stdin_vacio(self):
        self.assertSilent(self.run_hook("", SKILL_ROUTER_DEMO="1"))

    def test_json_que_no_es_objeto(self):
        self.assertSilent(self.run_hook('["examen", "guion"]', SKILL_ROUTER_DEMO="1"))
        self.assertSilent(self.run_hook('"examen de calculo"', SKILL_ROUTER_DEMO="1"))

    def test_prompt_ausente_o_no_texto(self):
        self.assertSilent(self.run_hook('{"hook_event_name": "UserPromptSubmit"}', SKILL_ROUTER_DEMO="1"))
        self.assertSilent(self.run_hook(self.event(["examen"]), SKILL_ROUTER_DEMO="1"))

    def test_otro_evento(self):
        self.assertSilent(self.run_hook(
            self.event("guion para un reel de tiktok", hook_event_name="PreToolUse"),
            SKILL_ROUTER_DEMO="1"))

    def test_comando_slash(self):
        self.assertSilent(self.run_hook(self.event("/skill-router guion para un reel"),
                                        SKILL_ROUTER_DEMO="1"))

    def test_ruta_absoluta_no_es_comando(self):
        ctx = self.context_of(self.run_hook(
            self.event("/home/ana/video.mp4 necesito un guion para un reel de tiktok"),
            SKILL_ROUTER_DEMO="1"))
        self.assertIn("guiones-video", ctx)

    def test_utf8_invalido(self):
        bad = b'{"hook_event_name":"UserPromptSubmit","prompt":"guion reel tiktok \xff\xfe"}'
        e = dict(self.env, SKILL_ROUTER_DEMO="1")
        proc = subprocess.run([sys.executable, str(HOOK)], input=bad, capture_output=True, env=e, timeout=60)
        self.assertIn("guiones-video", self.context_of(proc))

    def test_guardia_de_recursion(self):
        self.assertSilent(self.run_hook(self.event("guion para un reel de tiktok"),
                                        SKILL_ROUTER_DEMO="1", SKILL_ROUTER_HOOK_ACTIVE="1"))

    def test_interruptor_off(self):
        self.assertSilent(self.run_hook(self.event("guion para un reel de tiktok"),
                                        SKILL_ROUTER_DEMO="1", SKILL_ROUTER_HOOK_OFF="1"))

    def test_router_no_encontrado(self):
        proc = self.run_hook(self.event("guion para un reel de tiktok"),
                             SKILL_ROUTER_DEMO="1", SKILL_ROUTER_HOME=str(self.tmp / "no-existe"))
        self.assertSilent(proc)
        self.assertIn(b"skill_router.py", proc.stderr)

    def test_umbral_y_top_configurables(self):
        # "astronomia" no coincide con nada -> la puntuación no llega a 100
        self.assertSilent(self.run_hook(self.event("guion para un reel de tiktok sobre astronomia"),
                                        SKILL_ROUTER_DEMO="1", SKILL_ROUTER_HOOK_THRESHOLD="100"))
        ctx = self.context_of(self.run_hook(
            self.event("guion de video y campaña de marketing para redes"),
            SKILL_ROUTER_DEMO="1", SKILL_ROUTER_HOOK_TOP="1"))
        listed = ctx.split("Skills que encajan con este mensaje:\n", 1)[1]
        self.assertEqual(sum(1 for line in listed.splitlines() if line.startswith("- ")), 1)
        # valores basura -> se usan los de por defecto, sin romper
        self.context_of(self.run_hook(self.event("guion para un reel de tiktok"), SKILL_ROUTER_DEMO="1",
                                      SKILL_ROUTER_HOOK_TOP="x", SKILL_ROUTER_HOOK_THRESHOLD="y"))

    def test_modo_texto_manual(self):
        proc = self.run_hook("", args=["--prompt", "guion para un reel de tiktok", "--texto"],
                             SKILL_ROUTER_DEMO="1")
        self.assertEqual(proc.returncode, 0)
        out = proc.stdout.decode("utf-8")
        self.assertTrue(out.startswith("Skill Router:"), out)
        self.assertIn("guiones-video", out)

    def test_llm_sin_backend_cae_al_lexico(self):
        proc = self.run_hook(self.event("guion para un reel de tiktok"), SKILL_ROUTER_DEMO="1",
                             SKILL_ROUTER_HOOK_LLM="1", PATH=str(self.tmp / "sin-binarios"),
                             ANTHROPIC_API_KEY="")
        ctx = self.context_of(proc)
        self.assertIn("scorer léxico+conceptos", ctx)
        self.assertIn(b"scorer LLM no disponible", proc.stderr)

    def test_llm_con_cli_falso_y_guardia_heredada(self):
        """Un `claude` falso en el PATH: comprueba el camino LLM y que el proceso
        hijo hereda SKILL_ROUTER_HOOK_ACTIVE=1 (lo que corta la recursión)."""
        bindir = self.tmp / "bin"
        bindir.mkdir()
        seen = self.tmp / "guardia.txt"
        fake = bindir / "claude"
        fake.write_text(
            "#!/bin/sh\n"
            f"printf '%s' \"$SKILL_ROUTER_HOOK_ACTIVE\" > '{seen}'\n"
            "cat <<'EOF'\n"
            '{"recommended": [{"name": "guiones-video", "score": 90, '
            '"reason": "pide un guion\\nde reel"}]}\n'  # \n escapado en el JSON
            "EOF\n", encoding="utf-8")
        fake.chmod(0o755)
        proc = self.run_hook(self.event("guion para un reel de tiktok"), SKILL_ROUTER_DEMO="1",
                             SKILL_ROUTER_HOOK_LLM="1", ANTHROPIC_API_KEY="",
                             PATH=f"{bindir}{os.pathsep}{self.env.get('PATH', '')}")
        ctx = self.context_of(proc)
        self.assertIn("scorer LLM", ctx)
        self.assertIn("- guiones-video [Video]: relevancia 90/100; motivo: pide un guion de reel", ctx)
        self.assertEqual(seen.read_text(), "1")


class TestSkillSources(HookTestCase):
    def test_skills_instaladas_y_autoexclusion(self):
        skills = self.home / ".claude" / "skills"
        write_skill(skills, "video-maker", "video-maker", "Guion de video corto para reels y tiktok con gancho.")
        write_skill(skills, "excel-pro", "excel-pro", "Hojas de calculo, formulas y graficos en Excel.")
        write_skill(skills, "skill-router", "skill-router",
                    "Recomienda que skills usar para un guion de video, reel o tiktok.")
        ctx = self.context_of(self.run_hook(self.event("guion para un reel de tiktok")))
        self.assertIn("- video-maker", ctx)
        self.assertNotIn("skill-router:", ctx)
        self.assertNotIn("skill-router,", ctx)
        self.assertIn("skills instaladas en ~/.claude/skills, 2 skills", ctx)

    def test_skills_del_proyecto(self):
        project = self.tmp / "proyecto"
        write_skill(project / ".claude" / "skills", "facturas", "facturas",
                    "Genera facturas y presupuestos en PDF para clientes.")
        write_skill(self.home / ".claude" / "skills", "video-maker", "video-maker",
                    "Guion de video corto para reels y tiktok con gancho.")
        ctx = self.context_of(self.run_hook(
            self.event("hazme las facturas del mes en pdf", cwd=str(project))))
        self.assertIn("- facturas", ctx)
        ctx2 = self.context_of(self.run_hook(self.event("hazme las facturas del mes en pdf"),
                                             CLAUDE_PROJECT_DIR=str(project)))
        self.assertIn("- facturas", ctx2)

    def test_nombres_repetidos_cuentan_una_vez(self):
        project = self.tmp / "proyecto"
        for root in (self.home / ".claude" / "skills", project / ".claude" / "skills"):
            write_skill(root, "video-maker", "video-maker", "Guion de video corto para reels y tiktok.")
        write_skill(project / ".claude" / "skills", "otra-video", "video-maker",
                    "Guion de video corto para reels y tiktok.")
        ctx = self.context_of(self.run_hook(self.event("guion para un reel de tiktok", cwd=str(project))))
        self.assertIn("~/.claude/skills, 1 skill)", ctx)

    def test_carpeta_explicita(self):
        other = self.tmp / "otras"
        write_skill(other, "podcast", "podcast", "Guion y escaleta para episodios de podcast.")
        ctx = self.context_of(self.run_hook(self.event("escaleta para mi podcast"),
                                            SKILL_ROUTER_SKILLS_DIR=str(other)))
        self.assertIn("- podcast", ctx)

    def test_sin_skills_usa_el_catalogo(self):
        catalog = ROUTER.parent / "skills.json"
        if not catalog.is_file():
            self.skipTest("no hay skills.json junto al router")
        ctx = self.context_of(self.run_hook(self.event("guion para un reel viral de tiktok")))
        self.assertIn("catálogo skills.json", ctx)
        self.assertIn("puede que no estén instaladas", ctx)

    def test_sin_skills_ni_catalogo(self):
        fake_router = self.tmp / "router"
        fake_router.mkdir()
        (fake_router / "skill_router.py").write_text((ROUTER / "skill_router.py").read_text("utf-8"), "utf-8")
        (fake_router / "concepts.py").write_text((ROUTER / "concepts.py").read_text("utf-8"), "utf-8")
        self.assertSilent(self.run_hook(self.event("guion para un reel viral de tiktok"),
                                        SKILL_ROUTER_HOME=str(fake_router)))


class TestCleanPrompt(unittest.TestCase):
    def test_quita_marcas_de_texto_pegado(self):
        spec = importlib.util.spec_from_file_location("route_prompt", HOOK)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        raw = ('mira esto\n<pasted_content id="1">\nlinea pegada\n</pasted_content id="1">\nfin')
        self.assertEqual(mod.clean_prompt(raw), "mira esto\n\nlinea pegada\n\nfin")
        self.assertEqual(len(mod.clean_prompt("x" * 50_000)), mod.MAX_PROMPT_CHARS)


class TestVelocidad(HookTestCase):
    def test_tiempo_por_ejecucion(self):
        runs = 10
        t0 = time.perf_counter()
        for _ in range(runs):
            self.run_hook(self.event("guion para un reel de tiktok"), SKILL_ROUTER_DEMO="1")
        per_run = (time.perf_counter() - t0) / runs
        # Holgado a propósito: solo detecta regresiones graves (p.ej. llamar a un LLM).
        self.assertLess(per_run, 2.0, f"{per_run:.3f}s por ejecución")


class TestSkillLauncher(HookTestCase):
    def run_launcher(self, *args, **env):
        e = dict(self.env)
        e.update(env)
        return subprocess.run([sys.executable, str(RUNNER), *args], capture_output=True,
                              env=e, timeout=60)

    def test_recomienda(self):
        proc = self.run_launcher("--demo", "--json", "repasar para el examen de calculo")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(data["recommended"][0]["name"], "estudio-a-medida")

    def test_conflictos(self):
        proc = self.run_launcher("--conflicts", "--demo", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("conflicts", json.loads(proc.stdout.decode("utf-8")))

    def test_skill_home_invalido_cae_a_la_ruta_real(self):
        proc = self.run_launcher("--demo", "--json", "guion para un reel",
                                 SKILL_ROUTER_HOME=str(self.tmp / "no-existe"))
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_enlace_simbolico(self):
        skills = self.home / ".claude" / "skills"
        skills.mkdir(parents=True)
        link = skills / "skill-router"
        link.symlink_to(RUNNER.parent, target_is_directory=True)
        proc = subprocess.run([sys.executable, str(link / "run_router.py"), "--demo", "--json", "guion reel"],
                              capture_output=True, env=self.env, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_frontmatter_de_la_skill(self):
        sys.path.insert(0, str(ROUTER))
        try:
            import skill_router
        finally:
            sys.path.remove(str(ROUTER))
        found = [s for s in skill_router.load_from_dir(INTEGRATION) if s.name == "skill-router"]
        self.assertEqual(len(found), 1)
        self.assertIn("qué skills debería usar", found[0].description)
        self.assertLessEqual(len(found[0].description), 1536)


if __name__ == "__main__":
    unittest.main()
