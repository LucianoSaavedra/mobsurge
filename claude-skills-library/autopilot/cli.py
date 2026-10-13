#!/usr/bin/env python3
"""CLI del autopilot del Skill Router (contrato C4). Solo biblioteca estándar.

Uso (desde claude-skills-library, o por ruta):
    python3 -m autopilot.cli setup  [--project DIR] [--auto] [--allow-scripts] [--dry-run] [--json]
    python3 -m autopilot.cli plan   [--project DIR] [--json]
    python3 -m autopilot.cli status [--project DIR] [--json]
    python3 -m autopilot.cli undo   [--project DIR] [--json] [--force] [--purge]
    python3 /ruta/a/claude-skills-library/autopilot/cli.py status

setup = detectar el tipo de proyecto -> plan (pocas skills del catálogo curado)
-> instalar SOLO en <proyecto>/.claude/skills (copias, deduplicadas) -> informar
del ahorro estimado de tokens. Nunca toca ~/.claude/skills.

Archivos propios del autopilot, junto a state.json (que escribe install):
  <proyecto>/.claude/skill-router/autopilot.json  último setup (resultado, tokens)
                                                  y "disabled": true tras undo
  <proyecto>/.claude/skill-router/setup.lock      evita dos setups a la vez
  <proyecto>/.claude/skill-router/setup.log       salida del setup en segundo plano
  <proyecto>/.claude/skill-router/.gitignore      "*": nada de esta carpeta va a git

Si el plan tenía repos y no se instaló nada porque ninguna fuente se pudo
descargar (p. ej. sin red), autopilot.json queda con ok:false y failed_at, y el
arranque automático lo reintenta pasadas 24 h. Código de salida 1.

Códigos de salida: 0 bien (o nada que hacer), 1 error, 2 uso incorrecto,
3 otro setup en curso (solo sin --auto; con --auto es 0).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

try:  # como paquete: python3 -m autopilot.cli
    from . import detect, install
    from . import plan as planmod
except ImportError:  # por ruta: python3 .../autopilot/cli.py
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from autopilot import detect, install  # type: ignore
    from autopilot import plan as planmod  # type: ignore

LIB_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = LIB_ROOT / "skills.json"
SR_REL = Path(".claude") / "skill-router"
AUTOPILOT_FILE = "autopilot.json"
LOCK_FILE = "setup.lock"
LOG_FILE = "setup.log"
GITIGNORE_FILE = ".gitignore"
LOCK_STALE_SECONDS = 15 * 60
# Si el último setup falló (p. ej. sin red), el arranque automático lo reintenta
# como mucho una vez por este intervalo.
RETRY_SECONDS = 24 * 3600


# --------------------------------------------------------------------------- utilidades
def catalog_path() -> Path:
    return Path(os.environ.get("SKILL_ROUTER_CATALOG") or DEFAULT_CATALOG)


def sr_dir(project) -> Path:
    return Path(project).expanduser().resolve() / SR_REL


def read_json(path: Path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def read_marker(project) -> dict | None:
    data = read_json(sr_dir(project) / AUTOPILOT_FILE)
    return data if isinstance(data, dict) else None


def write_marker(project, data: dict) -> None:
    d = sr_dir(project)
    if d.is_symlink() or (d.parent.is_symlink()):
        raise ValueError(f"{d} es (o cuelga de) un enlace simbólico; no se toca")
    d.mkdir(parents=True, exist_ok=True)
    ensure_gitignore(d)
    tmp = d / (AUTOPILOT_FILE + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, d / AUTOPILOT_FILE)


def ensure_gitignore(d: Path) -> None:
    """Carpeta con rutas absolutas y logs: que no acabe en un commit."""
    gi = d / GITIGNORE_FILE
    if not gi.exists() and not gi.is_symlink():
        gi.write_text("*\n", encoding="utf-8")


# Motivos de salto de repo entero que NO son fallos de descarga (reintentar no ayuda).
_NOT_FETCH_PREFIXES = ("el repo no está en el catálogo", "ninguna de las skills pedidas",
                       "catálogo curado no disponible", "URL no permitida")


def fetch_failures(p: dict, res: dict) -> list[dict]:
    """Repos del plan que se saltaron enteros por no poder descargarse."""
    names = {str(r.get("name")) for r in (p or {}).get("repos") or [] if isinstance(r, dict)}
    out = []
    for s in res.get("skipped") or []:
        if s.get("skill") == "*" and s.get("repo") in names \
                and not str(s.get("reason") or "").startswith(_NOT_FETCH_PREFIXES):
            out.append(s)
    return out


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def lock_is_fresh(project) -> bool:
    """True si hay un setup en curso (lock reciente con proceso vivo)."""
    p = sr_dir(project) / LOCK_FILE
    try:
        age = time.time() - p.stat().st_mtime
    except OSError:
        return False
    if age > LOCK_STALE_SECONDS:
        return False
    info = read_json(p) or {}
    pid = info.get("pid") if isinstance(info, dict) else None
    if isinstance(pid, int) and not _pid_alive(pid):
        return False
    return True


class Lock:
    """Lock por archivo con O_EXCL. Se considera abandonado si es viejo o su
    proceso ya no existe."""

    def __init__(self, project):
        self.path = sr_dir(project) / LOCK_FILE
        self.held = False

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                if lock_is_fresh(self.path.parents[2]):
                    return False
                try:
                    self.path.unlink()
                except OSError:
                    return False
                continue
            with os.fdopen(fd, "w") as f:
                json.dump({"pid": os.getpid(), "since": time.time()}, f)
            self.held = True
            return True
        return False

    def release(self) -> None:
        if self.held:
            try:
                self.path.unlink()
            except OSError:
                pass
            self.held = False


def savings(te: dict | None) -> dict:
    te = te or {}
    b = int(te.get("before_tokens_est") or 0)
    a = int(te.get("after_tokens_est") or 0)
    pct = round(100 * (b - a) / b) if b > 0 else 0
    return {"before_tokens_est": b, "after_tokens_est": a, "saved_tokens_est": max(0, b - a),
            "saved_pct": max(0, pct), "before_skills": te.get("before_skills"),
            "after_skills": te.get("after_skills")}


def _out(data, as_json: bool, text: str) -> None:
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(text)


# --------------------------------------------------------------------------- comandos
def build_plan(project, max_repos: int) -> tuple[dict, dict]:
    profile = detect.profile_project(project)
    p = planmod.make_plan(profile, str(catalog_path()), max_repos=max_repos)
    return profile, p


def plan_text(profile: dict, p: dict) -> str:
    lines = [f"Proyecto: {profile.get('root')}",
             f"Tipo detectado: {profile.get('summary') or '(sin señales claras)'}"]
    if p.get("scenarios"):
        lines.append("Escenarios: " + ", ".join(p["scenarios"]))
    if p.get("repos"):
        lines.append("Repos elegidos del catálogo curado:")
        for r in p["repos"]:
            lines.append(f"  - {r.get('name')} [{r.get('trust')}] — {r.get('reason')}")
    else:
        lines.append("No se elige ningún repo: no hay señales suficientes (0 tokens extra).")
    for s in p.get("skipped") or []:
        lines.append(f"  (descartado) {s.get('name')}: {s.get('reason')}")
    if p.get("token_budget_note"):
        lines.append(p["token_budget_note"])
    return "\n".join(lines)


def cmd_plan(a) -> int:
    profile, p = build_plan(a.project, a.max_repos)
    _out({"profile": profile, "plan": p}, a.json, plan_text(profile, p))
    return 0


def _skip_priority(s: dict) -> int:
    """Primero lo que el usuario debe ver: modificadas a mano y rechazadas por seguridad."""
    r = str(s.get("reason") or "").lower()
    if "a mano" in r:
        return 0
    if "seguridad" in r:
        return 1
    return 2


def setup_text(res: dict, p: dict, profile: dict) -> str:
    lines = [f"Skill Router — proyecto: {profile.get('root')}",
             f"Tipo detectado: {profile.get('summary') or '(sin señales claras)'}"]
    if res.get("dry_run"):
        lines.append("(simulación: no se ha cambiado nada)")
    inst = res.get("installed") or []
    if inst:
        lines.append(f"Skills instaladas en .claude/skills ({len(inst)}):")
        for e in inst:
            extra = " (sin cambios)" if e.get("unchanged") else ""
            lines.append(f"  - {e.get('skill')}  ← {e.get('repo')}{extra}")
    else:
        lines.append("No se ha instalado ninguna skill.")
    for d in res.get("dedup_removed") or []:
        lines.append(f"  (duplicada) {d.get('skill')} ≈ {d.get('kept')} (similitud {d.get('similarity')})")
    sk = sorted(res.get("skipped") or [], key=_skip_priority)
    if sk:
        lines.append(f"Saltadas: {len(sk)}")
        for s in sk[:10]:
            lines.append(f"  - {s.get('skill')} ({s.get('repo')}): {s.get('reason')}")
        if len(sk) > 10:
            lines.append(f"  … y {len(sk) - 10} más (usa --json para verlas todas)")
    sv = savings(res.get("token_estimate"))
    if sv["before_tokens_est"]:
        lines.append(
            f"Tokens por turno (estimación name+description): instalar todo ≈ {sv['before_tokens_est']}"
            f" ({sv['before_skills']} skills); instalado ≈ {sv['after_tokens_est']}"
            f" ({sv['after_skills']} skills) → ahorro ≈ {sv['saved_tokens_est']} ({sv['saved_pct']}%).")
    if sv["after_tokens_est"] or inst:
        lines.append(f"Coste neto añadido frente a no instalar nada: ≈ {sv['after_tokens_est']} tokens/turno"
                     f" ({sv['after_skills'] if sv['after_skills'] is not None else len(inst)} skills).")
    if res.get("fetch_failed"):
        lines.append("No se pudo descargar ninguna fuente; se reintentará automáticamente pasadas 24 h "
                     "(o ahora con: setup).")
    lines.append("Deshacer: python3 -m autopilot.cli undo --project <proyecto>  (o /skill-router:undo)")
    return "\n".join(lines)


def cmd_setup(a) -> int:
    project = Path(a.project).expanduser().resolve()
    marker = read_marker(project) or {}
    if a.auto and marker.get("disabled"):
        _out({"skipped": True, "reason": "desactivado en este proyecto (undo)"}, a.json,
             "Autopilot desactivado en este proyecto tras un undo; usa 'setup' sin --auto para reactivarlo.")
        return 0

    profile, p = build_plan(project, a.max_repos)
    if not a.auto and not a.dry_run and not a.json and not a.yes and sys.stdin.isatty():
        print(plan_text(profile, p))
        try:
            ans = input("¿Instalar este plan en .claude/skills del proyecto? [s/N] ").strip().lower()
        except EOFError:
            ans = ""
        if ans not in ("s", "si", "sí", "y", "yes"):
            print("Cancelado; no se ha cambiado nada.")
            return 0

    # .claude/ creada por nosotros (aquí o por el hook de arranque) -> undo --purge la quita si queda vacía.
    created = list(marker.get("created_dirs") or [])
    if (not (project / ".claude").exists() or os.environ.get("SKILL_ROUTER_CREATED_CLAUDE") == "1") \
            and ".claude" not in created:
        created.append(".claude")
    lock = None
    if not a.dry_run:
        lock = Lock(project)
        if not lock.acquire():
            _out({"skipped": True, "reason": "otro setup en curso"}, a.json,
                 "Ya hay un setup en curso para este proyecto; inténtalo en un momento.")
            return 0 if a.auto else 3
    try:
        res = install.apply_plan(p, str(project), allow_scripts=a.allow_scripts, dry_run=a.dry_run,
                                 sources_dir=a.sources_dir, max_skills=a.max_skills)
        failures = fetch_failures(p, res) if not (res.get("installed") or []) else []
        if failures:
            res["fetch_failed"] = True
        if not a.dry_run and failures:
            # Nada instalado por no poder descargar: ok:false para que el arranque reintente a las 24 h.
            write_marker(project, {
                "version": 1, "ok": False, "disabled": False,
                "updated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "mode": "auto" if a.auto else "manual",
                "failed_at": time.time(), "created_dirs": created,
                "summary": profile.get("summary"),
                "scenarios": p.get("scenarios") or [],
                "installed": [],
                "error": "no se pudo descargar ninguna fuente: " + "; ".join(
                    f"{f.get('repo')}: {f.get('reason')}" for f in failures),
            })
        elif not a.dry_run:
            write_marker(project, {
                "version": 1, "ok": True, "disabled": False,
                "updated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "mode": "auto" if a.auto else "manual",
                "summary": profile.get("summary"),
                "scenarios": p.get("scenarios") or [],
                "installed": [e.get("skill") for e in res.get("installed") or []],
                "skipped_count": len(res.get("skipped") or []),
                "dedup_count": len(res.get("dedup_removed") or []),
                "token_estimate": res.get("token_estimate"),
                "savings": savings(res.get("token_estimate")),
                "created_dirs": created,
            })
    except Exception as exc:  # se registra para no reintentar en bucle
        if not a.dry_run:
            try:
                write_marker(project, {"version": 1, "ok": False, "disabled": marker.get("disabled", False),
                                       "updated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                                       "failed_at": time.time(), "created_dirs": created,
                                       "error": f"{type(exc).__name__}: {exc}"})
            except Exception:
                pass
        raise
    finally:
        if lock:
            lock.release()

    _out({"profile": profile, "plan": p, "result": res, "savings": savings(res.get("token_estimate"))},
         a.json, setup_text(res, p, profile))
    return 1 if res.get("fetch_failed") else 0


def cmd_status(a) -> int:
    st = install.status(a.project)
    m = read_marker(a.project)
    st["autopilot"] = m
    st["setup_running"] = lock_is_fresh(a.project)
    lines = [f"Proyecto: {st.get('project')}"]
    if st.get("setup_running"):
        lines.append("Hay un setup en curso.")
    if not st.get("active"):
        lines.append("Skill Router no ha instalado nada en este proyecto.")
    else:
        lines.append(f"Skills instaladas por Skill Router ({st.get('count', 0)}):")
        for e in st.get("installed") or []:
            flag = "" if e.get("present") else "  [falta]"
            if e.get("modified"):
                flag += "  [modificada a mano]"
            lines.append(f"  - {e.get('skill')}  ← {e.get('repo')}{flag}")
    if m:
        if m.get("disabled"):
            lines.append("Autopilot desactivado en este proyecto (tras undo).")
        elif m.get("ok") is False:
            lines.append(f"El último setup falló: {m.get('error')}")
        sv = m.get("savings") or {}
        if st.get("active") or sv.get("after_tokens_est"):
            n_sk = sv.get("after_skills") if sv.get("after_skills") is not None else st.get("count", 0)
            lines.append(f"Coste por turno de las skills instaladas: ≈ {sv.get('after_tokens_est') or 0} "
                         f"tokens ({n_sk} skills).")
        if sv.get("before_tokens_est"):
            lines.append(f"Ahorro estimado por turno: ≈ {sv.get('saved_tokens_est')} tokens "
                         f"({sv.get('saved_pct')}%) frente a instalar todas las skills de los repos.")
    _out(st, a.json, "\n".join(lines))
    return 0


def cmd_undo(a) -> int:
    project = Path(a.project).expanduser().resolve()
    if lock_is_fresh(project):
        _out({"ok": False, "reason": "setup en curso"}, a.json,
             "Hay un setup en curso; espera a que termine y repite el undo.")
        return 3
    created = (read_marker(project) or {}).get("created_dirs") or []
    res = install.undo(str(project), force=a.force)
    d = sr_dir(project)
    if a.purge:
        for name in (AUTOPILOT_FILE, LOG_FILE, GITIGNORE_FILE):
            try:
                (d / name).unlink()
            except OSError:
                pass
        for p in (d, *[project / c for c in created if c == ".claude"]):
            try:
                p.rmdir()  # solo si quedó vacía
            except OSError:
                pass
        res["autopilot_disabled"] = False
    else:
        # Sin esta marca, el arranque automático volvería a instalar en la siguiente sesión.
        write_marker(project, {"version": 1, "disabled": True, "ok": True, "created_dirs": created,
                               "updated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                               "reason": "undo"})
        res["autopilot_disabled"] = True
    lines = []
    if res.get("removed"):
        lines.append("Retiradas: " + ", ".join(map(str, res["removed"])))
    for k in res.get("kept") or []:
        lines.append(f"Conservada {k.get('skill')}: {k.get('reason')}")
    if res.get("message"):
        lines.append(res["message"].capitalize())
    if res["autopilot_disabled"]:
        lines.append("Autopilot desactivado en este proyecto: no se reinstalará solo. "
                     "Para reactivarlo: setup (sin --auto). Para borrar también esta marca: undo --purge.")
    else:
        lines.append("Marca del autopilot borrada: en la próxima sesión volverá a configurarse solo "
                     "(salvo SKILL_ROUTER_AUTOPILOT=0).")
    _out(res, a.json, "\n".join(lines))
    return 0


# --------------------------------------------------------------------------- main
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="autopilot.cli",
                                 description="Autopilot del Skill Router: skills curadas por proyecto.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--project", default=os.getcwd(), help="carpeta del proyecto (por defecto, la actual)")
        sp.add_argument("--json", action="store_true", help="salida JSON")

    sp = sub.add_parser("setup", help="detectar, planificar e instalar skills curadas")
    common(sp)
    sp.add_argument("--auto", action="store_true", help="sin preguntas; respeta la desactivación tras undo")
    sp.add_argument("--yes", "-y", action="store_true", help="no pedir confirmación")
    sp.add_argument("--allow-scripts", action="store_true", help="permitir skills con scripts ejecutables")
    sp.add_argument("--dry-run", action="store_true", help="simular sin cambiar nada")
    sp.add_argument("--max-repos", type=int, default=3)
    sp.add_argument("--max-skills", type=int, default=12)
    sp.add_argument("--sources-dir", default=None, help="caché de fuentes (def. ~/.cache/skill-router/sources)")
    sp.set_defaults(func=cmd_setup)

    sp = sub.add_parser("plan", help="mostrar el plan sin instalar nada")
    common(sp)
    sp.add_argument("--max-repos", type=int, default=3)
    sp.set_defaults(func=cmd_plan)

    sp = sub.add_parser("status", help="ver qué instaló Skill Router en el proyecto")
    common(sp)
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("undo", help="retirar lo instalado y desactivar el autopilot en el proyecto")
    common(sp)
    sp.add_argument("--force", action="store_true", help="borrar también copias modificadas a mano")
    sp.add_argument("--purge", action="store_true", help="borrar también la marca del autopilot y el log")
    sp.set_defaults(func=cmd_undo)
    return ap


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    try:
        return a.func(a)
    except Exception as exc:  # noqa: BLE001 — mensaje claro en vez de traza
        msg = f"Error: {exc}"
        if getattr(a, "json", False):
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        else:
            print(msg, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
