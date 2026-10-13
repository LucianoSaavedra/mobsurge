#!/usr/bin/env python3
"""
Pruebas del hook UserPromptSubmit y del lanzador de la skill (solo stdlib).

    python3 integration/tests/test_route_prompt.py -v

Cada prueba ejecuta el hook como lo haría Claude Code: un proceso aparte que
recibe el JSON del evento por stdin. HOME y cwd apuntan a carpetas temporales
para no depender de las skills de la máquina, y un `claude` trampa en el PATH
hace fallar la prueba si alguien llama al LLM de verdad.
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


TRAP_MARK = "trap-claude-called.txt"


def load_hook_module():
    spec = importlib.util.spec_from_file_location("route_prompt_under_test", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write_fake_claude(bindir: Path, body: str) -> Path:
    bindir.mkdir(parents=True, exist_ok=True)
    fake = bindir / "claude"
    fake.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    fake.chmod(0o755)
    return fake


class HookTestCase(unittest.TestCase):
    """Aislado: HOME y cwd en carpetas temporales, y un `claude` trampa en el PATH
    que hace fallar la prueba si alguien lo invoca de verdad."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="skill-router-test-")
        self.tmp = Path(self._tmp.name)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.cwd = self.tmp / "cwd"
        self.cwd.mkdir()
        self.trap_mark = self.tmp / TRAP_MARK
        trapdir = self.tmp / "trapbin"
        write_fake_claude(trapdir, f"echo \"$@\" >> '{self.trap_mark}'\nexit 97\n")
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith("SKILL_ROUTER_") and k not in ("CLAUDE_PROJECT_DIR",
                                                                      "ANTHROPIC_API_KEY")}
        self.env["HOME"] = str(self.home)
        self.env["PYTHONDONTWRITEBYTECODE"] = "1"
        self.env["PATH"] = f"{trapdir}{os.pathsep}{os.environ.get('PATH', '')}"

    def tearDown(self):
        called = self.trap_mark.read_text() if self.trap_mark.exists() else ""
        self._tmp.cleanup()
        self.assertEqual(called, "", "se invocó el `claude` real (trampa del PATH)")

    def run_hook(self, stdin: str, args: list[str] | None = None, **env) -> subprocess.CompletedProcess:
        e = dict(self.env)
        e.update(env)
        return subprocess.run([sys.executable, str(HOOK), *(args or [])], input=stdin.encode("utf-8"),
                              capture_output=True, env=e, timeout=60, cwd=str(self.cwd))

    def event(self, prompt, **extra) -> str:
        ev = {"session_id": "test", "transcript_path": str(self.tmp / "t.jsonl"),
              "cwd": str(self.cwd), "permission_mode": "default",
              "hook_event_name": "UserPromptSubmit", "prompt": prompt}
        ev.update(extra)
        return json.dumps(ev, ensure_ascii=False)

    def assertNoHiddenError(self, proc: subprocess.CompletedProcess):
        self.assertNotIn(b"error ignorado", proc.stderr, proc.stderr)
        self.assertNotIn(b"Traceback", proc.stderr, proc.stderr)

    def context_of(self, proc: subprocess.CompletedProcess) -> str:
        self.assertEqual(proc.returncode, 0)
        self.assertNoHiddenError(proc)
        out = proc.stdout.decode("utf-8")
        self.assertTrue(out.strip(), f"se esperaba salida; stderr={proc.stderr!r}")
        data = json.loads(out)  # stdout debe ser UN objeto JSON y nada más
        self.assertEqual(set(data), {"hookSpecificOutput"})
        hso = data["hookSpecificOutput"]
        self.assertEqual(set(hso), {"hookEventName", "additionalContext"})
        self.assertEqual(hso["hookEventName"], "UserPromptSubmit")
        ctx = hso["additionalContext"]
        self.assertIsInstance(ctx, str)
        self.assertNotIn("\n", ctx)  # una sola línea: ahorra tokens en cada turno
        self.assertLessEqual(len(ctx), 400)
        return ctx

    @staticmethod
    def first_skill(ctx: str) -> str:
        return ctx.split("podría servir ", 1)[1]

    def assertSilent(self, proc: subprocess.CompletedProcess, allow_error: bool = False):
        """Silencio deliberado: exit 0, stdout vacío y, salvo que se diga lo
        contrario, ningún error tragado en stderr."""
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, b"")
        if not allow_error:
            self.assertNoHiddenError(proc)


class TestHookContract(HookTestCase):
    def test_estudio_directo(self):
        ctx = self.context_of(self.run_hook(
            self.event("quiero preparar mi examen de cálculo con flashcards y un simulacro"),
            SKILL_ROUTER_DEMO="1"))
        self.assertTrue(self.first_skill(ctx).startswith("estudio-a-medida ("), ctx)
        self.assertNotIn("Ruido", ctx)  # no se listan las que sobran: gasta tokens

    def test_video(self):
        ctx = self.context_of(self.run_hook(
            self.event("escríbeme un guion para un reel de tiktok con buen gancho"),
            SKILL_ROUTER_DEMO="1"))
        self.assertTrue(self.first_skill(ctx).startswith("guiones-video ("), ctx)

    def test_negativo_hola(self):
        self.assertSilent(self.run_hook(self.event("hola"), SKILL_ROUTER_DEMO="1"))

    def test_json_malformado(self):
        proc = self.run_hook('{"prompt": "examen de calculo", ', SKILL_ROUTER_DEMO="1")
        self.assertSilent(proc, allow_error=True)
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
        self.assertIn(b"no encuentro skill_router.py", proc.stderr)

    def test_umbral_y_top_configurables(self):
        # "astronomia" no coincide con nada -> la puntuación no llega a 100
        self.assertSilent(self.run_hook(self.event("guion para un reel de tiktok sobre astronomia"),
                                        SKILL_ROUTER_DEMO="1", SKILL_ROUTER_HOOK_THRESHOLD="100"))
        ctx = self.context_of(self.run_hook(
            self.event("guion de video y campaña de marketing para redes"),
            SKILL_ROUTER_DEMO="1", SKILL_ROUTER_HOOK_TOP="1"))
        self.assertNotIn("; ", self.first_skill(ctx).split(". Ignóralo")[0], ctx)
        ctx2 = self.context_of(self.run_hook(
            self.event("guion de video y campaña de marketing para redes"),
            SKILL_ROUTER_DEMO="1", SKILL_ROUTER_HOOK_TOP="2", SKILL_ROUTER_HOOK_MARGIN="0"))
        self.assertIn("; ", self.first_skill(ctx2), ctx2)
        # valores basura -> se usan los de por defecto, sin romper
        self.context_of(self.run_hook(self.event("guion para un reel de tiktok"), SKILL_ROUTER_DEMO="1",
                                      SKILL_ROUTER_HOOK_TOP="x", SKILL_ROUTER_HOOK_THRESHOLD="y"))

    def test_modo_texto_manual(self):
        proc = self.run_hook("", args=["--prompt", "guion para un reel de tiktok", "--texto"],
                             SKILL_ROUTER_DEMO="1")
        self.assertEqual(proc.returncode, 0)
        out = proc.stdout.decode("utf-8")
        self.assertTrue(out.startswith("Skill Router ("), out)
        self.assertIn("guiones-video", out)

    def test_llm_sin_backend_cae_al_lexico(self):
        proc = self.run_hook(self.event("guion para un reel de tiktok"), SKILL_ROUTER_DEMO="1",
                             SKILL_ROUTER_HOOK_LLM="1", PATH=str(self.tmp / "sin-binarios"),
                             ANTHROPIC_API_KEY="")
        ctx = self.context_of(proc)
        self.assertIn("(palabras clave)", ctx)
        self.assertIn(b"scorer LLM no disponible", proc.stderr)

    def test_llm_con_cli_falso_y_guardia_heredada(self):
        """Un `claude` falso en el PATH: comprueba el camino LLM y que el proceso
        hijo hereda SKILL_ROUTER_HOOK_ACTIVE=1 (lo que corta la recursión)."""
        bindir = self.tmp / "bin"
        seen = self.tmp / "guardia.txt"
        write_fake_claude(bindir,
            f"printf '%s|%s|%s' \"$SKILL_ROUTER_HOOK_ACTIVE\" \"$SKILL_ROUTER_CHILD\" "
            f"\"$SKILL_ROUTER_LLM_TIMEOUT\" > '{seen}'\n"
            "cat <<'EOF'\n"
            '{"recommended": [{"name": "guiones-video", "score": 90, '
            '"reason": "pide un guion\\nde reel"}]}\n'  # \n escapado en el JSON
            "EOF\n")
        proc = self.run_hook(self.event("guion para un reel de tiktok"), SKILL_ROUTER_DEMO="1",
                             SKILL_ROUTER_HOOK_LLM="1",
                             PATH=f"{bindir}{os.pathsep}{self.env.get('PATH', '')}")
        ctx = self.context_of(proc)
        self.assertIn("Skill Router (LLM)", ctx)
        self.assertIn("guiones-video (pide un guion de reel)", ctx)
        # guardias heredadas por el hijo y timeout del hook (20 s) para el LLM
        self.assertEqual(seen.read_text(), "1|1|20")


class TestSkillSources(HookTestCase):
    """De dónde salen las skills. La fuente se lee del diagnóstico en stderr
    (SKILL_ROUTER_HOOK_DEBUG=1): ya no va en la nota, para ahorrar tokens."""

    def debug_hook(self, prompt, **env):
        proc = self.run_hook(self.event(prompt, **env.pop("ev", {})), SKILL_ROUTER_HOOK_DEBUG="1", **env)
        self.assertEqual(proc.returncode, 0)
        self.assertNoHiddenError(proc)
        return proc, proc.stderr.decode("utf-8")

    def test_skills_instaladas_y_autoexclusion(self):
        skills = self.home / ".claude" / "skills"
        write_skill(skills, "video-maker", "video-maker", "Guion de video corto para reels y tiktok con gancho.")
        write_skill(skills, "excel-pro", "excel-pro", "Hojas de calculo, formulas y graficos en Excel.")
        write_skill(skills, "skill-router", "skill-router",
                    "Recomienda que skills usar para un guion de video, reel o tiktok.")
        proc, err = self.debug_hook("guion para un reel de tiktok")
        ctx = self.context_of(proc)
        self.assertTrue(self.first_skill(ctx).startswith("video-maker ("), ctx)
        self.assertNotIn("skill-router", ctx)
        self.assertIn("2 skills (skills instaladas en ~/.claude/skills)", err)

    def test_skills_del_proyecto(self):
        project = self.tmp / "proyecto"
        write_skill(project / ".claude" / "skills", "facturas", "facturas",
                    "Genera facturas y presupuestos en PDF para clientes.")
        write_skill(self.home / ".claude" / "skills", "video-maker", "video-maker",
                    "Guion de video corto para reels y tiktok con gancho.")
        ctx = self.context_of(self.run_hook(
            self.event("hazme las facturas del mes en pdf", cwd=str(project))))
        self.assertIn("podría servir facturas (", ctx)
        ctx2 = self.context_of(self.run_hook(self.event("hazme las facturas del mes en pdf"),
                                             CLAUDE_PROJECT_DIR=str(project)))
        self.assertIn("podría servir facturas (", ctx2)

    def test_skills_de_la_raiz_git_desde_subdirectorio(self):
        """int-7: Claude Code carga .claude/skills desde el directorio de inicio
        subiendo hasta la raíz del repo; el hook también."""
        repo = self.tmp / "repo"
        (repo / ".git").mkdir(parents=True)
        write_skill(repo / ".claude" / "skills", "guiones", "guiones",
                    "Guion de video corto para reels y tiktok con gancho.")
        app = repo / "app" / "web"
        app.mkdir(parents=True)
        proc, err = self.debug_hook("guion para un reel de tiktok", ev={"cwd": str(app)})
        ctx = self.context_of(proc)
        self.assertIn("podría servir guiones (", ctx)
        self.assertIn(f"{repo}/.claude/skills", err)
        ctx2 = self.context_of(self.run_hook(self.event("guion para un reel de tiktok"),
                                             CLAUDE_PROJECT_DIR=str(app)))
        self.assertIn("podría servir guiones (", ctx2)

    def test_sin_repo_git_no_sube_a_los_padres(self):
        parent = self.tmp / "suelto"
        write_skill(parent / ".claude" / "skills", "guiones", "guiones",
                    "Guion de video corto para reels y tiktok con gancho.")
        sub = parent / "sub"
        sub.mkdir()
        write_skill(self.home / ".claude" / "skills", "excel-pro", "excel-pro",
                    "Hojas de calculo, formulas y graficos en Excel.")
        proc, err = self.debug_hook("guion para un reel de tiktok", ev={"cwd": str(sub)})
        self.assertSilent(proc)
        self.assertIn("1 skills (skills instaladas en ~/.claude/skills)", err)

    def test_nombres_repetidos_cuentan_una_vez(self):
        project = self.tmp / "proyecto"
        for root in (self.home / ".claude" / "skills", project / ".claude" / "skills"):
            write_skill(root, "video-maker", "video-maker", "Guion de video corto para reels y tiktok.")
        write_skill(project / ".claude" / "skills", "otra-video", "video-maker",
                    "Guion de video corto para reels y tiktok.")
        proc, err = self.debug_hook("guion para un reel de tiktok", ev={"cwd": str(project)})
        self.context_of(proc)
        self.assertIn("1 skills (skills instaladas en ~/.claude/skills)", err)

    def test_carpeta_explicita(self):
        other = self.tmp / "otras"
        write_skill(other, "podcast", "podcast", "Guion y escaleta para episodios de podcast.")
        ctx = self.context_of(self.run_hook(self.event("escaleta para mi podcast"),
                                            SKILL_ROUTER_SKILLS_DIR=str(other)))
        self.assertIn("podría servir podcast (", ctx)

    def test_sin_skills_usa_el_catalogo(self):
        catalog = ROUTER.parent / "skills.json"
        if not catalog.is_file():
            self.skipTest("no hay skills.json junto al router")
        ctx = self.context_of(self.run_hook(self.event("guion para un reel viral de tiktok")))
        self.assertIn("del catálogo, quizá no instalada", ctx)

    def test_solo_skill_router_instalada_usa_el_catalogo(self):
        """int-5: tras instalar (paso 4 del README) ~/.claude/skills solo tiene
        skill-router. Se descarta ANTES de decidir si hay skills, así que el
        catálogo de reserva sí se usa."""
        if not (ROUTER.parent / "skills.json").is_file():
            self.skipTest("no hay skills.json junto al router")
        skills = self.home / ".claude" / "skills"
        skills.mkdir(parents=True)
        (skills / "skill-router").symlink_to(RUNNER.parent, target_is_directory=True)
        proc, err = self.debug_hook("guion para un reel viral de tiktok")
        self.assertIn("del catálogo, quizá no instalada", self.context_of(proc))
        self.assertIn("(catálogo skills.json)", err)

    def test_sin_skills_ni_catalogo(self):
        fake_router = self.tmp / "router"
        fake_router.mkdir()
        (fake_router / "skill_router.py").write_text((ROUTER / "skill_router.py").read_text("utf-8"), "utf-8")
        (fake_router / "concepts.py").write_text((ROUTER / "concepts.py").read_text("utf-8"), "utf-8")
        proc, err = self.debug_hook("guion para un reel viral de tiktok",
                                    SKILL_ROUTER_HOME=str(fake_router))
        self.assertSilent(proc)
        self.assertIn("no hay skills; no recomiendo nada", err)


class TestPrompt(unittest.TestCase):
    def setUp(self):
        self.mod = load_hook_module()
        sys.path.insert(0, str(ROUTER))
        import skill_router
        self.sr = skill_router

    def tearDown(self):
        sys.path.remove(str(ROUTER))

    def test_clean_prompt_quita_marcas_y_recorta(self):
        raw = ('mira esto\n<pasted_content id="1">\nlinea pegada\n</pasted_content id="1">\nfin')
        self.assertEqual(self.mod.clean_prompt(raw), "mira esto\n\nlinea pegada\n\nfin")
        self.assertEqual(len(self.mod.clean_prompt("x" * 50_000)), self.mod.MAX_PROMPT_CHARS)

    def test_recorte_conserva_el_final(self):
        text = "a " * 5000 + "PETICION FINAL"
        clipped = self.mod.clip(text)
        self.assertEqual(len(clipped), self.mod.MAX_PROMPT_CHARS)
        self.assertTrue(clipped.endswith("PETICION FINAL"))

    def test_split_normaliza_antes_de_recortar(self):
        """int-2: el log pegado se quita ANTES del recorte; lo tecleado sobrevive."""
        log = "\n".join(f"2026-10-13 12:00:{i % 60:02d} ERROR worker fallo {i}" for i in range(400))
        raw = f'<pasted_content id="1">\n{log}\n</pasted_content id="1">\nescríbeme un guion para un reel'
        typed, full = self.mod.split_prompt(self.sr, raw)
        self.assertIn("escríbeme un guion para un reel", typed)
        self.assertNotIn("ERROR", typed)
        self.assertLessEqual(len(full), self.mod.MAX_PROMPT_CHARS)


class TestPrecision(HookTestCase):
    """int-1: ante la duda, no inyectar nada."""

    def test_una_sola_palabra_suelta_no_basta(self):
        skills = self.home / ".claude" / "skills"
        write_skill(skills, "browser", "browser", "Control the browser app and click on web pages.")
        write_skill(skills, "desktop", "desktop", "Use any desktop app on the computer with screenshots.")
        write_skill(skills, "sheets", "sheets", "Edit spreadsheets, cells and formulas.")
        self.assertSilent(self.run_hook(self.event("genera un dockerfile para esta app")))
        # con el filtro apagado (MIN_MATCHED=1, umbral 15, sin margen) sí inyectaba
        self.context_of(self.run_hook(self.event("genera un dockerfile para esta app"),
                                      SKILL_ROUTER_HOOK_MIN_MATCHED="1", SKILL_ROUTER_HOOK_THRESHOLD="15",
                                      SKILL_ROUTER_HOOK_MARGIN="0"))

    def test_margen_frente_al_segundo(self):
        mod = load_hook_module()
        sys.path.insert(0, str(ROUTER))
        try:
            import skill_router as sr
        finally:
            sys.path.remove(str(ROUTER))
        results = [{"name": "a", "score": 40, "matched": ["guion", "reel"], "scenario": "", "source": ""},
                   {"name": "b", "score": 35, "matched": ["guion"], "scenario": "", "source": ""},
                   {"name": "c", "score": 0, "matched": [], "scenario": "", "source": ""}]
        self.assertEqual(mod.select(sr, results, False), [])  # 5 puntos: no destaca
        results[1]["score"] = 20
        self.assertEqual([r["name"] for r in mod.select(sr, results, False)], ["a"])
        # el LLM ya decidió: no se le aplican min_matched ni margen
        llm = [{"name": "b", "score": 80, "matched": ["motivo"], "scenario": "", "source": ""},
               {"name": "a", "score": 75, "matched": ["motivo"], "scenario": "", "source": ""}]
        self.assertEqual([r["name"] for r in mod.select(sr, llm, True)], ["b", "a"])

    def test_nota_corta(self):
        ctx = self.context_of(self.run_hook(
            self.event("quiero preparar mi examen de cálculo con flashcards y un simulacro"),
            SKILL_ROUTER_DEMO="1"))
        self.assertLess(len(ctx), 160, ctx)
        self.assertNotIn("relevancia", ctx)


class TestPegado(HookTestCase):
    """int-2: lo tecleado manda; lo pegado solo apoya si lo tecleado no basta."""

    def test_log_largo_y_peticion_al_final(self):
        log = "\n".join(f"2026-10-13 12:00:{i % 60:02d} ERROR worker fallo bug {i}" for i in range(400))
        prompt = (f'<pasted_content id="1">\n{log}\n</pasted_content id="1">\n'
                  "escríbeme un guion para un reel de tiktok con buen gancho sobre esto")
        ctx = self.context_of(self.run_hook(self.event(prompt), SKILL_ROUTER_DEMO="1"))
        self.assertTrue(self.first_skill(ctx).startswith("guiones-video ("), ctx)

    def test_traza_pegada_no_arrastra_la_recomendacion(self):
        tb = ("Traceback (most recent call last):\n  File \"app.py\", line 3, in <module>\n"
              "    main()\nValueError: error de seguridad, bug en el codigo\n")
        ctx = self.context_of(self.run_hook(
            self.event(tb + "con esto hazme un guion para un reel de tiktok con buen gancho"),
            SKILL_ROUTER_DEMO="1"))
        self.assertTrue(self.first_skill(ctx).startswith("guiones-video ("), ctx)
        self.assertNotIn("code-review", ctx)


class TestGuardiasYModos(HookTestCase):
    def test_hijo_del_scorer_sale_en_silencio(self):
        proc = self.run_hook(self.event("guion para un reel de tiktok"),
                             SKILL_ROUTER_DEMO="1", SKILL_ROUTER_CHILD="1")
        self.assertSilent(proc)
        self.assertEqual(proc.stderr, b"")

    def test_modo_por_defecto_es_lexico(self):
        # con SKILL_ROUTER_HOOK_MODE explícito, manda sobre SKILL_ROUTER_HOOK_LLM;
        # la trampa del PATH fallaría si se llamara a `claude`
        for env in ({}, {"SKILL_ROUTER_HOOK_MODE": "lexical", "SKILL_ROUTER_HOOK_LLM": "1"},
                    {"SKILL_ROUTER_HOOK_MODE": "rarisimo"}):
            ctx = self.context_of(self.run_hook(self.event("guion para un reel de tiktok"),
                                                SKILL_ROUTER_DEMO="1", **env))
            self.assertIn("(palabras clave)", ctx)

    def _fake_llm(self, calls: Path) -> str:
        bindir = self.tmp / "bin"
        write_fake_claude(bindir,
            f"echo llamada >> '{calls}'\n"
            "cat <<'EOF'\n"
            '{"recommended": [{"name": "marca-marketing", "score": 85, "reason": "contenido para redes"}]}\n'
            "EOF\n")
        return f"{bindir}{os.pathsep}{self.env['PATH']}"

    def test_modo_llm(self):
        calls = self.tmp / "calls.txt"
        ctx = self.context_of(self.run_hook(
            self.event("necesito ideas para el contenido de la semana que viene en mis cuentas"),
            SKILL_ROUTER_DEMO="1", SKILL_ROUTER_HOOK_MODE="llm", PATH=self._fake_llm(calls)))
        self.assertIn("Skill Router (LLM): podría servir marca-marketing (contenido para redes)", ctx)
        self.assertEqual(calls.read_text().count("llamada"), 1)

    def test_modo_cascada_solo_llama_si_duda(self):
        calls = self.tmp / "calls.txt"
        path = self._fake_llm(calls)
        # claro para el léxico: ninguna llamada
        ctx = self.context_of(self.run_hook(
            self.event("escríbeme un guion para un reel de tiktok con buen gancho"),
            SKILL_ROUTER_DEMO="1", SKILL_ROUTER_HOOK_MODE="cascade", PATH=path))
        self.assertIn("(palabras clave)", ctx)
        self.assertFalse(calls.exists())
        # ambiguo: una llamada
        ctx = self.context_of(self.run_hook(
            self.event("necesito ideas para el contenido de la semana que viene en mis cuentas"),
            SKILL_ROUTER_DEMO="1", SKILL_ROUTER_HOOK_MODE="cascade", PATH=path))
        self.assertIn("Skill Router (LLM)", ctx)
        self.assertEqual(calls.read_text().count("llamada"), 1)


class TestContratoSalida(HookTestCase):
    def test_json_ascii(self):
        proc = self.run_hook(self.event("guion para un reel de tiktok con música y canción"),
                             SKILL_ROUTER_DEMO="1")
        self.context_of(proc)
        self.assertTrue(proc.stdout.isascii(), proc.stdout)

    def test_descripcion_enorme_no_desborda(self):
        skills = self.home / ".claude" / "skills"
        long_name = "video-" + "x" * 300
        write_skill(skills, "larga", long_name, "Guion de video corto para reels y tiktok. " + "relleno " * 2000)
        ctx = self.context_of(self.run_hook(self.event("guion para un reel de tiktok")))
        self.assertLessEqual(len(ctx), 400)


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
        cwd = e.pop("_cwd", str(self.cwd))
        return subprocess.run([sys.executable, str(RUNNER), *args], capture_output=True,
                              env=e, timeout=60, cwd=cwd)

    def test_skill_y_hook_ven_las_mismas_skills(self):
        """int-5: HOME con solo skill-router enlazada (paso 4 del README) y skills
        del proyecto en la raíz git; la skill se lanza desde un subdirectorio."""
        skills = self.home / ".claude" / "skills"
        skills.mkdir(parents=True)
        (skills / "skill-router").symlink_to(RUNNER.parent, target_is_directory=True)
        repo = self.tmp / "repo"
        (repo / ".git").mkdir(parents=True)
        write_skill(repo / ".claude" / "skills", "guiones", "guiones",
                    "Guion de video corto para reels y tiktok con gancho.")
        write_skill(repo / ".claude" / "skills", "excel-pro", "excel-pro",
                    "Hojas de calculo, formulas y graficos en Excel.")
        sub = repo / "src"
        sub.mkdir()
        proc = self.run_launcher("--json", "guion para un reel de tiktok", _cwd=str(sub))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual([r["name"] for r in data["recommended"]], ["guiones"])
        everyone = {r["name"] for r in data["recommended"] + data["noise"]}
        self.assertNotIn("skill-router", everyone)
        self.assertIn(f"{repo}/.claude/skills", data["source"])
        ctx = self.context_of(self.run_hook(self.event("guion para un reel de tiktok", cwd=str(sub))))
        self.assertIn("podría servir guiones (", ctx)

    def test_sin_skills_ni_catalogo_lo_dice(self):
        fake_router = self.tmp / "router"
        (fake_router / "integration").mkdir(parents=True)
        for f in ("skill_router.py", "concepts.py"):
            (fake_router / f).write_text((ROUTER / f).read_text("utf-8"), "utf-8")
        (fake_router / "integration" / "hooks").mkdir()
        (fake_router / "integration" / "hooks" / "route_prompt.py").write_text(HOOK.read_text("utf-8"), "utf-8")
        copy = self.tmp / "copia" / "run_router.py"
        copy.parent.mkdir()
        copy.write_text(RUNNER.read_text("utf-8"), "utf-8")
        e = dict(self.env, SKILL_ROUTER_HOME=str(fake_router))
        proc = subprocess.run([sys.executable, str(copy), "--json", "guion reel"], capture_output=True,
                              env=e, timeout=60, cwd=str(self.cwd))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("No encontre skills", proc.stderr.decode("utf-8"))

    def test_copia_con_skill_router_home_valido(self):
        copy = self.tmp / "copia" / "run_router.py"
        copy.parent.mkdir()
        copy.write_text(RUNNER.read_text("utf-8"), "utf-8")
        e = dict(self.env, SKILL_ROUTER_HOME=str(ROUTER))
        proc = subprocess.run([sys.executable, str(copy), "--demo", "--json", "repasar para el examen"],
                              capture_output=True, env=e, timeout=60, cwd=str(self.cwd))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["recommended"][0]["name"], "estudio-a-medida")

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
                              capture_output=True, env=self.env, timeout=60, cwd=str(self.cwd))
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
