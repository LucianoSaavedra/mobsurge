"""
Tests de la cascada léxico → LLM (cascade.py) SIN llamadas reales al modelo.

El LLM se simula parcheando llm_scorer.llm_score (o _call_cli, para probar la
cadena completa con la caché). Cualquier llamada real no simulada hace fallar el
test (ver _support).

    cd claude-skills-library/skill-router && python3 -m unittest tests.test_cascade -v
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

try:
    import _support  # noqa: F401  (sys.path + sin .pyc)
except ModuleNotFoundError:  # python3 -m unittest tests.test_cascade (desde skill-router)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import _support  # noqa: F401
from _support import CACHE_ENV, NoRealLLMMixin, TempDirTestCase

import cascade
import eval as bench
import llm_scorer
from cascade import DEFAULT_HIGH, DEFAULT_MARGIN, cascade_score, lexical_decision
from llm_scorer import LLMUnavailable
from skill_router import Skill, classify, demo_skills, score_skills


def llm_rows(skills, picks):
    """Resultado con la forma de llm_score: {nombre: score} elegidas, el resto a 0."""
    rows = [{"name": s.name, "scenario": s.scenario, "score": picks.get(s.name, 0),
             "matched": ["elegida por el LLM"] if s.name in picks else [], "source": s.source}
            for s in skills]
    rows.sort(key=lambda r: (-r["score"], r["name"]))
    return rows


class FakeLLM:
    """Sustituto de llm_scorer.llm_score que registra las llamadas y rellena usage."""

    def __init__(self, picks=None, error=None):
        self.picks = picks or {}
        self.error = error
        self.calls = []

    def __call__(self, skills, query, backend=None, model=None, usage=None):
        self.calls.append({"query": query, "backend": backend, "model": model})
        if isinstance(usage, dict):
            usage.update({"backend": backend or "cli", "model": model or "claude-opus-5-5",
                          "input_tokens": 2900, "output_tokens": 60, "cache_read_tokens": 0,
                          "cost_usd": 0.025, "latency_s": 4.2, "cached": False})
        if self.error:
            raise self.error
        return llm_rows(skills, self.picks)


def rows(*scores):
    return [{"name": f"s{i}", "score": sc} for i, sc in enumerate(scores)]


class LexicalDecisionTest(unittest.TestCase):
    def test_skill_clara(self):
        self.assertEqual(lexical_decision(rows(DEFAULT_HIGH, DEFAULT_HIGH - DEFAULT_MARGIN), 4),
                         "lexical")
        self.assertEqual(lexical_decision(rows(100), 1), "lexical")  # una sola skill

    def test_por_debajo_de_high_o_sin_margen_va_al_llm(self):
        self.assertEqual(lexical_decision(rows(DEFAULT_HIGH - 1, 0), 4), "llm")
        self.assertEqual(lexical_decision(rows(100, 100 - DEFAULT_MARGIN + 1), 4), "llm")
        self.assertEqual(lexical_decision(rows(100, 100), 1), "llm")  # empate

    def test_nada_aplica_solo_si_el_mensaje_es_corto(self):
        self.assertEqual(lexical_decision(rows(0, 0), 0), "none")
        self.assertEqual(lexical_decision(rows(0, 0), cascade.DEFAULT_SHORT), "none")
        # mensaje con contenido pero sin coincidencias (p. ej. español frente a
        # descripciones en inglés): eso lo decide el LLM, no el léxico
        self.assertEqual(lexical_decision(rows(0, 0), cascade.DEFAULT_SHORT + 1), "llm")
        self.assertEqual(lexical_decision(rows(5, 0), 1), "llm")

    def test_sin_skills(self):
        self.assertEqual(lexical_decision([], 0), "none")
        self.assertEqual(lexical_decision([], 5), "llm")

    def test_umbrales_configurables(self):
        self.assertEqual(lexical_decision(rows(30, 0), 4, high=30, margin=10), "lexical")
        self.assertEqual(lexical_decision(rows(0), 3, short=3), "none")


class CascadeScoreTest(NoRealLLMMixin, TempDirTestCase):
    def setUp(self):
        super().setUp()
        self.block_real_llm()
        self.skills = demo_skills()

    def run_cascade(self, query, fake=None, **kw):
        fake = fake or FakeLLM({"debug": 90})
        usage = {}
        with mock.patch.object(llm_scorer, "llm_score", side_effect=fake):
            results, used = cascade_score(self.skills, query, usage=usage, **kw)
        return results, used, usage, fake

    def test_lexico_claro_no_llama_al_llm(self):
        query = "tengo un stack trace, ayudame a diagnosticar el error"
        results, used, usage, fake = self.run_cascade(query)
        self.assertFalse(used)
        self.assertEqual(fake.calls, [])
        self.assertEqual(results, score_skills(demo_skills(), query))
        self.assertEqual(results[0]["name"], "debug")
        self.assertEqual(usage["decision"], "lexical")
        self.assertEqual((usage["input_tokens"], usage["output_tokens"], usage["cost_usd"]),
                         (0, 0, 0.0))
        self.assertIsNone(usage["backend"])

    def test_mensaje_corto_sin_coincidencias_no_llama_al_llm(self):
        for query in ("hola, qué tal", "gracias!", "ok, sigue", "que hora es en tokio"):
            with self.subTest(query=query):
                results, used, usage, fake = self.run_cascade(query)
                self.assertFalse(used)
                self.assertEqual(fake.calls, [])
                self.assertEqual(usage["decision"], "none")
                self.assertEqual(classify(results, 3, 15)[0], [])

    def test_sin_coincidencias_pero_con_contenido_llama_al_llm(self):
        # descripciones en inglés, mensaje en español: el léxico da 0 en todo
        skills = [Skill("cancel-unsubscribe", "Cancel a subscription or unsubscribe from a service."),
                  Skill("pptx", "Create slide decks and PowerPoint presentations.")]
        fake = FakeLLM({"cancel-unsubscribe": 92})
        usage = {}
        with mock.patch.object(llm_scorer, "llm_score", side_effect=fake):
            results, used = cascade_score(skills, "cancela mi suscripción de spotify", usage=usage)
        self.assertTrue(used)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual((results[0]["name"], results[0]["score"]), ("cancel-unsubscribe", 92))
        self.assertEqual(usage["decision"], "llm")
        self.assertEqual((usage["input_tokens"], usage["cost_usd"]), (2900, 0.025))

    def test_lexico_dudoso_llama_al_llm(self):
        # sinónimo: el léxico acierta pero con poca puntuación → no es fiable
        query = "mi app crashea con una excepcion, no se por que"
        lex = score_skills(demo_skills(), query)
        self.assertLess(lex[0]["score"], DEFAULT_HIGH)
        results, used, usage, fake = self.run_cascade(query, FakeLLM({"debug": 88}))
        self.assertTrue(used)
        self.assertEqual(fake.calls[0]["query"], query)  # el mensaje original, sin recortar
        self.assertEqual(results[0]["score"], 88)

    def test_modelo_y_backend_llegan_al_llm(self):
        _, _, usage, fake = self.run_cascade("ideas de contenido para instagram sobre mi negocio",
                                             model="claude-haiku-5-5", backend="cli")
        self.assertEqual(fake.calls[0]["model"], "claude-haiku-5-5")
        self.assertEqual(fake.calls[0]["backend"], "cli")
        self.assertEqual(usage["model"], "claude-haiku-5-5")

    def test_high_y_margin_cambian_la_decision(self):
        query = "busca inyecciones y errores en el diff"  # code-review 45, debug 15
        _, used_default, _, _ = self.run_cascade(query)
        self.assertTrue(used_default)
        _, used_low, usage, _ = self.run_cascade(query, high=40, margin=20)
        self.assertFalse(used_low)
        self.assertEqual(usage["decision"], "lexical")

    def test_llm_caido_cae_al_lexico(self):
        query = "mi app crashea con una excepcion, no se por que"
        fake = FakeLLM(error=LLMUnavailable("sin backend"))
        results, used, usage, _ = self.run_cascade(query, fake)
        self.assertFalse(used)
        self.assertEqual(results, score_skills(demo_skills(), query))
        self.assertEqual(usage["decision"], "llm_error")
        self.assertIn("sin backend", usage["error"])

    def test_llm_caido_sin_fallback_lanza(self):
        fake = FakeLLM(error=LLMUnavailable("sin backend"))
        with self.assertRaises(LLMUnavailable):
            self.run_cascade("mi app crashea con una excepcion, no se por que", fake, fallback=False)

    def test_otros_errores_no_se_esconden(self):
        fake = FakeLLM(error=KeyError("bug de programación"))
        with self.assertRaises(KeyError):
            self.run_cascade("mi app crashea con una excepcion, no se por que", fake)

    def test_usage_tiene_las_claves_del_contrato(self):
        for query in ("revisa este pull request por bugs de seguridad", "hola",
                      "un clip vertical para youtube que enganche"):
            with self.subTest(query=query):
                _, _, usage, _ = self.run_cascade(query)
                self.assertTrue(set(llm_scorer.USAGE_KEYS) <= set(usage))
                self.assertIn(usage["decision"], ("lexical", "none", "llm"))
                self.assertIsInstance(usage["latency_s"], float)

    def test_camino_barato_no_importa_llm_scorer(self):
        with mock.patch.dict(sys.modules):
            sys.modules.pop("llm_scorer", None)
            _, used = cascade_score(demo_skills(), "un guion para un reel viral de tiktok")
            self.assertFalse(used)
            self.assertNotIn("llm_scorer", sys.modules)

    def test_cadena_completa_con_cache(self):
        """cascade → llm_score real → _call_cli simulado, con SKILL_ROUTER_CACHE."""
        os.environ[CACHE_ENV] = str(self.tmp / "cache")
        reply = llm_scorer._Reply(json.dumps({"recommended": [{"name": "debug", "score": 85}]}),
                                  {"input_tokens": 3100, "output_tokens": 70,
                                   "cache_read_tokens": 0, "cost_usd": 0.03})
        query = "mi app crashea con una excepcion, no se por que"
        with mock.patch.object(llm_scorer, "_call_cli", return_value=reply) as cli:
            first, second = {}, {}
            r1, used1 = cascade_score(self.skills, query, backend="cli", usage=first)
            r2, used2 = cascade_score(self.skills, query, backend="cli", usage=second)
        self.assertEqual(cli.call_count, 1)
        self.assertTrue(used1 and used2)
        self.assertEqual(r1, r2)
        self.assertEqual((first["input_tokens"], first["cached"]), (3100, False))
        self.assertEqual((second["input_tokens"], second["cost_usd"], second["cached"]),
                         (0, 0.0, True))


class DefaultsOnInlineSetTest(NoRealLLMMixin, unittest.TestCase):
    """Los valores por defecto se fijaron con el set inline y casos propios (nunca con
    bench/heldout.json). Aquí se comprueba sobre el inline que el camino barato no se
    equivoca y que de verdad ahorra llamadas."""

    def setUp(self):
        self.block_real_llm()

    def test_camino_barato_preciso_y_ahorra(self):
        skills = demo_skills()
        avoided = wrong = 0
        for case in bench.default_cases():
            oracle = FakeLLM({e: 90 for e in case["expected"]})
            with mock.patch.object(llm_scorer, "llm_score", side_effect=oracle):
                results, used = cascade_score(skills, case["query"])
            if used:
                continue
            avoided += 1
            rec = [r["name"] for r in classify(results, 3, 15)[0]]
            ok = (rec[:1] and rec[0] in case["expected"]) if case["expected"] else not rec
            wrong += int(not ok)
        self.assertEqual(wrong, 0)
        self.assertGreaterEqual(avoided, 5)  # 8 de 19 con el léxico actual


if __name__ == "__main__":
    unittest.main()
