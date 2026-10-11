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

Métricas: precisión top-1 (la #1 recomendada está en expected) y top-3 (alguna de
expected está entre las recomendadas, máx. 3). En los negativos, acierto = no
recomendar nada. Se desglosa además por tipo de caso.

Uso:
    python3 eval.py                         # tabla con conceptos activados
    python3 eval.py --compare               # baseline vs conceptos (el número clave)
    python3 eval.py --compare --llm         # + scorer LLM (1 llamada a Claude por caso)
    python3 eval.py --llm                   # tabla detallada con el scorer LLM
    python3 eval.py --cases bench/x.json    # usa un set externo (combinable)
    python3 eval.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import llm_scorer
from llm_scorer import LLMUnavailable
from skill_router import demo_skills, score_skills, classify

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
    if "version" in data and data["version"] != 1:
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


def eval_one(skills, msg, expected, use_concepts, top, threshold, use_llm=False):
    """Evalúa un caso. expected: lista de skills aceptables ([] o None = negativo)."""
    exp = _as_list(expected)
    if use_llm:
        results = llm_scorer.llm_score(skills, msg)  # vía módulo: se puede simular en tests
    else:
        results = score_skills(skills, msg, use_concepts=use_concepts)
    recommended, _ = classify(results, top, threshold)
    rec_names = [r["name"] for r in recommended]
    if not exp:
        # acierto negativo = no se recomendo nada
        hit1 = hit3 = (len(rec_names) == 0)
    else:
        hit1 = bool(rec_names) and rec_names[0] in exp
        hit3 = any(n in exp for n in rec_names)
    return rec_names, hit1, hit3


def _pct(num: int, den: int) -> float:
    return round(100 * num / den, 1) if den else 0.0


def run(use_concepts: bool, top: int = 3, threshold: int = 15, use_llm: bool = False,
        cases: list[dict] | None = None, skills=None) -> dict:
    """Corre el benchmark. Con use_llm, un caso cuya llamada falla cuenta como
    fallo (y queda marcado con "error"); 3 fallos seguidos abortan con
    LLMUnavailable para no gastar tiempo en un backend caído."""
    cases = default_cases() if cases is None else cases
    skills = demo_skills() if skills is None else skills
    total = len(cases)
    top1 = top3 = errors = streak = cached = 0
    details = []
    by_kind: dict[str, dict] = {}
    for case in cases:
        msg, expected, kind = case["query"], case["expected"], case["kind"]
        err = None
        try:
            rec_names, hit1, hit3 = eval_one(skills, msg, expected, use_concepts,
                                             top, threshold, use_llm)
            streak = 0
            if use_llm:
                cached += int(bool(llm_scorer.last_call_info.get("cached")))
        except LLMUnavailable as e:  # solo el scorer LLM lanza esto
            err, rec_names, hit1, hit3 = str(e), [], False, False
            errors += 1
            streak += 1
            if streak >= MAX_CONSECUTIVE_LLM_ERRORS:
                raise LLMUnavailable(f"abortado tras {streak} fallos seguidos del LLM; último: {e}") from e
        top1 += int(hit1)
        top3 += int(hit3)
        k = by_kind.setdefault(kind, {"total": 0, "top1": 0, "top3": 0})
        k["total"] += 1
        k["top1"] += int(hit1)
        k["top3"] += int(hit3)
        d = {"query": msg, "expected": _as_list(expected), "kind": kind,
             "got": rec_names, "top1": hit1, "top3": hit3}
        if err:
            d["error"] = err
        details.append(d)
    for k in by_kind.values():
        k["top1_accuracy"] = _pct(k["top1"], k["total"])
        k["top3_accuracy"] = _pct(k["top3"], k["total"])
    # orden canónico de KINDS; un tipo desconocido (casos pasados a mano) va al final
    ordered = {kind: by_kind[kind] for kind in KINDS if kind in by_kind}
    ordered.update({kind: v for kind, v in by_kind.items() if kind not in ordered})
    out = {"total": total,
           "top1_accuracy": _pct(top1, total),
           "top3_accuracy": _pct(top3, total),
           "by_kind": ordered,
           "details": details}
    if use_llm:
        out["llm_errors"] = errors
        out["llm_cached"] = cached
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


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Benchmark del Skill Router")
    p.add_argument("--json", action="store_true")
    p.add_argument("--compare", action="store_true", help="baseline vs conceptos")
    p.add_argument("--llm", action="store_true",
                   help="con --compare añade el scorer LLM; sin él, usa el LLM para la tabla "
                        "(1 llamada a Claude por caso)")
    p.add_argument("--cases", type=Path,
                   help="archivo JSON con un set de casos externo (por defecto, los 19 inline)")
    args = p.parse_args(argv)

    skills = demo_skills()
    if args.cases:
        try:
            cases, warnings = load_cases(args.cases)
        except CasesError as e:
            print(f"❌ {e}", file=sys.stderr)
            return 2
        for w in warnings:
            print(f"⚠️  {w}", file=sys.stderr)
        cases_label = str(args.cases)
    else:
        cases, cases_label = default_cases(), "inline"
    missing = unknown_skills(cases, skills)
    if missing:
        known = ", ".join(sorted(s.name for s in skills))
        print(f"❌ Skills esperadas que no existen en el set de demo: {', '.join(missing)}\n"
              f"   Disponibles: {known}", file=sys.stderr)
        return 2

    if args.llm and llm_scorer.available_backend() is None:
        print("❌ --llm: no hay ANTHROPIC_API_KEY+SDK ni CLI `claude` disponible.", file=sys.stderr)
        return 2

    try:
        if args.compare:
            return _main_compare(args, cases, cases_label, skills)
        return _main_report(args, cases, cases_label, skills)
    except LLMUnavailable as e:
        print(f"❌ El benchmark con LLM se detuvo: {e}", file=sys.stderr)
        return 2


def _main_compare(args, cases, cases_label, skills) -> int:
    base = run(use_concepts=False, cases=cases, skills=skills)
    conc = run(use_concepts=True, cases=cases, skills=skills)
    llm = run(use_concepts=True, use_llm=True, cases=cases, skills=skills) if args.llm else None
    if args.json:
        out = {"cases": cases_label, "baseline": base, "concepts": conc}
        if llm:
            out["llm"] = llm
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    print(f"\n📊 Skill Router — comparativa de scorers ({base['total']} casos, set: {cases_label})\n")
    print(f"   {'':12} {'Top-1':>8} {'Top-3':>8}")
    print(f"   {'baseline':12} {base['top1_accuracy']:>7}% {base['top3_accuracy']:>7}%")
    print(f"   {'+conceptos':12} {conc['top1_accuracy']:>7}% {conc['top3_accuracy']:>7}%")
    if llm:
        print(f"   {'LLM':12} {llm['top1_accuracy']:>7}% {llm['top3_accuracy']:>7}%")
    print()
    columns = [("baseline", base), ("+conceptos", conc)] + ([("LLM", llm)] if llm else [])
    _print_by_kind(columns)
    if llm:
        _print_llm_notes(llm)
        fails = [d for d in llm["details"] if not d["top1"]]
        if fails:
            print("   Fallos del LLM:")
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
        flip = "✅ arreglado" if c["top1"] else "⚠️ empeoro"
        print(f"     {flip}: \"{b['query'][:50]}\"")
    print()
    return 0


def _main_report(args, cases, cases_label, skills) -> int:
    report = run(use_concepts=True, use_llm=args.llm, cases=cases, skills=skills)
    scorer = "LLM" if args.llm else "con conceptos"
    if args.json:
        print(json.dumps({"cases": cases_label, "scorer": scorer, **report},
                         ensure_ascii=False, indent=2))
        return 0
    print(f"\n📊 Benchmark Skill Router — {report['total']} casos ({scorer}, set: {cases_label})")
    print(f"   Top-1: {report['top1_accuracy']}%   Top-3: {report['top3_accuracy']}%\n")
    _print_by_kind([(scorer, report)])
    if args.llm:
        _print_llm_notes(report)
    for d in report["details"]:
        mark = "✅" if d["top1"] else ("🟡" if d["top3"] else "❌")
        print(f"  {mark} [{d['kind']:8}] \"{d['query'][:42]}\"")
        line = f"       esperado: {_fmt_exp(d['expected'])}  |  obtuvo: {d['got'] or '—'}"
        if d.get("error"):
            line += f"  |  error: {d['error'][:60]}"
        print(line)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
