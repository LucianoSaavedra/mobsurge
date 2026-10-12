"""
Tests del núcleo léxico (skill_router.py): tokenización, frontmatter, carga de
skills (carpeta y catálogo), scoring IDF, clasificación, conflictos y CLI.

    cd claude-skills-library/skill-router && python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest import mock

import _support  # noqa: F401  (sys.path + sin .pyc)
from _support import NoRealLLMMixin, TempDirTestCase, captured_output, write_skill, write_skill_md

import llm_scorer
import skill_router
from skill_router import (
    SCENARIO_SEP,
    Skill,
    classify,
    demo_skills,
    detect_conflicts,
    load_from_catalog,
    load_from_dir,
    parse_frontmatter,
    score_skills,
    strip_accents,
    tokenize,
)

CATALOG = _support.ROUTER_DIR.parent / "skills.json"


# --- strip_accents / tokenize --------------------------------------------------
class StripAccentsTest(unittest.TestCase):
    def test_quita_tildes_dieresis_y_enie(self):
        self.assertEqual(strip_accents("Cálculo Pingüino Ñandú acción"), "Calculo Pinguino Nandu accion")

    def test_no_toca_texto_sin_acentos(self):
        self.assertEqual(strip_accents("code review 123"), "code review 123")


class TokenizeTest(unittest.TestCase):
    def test_minusculas_y_sin_acentos(self):
        self.assertEqual(tokenize("EXAMEN de Cálculo"), ["examen", "calculo"])

    def test_quita_stopwords_espanol_e_ingles(self):
        self.assertEqual(tokenize("quiero que la de para the and with your"), [])
        self.assertEqual(tokenize("necesito un guion para el reel"), ["guion", "reel"])

    def test_tokens_cortos_solo_si_son_siglas_utiles(self):
        # "pr", "ui", "3d" se conservan; "xy" y "ok" (2 letras sin valor) se descartan
        self.assertEqual(tokenize("PR de UI en 3D xy ok"), ["pr", "ui", "3d"])

    def test_separa_por_signos_y_conserva_numeros(self):
        self.assertEqual(tokenize("¿pull-request #2024? ¡sí!"), ["pull", "request", "2024"])

    def test_texto_vacio_o_none(self):
        self.assertEqual(tokenize(""), [])
        self.assertEqual(tokenize(None), [])
        self.assertEqual(tokenize("¿¡!? ... --"), [])


# --- parse_frontmatter -----------------------------------------------------------
class ParseFrontmatterTest(TempDirTestCase):
    def md(self, text, rel="mi-skill"):
        return write_skill_md(self.tmp, rel, text)

    def test_description_en_linea(self):
        p = self.md("---\nname: alfa\ndescription: Una descripción en línea\n---\n# Alfa\n")
        self.assertEqual(parse_frontmatter(p), ("alfa", "Una descripción en línea"))

    def test_description_en_bloque_plegado_con_chomping(self):
        p = self.md("---\nname: beta\ndescription: >-\n  Primera línea\n  segunda línea\n"
                    "license: MIT\n---\ncuerpo\n")
        self.assertEqual(parse_frontmatter(p), ("beta", "Primera línea segunda línea"))

    def test_bloque_literal_con_lineas_en_blanco(self):
        p = self.md("---\nname: gamma\ndescription: |\n  uno\n\n  dos\n---\n")
        self.assertEqual(parse_frontmatter(p), ("gamma", "uno dos"))

    def test_almohadilla_dentro_de_bloque_es_literal(self):
        p = self.md("---\nname: d\ndescription: >\n  usa C# y el issue #42\n---\n")
        self.assertEqual(parse_frontmatter(p)[1], "usa C# y el issue #42")

    def test_escalar_plano_que_continua_en_lineas_indentadas(self):
        p = self.md("---\nname: eta\ndescription: plano que\n  continúa en otra línea\nother: x\n---\n")
        self.assertEqual(parse_frontmatter(p), ("eta", "plano que continúa en otra línea"))

    def test_name_anidado_en_metadata_no_pisa_el_nombre(self):
        p = self.md("---\nname: real\ndescription: d\nmetadata:\n  name: anidado\n  version: 2\n---\n")
        self.assertEqual(parse_frontmatter(p), ("real", "d"))

    def test_sin_name_usa_la_carpeta(self):
        p = self.md("---\ndescription: algo\n---\n", rel="carpeta-x")
        self.assertEqual(parse_frontmatter(p), ("carpeta-x", "algo"))

    def test_sin_description_usa_primer_parrafo_del_cuerpo(self):
        p = self.md("---\nname: k\n---\n# Título\n\n```\ncódigo\n```\n"
                    "Primer párrafo\nsigue aquí.\n\nSegundo párrafo.\n")
        self.assertEqual(parse_frontmatter(p), ("k", "Primer párrafo sigue aquí."))

    def test_bloque_vacio_cae_al_primer_parrafo(self):
        p = self.md("---\nname: lam\ndescription: >\n---\nCuerpo de respaldo.\n")
        self.assertEqual(parse_frontmatter(p), ("lam", "Cuerpo de respaldo."))

    def test_sin_frontmatter(self):
        p = self.md("# Título\n\nTexto del cuerpo.\n", rel="sin-fm")
        self.assertEqual(parse_frontmatter(p), ("sin-fm", "Texto del cuerpo."))

    def test_primer_parrafo_se_recorta_a_1024(self):
        p = self.md("---\nname: largo\n---\n" + "palabra " * 500 + "\n")
        desc = parse_frontmatter(p)[1]
        self.assertEqual(len(desc), 1024)
        self.assertTrue(desc.startswith("palabra palabra"))

    def test_comillas_dobles_y_simples(self):
        p = self.md('---\nname: \'it\'\'s\'\ndescription: "con \\"comillas\\" y: dos puntos"\n---\n')
        self.assertEqual(parse_frontmatter(p), ("it's", 'con "comillas" y: dos puntos'))

    def test_comillas_dobles_en_varias_lineas(self):
        p = self.md('---\nname: nu\ndescription: "multi\n  línea entre comillas"\n---\n')
        self.assertEqual(parse_frontmatter(p), ("nu", "multi línea entre comillas"))

    def test_comentario_tras_escalar_plano(self):
        p = self.md("---\nname: iota\ndescription: C# y F# # comentario\n---\n")
        self.assertEqual(parse_frontmatter(p), ("iota", "C# y F#"))

    def test_comentario_tras_valor_entre_comillas(self):
        # En YAML `clave: "valor" # comentario` vale "valor" (sin comillas ni comentario).
        p = self.md('---\nname: "omicron" # nombre\ndescription: "Hola mundo" # comentario\n---\n')
        self.assertEqual(parse_frontmatter(p), ("omicron", "Hola mundo"))

    def test_comentario_html_de_varias_lineas_no_entra_en_la_descripcion(self):
        # El primer párrafo debe excluir comentarios (lo dice el docstring de
        # _first_paragraph); un comentario HTML multilínea es habitual en plantillas.
        p = self.md("<!--\nnota interna\n-->\nTexto real.\n", rel="html")
        self.assertEqual(parse_frontmatter(p), ("html", "Texto real."))

    def test_bom_y_crlf(self):
        p = self.md("﻿---\r\nname: crlf\r\ndescription: con CRLF\r\n---\r\n")
        self.assertEqual(parse_frontmatter(p), ("crlf", "con CRLF"))

    def test_bytes_no_utf8_no_revientan(self):
        p = self.md(b"---\nname: latin\ndescription: caf\xe9 latin1\n---\n")
        name, desc = parse_frontmatter(p)
        self.assertEqual(name, "latin")
        self.assertTrue(desc.startswith("caf") and desc.endswith("latin1"))

    def test_frontmatter_sin_cierre(self):
        p = self.md("---\nname: mu\ndescription: sin cierre\n")
        self.assertEqual(parse_frontmatter(p), ("mu", "sin cierre"))

    def test_archivo_inexistente(self):
        self.assertEqual(parse_frontmatter(self.tmp / "no" / "SKILL.md"), ("", ""))


# --- load_from_dir -----------------------------------------------------------------
class LoadFromDirTest(TempDirTestCase):
    def names(self, skills):
        return sorted(s.name for s in skills)

    def test_layout_estandar_y_metadatos(self):
        write_skill(self.tmp, "uno", "skill-uno", "primera skill")
        write_skill_md(self.tmp, "dos", "# Dos\n\nSin frontmatter.\n")
        skills = load_from_dir(self.tmp)
        self.assertEqual(self.names(skills), ["dos", "skill-uno"])
        uno = next(s for s in skills if s.name == "skill-uno")
        self.assertEqual(uno.description, "primera skill")
        self.assertEqual(Path(uno.source), self.tmp / "uno")
        dos = next(s for s in skills if s.name == "dos")
        self.assertEqual(dos.description, "Sin frontmatter.")

    def test_ignora_ocultas_sources_y_basura(self):
        write_skill(self.tmp, "buena", "buena", "x")
        write_skill(self.tmp, ".oculta", "oculta", "x")
        write_skill(self.tmp, "_sources/repo/clonada", "clonada", "x")
        write_skill(self.tmp, "node_modules/pkg", "pkg", "x")
        write_skill(self.tmp, "__pycache__/c", "c", "x")
        (self.tmp / "vacia").mkdir()
        (self.tmp / "archivo.txt").write_text("no soy skill", encoding="utf-8")
        self.assertEqual(self.names(load_from_dir(self.tmp)), ["buena"])

    def test_no_entra_dentro_de_una_skill(self):
        write_skill(self.tmp, "madre", "madre", "x")
        write_skill(self.tmp, "madre/recursos/hija", "hija", "x")
        self.assertEqual(self.names(load_from_dir(self.tmp)), ["madre"])

    def test_profundidad_maxima(self):
        write_skill(self.tmp, "synced/abc/tres", "tres", "nivel 3")
        write_skill(self.tmp, "a/b/c/cuatro", "cuatro", "nivel 4")
        self.assertEqual(self.names(load_from_dir(self.tmp)), ["tres"])
        self.assertEqual(self.names(load_from_dir(self.tmp, max_depth=4)), ["cuatro", "tres"])
        self.assertEqual(self.names(load_from_dir(self.tmp, max_depth=1)), [])

    def test_enlaces_a_la_misma_carpeta_cuentan_una_vez_y_rotos_se_saltan(self):
        write_skill(self.tmp, "uno", "uno", "x")
        try:
            os.symlink(self.tmp / "uno", self.tmp / "uno-enlace")
            os.symlink(self.tmp / "no-existe", self.tmp / "roto")
        except (OSError, NotImplementedError) as e:  # p.ej. Windows sin permisos
            self.skipTest(f"sin soporte de enlaces simbólicos: {e}")
        skills = load_from_dir(self.tmp)
        self.assertEqual(self.names(skills), ["uno"])

    def test_carpeta_que_es_una_skill(self):
        write_skill(self.tmp, "sola", "sola", "x")
        write_skill(self.tmp, "sola/sub", "sub", "x")
        self.assertEqual(self.names(load_from_dir(self.tmp / "sola")), ["sola"])

    def test_sources_se_lee_si_se_apunta_directamente(self):
        write_skill(self.tmp, "_sources/repo/clonada", "clonada", "x")
        self.assertEqual(self.names(load_from_dir(self.tmp / "_sources")), ["clonada"])

    def test_ruta_inexistente_o_archivo(self):
        (self.tmp / "f.txt").write_text("x", encoding="utf-8")
        self.assertEqual(load_from_dir(self.tmp / "nada"), [])
        self.assertEqual(load_from_dir(self.tmp / "f.txt"), [])
        self.assertEqual(load_from_dir(str(self.tmp / "nada")), [])


# --- load_from_catalog -----------------------------------------------------------------
class LoadFromCatalogTest(TempDirTestCase):
    def write(self, data) -> Path:
        p = self.tmp / "skills.json"
        p.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
        return p

    def test_deduplica_por_nombre_y_fusiona(self):
        p = self.write({"scenarios": {
            "s1": {"title": "T1", "repos": [{"name": "r", "about": "A", "url": "u1"},
                                            {"name": "q", "about": "Q"}]},
            "s2": {"title": "T2", "repos": [{"name": "r", "about": "B", "url": "u2"},
                                            {"name": "r", "about": "A"}]},
            "s3": {"repos": [{"name": "r", "about": "  A  "}]},
        }})
        skills = {s.name: s for s in load_from_catalog(p)}
        self.assertEqual(sorted(skills), ["q", "r"])
        r = skills["r"]
        self.assertEqual(r.scenario, SCENARIO_SEP.join(["T1", "T2", "s3"]))  # sin título -> clave
        self.assertEqual(r.description, "A B")  # descripciones distintas, sin repetir
        self.assertEqual(r.source, "u1")  # URL de la primera aparición
        self.assertEqual(skills["q"].source, "catalog")

    def test_acepta_ruta_como_string(self):
        p = self.write({"scenarios": {"s": {"title": "T", "repos": [{"name": "x", "about": "y"}]}}})
        self.assertEqual([s.name for s in load_from_catalog(str(p))], ["x"])

    def test_tolera_entradas_mal_formadas(self):
        p = self.write({"scenarios": {"malo": "x", "malo2": {"repos": "x"},
                                      "ok": {"title": "T", "repos": [5, None, {"name": "x", "about": "y"}]}}})
        self.assertEqual([s.name for s in load_from_catalog(p)], ["x"])

    def test_json_invalido_o_inexistente_devuelve_vacio(self):
        self.assertEqual(load_from_catalog(self.write("{no es json")), [])
        self.assertEqual(load_from_catalog(self.write("[1, 2]")), [])
        self.assertEqual(load_from_catalog(self.write({"scenarios": []})), [])
        self.assertEqual(load_from_catalog(self.tmp / "no-existe.json"), [])
        bad = self.tmp / "binario.json"
        bad.write_bytes(b"\xff\xfe\x00")
        self.assertEqual(load_from_catalog(bad), [])

    @unittest.skipUnless(CATALOG.is_file(), "no está el skills.json del repo")
    def test_catalogo_real_sin_nombres_repetidos(self):
        raw = json.loads(CATALOG.read_text(encoding="utf-8"))
        entries = [r.get("name") for sc in raw["scenarios"].values() for r in sc.get("repos", [])]
        skills = load_from_catalog(CATALOG)
        names = [s.name for s in skills]
        self.assertEqual(len(names), len(set(names)), f"nombres repetidos: {names}")
        self.assertEqual(set(names), set(entries))
        dupes = {n for n in entries if entries.count(n) > 1}
        for s in skills:
            if s.name in dupes:  # una skill repetida conserva todos sus escenarios
                self.assertIn(SCENARIO_SEP, s.scenario, s.name)


# --- Skill.build_terms / score_skills ---------------------------------------------------
class ScoreSkillsTest(unittest.TestCase):
    OBVIOUS = [
        ("quiero preparar mi examen de calculo y repasar", "estudio-a-medida"),
        ("un guion para un reel viral de tiktok", "guiones-video"),
        ("campana de marketing y copywriting para redes", "marca-marketing"),
        ("revisa este pull request por bugs de seguridad", "code-review"),
        ("tengo un stack trace, ayudame a diagnosticar el error", "debug"),
        ("investiga el mercado y compara tendencias", "deep-research"),
    ]

    def test_la_skill_obvia_gana(self):
        for query, expected in self.OBVIOUS:
            for use_concepts in (False, True):
                with self.subTest(query=query, conceptos=use_concepts):
                    res = score_skills(demo_skills(), query, use_concepts=use_concepts)
                    self.assertEqual(res[0]["name"], expected)
                    self.assertGreater(res[0]["score"], res[1]["score"])

    def test_query_sin_terminos_utiles_da_cero(self):
        for query in ("", "   ", "de la que para el", "¿¡...!?", "the and with"):
            with self.subTest(query=query):
                res = score_skills(demo_skills(), query)
                self.assertEqual(len(res), len(demo_skills()))
                self.assertTrue(all(r["score"] == 0 and r["matched"] == [] for r in res))

    def test_terminos_ausentes_dan_cero(self):
        res = score_skills(demo_skills(), "zxqwv plorf")
        self.assertTrue(all(r["score"] == 0 for r in res))

    def test_rango_orden_y_forma(self):
        res = score_skills(demo_skills(), "revisa el diff y diagnostica el error del examen")
        for r in res:
            self.assertEqual(set(r), {"name", "scenario", "score", "matched", "source"})
            self.assertIsInstance(r["score"], int)
            self.assertGreaterEqual(r["score"], 0)
            self.assertLessEqual(r["score"], 100)
        keys = [(-r["score"], r["name"]) for r in res]
        self.assertEqual(keys, sorted(keys))  # score desc; empate por nombre

    def test_todos_los_terminos_coinciden_da_100(self):
        res = score_skills(demo_skills(), "debug")
        self.assertEqual((res[0]["name"], res[0]["score"]), ("debug", 100))

    def test_idf_pesa_mas_el_termino_raro(self):
        skills = [Skill("a", "comun raro"), Skill("b", "comun"), Skill("c", "comun")]
        res = {r["name"]: r["score"] for r in score_skills(skills, "comun raro", use_concepts=False)}
        self.assertEqual(res["a"], 100)
        # idf(comun)=1.0, idf(raro)=ln(4/2)+1≈1.69 -> b = 100*1/2.69 ≈ 37
        self.assertEqual(res["b"], 37)
        self.assertEqual(res["b"], res["c"])

    def test_matched_solo_contiene_terminos_del_mensaje(self):
        res = score_skills(demo_skills(), "revisa este pull request")
        cr = next(r for r in res if r["name"] == "code-review")
        self.assertIn("pull", cr["matched"])
        self.assertIn("request", cr["matched"])
        q_terms = set(tokenize("revisa este pull request")) | {"concept:revision_codigo"}
        self.assertTrue(set(cr["matched"]) <= q_terms, cr["matched"])

    def test_conceptos_hacen_coincidir_sinonimos(self):
        skills = [Skill("estudio", "examen simulacro"), Skill("cocina", "receta pasta")]
        q = "repasar para la prueba"
        base = {r["name"]: r["score"] for r in score_skills(skills, q, use_concepts=False)}
        conc = {r["name"]: r["score"] for r in score_skills(skills, q, use_concepts=True)}
        self.assertEqual(base["estudio"], 0)
        self.assertGreater(conc["estudio"], 0)
        self.assertEqual(conc["cocina"], 0)

    def test_lista_vacia_de_skills(self):
        self.assertEqual(score_skills([], "examen"), [])

    def test_descripcion_none_no_aporta_el_token_none(self):
        s = Skill("x", None)
        s.build_terms()
        self.assertNotIn("none", s.terms)
        self.assertEqual(s.terms, {})  # "x" tiene 1 letra: se descarta


# --- classify -----------------------------------------------------------------------------
class ClassifyTest(unittest.TestCase):
    def setUp(self):
        self.res = [{"name": n, "score": s} for n, s in
                    [("a", 80), ("b", 50), ("c", 20), ("d", 10), ("e", 0)]]

    def names(self, rows):
        return [r["name"] for r in rows]

    def test_top_y_umbral(self):
        rec, noise = classify(self.res, top=2, threshold=15)
        self.assertEqual(self.names(rec), ["a", "b"])
        # "c" supera el umbral pero queda fuera del top: ni recomendada ni ruido
        self.assertEqual(self.names(noise), ["d", "e"])

    def test_umbral_alto(self):
        rec, noise = classify(self.res, top=10, threshold=60)
        self.assertEqual(self.names(rec), ["a"])
        self.assertEqual(self.names(noise), ["b", "c", "d", "e"])

    def test_score_cero_nunca_se_recomienda_aunque_umbral_sea_cero(self):
        rec, noise = classify(self.res, top=10, threshold=0)
        self.assertEqual(self.names(rec), ["a", "b", "c", "d"])
        self.assertEqual(self.names(noise), ["e"])

    def test_top_cero(self):
        rec, noise = classify(self.res, top=0, threshold=15)
        self.assertEqual(rec, [])
        self.assertEqual(self.names(noise), ["d", "e"])

    def test_negativo_nada_supera_el_umbral(self):
        rows = [{"name": "x", "score": 14}, {"name": "y", "score": 0}]
        rec, noise = classify(rows, top=3, threshold=15)
        self.assertEqual(rec, [])
        self.assertEqual(self.names(noise), ["x", "y"])

    def test_empate_en_el_umbral_cuenta_como_recomendada(self):
        rec, _ = classify([{"name": "x", "score": 15}], top=3, threshold=15)
        self.assertEqual(self.names(rec), ["x"])

    def test_filas_iguales_por_valor_se_distinguen(self):
        rows = [{"name": "x", "score": 50}, {"name": "x", "score": 50}]
        rec, noise = classify(rows, top=1, threshold=15)
        self.assertEqual(len(rec), 1)
        self.assertEqual(noise, [])

    def test_con_score_skills_mensaje_negativo(self):
        rec, noise = classify(score_skills(demo_skills(), "hola, como estas hoy"), 3, 15)
        self.assertEqual(rec, [])
        self.assertEqual(len(noise), len(demo_skills()))


# --- detect_conflicts -----------------------------------------------------------------------
class DetectConflictsTest(unittest.TestCase):
    def test_par_solapado_vs_distinto(self):
        a = Skill("alfa", "revisar codigo bugs seguridad pull request diff")
        b = Skill("beta", "revisar codigo bugs seguridad pull request diff rendimiento")
        c = Skill("gamma", "receta cocina pasta tomate")
        pairs = detect_conflicts([a, b, c])
        self.assertEqual(len(pairs), 1)
        p = pairs[0]
        self.assertEqual({p["a"], p["b"]}, {"alfa", "beta"})
        self.assertGreaterEqual(p["similarity"], 0.35)
        self.assertLessEqual(p["similarity"], 1.0)
        self.assertIn("seguridad", p["shared"])
        self.assertIn("~revision_codigo", p["shared"])  # concepto mostrado legible
        self.assertFalse(any(s.startswith("concept:") for s in p["shared"]))

    def test_contenido_identico_da_similitud_1(self):
        # objetos distintos con el mismo contenido sí se comparan (y son idénticos)
        same = detect_conflicts([Skill("repaso", "examen repaso"), Skill("repaso", "examen repaso")])
        self.assertEqual(len(same), 1)
        self.assertEqual(same[0]["similarity"], 1.0)

    def test_sin_conceptos_solo_cuentan_las_palabras(self):
        a = Skill("aaa", "examen simulacro")
        b = Skill("bbb", "prueba parcial")  # sinónimos: solo comparten el concepto ~estudio
        self.assertEqual(detect_conflicts([a, b], threshold=0.01, use_concepts=False), [])
        pairs = detect_conflicts([a, b], threshold=0.01, use_concepts=True)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]["shared"], ["~estudio"])

    def test_mismo_objeto_repetido_no_se_compara_consigo_mismo(self):
        a = Skill("alfa", "revisar codigo bugs")
        c = Skill("gamma", "receta cocina pasta")
        self.assertEqual(detect_conflicts([a, a, c]), [])

    def test_umbral_y_orden(self):
        # Antes se comprobaba con el demo a 0.1, pero esos dos pares eran espurios
        # (estudio-a-medida/deep-research solo por ~investigacion, que salía de la
        # palabra "estudio"; code-review/debug por conceptos de una palabra
        # suelta). Ver test_demo_sin_solapes_espurios. Aquí, un set con solapes
        # graduados conocidos.
        skills = [
            Skill("a1", "revisar codigo bugs seguridad diff rendimiento"),
            Skill("a2", "revisar codigo bugs seguridad diff inyeccion"),
            Skill("a3", "revisar codigo bugs reel tiktok guion escena gancho viral montaje"),
            Skill("z", "receta cocina pasta tomate"),
        ]
        self.assertEqual(len(detect_conflicts(skills)), 1)  # umbral por defecto: solo a1-a2
        low = detect_conflicts(skills, threshold=0.1)
        self.assertGreaterEqual(len(low), 2)
        sims = [p["similarity"] for p in low]
        self.assertEqual(sims, sorted(sims, reverse=True))
        self.assertTrue(all(s >= 0.1 for s in sims))
        self.assertEqual({low[0]["a"], low[0]["b"]}, {"a1", "a2"})
        self.assertFalse(any("z" in (p["a"], p["b"]) for p in low))

    def test_demo_sin_solapes_espurios(self):
        # core-2: con 1 sola palabra de un concepto una skill ya lo recibía, y
        # "estudio" (que también es ~investigacion) emparejaba estudio-a-medida
        # con deep-research. Ahora el demo no tiene solapes ni a 0.1.
        self.assertEqual(detect_conflicts(demo_skills()), [])
        self.assertEqual(detect_conflicts(demo_skills(), threshold=0.1), [])

    def test_sin_terminos_en_comun(self):
        self.assertEqual(detect_conflicts([Skill("aaa", "uno dos"), Skill("bbb", "tres cuatro")],
                                          threshold=0.0001), [])


# --- CLI ---------------------------------------------------------------------------------------
class CliTest(NoRealLLMMixin, TempDirTestCase):
    def setUp(self):
        super().setUp()
        self.block_real_llm()

    def run_main(self, *argv):
        with captured_output() as (out, err):
            code = skill_router.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_json_demo(self):
        code, out, _ = self.run_main("--demo", "--json", "revisa", "este", "pull", "request")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["query"], "revisa este pull request")
        self.assertEqual(data["source"], "demo")
        self.assertEqual(data["recommended"][0]["name"], "code-review")
        self.assertLessEqual(len(data["recommended"]), 3)

    def test_texto_demo(self):
        code, out, _ = self.run_main("--demo", "un guion para un reel viral")
        self.assertEqual(code, 0)
        self.assertIn("guiones-video", out)
        self.assertIn("Usa estas ahora", out)

    def test_sin_mensaje_es_error_de_uso(self):
        with captured_output(), self.assertRaises(SystemExit) as cm:
            skill_router.main(["--demo"])
        self.assertEqual(cm.exception.code, 2)

    def test_top_cero_es_error_de_uso(self):
        with captured_output(), self.assertRaises(SystemExit) as cm:
            skill_router.main(["--demo", "--top", "0", "hola"])
        self.assertEqual(cm.exception.code, 2)

    def test_conflicts_json(self):
        code, out, _ = self.run_main("--demo", "--conflicts", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"source": "demo", "conflicts": []})

    def test_skills_dir_vacio_sale_con_1(self):
        code, _, err = self.run_main("--skills-dir", str(self.tmp), "examen")
        self.assertEqual(code, 1)
        self.assertIn("No encontre skills", err)

    def test_skills_dir_con_skills(self):
        write_skill(self.tmp, "cocina", "cocina", "recetas de pasta y salsa de tomate")
        write_skill(self.tmp, "fiscal", "fiscal", "impuestos declaracion renta hacienda")
        code, out, _ = self.run_main("--skills-dir", str(self.tmp), "--json", "receta de pasta")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual([r["name"] for r in data["recommended"]], ["cocina"])
        self.assertEqual([r["name"] for r in data["noise"]], ["fiscal"])

    def test_llm_no_disponible_cae_al_lexico(self):
        with mock.patch.object(llm_scorer, "llm_score",
                               side_effect=llm_scorer.LLMUnavailable("sin backend")):
            code, out, err = self.run_main("--demo", "--llm", "--json", "revisa este pull request")
        self.assertEqual(code, 0)
        self.assertIn("Scorer LLM no disponible", err)
        data = json.loads(out)
        self.assertEqual(data["scorer"], "léxico+conceptos")
        self.assertEqual(data["recommended"][0]["name"], "code-review")

    def test_llm_simulado_se_usa_y_se_etiqueta(self):
        def fake_llm_score(skills, query, **_kw):  # el CLI pasa model= y usage=
            llm_scorer.last_call_info.clear()
            llm_scorer.last_call_info.update({"backend": "cli", "cached": True})
            rows = [{"name": s.name, "scenario": s.scenario, "score": 0, "matched": [],
                     "source": s.source} for s in skills]
            rows[0]["score"] = 90
            rows[0]["matched"] = ["porque sí"]
            return rows

        with mock.patch.object(llm_scorer, "llm_score", side_effect=fake_llm_score):
            code, out, _ = self.run_main("--demo", "--llm", "--json", "lo que sea")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["scorer"], "LLM (cli, caché)")
        self.assertEqual([r["name"] for r in data["recommended"]], [demo_skills()[0].name])

    def test_default_skills_sin_home_usa_catalogo_o_demo(self):
        with mock.patch.object(skill_router.Path, "home", return_value=self.tmp):
            skills, label = skill_router._default_skills()
        self.assertTrue(skills)
        if CATALOG.is_file():
            self.assertTrue(label.startswith("catalog:"), label)
        else:
            self.assertEqual(label, "demo")

    def test_default_skills_prefiere_home(self):
        write_skill(self.tmp, ".claude/skills/mia", "mia", "mi skill")
        with mock.patch.object(skill_router.Path, "home", return_value=self.tmp):
            skills, label = skill_router._default_skills()
        self.assertEqual([s.name for s in skills], ["mia"])
        self.assertTrue(label.startswith("dir:"), label)


if __name__ == "__main__":
    unittest.main()
