"""
Tests del benchmark (eval.py): set por defecto, carga y validación de --cases,
semántica de expected múltiple y de negativos, agregados por tipo, manejo de
fallos del LLM (simulado) y la CLI.

    cd claude-skills-library/skill-router && python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

# Para que también funcione `python3 -m unittest tests.test_eval` desde la carpeta
# del router (tests/ no es un paquete, así que _support no está en sys.path).
sys.path.insert(0, str(Path(__file__).resolve().parent))

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

    def __init__(self, plan: dict, cached: bool = False, cost=None):
        self.plan = plan  # query -> dict de scores | Exception
        self.cached = cached
        self.cost = cost  # None = el backend no informa el coste (como el SDK)
        self.calls: list[str] = []
        self.models: list = []

    def __init_usage(self, usage, model):
        if isinstance(usage, dict):
            usage.update({"backend": "cli", "model": model or "x", "cached": self.cached,
                          "input_tokens": 0 if self.cached else 100,
                          "output_tokens": 0 if self.cached else 20,
                          "cache_read_tokens": 0, "cost_usd": self.cost, "latency_s": 0.5})

    def __call__(self, skills, query, backend=None, model=None, usage=None):
        self.calls.append(query)
        self.models.append(model)
        outcome = self.plan[query]
        if isinstance(outcome, Exception):
            raise outcome
        self.__init_usage(usage, model)
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
        # Antes: baseline 63.2/63.2, +conceptos 84.2/89.5. +conceptos pasó a 94.7/94.7
        # por los arreglos del núcleo (normalize_query y conceptos en skill_router.py
        # y concepts.py), no por cambios en el set ni en eval.py: valores copiados de
        # la salida real de `python3 eval.py --compare`. Ya no hay empates en el #1
        # en este set, así que la regla de empates (bench-2) no los altera. Si cambias
        # el scorer o concepts.py a propósito, actualiza también el README y este test.
        base = ev.run(use_concepts=False)
        conc = ev.run(use_concepts=True)
        self.assertEqual((base["top1_accuracy"], base["top3_accuracy"]), (63.2, 63.2))
        self.assertEqual((conc["top1_accuracy"], conc["top3_accuracy"]), (94.7, 94.7))

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

    def test_version_float_no_es_1(self):
        self.assertIn("versión", self.load_error({"version": 1.0, "cases": [case("q", [], "negativo")]}))
        self.assertEqual(len(ev.load_cases(self.write({"version": 1, "cases": [case("q", [], "negativo")]}))[0]), 1)

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
        with mock.patch.object(llm_scorer, "llm_score", side_effect=lambda s, q, **kw: rows_from(scores, s)):
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

# --- Empates (bench-2) ----------------------------------------------------------------------
class TiesTest(NoRealLLMMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.block_real_llm()

    def judge(self, scores, expected, skills=None, top=3):
        skills = skills or demo_skills()
        with mock.patch.object(llm_scorer, "llm_score", side_effect=lambda s, q, **kw: rows_from(scores, s)):
            return ev.eval_one(skills, "q", expected, True, top, 15, use_llm=True)

    def test_empate_en_1_da_credito_parcial_sin_mirar_el_alfabeto(self):
        # code-review < debug alfabéticamente: antes fallaba si la esperada era debug
        # y acertaba si era code-review. Ahora las dos valen 0.5.
        scores = {"code-review": 17, "debug": 17}
        self.assertEqual(self.judge(scores, ["debug"])[1:], (0.5, True))
        self.assertEqual(self.judge(scores, ["code-review"])[1:], (0.5, True))
        self.assertIs(self.judge(scores, ["debug", "code-review"])[1], True)  # las dos esperadas

    def test_renombrar_una_skill_empatada_no_cambia_la_cifra(self):
        cases = [case("q1", ["deep-research"]), case("q2", ["debug"])]
        plan = {"q1": {"deep-research": 17, "estudio-a-medida": 17}, "q2": {"code-review": 17, "debug": 17}}
        renamed = [Skill("zz-research" if s.name == "deep-research" else s.name, s.description)
                   for s in demo_skills()]
        plan2 = {"q1": {"zz-research": 17, "estudio-a-medida": 17}, "q2": plan["q2"]}
        cases2 = [case("q1", ["zz-research"]), case("q2", ["debug"])]
        with mock.patch.object(llm_scorer, "llm_score", side_effect=FakeLLM(plan)):
            a = ev.run(use_concepts=True, use_llm=True, cases=cases)
        with mock.patch.object(llm_scorer, "llm_score", side_effect=FakeLLM(plan2)):
            b = ev.run(use_concepts=True, use_llm=True, cases=cases2, skills=renamed)
        self.assertEqual(a["top1_accuracy"], 50.0)
        self.assertEqual(b["top1_accuracy"], a["top1_accuracy"])
        self.assertEqual(a["diagnostics"]["ties_at_top1"], 2)
        self.assertEqual(a["diagnostics"]["failures"], {"empate_en_1": 2})
        self.assertTrue(a["details"][0]["tie_at_top1"])

    def test_empate_en_el_corte_del_top3(self):
        # 4 skills a 50 y solo caben 3: la esperada entra con probabilidad 3/4.
        scores = {"code-review": 50, "debug": 50, "deep-research": 50, "guiones-video": 50}
        got, h1, h3 = self.judge(scores, ["guiones-video"])
        self.assertNotIn("guiones-video", got)
        self.assertEqual((h1, h3), (0.25, 0.75))
        # sin empate en el corte, top-3 sigue siendo bool
        self.assertIs(self.judge({"debug": 80, "code-review": 50}, ["code-review"])[2], True)


# --- Diagnóstico del ranking, multi y sobre-recomendación (bench-1, bench-3) -------------------
class DiagnosticsTest(NoRealLLMMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.block_real_llm()

    def run_plan(self, plan, cases):
        with mock.patch.object(llm_scorer, "llm_score", side_effect=FakeLLM(plan)):
            return ev.run(use_concepts=True, use_llm=True, cases=cases)

    def test_ranking_bruto_separado_de_la_abstencion(self):
        plan = {"a": {"debug": 10},                       # #1 correcta pero bajo el umbral
                "b": {"code-review": 40, "debug": 20},    # #2 (MRR 0.5), recomendada en top-3
                "c": {},                                  # sin señal
                "d": {"debug": 30},                       # negativo con falso positivo
                "e": {}}                                  # negativo bien
        cases = [case("a", ["debug"]), case("b", ["debug"]), case("c", ["debug"]),
                 case("d", [], "negativo"), case("e", [], "negativo")]
        rep = self.run_plan(plan, cases)
        dg = rep["diagnostics"]
        self.assertEqual(rep["top1_accuracy"], 20.0)        # la métrica principal no cambia
        self.assertEqual(dg["hit1_rank"], 33.3)             # 1 de 3 positivos
        self.assertEqual(dg["mrr"], 0.5)                    # (1 + 0.5 + 0) / 3
        self.assertEqual(dg["false_positive_rate"], 50.0)
        self.assertEqual(dg["abstain_rate"], 66.7)
        self.assertEqual(dg["failures"], {"abstuvo_con_1_correcta": 1, "ranking_incorrecto": 1,
                                          "sin_senal": 1, "falso_positivo": 1})
        self.assertEqual(dg["by_kind"]["negativo"]["false_positive_rate"], 50.0)
        self.assertNotIn("hit1_rank", rep["by_kind"]["directo"])  # by_kind intacto

    def test_multi_completa_exige_todas_las_intenciones(self):
        plan = {"a": {"guiones-video": 80}, "b": {"guiones-video": 80, "deep-research": 60}}
        cases = [case("a", ["guiones-video", "deep-research"], "multi"),
                 case("b", ["guiones-video", "deep-research"], "multi")]
        rep = self.run_plan(plan, cases)
        self.assertEqual(rep["top1_accuracy"], 100.0)       # "al menos una" en los dos
        self.assertEqual(rep["diagnostics"]["multi_complete"], 50.0)
        self.assertEqual(rep["diagnostics"]["by_kind"]["multi"]["multi_cases"], 2)

    def test_precision_de_las_recomendaciones(self):
        plan = {"a": {"debug": 90, "code-review": 80, "deep-research": 70}, "b": {"debug": 90}}
        rep = self.run_plan(plan, [case("a", ["debug"]), case("b", ["debug"])])
        self.assertEqual(rep["top1_accuracy"], 100.0)
        self.assertEqual(rep["diagnostics"]["rec_precision"], 50.0)  # 2 de 4
        self.assertEqual(rep["diagnostics"]["avg_recommended"], 2.0)


# --- Economía de tokens -------------------------------------------------------------------------
class EconomyTest(NoRealLLMMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.block_real_llm()

    def test_llm_tokens_coste_latencia_y_eficiencia(self):
        fake = FakeLLM({"a": {"debug": 90}, "b": {}}, cost=0.01)
        with mock.patch.object(llm_scorer, "llm_score", side_effect=fake):
            rep = ev.run(use_concepts=True, scorer="llm", model="claude-haiku-5-5",
                         cases=[case("a", ["debug"]), case("b", ["debug"])])
        eco = rep["economy"]
        self.assertEqual(fake.models, ["claude-haiku-5-5"] * 2)
        self.assertEqual((eco["llm_calls"], eco["input_tokens"], eco["output_tokens"]), (2, 200, 40))
        self.assertEqual((eco["input_tokens_per_query"], eco["output_tokens_per_query"]), (100.0, 20.0))
        self.assertEqual(eco["cost_usd"], 0.02)
        self.assertEqual(eco["avg_latency_s"], 0.5)
        self.assertEqual(eco["top1_per_1k_tokens"], round(1000 * 1 / 240, 2))
        self.assertIsNone(eco["llm_avoided_pct"])
        self.assertEqual(eco["model"], "claude-haiku-5-5")

    def test_campo_none_sale_como_nd(self):
        fake = FakeLLM({"a": {"debug": 90}})  # cost=None: el backend no informa el coste
        with mock.patch.object(llm_scorer, "llm_score", side_effect=fake):
            rep = ev.run(use_concepts=True, use_llm=True, cases=[case("a", ["debug"])])
        self.assertIsNone(rep["economy"]["cost_usd"])
        cells = dict(zip(ev._ROWS, ev._metric_rows(rep)))
        self.assertEqual(cells["coste"], "n/d")
        self.assertEqual(cells["tokens entrada"], "100")
        self.assertEqual(ev._nd(None), "n/d")

    def test_cascada_cuenta_llamadas_evitadas(self):
        q_clear = "revisa este pull request por bugs de seguridad"  # el léxico basta
        q_doubt = "algo raro pasa con lo mío"                        # el léxico duda
        fake = FakeLLM({q_doubt: {"debug": 90}})
        with mock.patch.object(llm_scorer, "llm_score", side_effect=fake):
            rep = ev.run(use_concepts=True, scorer="cascade", model="claude-haiku-5-5",
                         cases=[case(q_clear, ["code-review"]), case(q_doubt, ["debug"])])
        eco = rep["economy"]
        self.assertEqual(fake.calls, [q_doubt])
        self.assertEqual((eco["llm_calls"], eco["llm_avoided_pct"]), (1, 50.0))
        self.assertEqual((eco["input_tokens"], eco["output_tokens"]), (100, 20))
        self.assertEqual(eco["input_tokens_per_query"], 50.0)
        self.assertEqual(rep["top1_accuracy"], 100.0)
        self.assertEqual([d["used_llm"] for d in rep["details"]], [False, True])

    def test_cascada_con_llm_caido_usa_el_lexico_y_cuenta_el_error(self):
        q = "algo raro pasa con lo mío"
        with mock.patch.object(llm_scorer, "llm_score", side_effect=FakeLLM({q: LLMUnavailable("x")})):
            rep = ev.run(use_concepts=True, scorer="cascade", cases=[case(q, [], "negativo")])
        self.assertEqual(rep["llm_errors"], 1)
        self.assertEqual(rep["top1_accuracy"], 100.0)  # respaldo léxico: no recomienda nada

    def test_resolve_models(self):
        self.assertEqual(ev.resolve_models("opus, haiku,opus"), ["claude-opus-5-5", "claude-haiku-5-5"])
        self.assertEqual(ev.resolve_models("claude-x-1"), ["claude-x-1"])
        self.assertEqual(ev.resolve_models(None), [])


class CompareEconomyCLITest(NoRealLLMMixin, TempDirTestCase):
    def setUp(self):
        super().setUp()
        self.block_real_llm()
        self.plan = {c["query"]: ({c["expected"][0]: 90} if c["expected"] else {})
                     for c in ev.default_cases()}

    def main(self, *argv):
        fake = FakeLLM(self.plan, cost=0.001)
        with mock.patch.object(llm_scorer, "available_backend", return_value="cli"), \
                mock.patch.object(llm_scorer, "llm_score", side_effect=fake), \
                captured_output() as (out, err):
            code = ev.main(list(argv))
        return code, out.getvalue(), err.getvalue(), fake

    def test_una_sola_tabla_con_cascada_y_un_llm_por_modelo(self):
        code, out, err, fake = self.main("--compare", "--llm", "--cascade", "--llm-models", "opus,haiku")
        self.assertEqual(code, 0, err)
        header = next(line for line in out.splitlines() if "baseline" in line and "+conceptos" in line)
        for label in ("cascada", "LLM opus", "LLM haiku"):
            self.assertIn(label, header)
        for row in ("llamadas al LLM", "tokens entrada", "tokens/consulta", "coste", "top-1 por 1k tokens"):
            self.assertEqual(sum(1 for line in out.splitlines() if line.strip().startswith(row)), 1, row)
        self.assertIn("evit.", out)
        self.assertIn("claude-haiku-5-5", fake.models)
        self.assertIn("claude-opus-5-5", fake.models)

    def test_json_anade_campos_sin_quitar(self):
        code, out, err, _ = self.main("--compare", "--llm", "--cascade", "--llm-models", "opus,haiku", "--json")
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertTrue({"cases", "baseline", "concepts", "llm", "cascade", "llm_models"} <= set(data))
        self.assertEqual(list(data["llm_models"]), ["claude-opus-5-5", "claude-haiku-5-5"])
        self.assertEqual(data["llm"]["top1_accuracy"], 100.0)
        self.assertIn("economy", data["cascade"])
        self.assertIsNotNone(data["cascade"]["economy"]["llm_avoided_pct"])
        self.assertIn("diagnostics", data["baseline"])
        self.assertNotIn("economy", data["baseline"])

    def test_cascada_sin_backend_sale_con_2(self):
        with mock.patch.object(llm_scorer, "available_backend", return_value=None), \
                captured_output() as (_, err):
            self.assertEqual(ev.main(["--cascade"]), 2)
        self.assertIn("--cascade", err.getvalue())


# --- Independencia y set de estrés (bench-5, tarea 9) ----------------------------------------------
class IndependenceAndStressTest(TempDirTestCase):
    def test_similar_pairs_detecta_casi_duplicados_del_inline(self):
        cases = [case("tengo este stack trace y no sé de dónde sale el error, ayúdame a diagnosticarlo", ["debug"]),
                 case("receta de tortilla", [], "negativo")]
        pairs = ev.similar_pairs(cases, ev.default_cases())
        self.assertEqual(len(pairs), 1)
        self.assertGreaterEqual(pairs[0][0], 0.3)
        dup = [case("revisa mi codigo", ["code-review"]), case("Revisa mi  codigo", ["code-review"])]
        self.assertEqual(ev.similar_pairs(dup), [(1.0, "revisa mi codigo", "Revisa mi  codigo")])
        self.assertEqual(ev.similar_pairs([case("ok", []), case("ok", [])]), [(1.0, "ok", "ok")])

    def test_aviso_en_la_cli(self):
        p = self.tmp / "c.json"
        p.write_text(json.dumps({"version": 1, "cases": [case("hola, como estas hoy", [], "negativo")]}),
                     encoding="utf-8")
        with captured_output() as (_, err):
            self.assertEqual(ev.main(["--cases", str(p), "--json"]), 0)
        self.assertIn("casi duplicado del set inline", err.getvalue())

    def test_make_stress_es_determinista_y_conserva_etiquetas(self):
        sys.path.insert(0, str(Path(ev.__file__).resolve().parent / "bench"))
        import make_stress
        src = json.loads((Path(ev.__file__).resolve().parent / "bench" / "heldout.json").read_text(encoding="utf-8"))
        a, b = make_stress.build(src), make_stress.build(src)
        self.assertEqual(a, b)
        self.assertEqual(len(a["cases"]), 3 * len(src["cases"]))
        for i, orig in enumerate(src["cases"]):
            group = a["cases"][3 * i: 3 * i + 3]
            self.assertEqual([c["variant"] for c in group], ["traceback", "contexto", "pegado"])
            for c in group:
                self.assertEqual((c["expected"], c["kind"]), (orig["expected"], orig["kind"]))
                self.assertIn(orig["query"], c["query"])
            self.assertIn("<pasted_content>", group[2]["query"])
        out = self.tmp / "s.json"
        with captured_output():
            make_stress.main([str(Path(ev.__file__).resolve().parent / "bench" / "heldout.json"), str(out)])
        cases, warnings = ev.load_cases(out)
        self.assertEqual(len(cases), len(a["cases"]))
        self.assertEqual(warnings, [])


if __name__ == "__main__":
    unittest.main()
