#!/usr/bin/env python3
"""
Lanzador del Skill Router para la skill `/skill-router`.

Localiza `skill_router.py` y lo ejecuta con los mismos argumentos, como si se
llamara directamente (`python3 skill_router.py ...`). Así la skill funciona
tanto si su carpeta está enlazada (ln -s) en ~/.claude/skills como si se copió:

1. Si existe la variable SKILL_ROUTER_HOME, usa esa carpeta.
2. Si no, sube desde la ruta real de este archivo (resolviendo enlaces):
   integration/skill-router/run_router.py -> skill-router/.

Sin --demo, --skills-dir ni --catalog, descubre las skills igual que el hook
(integration/hooks/route_prompt.py: discover_skills): las personales
(~/.claude/skills) más las del proyecto, desde CLAUDE_PROJECT_DIR y el directorio
actual subiendo hasta la raíz git, sin contarse a sí misma. El catálogo
skills.json solo se usa si no queda ninguna. Así la skill y el hook responden lo
mismo para el mismo mensaje.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

_SOURCE_FLAGS = ("--demo", "--skills-dir", "--catalog")


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


def _load_hook(router: Path):
    hook = router / "integration" / "hooks" / "route_prompt.py"
    if not hook.is_file():
        return None
    spec = importlib.util.spec_from_file_location("skill_router_hook", hook)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    router = find_router()
    if router is None:
        print("No encuentro skill_router.py. Enlaza la carpeta de la skill con `ln -s` "
              "(no la copies) o define SKILL_ROUTER_HOME=/ruta/a/skill-router.",
              file=sys.stderr)
        return 1
    sys.path.insert(0, str(router))  # para `from concepts import ...`
    import skill_router as sr

    argv = sys.argv[1:]
    explicit = any(a == f or a.startswith(f + "=") for a in argv for f in _SOURCE_FLAGS)
    hook = None if explicit else _load_hook(router)
    if hook is not None:
        os.environ["SKILL_ROUTER_HOME"] = str(router)  # el catálogo vecino del hook
        skills, source, _ = hook.discover_skills(
            sr, (os.environ.get("CLAUDE_PROJECT_DIR"), os.getcwd()))
        # Fuente por defecto del CLI = la misma que usa el hook. Si no hay
        # ninguna, el CLI dice "No encontre skills" y sale con 1.
        sr._default_skills = lambda: (skills, source)
    return sr.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
