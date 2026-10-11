#!/usr/bin/env python3
"""
Lanzador del Skill Router para la skill `/skill-router`.

Localiza `skill_router.py` y lo ejecuta con los mismos argumentos, como si se
llamara directamente (`python3 skill_router.py ...`). Así la skill funciona
tanto si su carpeta está enlazada (ln -s) en ~/.claude/skills como si se copió:

1. Si existe la variable SKILL_ROUTER_HOME, usa esa carpeta.
2. Si no, sube desde la ruta real de este archivo (resolviendo enlaces):
   integration/skill-router/run_router.py -> skill-router/.
"""

from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path


def find_router() -> Path | None:
    candidates = []
    env = os.environ.get("SKILL_ROUTER_HOME", "").strip()
    if env:
        candidates.append(Path(env).expanduser())
    candidates.append(Path(__file__).resolve().parents[2])
    for c in candidates:
        if (c / "skill_router.py").is_file():
            return c
    return None


def main() -> int:
    router = find_router()
    if router is None:
        print("No encuentro skill_router.py. Enlaza la carpeta de la skill con `ln -s` "
              "(no la copies) o define SKILL_ROUTER_HOME=/ruta/a/skill-router.",
              file=sys.stderr)
        return 1
    sys.path.insert(0, str(router))  # para `from concepts import ...`
    sys.argv = [str(router / "skill_router.py"), *sys.argv[1:]]
    runpy.run_path(str(router / "skill_router.py"), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
