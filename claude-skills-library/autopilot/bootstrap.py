#!/usr/bin/env python3
"""Hook SessionStart del plugin Skill Router (autopilot). Solo biblioteca estándar.

Lee el JSON del evento por stdin (usa `cwd`; si falta, $CLAUDE_PROJECT_DIR) y:
- Proyecto sin nada que instalar (detect + plan síncronos, ~5-15 ms, sin red):
  no crea .claude/ ni emite contexto; deja la marca FUERA del proyecto, en
  ${CLAUDE_PLUGIN_DATA}/projects/<sha1(raíz)>.json (si la variable no existe,
  en $XDG_CACHE_HOME/skill-router/projects o ~/.cache/skill-router/projects). Se reevalúa a las 24 h.
- Proyecto ya configurado (existe .claude/skill-router/state.json o la marca
  autopilot.json): sale enseguida con, como mucho, UNA línea breve de contexto.
- Proyecto sin configurar: lanza en segundo plano y desacoplado
  `cli.py setup --auto --project <cwd>` (log en .claude/skill-router/setup.log,
  carpeta con un .gitignore "*" para que nada de ella acabe en un commit)
  y devuelve al momento un contexto breve. El setup clona fuentes la primera vez,
  así que no se espera a que termine: SessionStart debe ser rápido.
- Nunca bloquea: siempre exit 0; cualquier excepción se captura.
- Se desactiva con SKILL_ROUTER_CHILD=1 (el `claude -p` hijo del router) o con
  SKILL_ROUTER_AUTOPILOT=0. Tampoco actúa en $HOME, en / ni dentro de ~/.claude.
- Un lock (setup.lock, lo gestiona cli.py) impide dos setups a la vez.

Salida (doc de hooks, SessionStart): JSON
  {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "..."}}

Variables para pruebas: SKILL_ROUTER_CLI (ruta alternativa a cli.py),
SKILL_ROUTER_CATALOG (skills.json alternativo, igual que cli.py).

Modo `route` (`bootstrap.py route`): envoltorio portable del hook UserPromptSubmit.
Fija SKILL_ROUTER_HOOK_MODE=llm si no está definido y ejecuta
skill-router/integration/hooks/route_prompt.py en el mismo proceso. Así hooks.json
usa la forma exec (`command` + `args`), sin depender de bash ni de la sintaxis
VAR=valor del shell (en Windows el shell puede ser PowerShell). Requisito: un
`python3` en el PATH (en Windows, instalar Python y activar el alias python3).
"""
from __future__ import annotations

import hashlib
import json
import os
import runpy
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIB_ROOT = HERE.parent
ROUTE_PROMPT = LIB_ROOT / "skill-router" / "integration" / "hooks" / "route_prompt.py"
SR_REL = Path(".claude") / "skill-router"
MAX_STDIN = 1_000_000
LOCK_STALE_SECONDS = 15 * 60
RETRY_SECONDS = 24 * 3600


def _emit(text: str, out=None) -> None:
    out = out or sys.stdout
    out.write(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                                 "additionalContext": text}},
                         ensure_ascii=True) + "\n")  # ASCII: sobrevive a stdout cp1252


def plugin_data_dir() -> Path:
    """${CLAUDE_PLUGIN_DATA} o, si no existe, la caché del usuario."""
    d = os.environ.get("CLAUDE_PLUGIN_DATA")
    if d:
        return Path(d).expanduser()
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base).expanduser() / "skill-router"


def external_marker_path(root: Path) -> Path:
    h = hashlib.sha1(str(root).encode("utf-8", "surrogatepass")).hexdigest()
    return plugin_data_dir() / "projects" / f"{h}.json"


def _write_external_marker(root: Path, data: dict) -> None:
    p = external_marker_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=True), encoding="utf-8")
    os.replace(tmp, p)


def _plan_repos(root: Path) -> list:
    """detect + plan síncronos (sin red). Devuelve los repos del plan."""
    if str(LIB_ROOT) not in sys.path:
        sys.path.insert(0, str(LIB_ROOT))
    from autopilot import detect, plan as planmod  # import diferido: el modo route no lo paga
    catalog = os.environ.get("SKILL_ROUTER_CATALOG") or str(LIB_ROOT / "skills.json")
    profile = detect.profile_project(str(root))
    p = planmod.make_plan(profile, catalog, max_repos=3)
    return list((p or {}).get("repos") or [])


def ensure_gitignore(sr: Path) -> None:
    gi = sr / ".gitignore"
    if not gi.exists():
        gi.write_text("*\n", encoding="utf-8")


def _read_json(path: Path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _lock_fresh(sr: Path) -> bool:
    p = sr / "setup.lock"
    try:
        if time.time() - p.stat().st_mtime > LOCK_STALE_SECONDS:
            return False
    except OSError:
        return False
    info = _read_json(p)
    pid = info.get("pid") if isinstance(info, dict) else None
    if isinstance(pid, int):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except OSError:
            pass
    return True


def _unsafe_root(root: Path) -> bool:
    home = Path.home().resolve()
    if root == home or root == Path(root.anchor) or not root.is_dir():
        return True
    try:
        root.relative_to(home / ".claude")
        return True
    except ValueError:
        return False


def _spawn_setup(root: Path, sr: Path, created_claude: bool) -> None:
    cli = os.environ.get("SKILL_ROUTER_CLI") or str(HERE / "cli.py")
    env = dict(os.environ)
    env.pop("SKILL_ROUTER_CHILD", None)
    if created_claude:
        env["SKILL_ROUTER_CREATED_CLAUDE"] = "1"
    log = open(sr / "setup.log", "a", encoding="utf-8")
    try:
        log.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} setup --auto (SessionStart) ===\n")
        log.flush()
        kw = {}
        if os.name == "nt":  # pragma: no cover
            kw["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | NEW_PROCESS_GROUP
        else:
            kw["start_new_session"] = True
        subprocess.Popen([sys.executable, cli, "setup", "--auto", "--project", str(root)],
                         stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                         cwd=str(root), env=env, close_fds=True, **kw)
    finally:
        log.close()


def run(stdin_text: str, out=None) -> str | None:
    """Devuelve el texto de contexto emitido (o None). No lanza excepciones."""
    if os.environ.get("SKILL_ROUTER_CHILD") == "1" or os.environ.get("SKILL_ROUTER_AUTOPILOT") == "0":
        return None
    try:
        event = json.loads(stdin_text) if stdin_text.strip() else {}
    except ValueError:
        event = None
    if not isinstance(event, dict):
        print("skill-router bootstrap: entrada JSON no válida; no se hace nada", file=sys.stderr)
        return None
    cwd = event.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR")
    if not cwd or not isinstance(cwd, str):
        return None
    root = Path(cwd).expanduser().resolve()
    if _unsafe_root(root):
        return None
    claude_dir = root / ".claude"
    sr = root / SR_REL
    if claude_dir.is_symlink() or sr.is_symlink():
        return None

    state = _read_json(sr / "state.json")
    marker = _read_json(sr / "autopilot.json")
    marker = marker if isinstance(marker, dict) else None
    text = None

    if marker and marker.get("disabled"):
        return None
    if _lock_fresh(sr):
        text = ("Skill Router: configurando las skills de este proyecto en segundo plano "
                "(.claude/skill-router/setup.log).")
    elif isinstance(state, dict) or (marker and marker.get("ok")):
        n = len((state or {}).get("installed") or []) if isinstance(state, dict) else 0
        if n:
            sv = (marker or {}).get("savings") or {}
            parts = []
            if sv.get("after_tokens_est"):
                parts.append(f"coste: ≈{sv['after_tokens_est']} tokens/turno "
                             f"({sv.get('after_skills') or n} skills)")
            if sv.get("saved_tokens_est"):
                parts.append(f"≈{sv['saved_tokens_est']} tokens/turno menos que instalar todo")
            extra = ("; " + "; ".join(parts)) if parts else ""
            text = (f"Skill Router: {n} skills curadas del proyecto en .claude/skills{extra}. "
                    "El router recomienda una por mensaje; /skill-router:status para detalles.")
        # Configurado sin skills: no se gasta contexto.
    else:
        failed_at = (marker or {}).get("failed_at")
        if marker and marker.get("ok") is False and isinstance(failed_at, (int, float)) \
                and time.time() - failed_at < RETRY_SECONDS:
            return None
        ext = _read_json(external_marker_path(root))
        if isinstance(ext, dict) and ext.get("empty") and isinstance(ext.get("checked_at"), (int, float)) \
                and time.time() - ext["checked_at"] < RETRY_SECONDS:
            return None
        if not _plan_repos(root):
            # Nada que instalar: ni .claude/ en el proyecto ni contexto para Claude.
            _write_external_marker(root, {"version": 1, "empty": True, "root": str(root),
                                          "checked_at": time.time()})
            return None
        created_claude = not claude_dir.exists()
        sr.mkdir(parents=True, exist_ok=True)
        ensure_gitignore(sr)
        _spawn_setup(root, sr, created_claude)
        text = ("Skill Router: preparando en segundo plano las skills curadas para este tipo de "
                "proyecto (.claude/skills; registro en .claude/skill-router/setup.log). Suele tardar "
                "menos de un minuto la primera vez; si .claude/skills no existía al empezar la sesión, "
                "se activan con /reload-skills o en la próxima sesión. Deshacer: /skill-router:undo.")
    if text:
        _emit(text, out)
    return text


def route_main() -> int:
    """Hook UserPromptSubmit portable: modo llm por defecto y route_prompt.py."""
    try:
        if not os.environ.get("SKILL_ROUTER_HOOK_MODE"):
            os.environ["SKILL_ROUTER_HOOK_MODE"] = "llm"
        sys.argv = [str(ROUTE_PROMPT)]
        runpy.run_path(str(ROUTE_PROMPT), run_name="__main__")
    except SystemExit:
        pass
    except BaseException as exc:  # noqa: BLE001 — el hook jamás bloquea el prompt
        try:
            print(f"skill-router route: {type(exc).__name__}: {exc}", file=sys.stderr)
        except Exception:
            pass
    return 0


def main() -> int:
    if sys.argv[1:2] == ["route"]:
        return route_main()
    try:
        data = sys.stdin.read(MAX_STDIN) if not sys.stdin.isatty() else ""
        run(data)
    except Exception as exc:  # noqa: BLE001 — nunca bloquear el arranque
        try:
            print(f"skill-router bootstrap: {type(exc).__name__}: {exc}", file=sys.stderr)
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
