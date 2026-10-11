#!/usr/bin/env python3
"""
Benchmark del Skill Router: mide si recomienda la skill correcta por contexto.

Usa casos etiquetados (mensaje -> skill esperada) sobre el set de demo y reporta
precision top-1 (la #1 recomendada es la correcta) y top-3 (la correcta aparece
entre las 3 recomendadas). Pensado para mostrar un numero claro en la demo.

Uso:
    python3 eval.py
    python3 eval.py --json
"""

from __future__ import annotations

import argparse
import json
import sys

from skill_router import demo_skills, score_skills, classify

# (mensaje del usuario, skill correcta esperada)
CASES = [
    ("quiero preparar mi examen de calculo y repasar", "estudio-a-medida"),
    ("hazme flashcards para estudiar biologia", "estudio-a-medida"),
    ("necesito un simulacro con mi rubrica", "estudio-a-medida"),
    ("un guion para un reel viral de tiktok", "guiones-video"),
    ("escribeme el gancho y el cta de un short", "guiones-video"),
    ("campana de marketing y copywriting para redes", "marca-marketing"),
    ("posts para instagram sobre mi marca", "marca-marketing"),
    ("revisa este pull request por bugs de seguridad", "code-review"),
    ("busca inyecciones y errores en el diff", "code-review"),
    ("tengo un stack trace, ayudame a diagnosticar el error", "debug"),
    ("investiga el mercado y compara tendencias", "deep-research"),
    ("necesito un informe con multiples fuentes", "deep-research"),
]


def run(top: int = 3, threshold: int = 15) -> dict:
    skills = demo_skills()
    total = len(CASES)
    top1 = 0
    top3 = 0
    details = []
    for msg, expected in CASES:
        results = score_skills(skills, msg)
        recommended, _ = classify(results, top, threshold)
        rec_names = [r["name"] for r in recommended]
        is_top1 = bool(rec_names) and rec_names[0] == expected
        is_top3 = expected in rec_names
        top1 += int(is_top1)
        top3 += int(is_top3)
        details.append({
            "query": msg,
            "expected": expected,
            "got": rec_names,
            "top1": is_top1,
            "top3": is_top3,
        })
    return {
        "total": total,
        "top1_accuracy": round(100 * top1 / total, 1),
        "top3_accuracy": round(100 * top3 / total, 1),
        "details": details,
    }


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Benchmark del Skill Router")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    report = run()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    print(f"\n📊 Benchmark Skill Router — {report['total']} casos")
    print(f"   Top-1: {report['top1_accuracy']}%   Top-3: {report['top3_accuracy']}%\n")
    for d in report["details"]:
        mark = "✅" if d["top1"] else ("🟡" if d["top3"] else "❌")
        print(f"  {mark} \"{d['query'][:45]}\"")
        print(f"       esperado: {d['expected']}  |  obtuvo: {d['got'] or '—'}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
