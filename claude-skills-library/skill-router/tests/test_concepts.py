"""
Tests de la capa de conceptos (concepts.py).

    cd claude-skills-library/skill-router && python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import re
import unittest

import _support  # noqa: F401  (sys.path + sin .pyc)

import concepts
from concepts import CONCEPTS, concepts_for, expand_tokens
from skill_router import strip_accents, tokenize


class ExpandTokensTest(unittest.TestCase):
    def test_conserva_tokens_y_anade_conceptos_al_final(self):
        self.assertEqual(expand_tokens(["examen", "xyz"]), ["examen", "xyz", "concept:estudio"])

    def test_termino_en_varios_conceptos(self):
        # "estudio" es a la vez estudio (académico) e investigación (estudio de mercado)
        out = expand_tokens(["estudio"])
        self.assertEqual(out[0], "estudio")
        self.assertEqual(sorted(out[1:]), ["concept:estudio", "concept:investigacion"])

    def test_un_concepto_por_token_que_lo_activa(self):
        out = expand_tokens(["examen", "repaso"])
        self.assertEqual(out, ["examen", "repaso", "concept:estudio", "concept:estudio"])

    def test_sin_conceptos(self):
        self.assertEqual(expand_tokens(["zzz", "qqq"]), ["zzz", "qqq"])
        self.assertEqual(expand_tokens([]), [])

    def test_no_muta_la_entrada(self):
        toks = ["reel", "tiktok"]
        out = expand_tokens(toks)
        self.assertEqual(toks, ["reel", "tiktok"])
        self.assertEqual(out, ["reel", "tiktok", "concept:video", "concept:video"])

    def test_sinonimos_de_distintas_familias(self):
        cases = {
            "crash": "depuracion", "traceback": "depuracion",
            "diff": "revision_codigo", "merge": "revision_codigo",
            "instagram": "redes", "newsletter": "marketing",
            "youtube": "video", "benchmark": "investigacion",
        }
        for term, concept in cases.items():
            with self.subTest(term=term):
                self.assertIn(f"concept:{concept}", expand_tokens([term]))

    def test_tras_tokenizar_un_mensaje_real(self):
        out = expand_tokens(tokenize("Revísame el PR, por favor"))
        self.assertIn("pr", out)
        self.assertIn("revisame", out)
        self.assertEqual(out.count("concept:revision_codigo"), 2)


class ConceptsCatalogTest(unittest.TestCase):
    def test_concepts_for(self):
        self.assertEqual(concepts_for("examen"), ["estudio"])
        self.assertEqual(concepts_for("no-existe"), [])

    def test_terminos_normalizados_como_el_tokenizador(self):
        for concept, terms in CONCEPTS.items():
            for t in terms:
                with self.subTest(concept=concept, term=t):
                    self.assertRegex(t, r"^[a-z0-9]+$")
                    self.assertEqual(t, strip_accents(t.lower()))

    def test_todos_los_terminos_sobreviven_al_tokenizador(self):
        # Un término que tokenize() descarta (stopword o demasiado corto) nunca
        # podría activar su concepto: sería una entrada muerta.
        dead = [(c, t) for c, ts in CONCEPTS.items() for t in ts if tokenize(t) != [t]]
        self.assertEqual(dead, [])

    def test_indice_inverso_sin_duplicados_y_completo(self):
        for term, cs in concepts._TERM_TO_CONCEPTS.items():
            self.assertEqual(len(cs), len(set(cs)), term)
            for c in cs:
                self.assertIn(term, CONCEPTS[c])
        n_terms = len({t for ts in CONCEPTS.values() for t in ts})
        self.assertEqual(len(concepts._TERM_TO_CONCEPTS), n_terms)

    def test_nombres_de_concepto_validos(self):
        for c in CONCEPTS:
            self.assertTrue(re.fullmatch(r"[a-z_]+", c), c)


if __name__ == "__main__":
    unittest.main()
