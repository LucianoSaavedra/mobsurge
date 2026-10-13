"""
Tests del CLI de skill_router.py: las flags que usa el guion de la demo
(--demo, --catalog, --skills-dir, --json, --no-concepts, --conflicts, --top,
--threshold, --min-matched) y los modos que gastan cuota (--llm, --cascade,
--model), siempre con backends simulados: ningún test llama a Claude.

    cd claude-skills-library/skill-router && python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import sys
import unittest
from unittest import mock

import _support
from _support import NoRealLLMMixin, TempDirTestCase, captured_output, write_skill

import cascade
import llm_scorer
import skill_router
from skill_router import demo_skills

CATALOG = _support.ROUTER_DIR.parent / "skills.json"
DEMO_NAMES = {s.name for s in demo_skills()}
PLAIN_KEYS = {"query", "source", "scorer", "recommended", "noise"}

PY_TRACEBACK = (
    "Traceback (most recent call last):\n"
    '  File "/app/server/main.py", line 42, in handle_request\n'
    '    result = process(payload["items"])\n'
    "ValueError: invalid literal for int() with base 10: 'abc'\n"
)


def _rows(skills, scores: dict[str, int], reason: str = "elegida por el LLM") -> list[dict]:
    """Filas con la forma de score_skills()/llm_score() (orden por score)."""
    rows = [{"name": s.name, "scenario": s.scenario, "score": scores.get(s.name, 0),
             "matched": [reason] if scores.get(s.name) else [], "source": s.source}
            for s in skills]
    rows.sort(key=lambda r: (-r["score"], r["name"]))
    return rows


def _usage(**kw) -> dict:
    """Un `usage` completo (contrato 5) con los valores dados."""
    base = dict.fromkeys(("backend", "model", "input_tokens", "output_tokens",
                          "cache_read_tokens", "cost_usd", "latency_s", "cached"))
    base.update(kw)
    return base


class CliBase(NoRealLLMMixin, TempDirTestCase):
    def setUp(self):
        super().setUp()
        self.block_real_llm()

    def run_main(self, *argv):
        with captured_output() as (out, err):
            code = skill_router.main([str(a) for a in argv])
        return code, out.getvalue(), err.getvalue()

    def run_json(self, *argv):
        code, out, err = self.run_main(*argv, "--json")
        self.assertEqual(code, 0, err)
        return json.loads(out), err

    @staticmethod
    def names(rows):
        return [r["name"] for r in rows]


# --- Guion de la demo: conceptos, umbral, top ------------------------------------------------
class DemoFlagsTest(CliBase):
    QUERY_SINONIMOS = "mañana tengo la prueba, necesito repasar"

    def test_no_concepts_apaga_los_sinonimos(self):
        # Paso 2 del guion: sin conceptos el sinónimo no encuentra nada.
        data, _ = self.run_json("--demo", "--no-concepts", self.QUERY_SINONIMOS)
        self.assertEqual(data["scorer"], "léxico")
        self.assertEqual(data["recommended"], [])
        data, _ = self.run_json("--demo", self.QUERY_SINONIMOS)
        self.assertEqual(data["scorer"], "léxico+conceptos")
        self.assertEqual(data["recommended"][0]["name"], "estudio-a-medida")

    def test_json_sin_llm_mantiene_su_forma(self):
        data, _ = self.run_json("--demo", "revisa este pull request")
        self.assertEqual(set(data), PLAIN_KEYS)
        self.assertEqual(data["source"], "demo")

    def test_threshold_se_respeta(self):
        q = "quiero preparar mi examen de calculo"
        data, _ = self.run_json("--demo", q)
        first = data["recommended"][0]
        self.assertEqual(first["name"], "estudio-a-medida")
        data, _ = self.run_json("--demo", "--threshold", first["score"] + 1, q)
        self.assertEqual(data["recommended"], [])
        self.assertIn("estudio-a-medida", self.names(data["noise"]))
        data, _ = self.run_json("--demo", "--threshold", first["score"], q)
        self.assertEqual(self.names(data["recommended"]), ["estudio-a-medida"])

    def test_top_limita_las_recomendadas(self):
        q = "revisa el diff y diagnostica el error del examen"
        data, _ = self.run_json("--demo", q)
        self.assertGreaterEqual(len(data["recommended"]), 2)
        one, _ = self.run_json("--demo", "--top", "1", q)
        self.assertEqual(self.names(one["recommended"]), self.names(data["recommended"])[:1])

    def test_min_matched_quita_las_coincidencias_sueltas(self):
        q = "cuentame un chiste corto"  # guiones-video solo por "corto"
        data, _ = self.run_json("--demo", q)
        self.assertEqual(self.names(data["recommended"]), ["guiones-video"])
        data, _ = self.run_json("--demo", "--min-matched", "2", q)
        self.assertEqual(data["recommended"], [])
        self.assertIn("guiones-video", self.names(data["noise"]))

    def test_traceback_pegado_no_tapa_la_peticion(self):
        q = "tengo este error en produccion, ayudame a depurarlo\n" + PY_TRACEBACK
        data, _ = self.run_json("--demo", q)
        self.assertEqual(self.names(data["recommended"]), ["debug"])
        code, out, _ = self.run_main("--demo", q)
        self.assertEqual(code, 0)
        self.assertIn("no se puntuó lo pegado", out)
        _code, out, _ = self.run_main("--demo", "tengo este error, ayudame a depurarlo")
        self.assertNotIn("no se puntuó lo pegado", out)


# --- Fuentes de skills: --catalog y --skills-dir ---------------------------------------------
class SourcesTest(CliBase):
    def write_catalog(self):
        # Dos skills que solo se parecen por el concepto ~estudio (sinónimos).
        p = self.tmp / "skills.json"
        p.write_text(json.dumps({"scenarios": {
            "s1": {"title": "Alfa", "repos": [{"name": "aaa", "about": "examen simulacro repaso",
                                               "url": "u1"}]},
            "s2": {"title": "Beta", "repos": [{"name": "bbb", "about": "prueba parcial apuntes",
                                               "url": "u2"}]},
        }}), encoding="utf-8")
        return p

    def test_catalog_carga_el_catalogo_no_el_demo(self):
        p = self.write_catalog()
        data, _ = self.run_json("--catalog", p, "quiero repasar el temario")
        self.assertTrue(data["source"].startswith("catalog:"), data["source"])
        names = set(self.names(data["recommended"]) + self.names(data["noise"]))
        self.assertEqual(names, {"aaa", "bbb"})
        self.assertFalse(names & DEMO_NAMES)

    def test_conflicts_catalog_con_y_sin_conceptos(self):
        p = self.write_catalog()
        data, _ = self.run_json("--conflicts", "--catalog", p)
        self.assertTrue(data["source"].startswith("catalog:"))
        self.assertEqual(len(data["conflicts"]), 1)
        pair = data["conflicts"][0]
        self.assertEqual({pair["a"], pair["b"]}, {"aaa", "bbb"})
        self.assertEqual(pair["shared"], ["~estudio"])
        data, _ = self.run_json("--conflicts", "--no-concepts", "--catalog", p)
        self.assertEqual(data["conflicts"], [])

    def test_conflicts_catalog_en_texto(self):
        # Paso 4 del guion: la salida de texto lista el par y lo que comparten.
        p = self.write_catalog()
        code, out, _ = self.run_main("--conflicts", "--catalog", p)
        self.assertEqual(code, 0)
        self.assertRegex(out, r"\d\.\d\d  aaa  ⇄  bbb")
        self.assertIn("comparten: ~estudio", out)
        code, out, _ = self.run_main("--conflicts", "--no-concepts", "--catalog", p)
        self.assertIn("Ninguno por encima del umbral", out)

    @unittest.skipUnless(CATALOG.is_file(), "no está el skills.json del repo")
    def test_conflicts_catalogo_real_remotion_primero(self):
        data, _ = self.run_json("--conflicts", "--catalog", CATALOG)
        self.assertTrue(data["conflicts"])
        first = data["conflicts"][0]
        self.assertEqual({first["a"], first["b"]}, {"remotion-skills", "remotion-video-skill"})

    def test_catalog_inexistente_sale_con_1(self):
        code, _, err = self.run_main("--catalog", self.tmp / "no.json", "examen")
        self.assertEqual(code, 1)
        self.assertIn("No encontre skills", err)

    def test_skills_dir_en_texto(self):
        write_skill(self.tmp, "cocina", "cocina", "recetas de pasta y salsa de tomate")
        write_skill(self.tmp, "fiscal", "fiscal", "impuestos declaracion renta hacienda")
        code, out, _ = self.run_main("--skills-dir", self.tmp, "receta de pasta")
        self.assertEqual(code, 0)
        self.assertIn(f"dir:{self.tmp}", out)
        self.assertIn("(2 skills)", out)
        self.assertIn("cocina", out.split("Usa estas ahora")[1].split("Ruido")[0])


# --- --llm y --model ------------------------------------------------------------------------
class LlmFlagTest(CliBase):
    def fake_llm(self, usage_values: dict, scores: dict | None = None):
        calls = []

        def fake(skills, query, backend=None, model=None, usage=None):
            calls.append({"query": query, "model": model, "backend": backend})
            if isinstance(usage, dict):
                usage.update(_usage(**usage_values))
            return _rows(skills, scores or {"debug": 90})
        return fake, calls

    def test_llm_ausente_cae_al_lexico(self):
        with mock.patch.object(llm_scorer, "available_backend", return_value=None):
            data, err = self.run_json("--demo", "--llm", "revisa este pull request")
        self.assertIn("Scorer LLM no disponible", err)
        self.assertEqual(data["scorer"], "léxico+conceptos")
        self.assertIs(data["used_llm"], False)
        self.assertEqual(data["recommended"][0]["name"], "code-review")

    def test_llm_muestra_tokens_latencia_y_modelo(self):
        fake, calls = self.fake_llm({"backend": "cli", "model": "claude-haiku-4-5",
                                     "input_tokens": 1234, "output_tokens": 56,
                                     "cache_read_tokens": 0, "cost_usd": 0.0123,
                                     "latency_s": 2.5, "cached": False})
        with mock.patch.object(llm_scorer, "llm_score", side_effect=fake):
            code, out, _ = self.run_main("--demo", "--llm", "--model", "claude-haiku-4-5",
                                         "mi app falla")
        self.assertEqual(code, 0)
        self.assertEqual(calls[0]["model"], "claude-haiku-4-5")  # --model llega al LLM
        self.assertIn("scorer: LLM (cli)", out)
        self.assertIn("1234 tokens de entrada", out)
        self.assertIn("56 de salida", out)
        self.assertIn("2.50 s", out)
        self.assertIn("0.0123 USD", out)
        self.assertIn("claude-haiku-4-5", out)

    def test_llm_json_incluye_usage(self):
        fake, _ = self.fake_llm({"backend": "sdk", "model": "m", "input_tokens": 10,
                                 "output_tokens": 2, "latency_s": 0.4, "cached": False})
        with mock.patch.object(llm_scorer, "llm_score", side_effect=fake):
            data, _ = self.run_json("--demo", "--llm", "x")
        self.assertIs(data["used_llm"], True)
        self.assertEqual(data["scorer"], "LLM (sdk)")
        self.assertEqual(data["usage"]["input_tokens"], 10)
        self.assertEqual(self.names(data["recommended"]), ["debug"])

    def test_llm_desde_cache_no_gasta_tokens(self):
        fake, _ = self.fake_llm({"backend": "cli", "model": "m", "input_tokens": 0,
                                 "output_tokens": 0, "latency_s": 0.01, "cached": True})
        with mock.patch.object(llm_scorer, "llm_score", side_effect=fake):
            code, out, _ = self.run_main("--demo", "--llm", "x")
        self.assertEqual(code, 0)
        self.assertIn("LLM (cli, caché)", out)
        self.assertIn("caché local (0 tokens gastados)", out)

    def test_campos_no_informados_salen_como_nd(self):
        # Contrato 5: lo que el backend no informa es None y no se inventa.
        fake, _ = self.fake_llm({"backend": "cli", "model": "m", "latency_s": None,
                                 "cached": False})
        with mock.patch.object(llm_scorer, "llm_score", side_effect=fake):
            _code, out, _ = self.run_main("--demo", "--llm", "x")
        self.assertIn("n/d tokens de entrada + n/d de salida  ·  n/d", out)

    def test_llm_con_backend_simulado_de_punta_a_punta(self):
        # El llm_score real con _call_cli simulado: el modelo de --model llega
        # al backend y la respuesta se usa como resultado.
        class Reply(str):
            usage = {"input_tokens": 700, "output_tokens": 20, "cache_read_tokens": 0,
                     "cost_usd": None}
        seen = {}

        def fake_cli(prompt, model=None):
            seen["model"] = model
            return Reply('{"recommended": [{"name": "debug", "score": 88, "reason": "traza"}]}')

        with mock.patch.object(llm_scorer, "available_backend", return_value="cli"), \
                mock.patch.object(llm_scorer, "_call_cli", side_effect=fake_cli):
            data, _ = self.run_json("--demo", "--llm", "--model", "modelo-x", "mi app falla")
        self.assertEqual(seen["model"], "modelo-x")
        self.assertEqual(data["scorer"], "LLM (cli)")
        self.assertEqual(self.names(data["recommended"]), ["debug"])
        self.assertEqual(data["usage"]["backend"], "cli")
        self.assertEqual(data["usage"]["model"], "modelo-x")
        self.assertIs(data["usage"]["cached"], False)

    def test_model_sin_llm_avisa_y_no_llama(self):
        code, _, err = self.run_main("--demo", "--model", "x", "revisa este pull request")
        self.assertEqual(code, 0)
        self.assertIn("--model solo se usa con --llm o --cascade", err)

    def test_llm_y_cascade_son_excluyentes(self):
        with captured_output(), self.assertRaises(SystemExit) as cm:
            skill_router.main(["--demo", "--llm", "--cascade", "hola"])
        self.assertEqual(cm.exception.code, 2)


# --- --cascade ----------------------------------------------------------------------------
class CascadeFlagTest(CliBase):
    def test_cascada_real_sin_llm_en_un_mensaje_claro(self):
        # Mensaje claro: el léxico basta y no se toca el LLM (las llamadas
        # reales revientan por NoRealLLMMixin y saldrían como aviso en stderr).
        data, err = self.run_json("--demo", "--cascade",
                                  "revisa este pull request por bugs de seguridad")
        self.assertEqual(err, "")
        self.assertIs(data["used_llm"], False)
        self.assertTrue(data["scorer"].startswith("cascada: léxico+conceptos"), data["scorer"])
        self.assertEqual(data["usage"]["decision"], "lexical")
        self.assertEqual(data["recommended"][0]["name"], "code-review")
        code, out, _ = self.run_main("--demo", "--cascade",
                                     "revisa este pull request por bugs de seguridad")
        self.assertIn("0 llamadas al LLM, 0 tokens", out)

    def test_cascada_real_consulta_al_llm_simulado(self):
        calls = []

        def fake(skills, query, backend=None, model=None, usage=None):
            calls.append(model)
            if isinstance(usage, dict):
                usage.update(_usage(backend="cli", model=model, input_tokens=900,
                                    output_tokens=30, cache_read_tokens=0, latency_s=1.5,
                                    cached=False))
            return _rows(skills, {"marca-marketing": 70})

        q = "necesito cancelar la suscripcion del gimnasio cuanto antes"  # el léxico no sabe
        with mock.patch.object(llm_scorer, "llm_score", side_effect=fake):
            data, err = self.run_json("--demo", "--cascade", "--model", "m-1", q)
        self.assertEqual(err, "")
        self.assertEqual(calls, ["m-1"])
        self.assertIs(data["used_llm"], True)
        self.assertEqual(data["scorer"], "cascada: LLM (cli)")
        self.assertEqual(data["usage"]["input_tokens"], 900)
        self.assertEqual(self.names(data["recommended"]), ["marca-marketing"])

    def test_cascada_simulada_texto_con_tokens(self):
        def fake(skills, query, high=60, margin=20, model=None, usage=None):
            usage.update(_usage(backend="sdk", model=model, input_tokens=321, output_tokens=12,
                                latency_s=0.75, cached=False), decision="llm")
            return _rows(skills, {"debug": 80}), True

        with mock.patch.object(cascade, "cascade_score", side_effect=fake):
            code, out, _ = self.run_main("--demo", "--cascade", "algo raro pasa")
        self.assertEqual(code, 0)
        self.assertIn("scorer: cascada: LLM (sdk)", out)
        self.assertIn("321 tokens de entrada + 12 de salida  ·  0.75 s", out)

    def test_cascada_con_error_del_llm_avisa_y_usa_el_lexico(self):
        def fake(skills, query, high=60, margin=20, model=None, usage=None):
            usage.update(_usage(latency_s=0.2, cached=False), decision="llm_error",
                         error="no hay backend")
            return skill_router.score_skills(skills, query), False

        with mock.patch.object(cascade, "cascade_score", side_effect=fake):
            code, out, err = self.run_main("--demo", "--cascade", "revisa este pull request")
        self.assertEqual(code, 0)
        self.assertIn("no pudo consultar al LLM (no hay backend)", err)
        self.assertIn("cascada: el LLM falló, se usó el léxico", out)
        self.assertIn("code-review", out)

    def test_cascada_que_revienta_cae_al_lexico(self):
        with mock.patch.object(cascade, "cascade_score", side_effect=RuntimeError("boom")):
            data, err = self.run_json("--demo", "--cascade", "revisa este pull request")
        self.assertIn("La cascada falló (RuntimeError: boom)", err)
        self.assertEqual(data["scorer"], "léxico+conceptos")
        self.assertIs(data["used_llm"], False)
        self.assertIsNone(data["usage"])
        self.assertEqual(data["recommended"][0]["name"], "code-review")

    def test_sin_cascade_py_cae_al_lexico(self):
        with mock.patch.dict(sys.modules, {"cascade": None}):  # import cascade -> ImportError
            data, err = self.run_json("--demo", "--cascade", "revisa este pull request")
        self.assertIn("--cascade no disponible", err)
        self.assertEqual(data["scorer"], "léxico+conceptos")
        self.assertEqual(data["recommended"][0]["name"], "code-review")

    def test_no_concepts_llega_a_la_cascada(self):
        seen = {}

        def fake(skills, query, high=60, margin=20, model=None, usage=None, *, use_concepts=True):
            seen["use_concepts"] = use_concepts
            usage.update(decision="lexical", latency_s=0.0)
            return skill_router.score_skills(skills, query, use_concepts=use_concepts), False

        with mock.patch.object(cascade, "cascade_score", fake):  # firma real: sin MagicMock
            data, _ = self.run_json("--demo", "--cascade", "--no-concepts",
                                    "mañana tengo la prueba, necesito repasar")
        self.assertIs(seen["use_concepts"], False)
        self.assertEqual(data["scorer"], "cascada: léxico, sin LLM")
        self.assertEqual(data["recommended"], [])


if __name__ == "__main__":
    unittest.main()
