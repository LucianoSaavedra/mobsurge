"""
Tests del benchmark (eval.py): set por defecto, carga y validación de --cases,
semántica de expected múltiple y de negativos, agregados por tipo, manejo de
fallos del LLM (simulado) y la CLI.

    cd claude-skills-library/skill-router && python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest import mock

import _support  # noqa: F401  (sys.path + sin .pyc)
from _support import NoRealLLMMixin, TempDirTestCase, captured_output

import eval as ev  # `eval` a secas taparía la función builtin en este módulo
import llm_scorer
from llm_scorer import LLMUnavailable
from skill_router import Skill, demo_skills

DEMO_NAMES = [s.name for s in demo_skills()]


def rows_from(scores: dict[str, int], skills=None) -> list[dict]:
    """Resultados con la forma de score_skills(), ordenados por score."""
    skills = demo_skills() if skills is None else skills
    rows = [{"name": s.name, "scenario": s.scenario, "score": scores.get(s.name, 0),
             "matched": [], "source": s.source} for s in skills]
    rows.sort(key=lambda r: (-r["score"], r["name"]))
    return rows


class FakeLLM:
    """Sustituto de llm_scorer.llm_score: respuestas por mensaje, o excepciones."""

    def __init__(self, plan: dict, cached: bool = False):
        self.plan = plan  # query -> dict de scores | Exception
        self.cached = cached
        self.calls: list[str] = []

    def __call__(self, skills, query, backend=None):
        self.calls.append(query)
        outcome = self.plan[query]
        if isinstance(outcome, Exception):
            raise outcome
        llm_scorer.last_call_info.clear()
        llm_scorer.last_call_info.update({"backend": "cli", "model": "x", "cached": self.cached})
        return rows_from(outcome, skills)


def case(query, expected, kind="directo"):
    return {"query": query, "expected": expected, "kind": kind}


# --- Set por defecto -----------------------------------------------------------------------
class DefaultCasesTest(unittest.TestCase):
    def test_formato_comun_de_los_19_casos(self):
        cases = ev.default_cases()
        self.assertEqual(len(cases), 19)
        self.assertEqual(len(ev.CASES), 19)
        for c, (q, e, k) in zip(cases, ev.CASES):
            self.assertEqual(c, {"query": q, "expected": [e] if e else [], "kind": k})
            self.assertIn(c["kind"], ev.KINDS)
        self.assertEqual(sum(1 for c in cases if c["kind"] == "negativo"), 3)
        self.assertTrue(all(c["expected"] == [] for c in cases if c["kind"] == "negativo"))
        self.assertTrue(all(len(c["expected"]) == 1 for c in cases if c["kind"] != "negativo"))

    def test_todas_las_esperadas_existen_en_demo(self):
        self.assertEqual(ev.unknown_skills(ev.default_cases(), demo_skills()), [])

    def test_numeros_publicados_del_set_por_defecto(self):
        # README: baseline 63.2/63.2, +conceptos 84.2/89.5. Si cambias el scorer o
        # concepts.py a propósito, actualiza también el README y este test.
        base = ev.run(use_concepts=False)
        conc = ev.run(use_concepts=True)
        self.assertEqual((base["top1_accuracy"], base["top3_accuracy"]), (63.2, 63.2))
        self.assertEqual((conc["top1_accuracy"], conc["top3_accuracy"]), (84.2, 89.5))

    def test_conceptos_no_empeoran_ningun_tipo(self):
        base = ev.run(use_concepts=False)["by_kind"]
        conc = ev.run(use_concepts=True)["by_kind"]
        for kind in base:
            with self.subTest(kind=kind):
                self.assertGreaterEqual(conc[kind]["top1_accuracy"], base[kind]["top1_accuracy"])
                self.assertGreaterEqual(conc[kind]["top3_accuracy"], base[kind]["top3_accuracy"])
        self.assertEqual(conc["directo"]["top1_accuracy"], 100.0)


# --- load_cases ------------------------------------------------------------------------------
class LoadCasesTest(TempDirTestCase):
    def write(self, data, name="casos.json") -> Path:
        p = self.tmp / name
        p.write_text(data if isinstance(data, str) else json.dumps(data, ensure_ascii=False),
                     encoding="utf-8")
        return p

    def load_error(self, data) -> str:
        with self.assertRaises(ev.CasesError) as cm:
            ev.load_cases(self.write(data))
        return str(cm.exception)

    def test_archivo_valido_con_los_7_tipos(self):
        cases_in = [case(f"mensaje {k}", [] if k == "negativo" else ["debug"], k) for k in ev.KINDS]
        cases, warnings = ev.load_cases(self.write({"version": 1, "cases": cases_in}))
        self.assertEqual(cases, cases_in)
        self.assertEqual(warnings, [])

    def test_expected_se_limpia_y_deduplica(self):
        data = {"version": 1, "cases": [case("q", [" debug ", "debug", "code-review"], "ambiguo")]}
        cases, _ = ev.load_cases(self.write(data))
        self.assertEqual(cases[0]["expected"], ["debug", "code-review"])

    def test_version_es_opcional(self):
        cases, _ = ev.load_cases(self.write({"cases": [case("q", [], "negativo")]}))
        self.assertEqual(len(cases), 1)

    def test_avisos_por_incoherencias(self):
        data = {"version": 1, "cases": [case("hola", ["debug"], "negativo"),
                                        case("revisa", [], "directo")]}
        cases, warnings = ev.load_cases(self.write(data))
        self.assertEqual(len(cases), 2)
        self.assertEqual(len(warnings), 2)
        self.assertIn("caso #1", warnings[0])
        self.assertIn("negativo", warnings[0])
        self.assertIn("caso #2", warnings[1])

    def test_todos_los_errores_juntos(self):
        msg = self.load_error({"version": 1, "cases": [
            {"query": "", "expected": "debug", "kind": "raro"},
            {"query": "ok", "expected": [1], "kind": "directo"},
            "no soy un objeto",
            {"query": "bien", "expected": ["debug"], "kind": "directo"},
            {"query": "  ", "expected": ["  "], "kind": None},
        ]})
        for needle in ("caso #1", "caso #2", "caso #3", "caso #5", '"query"', '"expected"',
                       '"kind"', "debe ser un objeto", "'raro'"):
            with self.subTest(needle=needle):
                self.assertIn(needle, msg)
        self.assertNotIn("caso #4", msg)

    def test_muchos_errores_se_resumen(self):
        msg = self.load_error({"cases": [{"query": f"q{i}", "expected": [], "kind": "x"}
                                         for i in range(20)]})
        self.assertIn("... y 5 más", msg)

    def test_version_no_soportada(self):
        for version in (2, "1", 0, None):
            with self.subTest(version=version):
                self.assertIn("versión", self.load_error({"version": version,
                                                          "cases": [case("q", [], "negativo")]}))

    def test_version_true_no_es_1(self):
        # En Python True == 1, pero en el JSON `true` no es la versión 1.
        self.load_error({"version": True, "cases": [case("q", [], "negativo")]})

    def test_archivos_invalidos(self):
        self.assertIn("no es JSON", self.load_error("{rota"))
        self.load_error("[1, 2]")
        self.load_error({"version": 1, "cases": "no lista"})
        self.load_error({"version": 1})
        self.assertIn("no tiene casos", self.load_error({"version": 1, "cases": []}))
        with self.assertRaises(ev.CasesError) as cm:
            ev.load_cases(self.tmp / "no-existe.json")
        self.assertIn("no pude leer", str(cm.exception))

    def test_caseserror_es_valueerror(self):
        self.assertTrue(issubclass(ev.CasesError, ValueError))

    def test_unknown_skills(self):
        cases = [case("a", ["debug", "fantasma"]), case("b", ["zeta", "debug"]), case("c", [])]
        self.assertEqual(ev.unknown_skills(cases, demo_skills()), ["fantasma", "zeta"])


# --- eval_one: semántica de expected ----------------------------------------------------------
class EvalOneSemanticsTest(NoRealLLMMixin, unittest.TestCase):
    """Con un scorer simulado se controla exactamente qué se recomienda."""

    def setUp(self):
        super().setUp()
        self.block_real_llm()
        self.skills = demo_skills()

    def judge(self, scores, expected, top=3, threshold=15):
        with mock.patch.object(llm_scorer, "llm_score", side_effect=lambda s, q: rows_from(scores, s)):
            return ev.eval_one(self.skills, "q", expected, True, top, threshold, use_llm=True)

    def test_expected_multiple_cualquiera_vale_como_top1(self):
        scores = {"code-review": 90, "debug": 60}
        self.assertEqual(self.judge(scores, ["debug", "code-review"]), (["code-review", "debug"], True, True))
        self.assertEqual(self.judge(scores, ["code-review", "zzz"]), (["code-review", "debug"], True, True))

    def test_top3_si_alguna_esperada_esta_entre_las_recomendadas(self):
        scores = {"code-review": 90, "debug": 60}
        self.assertEqual(self.judge(scores, ["debug"])[1:], (False, True))
        self.assertEqual(self.judge(scores, ["deep-research", "debug"])[1:], (False, True))

    def test_ninguna_esperada_recomendada(self):
        self.assertEqual(self.judge({"code-review": 90}, ["debug", "guiones-video"])[1:], (False, False))

    def test_nada_recomendado_en_caso_positivo(self):
        self.assertEqual(self.judge({}, ["debug"]), ([], False, False))

    def test_negativo_acierta_solo_si_no_se_recomienda_nada(self):
        self.assertEqual(self.judge({"debug": 10}, []), ([], True, True))  # bajo el umbral
        self.assertEqual(self.judge({"debug": 40}, []), (["debug"], False, False))
        self.assertEqual(self.judge({}, None), ([], True, True))  # None == negativo

    def test_expected_como_string(self):
        self.assertEqual(self.judge({"debug": 70}, "debug")[1:], (True, True))

    def test_respeta_top_y_umbral(self):
        scores = {"code-review": 90, "debug": 80, "deep-research": 70, "guiones-video": 60}
        self.assertEqual(self.judge(scores, ["guiones-video"], top=3)[1:], (False, False))
        self.assertEqual(self.judge(scores, ["guiones-video"], top=4)[1:], (False, True))
        self.assertEqual(self.judge({"debug": 14}, ["debug"], threshold=15)[1:], (False, False))
        self.assertEqual(self.judge({"debug": 15}, ["debug"], threshold=15)[1:], (True, True))

    def test_con_el_scorer_lexico_real(self):
        q = "revisa este pull request por bugs de seguridad"
        got, h1, h3 = ev.eval_one(self.skills, q, ["debug", "code-review"], True, 3, 15)
        self.assertEqual(got[0], "code-review")
        self.assertEqual((h1, h3), (True, True))
        self.assertEqual(ev.eval_one(self.skills, q, ["deep-research"], True, 3, 15)[1:], (False, False))


# --- run: agregados y fallos del LLM ------------------------------------------------------------
class RunTest(NoRealLLMMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.block_real_llm()

    def test_agregados_y_desglose_por_tipo(self):
        cases = [
            case("revisa este pull request por bugs de seguridad", ["debug", "code-review"], "ambiguo"),
            case("un guion para un reel viral de tiktok", ["guiones-video"], "directo"),
            case("investiga el mercado y compara tendencias", ["debug"], "directo"),  # fallo a propósito
            case("hola, como estas hoy", [], "negativo"),
        ]
        rep = ev.run(use_concepts=True, cases=cases)
        self.assertEqual(rep["total"], 4)
        self.assertEqual(rep["top1_accuracy"], 75.0)
        self.assertEqual(rep["top3_accuracy"], 75.0)
        self.assertEqual(list(rep["by_kind"]), ["directo", "negativo", "ambiguo"])  # orden de KINDS
        self.assertEqual(rep["by_kind"]["directo"],
                         {"total": 2, "top1": 1, "top3": 1, "top1_accuracy": 50.0, "top3_accuracy": 50.0})
        self.assertEqual(rep["by_kind"]["negativo"]["top1_accuracy"], 100.0)
        self.assertEqual([d["top1"] for d in rep["details"]], [True, True, False, True])
        self.assertEqual(rep["details"][0]["expected"], ["debug", "code-review"])
        self.assertNotIn("llm_errors", rep)

    def test_tipo_desconocido_va_al_final(self):
        rep = ev.run(use_concepts=True, cases=[case("hola", [], "inventado"), case("debug", ["debug"])])
        self.assertEqual(list(rep["by_kind"]), ["directo", "inventado"])

    def test_skills_personalizadas(self):
        skills = [Skill("cocina", "receta pasta tomate"), Skill("fiscal", "impuestos renta")]
        rep = ev.run(use_concepts=True, cases=[case("receta de pasta", ["cocina"])], skills=skills)
        self.assertEqual(rep["top1_accuracy"], 100.0)

    def test_fallo_aislado_del_llm_cuenta_como_fallo(self):
        fake = FakeLLM({"a": {"debug": 90}, "b": LLMUnavailable("timeout"), "c": {"debug": 90}})
        cases = [case("a", ["debug"]), case("b", ["debug"]), case("c", ["debug"])]
        with mock.patch.object(llm_scorer, "llm_score", side_effect=fake):
            rep = ev.run(use_concepts=True, use_llm=True, cases=cases)
        self.assertEqual(fake.calls, ["a", "b", "c"])
        self.assertEqual(rep["llm_errors"], 1)
        self.assertEqual(rep["top1_accuracy"], 66.7)
        bad = rep["details"][1]
        self.assertEqual((bad["got"], bad["top1"], bad["top3"]), ([], False, False))
        self.assertIn("timeout", bad["error"])
        self.assertNotIn("error", rep["details"][0])

    def test_fallo_del_llm_en_negativo_no_cuenta_como_acierto(self):
        fake = FakeLLM({"hola": LLMUnavailable("caído")})
        with mock.patch.object(llm_scorer, "llm_score", side_effect=fake):
            rep = ev.run(use_concepts=True, use_llm=True, cases=[case("hola", [], "negativo")])
        self.assertEqual(rep["top1_accuracy"], 0.0)

    def test_tres_fallos_seguidos_abortan(self):
        fake = FakeLLM({q: LLMUnavailable(f"fallo {q}") for q in "abcd"})
        cases = [case(q, ["debug"]) for q in "abcd"]
        with mock.patch.object(llm_scorer, "llm_score", side_effect=fake):
            with self.assertRaises(LLMUnavailable) as cm:
                ev.run(use_concepts=True, use_llm=True, cases=cases)
        self.assertEqual(fake.calls, ["a", "b", "c"])  # no sigue gastando llamadas
        self.assertIn("3 fallos seguidos", str(cm.exception))

    def test_un_acierto_reinicia_la_racha(self):
        err = LLMUnavailable("x")
        plan = {"a": err, "b": err, "c": {"debug": 90}, "d": err, "e": err}
        cases = [case(q, ["debug"]) for q in "abcde"]
        with mock.patch.object(llm_scorer, "llm_score", side_effect=FakeLLM(plan)):
            rep = ev.run(use_concepts=True, use_llm=True, cases=cases)
        self.assertEqual(rep["llm_errors"], 4)
        self.assertEqual(rep["top1_accuracy"], 20.0)

    def test_cuenta_respuestas_de_cache(self):
        fake = FakeLLM({"a": {"debug": 90}, "b": {}}, cached=True)
        with mock.patch.object(llm_scorer, "llm_score", side_effect=fake):
            rep = ev.run(use_concepts=True, use_llm=True,
                         cases=[case("a", ["debug"]), case("b", [], "negativo")])
        self.assertEqual(rep["llm_cached"], 2)
        self.assertEqual(rep["llm_errors"], 0)
        self.assertEqual(rep["top1_accuracy"], 100.0)


# --- CLI --------------------------------------------------------------------------------------------
class MainTest(NoRealLLMMixin, TempDirTestCase):
    def setUp(self):
        super().setUp()
        self.block_real_llm()

    def write_cases(self, cases, version=1) -> Path:
        p = self.tmp / "casos.json"
        p.write_text(json.dumps({"version": version, "cases": cases}, ensure_ascii=False), encoding="utf-8")
        return p

    def main(self, *argv):
        with captured_output() as (out, err):
            code = ev.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_cases_json_con_expected_multiple_y_negativo(self):
        p = self.write_cases([
            case("revisa este pull request por bugs de seguridad", ["debug", "code-review"], "ambiguo"),
            case("hola, como estas hoy", [], "negativo"),
        ])
        code, out, err = self.main("--cases", str(p), "--json")
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(data["cases"], str(p))
        self.assertEqual(data["total"], 2)
        self.assertEqual(data["top1_accuracy"], 100.0)
        self.assertEqual(set(data["by_kind"]), {"ambiguo", "negativo"})
        self.assertEqual(data["details"][1]["got"], [])

    def test_cases_texto(self):
        p = self.write_cases([case("un guion para un reel viral de tiktok", ["guiones-video"])])
        code, out, _ = self.main("--cases", str(p))
        self.assertEqual(code, 0)
        self.assertIn("1 casos", out)
        self.assertIn("Precisión por tipo", out)

    def test_cases_invalido_sale_con_2(self):
        p = self.write_cases([{"query": "", "expected": "x", "kind": "?"}])
        code, out, err = self.main("--cases", str(p))
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("formato inválido", err)

    def test_cases_inexistente_o_version_mala_sale_con_2(self):
        self.assertEqual(self.main("--cases", str(self.tmp / "no.json"))[0], 2)
        p = self.write_cases([case("q", [], "negativo")], version=2)
        self.assertEqual(self.main("--cases", str(p))[0], 2)

    def test_skill_esperada_desconocida_sale_con_2(self):
        p = self.write_cases([case("q", ["skill-que-no-existe"])])
        code, _, err = self.main("--cases", str(p))
        self.assertEqual(code, 2)
        self.assertIn("skill-que-no-existe", err)
        for name in DEMO_NAMES:
            self.assertIn(name, err)

    def test_avisos_no_abortan(self):
        p = self.write_cases([case("hola", ["debug"], "negativo")])
        code, _, err = self.main("--cases", str(p), "--json")
        self.assertEqual(code, 0)
        self.assertIn("negativo", err)

    def test_compare_json_con_cases(self):
        p = self.write_cases([case("ideas de contenido para instagram sobre mi negocio",
                                   ["marca-marketing"], "sinonimo")])
        code, out, _ = self.main("--cases", str(p), "--compare", "--json")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(set(data), {"cases", "baseline", "concepts"})
        self.assertEqual(data["baseline"]["top1_accuracy"], 0.0)  # sinónimo: el baseline falla
        self.assertEqual(data["concepts"]["top1_accuracy"], 100.0)

    def test_por_defecto_19_casos(self):
        code, out, _ = self.main("--json")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual((data["cases"], data["total"]), ("inline", 19))

    def test_llm_sin_backend_sale_con_2(self):
        with mock.patch.object(llm_scorer, "available_backend", return_value=None):
            code, _, err = self.main("--llm")
        self.assertEqual(code, 2)
        self.assertIn("--llm", err)

    def test_llm_caido_aborta_con_2(self):
        with mock.patch.object(llm_scorer, "available_backend", return_value="cli"), \
                mock.patch.object(llm_scorer, "llm_score", side_effect=LLMUnavailable("caído")) as fake:
            code, _, err = self.main("--llm")
        self.assertEqual(code, 2)
        self.assertIn("se detuvo", err)
        self.assertEqual(fake.call_count, ev.MAX_CONSECUTIVE_LLM_ERRORS)

    def test_compare_con_llm_simulado(self):
        plan = {c["query"]: ({c["expected"][0]: 90} if c["expected"] else {}) for c in ev.default_cases()}
        with mock.patch.object(llm_scorer, "available_backend", return_value="cli"), \
                mock.patch.object(llm_scorer, "llm_score", side_effect=FakeLLM(plan)):
            code, out, _ = self.main("--compare", "--llm", "--json")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["llm"]["top1_accuracy"], 100.0)
        self.assertEqual(data["llm"]["llm_errors"], 0)
        self.assertEqual(data["baseline"]["top1_accuracy"], 63.2)


if __name__ == "__main__":
    unittest.main()
