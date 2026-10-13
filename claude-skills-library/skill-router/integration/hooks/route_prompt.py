#!/usr/bin/env python3
"""
Hook `UserPromptSubmit` del Skill Router para Claude Code.

En cada mensaje que envías, Claude Code ejecuta este script ANTES de que Claude lo
procese. El script puntúa tus skills instaladas contra el mensaje y, solo si una
encaja con claridad, le pasa a Claude una nota de una línea: qué skill usar y por
qué. Ante la duda no imprime nada: es mejor no inyectar nada que inyectar una
skill equivocada (y cada carácter inyectado gasta tokens en cada turno).

Contrato (verificado en https://code.claude.com/docs/en/hooks):
- Entrada: un objeto JSON por stdin con los campos comunes (`session_id`,
  `transcript_path`, `cwd`, `permission_mode`, `hook_event_name`, ...) más
  `prompt`, el texto enviado.
- Salida: exit 0 y, solo si hay recomendación, UN objeto JSON en stdout:
      {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                              "additionalContext": "..."}}
  Claude Code inyecta `additionalContext` junto al prompt. Tope documentado:
  10.000 caracteres (aquí, una línea de ~100-200).
- Nunca bloquea: no usa exit 2 ni `"decision": "block"`. Cualquier error se
  registra en stderr (Claude no ve stderr cuando el hook sale con 0) y sale con 0.

Variables de entorno (todas opcionales):
  SKILL_ROUTER_HOME            carpeta del router (por defecto, dos niveles arriba).
  SKILL_ROUTER_SKILLS_DIR      carpeta(s) de skills, separadas por ':' (';' en Windows).
  SKILL_ROUTER_CATALOG         usar este skills.json en vez de las skills instaladas.
  SKILL_ROUTER_DEMO=1          usar las 6 skills de demo (para probar).
  SKILL_ROUTER_HOOK_MODE       lexical (def.) | cascade | llm. cascade y llm gastan
                               cuota del plan: cascade solo cuando el léxico duda.
  SKILL_ROUTER_HOOK_LLM=1      compatibilidad: equivale a SKILL_ROUTER_HOOK_MODE=llm.
  SKILL_ROUTER_HOOK_TOP        máximo de skills en la nota (def. 2).
  SKILL_ROUTER_HOOK_THRESHOLD  puntuación mínima 0-100 (def. 20; el CLI usa 15).
  SKILL_ROUTER_HOOK_MIN_MATCHED coincidencias distintas mínimas en léxico (def. 2).
  SKILL_ROUTER_HOOK_MARGIN     ventaja mínima del #1 sobre el #2 en léxico (def. 10).
  SKILL_ROUTER_HOOK_OFF=1      desactiva el hook sin tocar settings.json.
  SKILL_ROUTER_HOOK_DEBUG=1    escribe diagnóstico y tiempos en stderr.
  SKILL_ROUTER_CHILD=1         lo pone llm_scorer en el `claude -p` hijo: el hook
                               sale en silencio (evita la recursión).

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
# Evitan la recursión cuando el scorer LLM llama a `claude -p`: ese Claude hijo
# ejecutaría este mismo hook, que volvería a llamar a `claude -p`, y así sin fin.
# llm_scorer pone SKILL_ROUTER_CHILD=1 en el hijo; SKILL_ROUTER_HOOK_ACTIVE es la
# guardia antigua, que se sigue respetando y heredando.
CHILD_ENV = "SKILL_ROUTER_CHILD"
GUARD_ENV = "SKILL_ROUTER_HOOK_ACTIVE"
# La propia skill del router no se recomienda a sí misma.
SELF_NAMES = {"skill-router"}

MODES = ("lexical", "cascade", "llm")
MAX_PROMPT_CHARS = 4000     # lo que se puntúa del mensaje
MAX_CONTEXT_CHARS = 400     # la nota es una línea; esto es solo un tope de seguridad
MAX_REASON_CHARS = 50
MAX_REASON_TERMS = 3
DEFAULT_TOP = 2
# Política de precisión del modo léxico (medida con skills reales y mensajes
# propios; ver integration/README.md, "Precisión del hook").
DEFAULT_THRESHOLD = 20
DEFAULT_MIN_MATCHED = 2
DEFAULT_MARGIN = 10
DEFAULT_LLM_TIMEOUT_S = 20  # el hook entero tiene 30 s por defecto en este evento

_TRUE = {"1", "true", "yes", "si", "sí", "on"}
# Líneas que Claude Code añade alrededor del texto pegado (documentado).
_PASTED_MARKER = re.compile(r"^[ \t]*</?pasted_content\b[^>\n]*>[ \t]*$", re.MULTILINE)
# Claude Code cierra el pegado con `</pasted_content id="…">`; normalize_query
# solo reconoce `</pasted_content>` y, sin él, borraría hasta el final (también
# lo tecleado después). Se reescribe el cierre antes de normalizar.
_PASTED_CLOSE = re.compile(r"</pasted_content\b[^>\n]*>")
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


def import_router():
    """Importa skill_router desde router_dir(). None si no está."""
    rdir = router_dir()
    if not (rdir / "skill_router.py").is_file():
        print(f"[skill-router hook] no encuentro skill_router.py en {rdir} "
              f"(define SKILL_ROUTER_HOME)", file=sys.stderr)
        return None
    if str(rdir) not in sys.path:
        sys.path.insert(0, str(rdir))
    import skill_router as sr
    return sr


def hook_mode() -> str:
    """lexical | cascade | llm. SKILL_ROUTER_HOOK_MODE manda; si no está,
    SKILL_ROUTER_HOOK_LLM=1 (compatibilidad) equivale a llm."""
    mode = os.environ.get("SKILL_ROUTER_HOOK_MODE", "").strip().lower()
    if mode in MODES:
        return mode
    if mode:
        _debug(f"SKILL_ROUTER_HOOK_MODE={mode!r} no válido; uso lexical")
        return "lexical"
    return "llm" if _flag("SKILL_ROUTER_HOOK_LLM") else "lexical"


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


def clip(text: str, limit: int = MAX_PROMPT_CHARS) -> str:
    """Recorta a `limit` conservando el principio Y el final: la petición suele
    ir al final de un pegado largo."""
    if len(text) <= limit:
        return text
    head = limit // 2
    return text[:head] + "\n" + text[-(limit - head - 1):]


def clean_prompt(prompt: str) -> str:
    """Todo el mensaje (tecleado + pegado) sin las marcas de pegado, recortado."""
    return clip(_PASTED_MARKER.sub("", prompt).strip())


def split_prompt(sr, prompt: str) -> tuple[str, str]:
    """(tecleado, completo). `tecleado` sale de normalize_query sobre el mensaje
    ENTERO, antes de recortar: así un log largo pegado no empuja fuera la petición.
    Si todo era pegado, normalize_query devuelve el original y ambos coinciden."""
    typed = clip(sr.normalize_query(_PASTED_CLOSE.sub("</pasted_content>", prompt)).strip())
    return typed, clean_prompt(prompt)


# --- Skills ------------------------------------------------------------------
def project_skill_dirs(roots) -> list[Path]:
    """<dir>/.claude/skills desde cada raíz subiendo hasta la raíz git (la que
    tiene .git), como hace Claude Code. Sin repo git, solo la propia raíz."""
    dirs: list[Path] = []
    for root in roots:
        if not (isinstance(root, (str, Path)) and str(root).strip()):
            continue
        try:
            start = Path(root).expanduser().resolve()
        except OSError:
            continue
        chain, d = [], start
        while True:
            chain.append(d)
            if (d / ".git").exists():
                break
            if d.parent == d:
                chain = [start]  # no hay repo: no se sube hasta /
                break
            d = d.parent
        dirs.extend(c / ".claude" / "skills" for c in chain)
    return dirs


def discover_skills(sr, roots) -> tuple[list, str, bool]:
    """Skills que ve Claude Code, sin la propia skill-router.

    Devuelve (skills, descripción de la fuente, es_catalogo). La comparten el hook
    y la skill /skill-router (run_router.py), para que respondan lo mismo:
    personales (~/.claude/skills) + las del proyecto desde cada raíz de `roots`
    hasta la raíz git. El catálogo skills.json solo se usa si, quitada la propia
    skill-router, no queda ninguna.
    """
    if _flag("SKILL_ROUTER_DEMO"):
        return sr.demo_skills(), "skills de demo", False

    catalog_env = os.environ.get("SKILL_ROUTER_CATALOG", "").strip()
    if catalog_env:
        return sr.load_from_catalog(Path(catalog_env).expanduser()), "catálogo skills.json", True

    explicit = os.environ.get("SKILL_ROUTER_SKILLS_DIR", "").strip()
    if explicit:
        dirs = [Path(p).expanduser() for p in explicit.split(os.pathsep) if p.strip()]
    else:
        dirs = [Path.home() / ".claude" / "skills", *project_skill_dirs(roots)]

    skills, names, seen_dirs, used = [], set(SELF_NAMES), set(), []
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
        found = [s for s in sr.load_from_catalog(catalog) if s.name not in SELF_NAMES]
        if found:
            return found, "catálogo skills.json", True
    return [], "", False


def load_skills(sr, event: dict) -> tuple[list, str, bool]:
    return discover_skills(sr, (os.environ.get("CLAUDE_PROJECT_DIR"), event.get("cwd")))


# --- Puntuación --------------------------------------------------------------
def score(sr, skills: list, text: str, mode: str) -> tuple[list[dict], bool]:
    """Devuelve (resultados, usó_llm). Si el LLM falla, cae al léxico."""
    if mode in ("llm", "cascade"):
        try:
            os.environ[GUARD_ENV] = "1"  # lo hereda el `claude -p` hijo
            os.environ.setdefault("SKILL_ROUTER_LLM_TIMEOUT", str(DEFAULT_LLM_TIMEOUT_S))
            if mode == "llm":
                import llm_scorer  # import tardío: solo cuesta si se pide
                return llm_scorer.llm_score(skills, text), True
            import cascade
            usage: dict = {}
            results, used = cascade.cascade_score(skills, text, usage=usage)
            if usage.get("error"):
                print(f"[skill-router hook] scorer LLM no disponible, uso el léxico: "
                      f"{usage['error']}", file=sys.stderr)
            return results, used
        except Exception as e:  # LLMUnavailable, timeout, JSON raro...
            print(f"[skill-router hook] scorer LLM no disponible, uso el léxico: {e}",
                  file=sys.stderr)
    return sr.score_skills(skills, text, use_concepts=True, normalize=False), False


def select(sr, results: list[dict], used_llm: bool) -> list[dict]:
    """Filtro de precisión. Ante la duda, nada.

    - umbral (def. 20) y top (def. 2);
    - léxico: al menos MIN_MATCHED coincidencias distintas (una sola palabra
      suelta como "app" o "type" no basta) y que el #1 saque MARGIN puntos al
      siguiente candidato (los empates entre skills sin relación son ruido).
    El LLM ya decidió: solo se le aplican umbral y top.
    """
    top = _env_int("SKILL_ROUTER_HOOK_TOP", DEFAULT_TOP, 1, 10)
    threshold = _env_int("SKILL_ROUTER_HOOK_THRESHOLD", DEFAULT_THRESHOLD, 0, 100)
    if used_llm:
        return sr.classify(results, top, threshold)[0]
    min_matched = _env_int("SKILL_ROUTER_HOOK_MIN_MATCHED", DEFAULT_MIN_MATCHED, 1, 10)
    margin = _env_int("SKILL_ROUTER_HOOK_MARGIN", DEFAULT_MARGIN, 0, 100)
    recommended = sr.classify(results, top, threshold, min_matched=min_matched)[0]
    if not recommended or margin <= 0:
        return recommended
    ranked = sorted(results, key=lambda r: -r["score"])
    first = recommended[0]
    rival = next((r for r in ranked if r is not first), None)
    if rival is not None and first["score"] - rival["score"] < margin:
        # Si el rival también pasó el filtro, son dos buenas candidatas: se
        # mantienen ambas. Si no, el #1 no destaca: silencio.
        if not any(r is rival for r in recommended):
            return []
    return recommended


def _reason(matched) -> str:
    if isinstance(matched, str):
        return _inline(matched, MAX_REASON_CHARS)
    parts = []
    for m in matched or []:
        m = str(m)
        parts.append("~" + m.split(":", 1)[1] if m.startswith("concept:") else m)
    return _inline(", ".join(parts[:MAX_REASON_TERMS]), MAX_REASON_CHARS) if parts else ""


def build_context(recommended: list[dict], used_llm: bool, is_catalog: bool) -> str:
    """Una sola línea: skill(s) y motivo breve. Cada carácter cuesta tokens en
    cada turno en que se inyecta."""
    items = []
    for r in recommended:
        why = _reason(r.get("matched"))
        items.append(_inline(r["name"], 60) + (f" ({why})" if why else ""))
    basis = "LLM" if used_llm else "palabras clave"
    extra = "; del catálogo, quizá no instalada" if is_catalog else ""
    text = (f"Skill Router ({basis}{extra}): podría servir {'; '.join(items)}. "
            "Ignóralo si no encaja.")
    return text if len(text) <= MAX_CONTEXT_CHARS else text[: MAX_CONTEXT_CHARS - 1] + "…"


def route(event: dict, prompt: str) -> str | None:
    """Devuelve el texto de contexto, o None si no hay nada que recomendar."""
    t0 = time.perf_counter()
    sr = import_router()
    if sr is None:
        return None

    skills, source, is_catalog = load_skills(sr, event)
    t1 = time.perf_counter()
    if not skills:
        _debug("no hay skills; no recomiendo nada")
        return None

    mode = hook_mode()
    typed, full = split_prompt(sr, prompt)
    results, used_llm = score(sr, skills, typed, mode)
    recommended = select(sr, results, used_llm)
    if not recommended and not used_llm and full != typed:
        # Lo tecleado no basta ("arréglalo" + traza pegada): se puntúa con lo
        # pegado, con el mismo filtro. Solo léxico: no gasta otra llamada al LLM.
        recommended = select(sr, sr.score_skills(skills, full, use_concepts=True,
                                                 normalize=False), False)
    t2 = time.perf_counter()
    _debug(f"{len(skills)} skills ({source}); modo {mode}; carga {1000 * (t1 - t0):.1f} ms, "
           f"puntuación {1000 * (t2 - t1):.1f} ms; recomendadas: "
           f"{[(r['name'], r['score']) for r in recommended]}")
    if not recommended:
        return None
    return build_context(recommended, used_llm, is_catalog)


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
        if _flag(CHILD_ENV) or os.environ.get(GUARD_ENV) or _flag("SKILL_ROUTER_HOOK_OFF"):
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
        if not prompt.strip() or _SLASH_COMMAND.match(prompt.strip()):
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
