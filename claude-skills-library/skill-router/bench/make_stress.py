#!/usr/bin/env python3
"""
Genera bench/stress.json a partir de bench/heldout.json (determinista).

Cada caso del held-out se repite en 3 variantes con LAS MISMAS etiquetas
(expected y kind); solo cambia el ruido que acompaña a la petición:

  (a) traceback: se pega debajo un traceback o un log.
  (b) contexto:  delante va una frase de contexto neutra y larga.
  (c) pegado:    debajo va un texto pegado dentro de <pasted_content>.

Mide si el router sigue recomendando por la petición del usuario y no por lo
que se pegó o por la palabrería de alrededor. El ruido se elige por posición
(índice del caso), sin azar: ejecutarlo dos veces da el mismo archivo.

No sirve para ajustar el router: hereda la regla de bench/README.md (no se
ajusta nada mirando el held-out, y este set se deriva de él).

    python3 bench/make_stress.py            # escribe bench/stress.json
    python3 eval.py --compare --cases bench/stress.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "heldout.json"
DST = HERE / "stress.json"

TRACEBACKS = (
    'Traceback (most recent call last):\n'
    '  File "/srv/app/main.py", line 42, in <module>\n'
    '    run()\n'
    '  File "/srv/app/main.py", line 17, in run\n'
    '    total = items[3]\n'
    'IndexError: list index out of range',
    '2026-03-02 10:14:07,881 INFO  worker started pid=4412\n'
    '2026-03-02 10:14:08,002 WARN  cache miss key=session:91\n'
    '2026-03-02 10:14:09,517 ERROR request failed status=500 path=/api/v1/orders',
    'TypeError: Cannot read properties of undefined (reading \'map\')\n'
    '    at render (webpack:///src/List.jsx:12:18)\n'
    '    at mountComponent (react-dom.development.js:1450:7)',
)

CONTEXTS = (
    "Te escribo desde el móvil mientras espero el autobús, así que perdona si me explico "
    "regular; llevo toda la semana liado con mil cosas y por fin tengo un rato tranquilo.",
    "Antes de nada, gracias por la paciencia de siempre; hoy ha sido un día largo, hace un "
    "frío tremendo y acabo de llegar a casa después de pasar la tarde con mi familia.",
    "Bueno, a ver cómo lo cuento: es lunes, el café se me ha quedado frío y tengo la mesa "
    "llena de papeles, pero quiero quitarme esto de encima cuanto antes.",
)

PASTED = (
    "Acta de la reunión de vecinos del 3 de marzo. Asistentes: 14. Se aprueba cambiar "
    "la iluminación del portal y revisar el presupuesto del ascensor. Próxima reunión "
    "en abril. Ruegos y preguntas: ninguno.",
    "Lista de la compra: leche, pan, huevos, tomates, aceite de oliva, arroz, café, "
    "papel de cocina, detergente. Recordar pasar por la farmacia antes de las ocho.",
    "Horario del gimnasio: lunes a viernes de 7:00 a 22:00, sábados de 9:00 a 14:00. "
    "Las clases de spinning se reservan en recepción con un día de antelación.",
)


def variants(i: int, query: str) -> list[tuple[str, str]]:
    tb = TRACEBACKS[i % len(TRACEBACKS)]
    ctx = CONTEXTS[i % len(CONTEXTS)]
    pasted = PASTED[i % len(PASTED)]
    return [
        ("traceback", f"{query}\n\n{tb}"),
        ("contexto", f"{ctx} {query}"),
        ("pegado", f"{query}\n<pasted_content>\n{pasted}\n</pasted_content>"),
    ]


def build(source: dict) -> dict:
    cases = []
    for i, c in enumerate(source["cases"]):
        for variant, query in variants(i, c["query"]):
            cases.append({"query": query, "expected": list(c["expected"]), "kind": c["kind"],
                          "variant": variant, "source": i + 1})
    return {"version": 1,
            "derived_from": "heldout.json",
            "note": "Generado por bench/make_stress.py desde heldout.json; no editar a mano.",
            "cases": cases}


def main(argv: list[str]) -> int:
    src = Path(argv[0]) if argv else SRC
    dst = Path(argv[1]) if len(argv) > 1 else DST
    data = build(json.loads(src.read_text(encoding="utf-8")))
    dst.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"✅ {len(data['cases'])} casos de estrés escritos en {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
