#!/usr/bin/env python3
"""
Hook `UserPromptSubmit` del Skill Router para Claude Code.

En cada mensaje que envías, Claude Code ejecuta este script ANTES de que Claude lo
procese. El script puntúa tus skills instaladas contra el mensaje y, si alguna
encaja, le pasa a Claude una nota breve: qué skills usar en este turno, por qué, y
cuáles son ruido. Si ninguna encaja, no imprime nada y Claude no ve nada.

Contrato (verificado en https://code.claude.com/docs/en/hooks):
- Entrada: un objeto JSON por stdin con los campos comunes (`session_id`,
  `transcript_path`, `cwd`, `permission_mode`, `hook_event_name`, ...) más
  `prompt`, el texto enviado.
- Salida: exit 0 y, solo si hay recomendación, UN objeto JSON en stdout:
      {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                              "additionalContext": "..."}}
  Claude Code inyecta `additionalContext` como recordatorio de sistema junto al
  prompt. Tope documentado: 10.000 caracteres (aquí se limita a ~2.000).
- Nunca bloquea: no usa exit 2 ni `"decision": "block"`. Cualquier error se
  registra en stderr (Claude no ve stderr cuando el hook sale con 0) y sale con 0.

Variables de entorno (todas opcionales):
  SKILL_ROUTER_HOME           carpeta del router (por defecto, dos niveles arriba).
  SKILL_ROUTER_SKILLS_DIR     carpeta(s) de skills, separadas por ':' (';' en Windows).
  SKILL_ROUTER_CATALOG        usar este skills.json en vez de las skills instaladas.
  SKILL_ROUTER_DEMO=1         usar las 6 skills de demo (para probar).
  SKILL_ROUTER_HOOK_LLM=1     usar Claude como scorer (más lento; cae al léxico si falla).
  SKILL_ROUTER_HOOK_TOP       máximo de skills recomendadas (def. 3).
  SKILL_ROUTER_HOOK_THRESHOLD puntuación mínima 0-100 para recomendar (def. 15).
  SKILL_ROUTER_HOOK_OFF=1     desactiva el hook sin tocar settings.json.
  SKILL_ROUTER_HOOK_DEBUG=1   escribe diagnóstico y tiempos en stderr.

Prueba a mano:
  echo '{"hook_event_name":"UserPromptSubmit","prompt":"repasar para el examen"}' \\
      | SKILL_ROUTER_DEMO=1 python3 route_prompt.py
  SKILL_ROUTER_DEMO=1 python3 route_prompt.py --prompt "guion para un reel" --texto
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

HOOK_EVENT = "UserPromptSubmit"
# Evita la recursión cuando el scorer LLM llama a `claude -p`: ese Claude hijo
# ejecutaría este mismo hook, que volvería a llamar a `claude -p`, y así sin fin.
GUARD_ENV = "SKILL_ROUTER_HOOK_ACTIVE"
# La propia skill del router no se recomienda a sí misma en cada turno.
SELF_NAMES = {"skill-router"}

MAX_PROMPT_CHARS = 4000     # lo que se puntúa del mensaje (pegados largos diluyen)
MAX_CONTEXT_CHARS = 2000    # muy por debajo del tope de 10.000 de Claude Code
MAX_NOISE_LISTED = 8        # nombres de "ruido" que se listan antes de resumir
MAX_REASON_CHARS = 160
DEFAULT_LLM_TIMEOUT_S = 20  # el hook entero tiene 30 s por defecto en este evento

_TRUE = {"1", "true", "yes", "si", "sí", "on"}
# Líneas que Claude Code añade alrededor del texto pegado (documentado).
_PASTED_MARKER = re.compile(r"^[ \t]*</?pasted_content\b[^>\n]*>[ \t]*$", re.MULTILINE)
# "/comando args": el usuario ya eligió skill/comando; no se sugiere nada.
_SLASH_COMMAND = re.compile(r"^/[\w:.\-]+(\s|$)")
_WS = re.compile(r"\s+")


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUE


def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(os.environ.get(name, default))))
    except (TypeError, ValueError):
        return default


def _debug(msg: str) -> None:
    if _flag("SKILL_ROUTER_HOOK_DEBUG"):
        print(f"[skill-router hook] {msg}", file=sys.stderr)


def _inline(text: object, limit: int) -> str:
    """Una sola línea, sin saltos ni espacios de más, recortada a `limit`."""
    s = _WS.sub(" ", str(text)).strip()
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


def _short_path(p: Path) -> str:
    s, home = str(p), str(Path.home())
    if home not in ("", "/") and (s == home or s.startswith(home + os.sep)):
        return "~" + s[len(home):]
    return s


def router_dir() -> Path:
    env = os.environ.get("SKILL_ROUTER_HOME", "").strip()
    if env:
        return Path(env).expanduser()
    # integration/hooks/route_prompt.py -> skill-router/
    return Path(__file__).resolve().parents[2]


# --- Entrada -----------------------------------------------------------------
def parse_args(argv: list[str]) -> tuple[str | None, bool]:
    """Solo para pruebas a mano. Sin argparse: un error de argumentos no debe
    terminar con un código distinto de 0."""
    prompt, as_text = None, False
    i = 0
    while i < len(argv):
        if argv[i] == "--prompt" and i + 1 < len(argv):
            prompt = argv[i + 1]
            i += 2
            continue
        if argv[i] == "--texto":
            as_text = True
        i += 1
    return prompt, as_text


def read_event() -> dict | None:
    """Lee el JSON del evento desde stdin. None si no hay nada utilizable."""
    stdin = sys.stdin
    if stdin is None or stdin.isatty():
        return None
    raw = stdin.buffer.read() if hasattr(stdin, "buffer") else stdin.read()
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    raw = raw.strip()
    if not raw:
        return None
    data = json.loads(raw)  # JSON malformado -> excepción -> main la captura
    return data if isinstance(data, dict) else None


def clean_prompt(prompt: str) -> str:
    return _PASTED_MARKER.sub("", prompt).strip()[:MAX_PROMPT_CHARS]


# --- Skills ------------------------------------------------------------------
def load_skills(sr, event: dict) -> tuple[list, str, bool]:
    """Devuelve (skills, descripción de la fuente, es_catalogo)."""
    if _flag("SKILL_ROUTER_DEMO"):
        return sr.demo_skills(), "skills de demo", False

    catalog_env = os.environ.get("SKILL_ROUTER_CATALOG", "").strip()
    if catalog_env:
        return sr.load_from_catalog(Path(catalog_env).expanduser()), "catálogo skills.json", True

    explicit = os.environ.get("SKILL_ROUTER_SKILLS_DIR", "").strip()
    if explicit:
        dirs = [Path(p).expanduser() for p in explicit.split(os.pathsep) if p.strip()]
    else:
        # Personales + las del proyecto (Claude Code carga ambas).
        dirs = [Path.home() / ".claude" / "skills"]
        for root in (os.environ.get("CLAUDE_PROJECT_DIR"), event.get("cwd")):
            if isinstance(root, str) and root.strip():
                dirs.append(Path(root) / ".claude" / "skills")

    skills, names, seen_dirs, used = [], set(), set(), []
    for d in dirs:
        try:
            key = d.resolve()
        except OSError:
            continue
        if key in seen_dirs:
            continue
        seen_dirs.add(key)
        added = 0
        for s in sr.load_from_dir(d):
            if s.name not in names:  # mismo nombre en dos sitios: cuenta una vez
                names.add(s.name)
                skills.append(s)
                added += 1
        if added:
            used.append(_short_path(d))
    if skills:
        return skills, "skills instaladas en " + ", ".join(used), False

    # Sin skills instaladas: el catálogo vecino (claude-skills-library/skills.json).
    catalog = router_dir().parent / "skills.json"
    if catalog.is_file():
        return sr.load_from_catalog(catalog), "catálogo skills.json", True
    return [], "", False


# --- Puntuación --------------------------------------------------------------
def score(sr, skills: list, prompt: str) -> tuple[list[dict], str]:
    if _flag("SKILL_ROUTER_HOOK_LLM"):
        try:
            os.environ[GUARD_ENV] = "1"  # lo hereda el `claude -p` hijo
            os.environ.setdefault("SKILL_ROUTER_LLM_TIMEOUT", str(DEFAULT_LLM_TIMEOUT_S))
            import llm_scorer  # import tardío: solo cuesta si se pide
            return llm_scorer.llm_score(skills, prompt), "LLM"
        except Exception as e:  # LLMUnavailable, timeout, JSON raro...
            print(f"[skill-router hook] scorer LLM no disponible, uso el léxico: {e}",
                  file=sys.stderr)
    return sr.score_skills(skills, prompt, use_concepts=True), "léxico+conceptos"


def _reason(matched: list) -> str:
    parts = []
    for m in matched or []:
        m = str(m)
        parts.append("~" + m.split(":", 1)[1] if m.startswith("concept:") else m)
    return _inline(", ".join(parts), MAX_REASON_CHARS) if parts else "coincidencia general"


def build_context(recommended: list[dict], noise: list[dict], scorer: str,
                  source: str, n_skills: int, is_catalog: bool) -> str:
    lines = [
        f"Skill Router: sugerencia automática para este turno "
        f"(scorer {scorer}; fuente: {source}, {n_skills} skill{'' if n_skills == 1 else 's'}).",
    ]
    if is_catalog:
        lines.append("Ojo: son colecciones del catálogo skills.json; puede que no estén instaladas.")
    lines.append("Skills que encajan con este mensaje:")
    for r in recommended:
        scen = f" [{_inline(r.get('scenario', ''), 60)}]" if r.get("scenario") else ""
        lines.append(f"- {_inline(r['name'], 80)}{scen}: relevancia {int(r['score'])}/100; "
                     f"motivo: {_reason(r.get('matched'))}")
    if noise:
        shown = ", ".join(_inline(r["name"], 60) for r in noise[:MAX_NOISE_LISTED])
        extra = f" (y {len(noise) - MAX_NOISE_LISTED} más)" if len(noise) > MAX_NOISE_LISTED else ""
        lines.append(f"Ruido para este turno (no hace falta cargarlas): {shown}{extra}.")
    basis = "" if scorer == "LLM" else " basada en palabras clave"
    lines.append(f"Es una pista orientativa{basis}: si no encaja con lo que "
                 "pide el usuario, ignórala. No hace falta mencionarla.")
    text = "\n".join(lines)
    return text if len(text) <= MAX_CONTEXT_CHARS else text[: MAX_CONTEXT_CHARS - 1] + "…"


def route(event: dict, prompt: str) -> str | None:
    """Devuelve el texto de contexto, o None si no hay nada que recomendar."""
    t0 = time.perf_counter()
    rdir = router_dir()
    if not (rdir / "skill_router.py").is_file():
        print(f"[skill-router hook] no encuentro skill_router.py en {rdir} "
              f"(define SKILL_ROUTER_HOME)", file=sys.stderr)
        return None
    if str(rdir) not in sys.path:
        sys.path.insert(0, str(rdir))
    import skill_router as sr

    skills, source, is_catalog = load_skills(sr, event)
    skills = [s for s in skills if s.name not in SELF_NAMES]
    t1 = time.perf_counter()
    if not skills:
        _debug("no hay skills; no recomiendo nada")
        return None

    results, scorer = score(sr, skills, prompt)
    top = _env_int("SKILL_ROUTER_HOOK_TOP", 3, 1, 10)
    threshold = _env_int("SKILL_ROUTER_HOOK_THRESHOLD", 15, 0, 100)
    recommended, noise = sr.classify(results, top, threshold)
    t2 = time.perf_counter()
    _debug(f"{len(skills)} skills ({source}); carga {1000 * (t1 - t0):.1f} ms, "
           f"puntuación {1000 * (t2 - t1):.1f} ms; recomendadas: "
           f"{[r['name'] for r in recommended]}")
    if not recommended:
        return None
    return build_context(recommended, noise, scorer, source, len(skills), is_catalog)


# --- Salida ------------------------------------------------------------------
def emit(context: str, as_text: bool) -> None:
    if as_text:
        out = context + "\n"
    else:
        # ensure_ascii: salida ASCII pura, válida aunque la consola no sea UTF-8.
        out = json.dumps({"hookSpecificOutput": {
            "hookEventName": HOOK_EVENT,
            "additionalContext": context,
        }}, ensure_ascii=True)
    sys.stdout.write(out)
    sys.stdout.flush()


def main(argv: list[str]) -> int:
    t0 = time.perf_counter()
    try:
        if os.environ.get(GUARD_ENV) or _flag("SKILL_ROUTER_HOOK_OFF"):
            return 0
        manual_prompt, as_text = parse_args(argv)
        if manual_prompt is not None:
            event: dict | None = {"hook_event_name": HOOK_EVENT, "prompt": manual_prompt}
        else:
            event = read_event()
        if not event:
            return 0
        if event.get("hook_event_name", HOOK_EVENT) != HOOK_EVENT:
            return 0
        prompt = event.get("prompt")
        if not isinstance(prompt, str):
            return 0
        prompt = clean_prompt(prompt)
        if not prompt or _SLASH_COMMAND.match(prompt):
            return 0
        context = route(event, prompt)
        if context:
            emit(context, as_text)
    except BaseException as e:  # nunca bloquear el prompt, pase lo que pase
        try:
            print(f"[skill-router hook] error ignorado: {type(e).__name__}: {e}", file=sys.stderr)
        except BaseException:
            pass
    finally:
        _debug(f"total {1000 * (time.perf_counter() - t0):.1f} ms")
    return 0


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except BaseException:
        pass
    sys.exit(0)  # siempre 0: el hook jamás bloquea el prompt
