#!/usr/bin/env python3
"""Hook SessionStart del plugin Skill Router (autopilot). Solo biblioteca estándar.

Lee el JSON del evento por stdin (usa `cwd`; si falta, $CLAUDE_PROJECT_DIR) y:
- Proyecto ya configurado (existe .claude/skill-router/state.json o la marca
  autopilot.json): sale enseguida con, como mucho, UNA línea breve de contexto.
- Proyecto sin configurar: lanza en segundo plano y desacoplado
  `cli.py setup --auto --project <cwd>` (log en .claude/skill-router/setup.log)
  y devuelve al momento un contexto breve. El setup clona fuentes la primera vez,
  así que no se espera a que termine: SessionStart debe ser rápido.
- Nunca bloquea: siempre exit 0; cualquier excepción se captura.
- Se desactiva con SKILL_ROUTER_CHILD=1 (el `claude -p` hijo del router) o con
  SKILL_ROUTER_AUTOPILOT=0. Tampoco actúa en $HOME, en / ni dentro de ~/.claude.
- Un lock (setup.lock, lo gestiona cli.py) impide dos setups a la vez.

Salida (doc de hooks, SessionStart): JSON
  {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "..."}}

Variables para pruebas: SKILL_ROUTER_CLI (ruta alternativa a cli.py).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SR_REL = Path(".claude") / "skill-router"
MAX_STDIN = 1_000_000
LOCK_STALE_SECONDS = 15 * 60
RETRY_SECONDS = 24 * 3600


def _emit(text: str, out=None) -> None:
    out = out or sys.stdout
    out.write(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                                 "additionalContext": text}},
                         ensure_ascii=False) + "\n")


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
            extra = (f"; ≈{sv['saved_tokens_est']} tokens/turno menos que instalar todo"
                     if sv.get("saved_tokens_est") else "")
            text = (f"Skill Router: {n} skills curadas del proyecto en .claude/skills{extra}. "
                    "El router recomienda una por mensaje; /skill-router:status para detalles.")
        # Configurado sin skills: no se gasta contexto.
    else:
        failed_at = (marker or {}).get("failed_at")
        if marker and marker.get("ok") is False and isinstance(failed_at, (int, float)) \
                and time.time() - failed_at < RETRY_SECONDS:
            return None
        created_claude = not claude_dir.exists()
        sr.mkdir(parents=True, exist_ok=True)
        _spawn_setup(root, sr, created_claude)
        text = ("Skill Router: preparando en segundo plano las skills curadas para este tipo de "
                "proyecto (.claude/skills; registro en .claude/skill-router/setup.log). Suele tardar "
                "menos de un minuto la primera vez; si .claude/skills no existía al empezar la sesión, "
                "se activan con /reload-skills o en la próxima sesión. Deshacer: /skill-router:undo.")
    if text:
        _emit(text, out)
    return text


def main() -> int:
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
