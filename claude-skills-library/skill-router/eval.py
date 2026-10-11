#!/usr/bin/env python3
"""
Benchmark del Skill Router: mide si recomienda la skill correcta por contexto,
y compara el baseline (solo palabras clave) contra la capa de conceptos.

Incluye tres tipos de caso:
- directos: el mensaje comparte palabras con la descripción de la skill.
- sinónimos: el mensaje usa otras palabras (aquí es donde los conceptos ayudan).
- negativos: no debería activarse ninguna skill (expected = None).

Métricas: precisión top-1 (la #1 es la correcta) y top-3 (la correcta está entre
las 3). En los negativos, acierto = no recomendar nada.

Uso:
    python3 eval.py                 # tabla con conceptos activados
    python3 eval.py --compare       # baseline vs conceptos (el número clave)
    python3 eval.py --json
"""

from __future__ import annotations

import argparse
import json
import sys

from skill_router import demo_skills, score_skills, classify

# (mensaje, skill esperada o None para negativos, tipo)
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


def eval_one(skills, msg, expected, use_concepts, top, threshold):
    results = score_skills(skills, msg, use_concepts=use_concepts)
    recommended, _ = classify(results, top, threshold)
    rec_names = [r["name"] for r in recommended]
    if expected is None:
        # acierto negativo = no se recomendo nada
        hit1 = hit3 = (len(rec_names) == 0)
    else:
        hit1 = bool(rec_names) and rec_names[0] == expected
        hit3 = expected in rec_names
    return rec_names, hit1, hit3


def run(use_concepts: bool, top: int = 3, threshold: int = 15) -> dict:
    skills = demo_skills()
    total = len(CASES)
    top1 = top3 = 0
    details = []
    for msg, expected, kind in CASES:
        rec_names, hit1, hit3 = eval_one(skills, msg, expected, use_concepts, top, threshold)
        top1 += int(hit1)
        top3 += int(hit3)
        details.append({"query": msg, "expected": expected, "kind": kind,
                        "got": rec_names, "top1": hit1, "top3": hit3})
    return {"total": total,
            "top1_accuracy": round(100 * top1 / total, 1),
            "top3_accuracy": round(100 * top3 / total, 1),
            "details": details}


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Benchmark del Skill Router")
    p.add_argument("--json", action="store_true")
    p.add_argument("--compare", action="store_true", help="baseline vs conceptos")
    args = p.parse_args(argv)

    if args.compare:
        base = run(use_concepts=False)
        conc = run(use_concepts=True)
        if args.json:
            print(json.dumps({"baseline": base, "concepts": conc}, ensure_ascii=False, indent=2))
            return 0
        print(f"\n📊 Skill Router — baseline vs conceptos ({base['total']} casos)\n")
        print(f"   {'':12} {'Top-1':>8} {'Top-3':>8}")
        print(f"   {'baseline':12} {base['top1_accuracy']:>7}% {base['top3_accuracy']:>7}%")
        print(f"   {'+conceptos':12} {conc['top1_accuracy']:>7}% {conc['top3_accuracy']:>7}%")
        d1 = round(conc['top1_accuracy'] - base['top1_accuracy'], 1)
        print(f"\n   Mejora top-1: {'+' if d1 >= 0 else ''}{d1} puntos\n")
        print("   Casos donde los conceptos cambiaron el resultado:")
        for b, c in zip(base["details"], conc["details"]):
            if b["top1"] != c["top1"]:
                flip = "✅ arreglado" if c["top1"] else "⚠️ empeoro"
                print(f"     {flip}: \"{b['query'][:50]}\"")
        print()
        return 0

    report = run(use_concepts=True)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    print(f"\n📊 Benchmark Skill Router — {report['total']} casos (con conceptos)")
    print(f"   Top-1: {report['top1_accuracy']}%   Top-3: {report['top3_accuracy']}%\n")
    for d in report["details"]:
        mark = "✅" if d["top1"] else ("🟡" if d["top3"] else "❌")
        exp = d["expected"] or "(ninguna)"
        print(f"  {mark} [{d['kind']:8}] \"{d['query'][:42]}\"")
        print(f"       esperado: {exp}  |  obtuvo: {d['got'] or '—'}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
