#!/usr/bin/env python3
"""
Benchmark del Skill Router: mide si recomienda la skill correcta por contexto,
y compara el baseline (solo palabras clave) contra la capa de conceptos (y,
opcionalmente, contra el scorer LLM).

Set por defecto: 19 casos inline sobre las skills de demo, de tres tipos:
- directos: el mensaje comparte palabras con la descripción de la skill.
- sinónimos: el mensaje usa otras palabras (aquí es donde los conceptos ayudan).
- negativos: no debería activarse ninguna skill.

Set externo (--cases archivo.json), con este formato:
    {"version": 1,
     "cases": [{"query": "<mensaje>", "expected": ["<skill>", ...], "kind": "<tipo>"}]}
- expected: skills aceptables como top-1 (cualquiera cuenta como acierto).
  Lista vacía [] = caso negativo: el acierto es no recomendar ninguna skill.
- kind: uno de directo, sinonimo, multi, ingles, typo, negativo, ambiguo.
Los nombres de expected deben existir en el set de skills (las de demo).

Métricas principales (las del producto, con el umbral por defecto 15):
- top-1: la #1 recomendada está en expected; top-3: alguna de expected está entre
  las recomendadas (máx. 3). En los negativos, acierto = no recomendar nada.
- Empates (bench-2): si varias recomendadas comparten la puntuación máxima, el
  top-1 vale |esperadas ∩ empatadas| / |empatadas| (el valor esperado con un
  desempate al azar), nunca lo que diga el orden alfabético. Igual en el corte
  del top-3: si el empate cruza el corte, el crédito es la probabilidad de que
  una esperada entre al elegir al azar entre las empatadas. Renombrar una skill
  no cambia ninguna cifra.

Diagnóstico (clave "diagnostics", no sustituye a lo anterior):
- hit@1 de ranking: en los positivos, la esperada es la #1 por score bruto (>0),
  sin mirar el umbral; con empate, crédito parcial como arriba.
- MRR: en los positivos, 1/posición del primer esperado en el ranking bruto
  (0 si no puntúa). Con empate se usa la posición esperada al azar.
- tasa de falsos positivos: % de negativos en los que se recomendó algo.
- abstención: % de positivos sin ninguna recomendación.
- multi completa: en los casos "multi", % en que TODAS las esperadas están entre
  las recomendadas (top-1/top-3 solo miden "al menos una").
- precisión de las recomendaciones: de todo lo recomendado, qué parte está en
  expected (castiga sobre-recomendar).
- causas de fallo: sin_senal, abstuvo_con_1_correcta, empate_en_1,
  ranking_incorrecto, falso_positivo, error_llm.

Economía (scorers con LLM: --llm, --cascade): llamadas al LLM (en la cascada, %
evitado), tokens de entrada/salida totales y por consulta, coste, latencia media
y aciertos top-1 por cada 1000 tokens. Un dato que el backend no informa sale
como "n/d" (null en --json).

Uso:
    python3 eval.py                         # tabla con conceptos activados
    python3 eval.py --compare               # baseline vs conceptos (el número clave)
    python3 eval.py --compare --llm         # + scorer LLM (1 llamada a Claude por caso)
    python3 eval.py --llm                   # tabla detallada con el scorer LLM
    python3 eval.py --cases bench/x.json    # usa un set externo (combinable)
    python3 eval.py --compare --llm --cascade --llm-models opus,haiku
                                            # una tabla: baseline, +conceptos, cascada y LLM por modelo
    python3 eval.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
from math import comb
from pathlib import Path

import llm_scorer
from llm_scorer import LLMUnavailable
from skill_router import demo_skills, score_skills, classify, normalize_query, tokenize

KINDS = ("directo", "sinonimo", "multi", "ingles", "typo", "negativo", "ambiguo")
MAX_CONSECUTIVE_LLM_ERRORS = 3

# (mensaje, skill esperada o None para negativos, tipo) — set por defecto
CASES = [
    # --- directos ---
    ("quiero preparar mi examen de calculo y repasar", "estudio-a-medida", "directo"),
    ("necesito un simulacro con mi rubrica", "estudio-a-medida", "directo"),
    ("un guion para un reel viral de tiktok", "guiones-video", "directo"),
    ("escribeme el gancho y el cta de un short", "guiones-video", "directo"),
    ("campana de marketing y copywriting para redes", "marca-marketing", "directo"),
    ("revisa este pull request por bugs de seguridad", "code-review", "directo"),
    ("busca inyecciones y errores en el diff", "code-review", "directo"),
    ("tengo un stack trace, ayudame a diagnosticar el error", "debug", "directo"),
    ("investiga el mercado y compara tendencias", "deep-research", "directo"),
    # --- sinónimos (el baseline suele fallar; los conceptos ayudan) ---
    ("manana tengo la prueba de biologia, ayudame a aprender", "estudio-a-medida", "sinonimo"),
    ("necesito notas para la clase y mejorar mi calificacion", "estudio-a-medida", "sinonimo"),
    ("ideas de contenido para instagram sobre mi negocio", "marca-marketing", "sinonimo"),
    ("mi app crashea con una excepcion, no se por que", "debug", "sinonimo"),
    ("quiero posicionar mi marca y conseguir clientes", "marca-marketing", "sinonimo"),
    ("un clip vertical para youtube que enganche", "guiones-video", "sinonimo"),
    ("analisis de la competencia y el sector", "deep-research", "sinonimo"),
    # --- negativos (no debe activarse nada) ---
    ("hola, como estas hoy", None, "negativo"),
    ("cuentame un chiste corto", None, "negativo"),
    ("que hora es en tokio", None, "negativo"),
]


class CasesError(ValueError):
    """El archivo de casos no existe, no es JSON o no cumple el formato."""


def default_cases() -> list[dict]:
    """Los 19 casos inline en el formato común (expected=[skill] o [])."""
    return [{"query": q, "expected": [e] if e else [], "kind": k} for q, e, k in CASES]


def load_cases(path: Path) -> tuple[list[dict], list[str]]:
    """Carga y valida un set de casos externo. Devuelve (casos, avisos).

    Lanza CasesError con todos los problemas de formato encontrados.
    """
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise CasesError(f"no pude leer {path}: {e}") from e
    except ValueError as e:
        raise CasesError(f"{path} no es JSON válido: {e}") from e
    if not isinstance(data, dict) or not isinstance(data.get("cases"), list):
        raise CasesError(f'{path}: se esperaba un objeto con una lista "cases"')
    if "version" in data and (type(data["version"]) is not int or data["version"] != 1):
        raise CasesError(f'{path}: versión {data["version"]!r} no soportada (se esperaba 1)')

    errors: list[str] = []
    warnings: list[str] = []
    cases: list[dict] = []
    for i, c in enumerate(data["cases"], 1):
        where = f"caso #{i}"
        if not isinstance(c, dict):
            errors.append(f"{where}: debe ser un objeto")
            continue
        query, expected, kind = c.get("query"), c.get("expected"), c.get("kind")
        if not isinstance(query, str) or not query.strip():
            errors.append(f'{where}: "query" debe ser un texto no vacío')
        else:
            where = f'caso #{i} ("{query.strip()[:40]}")'
        if not isinstance(expected, list) or not all(isinstance(x, str) and x.strip() for x in expected):
            errors.append(f'{where}: "expected" debe ser una lista de nombres de skill ([] = negativo)')
            expected = []
        if kind not in KINDS:
            errors.append(f'{where}: "kind" debe ser uno de {", ".join(KINDS)} (vino {kind!r})')
        exp = list(dict.fromkeys(x.strip() for x in expected))
        if kind == "negativo" and exp:
            warnings.append(f'{where}: kind "negativo" pero expected no está vacío; se puntúa como positivo')
        elif kind in KINDS and kind != "negativo" and not exp:
            warnings.append(f'{where}: expected vacío (se puntúa como negativo) pero kind es "{kind}"')
        cases.append({"query": query if isinstance(query, str) else "", "expected": exp, "kind": kind})
    if not data["cases"]:
        errors.append("el archivo no tiene casos")
    if errors:
        shown = errors[:15] + ([f"... y {len(errors) - 15} más"] if len(errors) > 15 else [])
        raise CasesError(f"{path}: formato inválido:\n  - " + "\n  - ".join(shown))
    return cases, warnings


def unknown_skills(cases: list[dict], skills) -> list[str]:
    """Nombres de expected que no existen en el set de skills (inalcanzables)."""
    names = {s.name for s in skills}
    return sorted({e for c in cases for e in c["expected"] if e not in names})


def _as_list(expected) -> list[str]:
    if expected is None:
        return []
    if isinstance(expected, str):
        return [expected]
    return list(expected)


def eval_one(skills, msg, expected, use_concepts, top, threshold, use_llm=False, *,
             scorer: str | None = None, model: str | None = None, usage: dict | None = None):
    """Evalúa un caso. expected: lista de skills aceptables ([] o None = negativo).

    Devuelve (recomendadas, top1, top3). top1/top3 son bool, o un float entre 0 y 1
    cuando un empate decide el resultado (ver la regla de empates arriba).
    """
    scorer = scorer or ("llm" if use_llm else "lexical")
    results = _score(skills, msg, use_concepts, scorer, model, usage)
    j = _judge(results, expected, top, threshold)
    return j["got"], j["top1"], j["top3"]


def _score(skills, msg, use_concepts, scorer, model=None, usage=None):
    if scorer == "llm":
        kw: dict = {"usage": usage if isinstance(usage, dict) else {}}
        if model:
            kw["model"] = model
        return llm_scorer.llm_score(skills, msg, **kw)  # vía módulo: se puede simular en tests
    if scorer == "cascade":
        import cascade
        results, _ = cascade.cascade_score(skills, msg, model=model, usage=usage)
        return results
    return score_skills(skills, msg, use_concepts=use_concepts)


def _credit(x: float):
    """bool si el resultado es seguro; float (0, 1) si lo decide un empate."""
    if x >= 1:
        return True
    if x <= 0:
        return False
    return round(x, 4)


def _judge(results, expected, top, threshold) -> dict:
    """Puntúa un resultado ya calculado: métricas principales y de diagnóstico."""
    exp = set(_as_list(expected))
    recommended, _ = classify(results, top, threshold)
    rec = [r["name"] for r in recommended]
    j = {"got": rec, "tie": False}
    if not exp:
        ok = not rec
        j.update(top1=ok, top3=ok, positive=False, fp=bool(rec), hit1_rank=None, rr=None)
        j["cause"] = None if ok else "falso_positivo"
        return j

    hit1 = False
    if rec:
        best = recommended[0]["score"]
        tied = [r["name"] for r in results if r["score"] == best]
        if len(tied) > 1:
            j["tie"] = True
            hit1 = sum(n in exp for n in tied) / len(tied)
        else:
            hit1 = rec[0] in exp
    hit3 = any(n in exp for n in rec)
    if rec and len(rec) == top:
        last = recommended[-1]["score"]
        group = [r["name"] for r in results if r["score"] == last]
        if len(group) > 1 and any(n not in rec for n in group):  # el empate cruza el corte
            above = [r["name"] for r in recommended if r["score"] > last]
            if not any(n in exp for n in above):
                k, m = top - len(above), len(group)
                e = sum(n in exp for n in group)
                hit3 = 1 - comb(m - e, k) / comb(m, k) if e else 0.0

    # ranking bruto, sin umbral
    best_e = max((r["score"] for r in results if r["name"] in exp), default=0)
    if best_e > 0:
        above = sum(1 for r in results if r["score"] > best_e)
        group = [r["name"] for r in results if r["score"] == best_e]
        e = sum(n in exp for n in group)
        rank = above + (len(group) + 1) / (e + 1)
        rr, hit1_rank = 1 / rank, (e / len(group) if above == 0 else 0.0)
    else:
        rr, hit1_rank = 0.0, 0.0

    cause = None
    if hit1 is not True:
        if all(r["score"] <= 0 for r in results):
            cause = "sin_senal"
        elif j["tie"]:
            cause = "empate_en_1"
        elif not rec and hit1_rank == 1:
            cause = "abstuvo_con_1_correcta"
        else:
            cause = "ranking_incorrecto"
    j.update(top1=_credit(float(hit1)), top3=_credit(float(hit3)), positive=True, fp=False,
             hit1_rank=hit1_rank, rr=rr, cause=cause,
             complete=all(n in rec for n in exp))
    return j


def _pct(num: float, den: int) -> float:
    return round(100 * num / den, 1) if den else 0.0


def _num(x):
    """Entero si no tiene decimales (los créditos parciales dejan floats)."""
    return int(x) if float(x).is_integer() else round(x, 4)


def _diag(js: list[dict]) -> dict:
    """Métricas de diagnóstico de una lista de juicios (_judge)."""
    pos = [j for j in js if j["positive"]]
    neg = [j for j in js if not j["positive"]]
    multi = [j for j in pos if j.get("kind") == "multi"]
    n_rec = sum(len(j["got"]) for j in js)
    good_rec = sum(sum(n in j["expected"] for n in j["got"]) for j in js)
    failures: dict[str, int] = {}
    for j in js:
        if j.get("cause"):
            failures[j["cause"]] = failures.get(j["cause"], 0) + 1
    return {
        "positives": len(pos),
        "negatives": len(neg),
        "hit1_rank": _pct(sum(j["hit1_rank"] for j in pos), len(pos)) if pos else None,
        "mrr": round(sum(j["rr"] for j in pos) / len(pos), 3) if pos else None,
        "false_positive_rate": _pct(sum(j["fp"] for j in neg), len(neg)) if neg else None,
        "abstain_rate": _pct(sum(not j["got"] for j in pos), len(pos)) if pos else None,
        "multi_cases": len(multi),
        "multi_complete": _pct(sum(j["complete"] for j in multi), len(multi)) if multi else None,
        "recommended": n_rec,
        "avg_recommended": round(n_rec / len(js), 2) if js else 0.0,
        "rec_precision": _pct(good_rec, n_rec) if n_rec else None,
        "ties_at_top1": sum(j["tie"] for j in js),
        "failures": failures,
    }


def _sum_or_none(values):
    """Suma; None si algún valor es None (el backend no lo informó)."""
    values = list(values)
    if any(v is None for v in values):
        return None
    return sum(values)


def _economy(usages: list[dict], scorer: str, total: int, top1: float) -> dict:
    """Coste en LLM de un scorer: llamadas, tokens, coste, latencia y eficiencia."""
    if scorer == "cascade":
        called = [u for u in usages if u.get("decision") in ("llm", "llm_error")]
    else:
        called = list(usages)
    tin = _sum_or_none(u.get("input_tokens") for u in usages)
    tout = _sum_or_none(u.get("output_tokens") for u in usages)
    tok = None if tin is None or tout is None else tin + tout
    cost = _sum_or_none(u.get("cost_usd") for u in usages)
    lat = [u.get("latency_s") for u in usages]
    models = sorted({u["model"] for u in usages if u.get("model")})
    return {
        "scorer": scorer,
        "model": models[0] if len(models) == 1 else (models or None),
        "queries": total,
        "llm_calls": len(called),
        "llm_calls_cached": sum(1 for u in called if u.get("cached")),
        "llm_avoided_pct": _pct(total - len(called), total) if scorer == "cascade" else None,
        "input_tokens": tin,
        "output_tokens": tout,
        "cache_read_tokens": _sum_or_none(u.get("cache_read_tokens") for u in usages),
        "input_tokens_per_query": round(tin / total, 1) if tin is not None and total else None,
        "output_tokens_per_query": round(tout / total, 1) if tout is not None and total else None,
        "cost_usd": round(cost, 6) if cost is not None else None,
        "avg_latency_s": (round(sum(lat) / len(lat), 3)
                          if lat and all(v is not None for v in lat) else None),
        "top1_per_1k_tokens": round(1000 * top1 / tok, 2) if tok else None,
    }


def run(use_concepts: bool, top: int = 3, threshold: int = 15, use_llm: bool = False,
        cases: list[dict] | None = None, skills=None, *, scorer: str | None = None,
        model: str | None = None) -> dict:
    """Corre el benchmark.

    scorer: "lexical" (por defecto), "llm" (= use_llm) o "cascade". Con un scorer
    que usa el LLM, un caso cuya llamada falla cuenta como fallo (marcado con
    "error"; en la cascada se usa el léxico como respaldo) y 3 fallos seguidos
    abortan con LLMUnavailable para no gastar tiempo en un backend caído. Añade
    la clave "economy" con el gasto de tokens.
    """
    scorer = scorer or ("llm" if use_llm else "lexical")
    uses_llm = scorer in ("llm", "cascade")
    cases = default_cases() if cases is None else cases
    skills = demo_skills() if skills is None else skills
    total = len(cases)
    top1 = top3 = 0.0
    errors = streak = cached = 0
    details, judged, usages = [], [], []
    by_kind: dict[str, dict] = {}
    for case in cases:
        msg, expected, kind = case["query"], case["expected"], case["kind"]
        err = None
        usage: dict = {}
        try:
            results = _score(skills, msg, use_concepts, scorer, model, usage)
            if scorer == "cascade" and usage.get("decision") == "llm_error":
                raise LLMUnavailable(usage.get("error") or "fallo del LLM en la cascada")
            streak = 0
        except LLMUnavailable as e:  # solo los scorers con LLM lanzan esto
            err = str(e)
            errors += 1
            streak += 1
            if streak >= MAX_CONSECUTIVE_LLM_ERRORS:
                raise LLMUnavailable(f"abortado tras {streak} fallos seguidos del LLM; último: {e}") from e
            results = (score_skills(skills, msg, use_concepts=use_concepts)
                       if scorer == "cascade" else [])
        if uses_llm:
            if scorer == "llm" and not usage and not err:
                usage = {"cached": llm_scorer.last_call_info.get("cached")}  # simulador sin usage
            usages.append(usage)
            cached += int(bool(usage.get("cached")))
        j = _judge(results, expected, top, threshold)
        if err and scorer == "llm":  # sin respuesta no hay acierto, ni siquiera en un negativo
            j.update(top1=False, top3=False, cause="error_llm", fp=False, complete=False)
        j["kind"], j["expected"] = kind, _as_list(expected)
        judged.append(j)
        hit1, hit3 = j["top1"], j["top3"]
        top1 += float(hit1)
        top3 += float(hit3)
        k = by_kind.setdefault(kind, {"total": 0, "top1": 0, "top3": 0})
        k["total"] += 1
        k["top1"] = _num(k["top1"] + float(hit1))
        k["top3"] = _num(k["top3"] + float(hit3))
        d = {"query": msg, "expected": _as_list(expected), "kind": kind,
             "got": list(j["got"]), "top1": hit1, "top3": hit3}
        if j["tie"]:
            d["tie_at_top1"] = True
        if j.get("cause"):
            d["cause"] = j["cause"]
        if scorer == "cascade":
            d["used_llm"] = usage.get("decision") == "llm"
        if err:
            d["error"] = err
        details.append(d)
    for k in by_kind.values():
        k["top1_accuracy"] = _pct(k["top1"], k["total"])
        k["top3_accuracy"] = _pct(k["top3"], k["total"])
    # orden canónico de KINDS; un tipo desconocido (casos pasados a mano) va al final
    ordered = {kind: by_kind[kind] for kind in KINDS if kind in by_kind}
    ordered.update({kind: v for kind, v in by_kind.items() if kind not in ordered})
    kinds_diag = {}
    for kind in ordered:
        kinds_diag[kind] = _diag([j for j in judged if j["kind"] == kind])
    out = {"total": total,
           "top1_accuracy": _pct(top1, total),
           "top3_accuracy": _pct(top3, total),
           "by_kind": ordered,
           "diagnostics": {**_diag(judged), "by_kind": kinds_diag},
           "details": details}
    if uses_llm:
        out["llm_errors"] = errors
        out["llm_cached"] = cached
        out["economy"] = _economy(usages, scorer, total, top1)
    return out


# --- Independencia del set externo (bench-5) -----------------------------------
def _terms(text: str) -> set[str]:
    """Términos útiles de la petición (sin lo pegado: lo mismo que ve el router)."""
    return set(tokenize(normalize_query(text)))


def jaccard(a: str, b: str) -> float:
    ta, tb = _terms(a), _terms(b)
    if not ta | tb:  # sin términos útiles: solo cuenta el duplicado exacto
        return 1.0 if " ".join(a.lower().split()) == " ".join(b.lower().split()) else 0.0
    return len(ta & tb) / len(ta | tb)


def similar_pairs(cases: list[dict], reference: list[dict] | None = None,
                  min_jaccard: float = 0.3) -> list[tuple[float, str, str]]:
    """Pares casi duplicados (Jaccard >= min_jaccard sobre tokenize()).

    Con reference, compara cada caso con los de reference; sin él, los casos
    entre sí. Devuelve [(jaccard, query, query_parecida)] de mayor a menor.
    """
    out = []
    for i, c in enumerate(cases):
        others = reference if reference is not None else cases[i + 1:]
        for o in others:
            s = jaccard(c["query"], o["query"])
            if s >= min_jaccard:
                out.append((round(s, 2), c["query"], o["query"]))
    out.sort(key=lambda t: (-t[0], t[1], t[2]))
    return out


# --- Salida -------------------------------------------------------------------
def _fmt_exp(expected: list[str]) -> str:
    return " | ".join(expected) if expected else "(ninguna)"


def _print_by_kind(columns: list[tuple[str, dict]]) -> None:
    kinds = list(dict.fromkeys(k for _, rep in columns for k in rep["by_kind"]))
    if not kinds:
        return
    print("   Precisión por tipo de caso (top-1 / top-3):")
    header = f"   {'tipo':10} {'n':>3}" + "".join(f"  {label:>15}" for label, _ in columns)
    print(header)
    for kind in kinds:
        n = next(rep["by_kind"][kind]["total"] for _, rep in columns if kind in rep["by_kind"])
        cells = ""
        for _, rep in columns:
            k = rep["by_kind"].get(kind)
            cell = f"{k['top1_accuracy']:.1f} / {k['top3_accuracy']:.1f}" if k else "—"
            cells += f"  {cell:>15}"
        print(f"   {kind:10} {n:>3}{cells}")
    print()


def _print_llm_notes(rep: dict) -> None:
    if rep.get("llm_cached"):
        print(f"   ({rep['llm_cached']} de {rep['total']} respuestas del LLM salieron de la caché)")
    if rep.get("llm_errors"):
        print(f"   ⚠️  {rep['llm_errors']} llamada(s) al LLM fallaron y cuentan como fallo.")


MODEL_ALIASES = {"opus": "claude-opus-5-5", "haiku": "claude-haiku-5-5"}


def resolve_models(spec: str | None) -> list[str]:
    """'opus,haiku' (o ids completos) -> ids de modelo, sin repetir."""
    out: list[str] = []
    for part in (spec or "").split(","):
        name = part.strip()
        if name:
            model = MODEL_ALIASES.get(name.lower(), name)
            if model not in out:
                out.append(model)
    return out


def _nd(value, fmt: str = "{}", suffix: str = "") -> str:
    """Formatea un valor; None (el backend no lo informó) se muestra como n/d."""
    return "n/d" if value is None else fmt.format(value) + suffix


def _metric_rows(rep: dict) -> list[str]:
    """Celdas de la tabla comparativa para un scorer (mismo orden que _ROWS)."""
    dg = rep["diagnostics"]
    eco = rep.get("economy")
    cells = [f"{rep['top1_accuracy']:.1f}%", f"{rep['top3_accuracy']:.1f}%",
             _nd(dg["hit1_rank"], "{:.1f}", "%"), _nd(dg["mrr"], "{:.3f}"),
             _nd(dg["false_positive_rate"], "{:.1f}", "%"),
             _nd(dg["multi_complete"], "{:.1f}", "%") if dg["multi_cases"] else "—",
             _nd(dg["rec_precision"], "{:.1f}", "%"), f"{dg['avg_recommended']:.2f}",
             str(dg["ties_at_top1"])]
    if not eco:
        return cells + ["0", "—", "0", "0", "0", "—", "—", "—"]
    calls = str(eco["llm_calls"])
    if eco["llm_avoided_pct"] is not None:
        calls += f" ({eco['llm_avoided_pct']:.0f}% evit.)"
    tok_q = ("n/d" if eco["input_tokens_per_query"] is None or eco["output_tokens_per_query"] is None
             else f"{eco['input_tokens_per_query'] + eco['output_tokens_per_query']:.0f}")
    return cells + [calls, str(eco["llm_calls_cached"]), _nd(eco["input_tokens"]),
                    _nd(eco["output_tokens"]), tok_q, _nd(eco["cost_usd"], "${:.4f}"),
                    _nd(eco["avg_latency_s"], "{:.2f}", " s"),
                    _nd(eco["top1_per_1k_tokens"], "{:.2f}")]


_ROWS = ("Top-1", "Top-3", "hit@1 ranking", "MRR (positivos)", "falsos positivos",
         "multi completa", "precisión recom.", "recom. por caso", "empates en #1",
         "llamadas al LLM", "  de la caché", "tokens entrada", "tokens salida",
         "tokens/consulta", "coste", "latencia media", "top-1 por 1k tokens")


def _print_table(columns: list[tuple[str, dict]]) -> None:
    """Una sola tabla: filas = métricas, columnas = scorers."""
    width = max(14, *(len(label) + 2 for label, _ in columns))
    cols = [_metric_rows(rep) for _, rep in columns]
    print(f"   {'':22}" + "".join(f"{label:>{width}}" for label, _ in columns))
    for i, name in enumerate(_ROWS):
        if i == 9:
            print(f"   {'— economía —':22}")
        print(f"   {name:22}" + "".join(f"{c[i]:>{width}}" for c in cols))
    print()


def _print_failures(columns: list[tuple[str, dict]]) -> None:
    causes = list(dict.fromkeys(c for _, rep in columns for c in rep["diagnostics"]["failures"]))
    if not causes:
        return
    print("   Causas de fallo (nº de casos):")
    print(f"   {'causa':24}" + "".join(f"{label:>16}" for label, _ in columns))
    for cause in causes:
        print(f"   {cause:24}" + "".join(
            f"{rep['diagnostics']['failures'].get(cause, 0):>16}" for _, rep in columns))
    print()


def _derived_from(path: Path):
    """Valor de "derived_from" del archivo (un set generado a partir de otro)."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data.get("derived_from") if isinstance(data, dict) else None


def _warn_similar(what: str, pairs, limit: int = 5) -> None:
    for s, q, o in pairs[:limit]:
        q1, o1 = (" ".join(t.split())[:50] for t in (q, o))
        print(f"⚠️  {what} (Jaccard {s:.2f}): \"{q1}\" ~ \"{o1}\"", file=sys.stderr)
    if len(pairs) > limit:
        print(f"⚠️  ... y {len(pairs) - limit} pares más ({what})", file=sys.stderr)


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Benchmark del Skill Router")
    p.add_argument("--json", action="store_true")
    p.add_argument("--compare", action="store_true", help="baseline vs conceptos")
    p.add_argument("--llm", action="store_true",
                   help="con --compare añade el scorer LLM; sin él, usa el LLM para la tabla "
                        "(1 llamada a Claude por caso)")
    p.add_argument("--cascade", action="store_true",
                   help="cascada: léxico y LLM solo si el léxico duda (con --compare la añade)")
    p.add_argument("--model", help="modelo del LLM para --llm y --cascade (alias: opus, haiku)")
    p.add_argument("--llm-models",
                   help="lista separada por comas (opus,haiku o ids completos): con --compare "
                        "añade una columna LLM por modelo; implica --llm")
    p.add_argument("--cases", type=Path,
                   help="archivo JSON con un set de casos externo (por defecto, los 19 inline)")
    p.add_argument("--threshold", type=int, default=15,
                   help="SOLO diagnóstico (def. 15, el del router). No elijas el umbral "
                        "mirando bench/heldout.json")
    p.add_argument("--top", type=int, default=3, help="SOLO diagnóstico (def. 3)")
    args = p.parse_args(argv)
    args.models = resolve_models(args.llm_models) or resolve_models(args.model) or [None]
    if args.llm_models:
        args.llm = True

    skills = demo_skills()
    if args.cases:
        try:
            cases, warnings = load_cases(args.cases)
        except CasesError as e:
            print(f"❌ {e}", file=sys.stderr)
            return 2
        for w in warnings:
            print(f"⚠️  {w}", file=sys.stderr)
        _warn_similar("casi duplicado del set inline", similar_pairs(cases, default_cases()))
        if _derived_from(args.cases) is None:  # en un set derivado lo son a propósito
            _warn_similar("casos casi duplicados entre sí", similar_pairs(cases))
        cases_label = str(args.cases)
    else:
        cases, cases_label = default_cases(), "inline"
    missing = unknown_skills(cases, skills)
    if missing:
        known = ", ".join(sorted(s.name for s in skills))
        print(f"❌ Skills esperadas que no existen en el set de demo: {', '.join(missing)}\n"
              f"   Disponibles: {known}", file=sys.stderr)
        return 2

    if (args.llm or args.cascade) and llm_scorer.available_backend() is None:
        flag = "--llm" if args.llm else "--cascade"
        print(f"❌ {flag}: no hay ANTHROPIC_API_KEY+SDK ni CLI `claude` disponible.", file=sys.stderr)
        return 2

    try:
        if args.compare:
            return _main_compare(args, cases, cases_label, skills)
        return _main_report(args, cases, cases_label, skills)
    except LLMUnavailable as e:
        print(f"❌ El benchmark con LLM se detuvo: {e}", file=sys.stderr)
        return 2


def _short(model: str | None) -> str:
    if not model:
        return "LLM"
    for alias, full in MODEL_ALIASES.items():
        if model == full:
            return f"LLM {alias}"
    return f"LLM {model}"


def _main_compare(args, cases, cases_label, skills) -> int:
    kw = {"cases": cases, "skills": skills, "top": args.top, "threshold": args.threshold}
    base = run(use_concepts=False, **kw)
    conc = run(use_concepts=True, **kw)
    casc = run(use_concepts=True, scorer="cascade", model=args.models[0], **kw) if args.cascade else None
    llms = ([(m, run(use_concepts=True, scorer="llm", model=m, **kw)) for m in args.models]
            if args.llm else [])
    if args.json:
        out = {"cases": cases_label, "baseline": base, "concepts": conc}
        if casc:
            out["cascade"] = casc
        if llms:
            out["llm"] = llms[0][1]
            out["llm_models"] = {(m or llm_scorer.DEFAULT_MODEL): rep for m, rep in llms}
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    columns = [("baseline", base), ("+conceptos", conc)]
    if casc:
        columns.append(("cascada" + (f" ({_short(args.models[0])[4:]})" if args.models[0] else ""), casc))
    columns += [(_short(m), rep) for m, rep in llms]
    print(f"\n📊 Skill Router — comparativa de scorers ({base['total']} casos, set: {cases_label})\n")
    _print_table(columns)
    _print_by_kind(columns)
    _print_failures(columns)
    for label, rep in columns[2:]:
        if rep.get("llm_cached") or rep.get("llm_errors"):
            print(f"   [{label}]")
            _print_llm_notes(rep)
    for label, rep in [(lab, r) for lab, r in columns if lab.startswith("LLM")]:
        fails = [d for d in rep["details"] if d["top1"] is not True]
        if fails:
            print(f"   Fallos de {label}:")
            for d in fails:
                extra = f"  (error: {d['error'][:60]})" if d.get("error") else ""
                print(f"     ❌ \"{d['query'][:50]}\" → esperado {_fmt_exp(d['expected'])}, "
                      f"obtuvo {d['got'] or '—'}{extra}")
            print()
    d1 = round(conc['top1_accuracy'] - base['top1_accuracy'], 1)
    print(f"   Mejora top-1 de los conceptos: {'+' if d1 >= 0 else ''}{d1} puntos\n")
    flips = [(b, c) for b, c in zip(base["details"], conc["details"]) if b["top1"] != c["top1"]]
    print("   Casos donde los conceptos cambiaron el resultado:" + ("" if flips else " ninguno"))
    for b, c in flips:
        flip = "✅ arreglado" if float(c["top1"]) > float(b["top1"]) else "⚠️ empeoro"
        print(f"     {flip}: \"{b['query'][:50]}\"")
    print()
    return 0


def _main_report(args, cases, cases_label, skills) -> int:
    scorer = "cascade" if args.cascade else ("llm" if args.llm else "lexical")
    report = run(use_concepts=True, scorer=scorer, model=args.models[0], cases=cases,
                 skills=skills, top=args.top, threshold=args.threshold)
    label = {"cascade": "cascada", "llm": "LLM", "lexical": "con conceptos"}[scorer]
    if args.json:
        print(json.dumps({"cases": cases_label, "scorer": label, **report},
                         ensure_ascii=False, indent=2))
        return 0
    print(f"\n📊 Benchmark Skill Router — {report['total']} casos ({label}, set: {cases_label})")
    print(f"   Top-1: {report['top1_accuracy']}%   Top-3: {report['top3_accuracy']}%\n")
    _print_table([(label, report)])
    _print_by_kind([(label, report)])
    _print_failures([(label, report)])
    if scorer != "lexical":
        _print_llm_notes(report)
    for d in report["details"]:
        mark = "✅" if d["top1"] is True else ("🟡" if d["top3"] else "❌")
        print(f"  {mark} [{d['kind']:8}] \"{d['query'][:42]}\"")
        line = f"       esperado: {_fmt_exp(d['expected'])}  |  obtuvo: {d['got'] or '—'}"
        if d.get("cause"):
            line += f"  |  causa: {d['cause']}"
        if d.get("error"):
            line += f"  |  error: {d['error'][:60]}"
        print(line)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
