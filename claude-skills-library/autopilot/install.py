"""Instalador seguro de skills curadas en <proyecto>/.claude/skills (contrato C3).

    apply_plan(plan, project_root, allow_scripts=False, dry_run=False,
               sources_dir=None, max_skills=12) -> dict
    status(project_root) -> dict
    undo(project_root) -> dict

Protecciones:
  (a) solo instala repos que estén en el catálogo curado (skills.json), con la
      misma URL;
  (b) las skills con scripts (ver safety.py) se saltan salvo allow_scripts;
  (c) todo se registra en <proyecto>/.claude/skill-router/state.json y undo()
      lo deshace (solo lo registrado, y no borra copias que el usuario editó);
  (d) nunca escribe en las skills globales (~/.claude/skills): se niega si
      <proyecto>/.claude/skills es esa carpeta o un enlace simbólico.

Fuentes: git clone --depth 1 en ~/.cache/skill-router/sources (o sources_dir,
o $SKILL_ROUTER_SOURCES). Las skills se COPIAN. Es idempotente: repetir el mismo
plan deja el proyecto igual. dry_run no escribe nada en el proyecto (sí clona en
la caché de fuentes, porque sin la fuente no se puede saber qué se instalaría).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

try:  # como paquete (python3 -m autopilot.cli) o por ruta
    from . import safety
except ImportError:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import safety  # type: ignore

LIB_ROOT = Path(__file__).resolve().parents[1]
ROUTER_DIR = LIB_ROOT / "skill-router"
DEFAULT_CATALOG = LIB_ROOT / "skills.json"
STATE_REL = Path(".claude") / "skill-router" / "state.json"
SKILLS_REL = Path(".claude") / "skills"
STATE_VERSION = 1

# Similitud (coseno de términos del router) a partir de la cual dos skills se
# consideran redundantes y se instala solo una. El router usa 0.35 para avisar
# de "competencia"; para BORRAR exigimos más.
DEDUP_THRESHOLD = 0.5
CLONE_TIMEOUT = 300
CHARS_PER_TOKEN = 4
_DISCOVER_SKIP = frozenset({".git", "node_modules", "__pycache__", ".venv", "venv",
                            "build", "dist", "target"})


def _router():
    """Importa skill_router (sin modificarlo) añadiendo su carpeta a sys.path."""
    p = str(ROUTER_DIR)
    if p not in sys.path:
        sys.path.insert(0, p)
    import skill_router  # noqa: E402
    return skill_router


# --- utilidades ---------------------------------------------------------------

def trust_rank(trust: str | None) -> int:
    t = (trust or "").lower()
    if t.startswith("oficial"):
        return 3
    if t == "comunidad-recomendada":
        return 2
    if t == "comunidad-popular":
        return 1
    return 0


def _norm_url(url: str) -> str:
    u = (url or "").strip().rstrip("/")
    if u.endswith(".git"):
        u = u[:-4]
    return u.lower() if u.startswith("https://") else u


def _allowed_url(url: str) -> bool:
    return isinstance(url, str) and (url.startswith("https://") or url.startswith("file://"))


def sources_root(sources_dir=None) -> Path:
    if sources_dir:
        return Path(sources_dir).expanduser()
    env = os.environ.get("SKILL_ROUTER_SOURCES")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".cache" / "skill-router" / "sources"


def global_skills_dir() -> Path:
    env = os.environ.get("SKILL_ROUTER_GLOBAL_SKILLS")
    return Path(env).expanduser() if env else Path.home() / ".claude" / "skills"


def _same_path(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return False


def _check_project(project_root) -> Path:
    root = Path(project_root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"el proyecto no existe o no es una carpeta: {root}")
    for rel in (Path(".claude"), SKILLS_REL, STATE_REL.parent):
        p = root / rel
        if p.is_symlink():
            raise ValueError(f"{p} es un enlace simbólico; por seguridad no se toca")
    if _same_path(root / SKILLS_REL, global_skills_dir()):
        raise ValueError("la carpeta de skills del proyecto es la global (~/.claude/skills); "
                         "ejecuta el instalador dentro de un proyecto, no en tu HOME")
    return root


def tree_hash(path: Path) -> str:
    """Hash del contenido (rutas + bytes + bit de ejecución) de una carpeta."""
    h = hashlib.sha256()
    path = Path(path)
    if not path.is_dir():
        return ""
    for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d != ".git")
        for f in sorted(filenames):
            full = Path(dirpath) / f
            rel = full.relative_to(path).as_posix()
            h.update(rel.encode() + b"\0")
            try:
                mode = full.stat().st_mode
                h.update(b"x" if mode & 0o111 else b"-")
                with open(full, "rb") as fh:
                    for chunk in iter(lambda: fh.read(65536), b""):
                        h.update(chunk)
            except OSError:
                h.update(b"?")
            h.update(b"\0")
    return h.hexdigest()


def _load_catalog(catalog_path) -> dict[str, dict]:
    """URL normalizada -> {"name", "trust", "scenarios"} del catálogo curado."""
    try:
        data = json.loads(Path(catalog_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict[str, dict] = {}
    for key, sc in (data.get("scenarios") or {}).items() if isinstance(data, dict) else []:
        for repo in (sc or {}).get("repos") or []:
            if not isinstance(repo, dict) or not repo.get("url"):
                continue
            e = out.setdefault(_norm_url(repo["url"]), {
                "name": repo.get("name"), "trust": repo.get("trust", ""), "scenarios": []})
            e["scenarios"].append(key)
    return out


def _git(args: list[str], cwd=None, timeout=CLONE_TIMEOUT) -> subprocess.CompletedProcess:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="true")
    return subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True,
                          text=True, timeout=timeout)


def fetch_repo(name: str, url: str, sources_dir=None) -> tuple[Path | None, str, str]:
    """Clona (depth 1) o reutiliza la fuente. Devuelve (ruta, commit, error)."""
    if not _allowed_url(url):
        return None, "", f"URL no permitida (solo https:// o file://): {url}"
    root = sources_root(sources_dir)
    safe = "".join(c if c.isalnum() or c in "-_." else "-" for c in (name or "repo"))[:60]
    dest = root / f"{safe.strip('.') or 'repo'}-{hashlib.sha1(_norm_url(url).encode()).hexdigest()[:8]}"
    if (dest / ".git").exists():
        r = _git(["-C", str(dest), "remote", "get-url", "origin"], timeout=30)
        if r.returncode == 0 and _norm_url(r.stdout.strip()) == _norm_url(url):
            head = _git(["-C", str(dest), "rev-parse", "HEAD"], timeout=30)
            return dest, head.stdout.strip(), ""
        shutil.rmtree(dest, ignore_errors=True)  # caché corrupta o de otra URL
    root.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=".clone-", dir=root))
    try:
        r = _git(["clone", "--depth", "1", "--quiet", "--no-tags", "--", url, str(tmp / "r")])
        if r.returncode != 0:
            msg = (r.stderr or r.stdout or "").strip().splitlines()
            return None, "", "git clone falló: " + (msg[-1] if msg else f"código {r.returncode}")
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        os.replace(tmp / "r", dest)
    except subprocess.TimeoutExpired:
        return None, "", f"git clone superó {CLONE_TIMEOUT} s"
    except OSError as e:
        return None, "", f"no se pudo clonar: {e}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    head = _git(["-C", str(dest), "rev-parse", "HEAD"], timeout=30)
    return dest, head.stdout.strip(), ""


def discover_skills(repo_dir: Path, max_depth: int = 4) -> list[Path]:
    """Carpetas con SKILL.md (sin seguir symlinks ni entrar dentro de una skill)."""
    found: list[Path] = []

    def walk(d: Path, depth: int) -> None:
        try:
            entries = sorted(d.iterdir(), key=lambda p: p.name)
        except OSError:
            return
        for e in entries:
            if e.name in _DISCOVER_SKIP or e.name.startswith("."):
                continue
            if e.is_symlink() or not e.is_dir():
                continue
            if (e / "SKILL.md").is_file():
                found.append(e)
            elif depth < max_depth:
                walk(e, depth + 1)

    repo_dir = Path(repo_dir)
    if (repo_dir / "SKILL.md").is_file():
        return [repo_dir]
    walk(repo_dir, 1)
    return found


def _skill_meta(skill_dir: Path) -> tuple[str, str]:
    sr = _router()
    return sr.parse_frontmatter(skill_dir / "SKILL.md")


def _copy_skill(src: Path, dest: Path, files: list[str]) -> None:
    """Copia los archivos ya validados (los symlinks internos se copian como
    archivos normales) de forma atómica: primero a una carpeta temporal."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f".{dest.name}.tmp-", dir=dest.parent))
    try:
        for rel in files:
            s = src / rel
            d = tmp / rel
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(s, d)  # sigue el enlace interno; sin permisos de ejecución
            os.chmod(d, 0o644)
        os.chmod(tmp, 0o755)
        if dest.exists():
            shutil.rmtree(dest)
        os.replace(tmp, dest)
    finally:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)


def _read_state(root: Path) -> dict | None:
    p = root / STATE_REL
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_state(root: Path, state: dict) -> None:
    p = root / STATE_REL
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, p)


def _tokens(chars: int) -> int:
    return (chars + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN


# --- API ------------------------------------------------------------------------

def apply_plan(plan: dict, project_root, allow_scripts: bool = False, dry_run: bool = False,
               sources_dir=None, max_skills: int = 12, catalog_path=None) -> dict:
    """Aplica un plan (formato C2) al proyecto. Ver docstring del módulo."""
    root = _check_project(project_root)
    skills_dir = root / SKILLS_REL
    state_path = root / STATE_REL
    catalog = _load_catalog(catalog_path or os.environ.get("SKILL_ROUTER_CATALOG") or DEFAULT_CATALOG)
    prev = _read_state(root) or {}
    prev_by_name = {e["skill"]: e for e in prev.get("installed", []) if isinstance(e, dict) and e.get("skill")}
    gdir = global_skills_dir()
    max_skills = max(0, int(max_skills))

    installed: list[dict] = []
    skipped: list[dict] = []
    dedup_removed: list[dict] = []
    repos_info: list[dict] = []
    candidates: list[dict] = []  # skills que pasan los filtros de seguridad
    all_found_chars = 0
    all_found = 0

    if not catalog:
        skipped.append({"skill": "*", "repo": "*",
                        "reason": "catálogo curado no disponible: no se instala nada"})

    for idx, repo in enumerate((plan or {}).get("repos") or []):
        if not isinstance(repo, dict):
            continue
        rname = str(repo.get("name") or "?")
        url = str(repo.get("url") or "")
        cat = catalog.get(_norm_url(url))
        if not cat:
            if catalog:
                skipped.append({"skill": "*", "repo": rname,
                                "reason": "el repo no está en el catálogo curado (o su URL no coincide)"})
            continue
        trust = cat.get("trust") or ""
        path, commit, err = fetch_repo(rname, url, sources_dir)
        if err:
            skipped.append({"skill": "*", "repo": rname, "reason": err})
            continue
        repos_info.append({"name": rname, "url": url, "commit": commit, "trust": trust,
                           "source": str(path)})
        only = repo.get("skills")
        only = set(only) if isinstance(only, list) else None
        for sdir in discover_skills(path):
            name = sdir.name
            if only is not None and name not in only:
                continue
            fm_name, desc = _skill_meta(sdir)
            all_found += 1
            all_found_chars += len(fm_name or name) + len(desc or "")
            info = safety.classify_skill(sdir, name)
            if info["kind"] == safety.RECHAZADA:
                skipped.append({"skill": name, "repo": rname,
                                "reason": "rechazada por seguridad: " + safety.summarize_reasons(info["reasons"])})
                continue
            if info["kind"] == safety.CON_SCRIPTS and not allow_scripts:
                skipped.append({"skill": name, "repo": rname,
                                "reason": "tiene scripts (usa --allow-scripts para permitirlo): "
                                          + safety.summarize_reasons(info["reasons"])})
                continue
            if (gdir / name).exists():
                skipped.append({"skill": name, "repo": rname,
                                "reason": "ya existe como skill global en ~/.claude/skills (no se duplica)"})
                continue
            dest = skills_dir / name
            if dest.exists() and name not in prev_by_name:
                skipped.append({"skill": name, "repo": rname,
                                "reason": "ya existe en .claude/skills y no la instaló skill-router (no se toca)"})
                continue
            candidates.append({"skill": name, "repo": rname, "url": url, "commit": commit,
                               "trust": trust, "kind": info["kind"], "files": info["files"],
                               "src": sdir, "description": desc or "", "fm_name": fm_name or name,
                               "order": idx, "src_hash": tree_hash(sdir)})

    # Para deduplicar: gana mayor trust y, a igualdad, la descripción más completa.
    candidates.sort(key=lambda c: (-trust_rank(c["trust"]), -len(c["description"]),
                                   c["order"], c["skill"]))

    # Deduplicación: mismo nombre o similitud >= umbral con una ya elegida.
    sr = _router()
    kept: list[dict] = []
    for c in candidates:
        dup = next((k for k in kept if k["skill"] == c["skill"]), None)
        sim = 1.0 if dup else 0.0
        if not dup and kept:
            objs = [sr.Skill(name=k["fm_name"], description=k["description"]) for k in kept]
            me = sr.Skill(name=c["fm_name"], description=c["description"])
            best = None
            for pair in sr.detect_conflicts(objs + [me], threshold=DEDUP_THRESHOLD):
                if me.name in (pair["a"], pair["b"]):
                    other = pair["b"] if pair["a"] == me.name else pair["a"]
                    k = next((k for k in kept if k["fm_name"] == other), None)
                    if k and (best is None or pair["similarity"] > best[1]):
                        best = (k, pair["similarity"])
            if best:
                dup, sim = best
        if dup:
            dedup_removed.append({"skill": c["skill"], "repo": c["repo"], "kept": dup["skill"],
                                  "kept_repo": dup["repo"], "similarity": round(sim, 2)})
        else:
            kept.append(c)

    # Para el límite: trust, orden del plan (el planificador ya prioriza), descripción.
    kept.sort(key=lambda c: (-trust_rank(c["trust"]), c["order"],
                             -len(c["description"]), c["skill"]))
    selected = kept[:max_skills]
    for c in kept[max_skills:]:
        skipped.append({"skill": c["skill"], "repo": c["repo"],
                        "reason": f"límite de max_skills={max_skills} alcanzado"})

    # Instalación
    created_dirs = list(prev.get("created_dirs") or [])
    new_entries: list[dict] = []
    for c in selected:
        dest = skills_dir / c["skill"]
        entry = {"skill": c["skill"], "repo": c["repo"], "path": str(dest)}
        old = prev_by_name.get(c["skill"])
        unchanged = bool(old and dest.is_dir() and old.get("src_hash") == c["src_hash"]
                         and tree_hash(dest) == old.get("hash"))
        if old and dest.is_dir() and tree_hash(dest) != old.get("hash"):
            skipped.append({"skill": c["skill"], "repo": c["repo"],
                            "reason": "la copia instalada se modificó a mano; no se sobrescribe"})
            new_entries.append(old)
            continue
        if not dry_run and not unchanged:
            for rel in (Path(".claude"), SKILLS_REL):
                p = root / rel
                if not p.exists():
                    p.mkdir(parents=True)
                    if str(rel) not in created_dirs:
                        created_dirs.append(str(rel))
            _copy_skill(c["src"], dest, c["files"])
        entry["unchanged"] = unchanged
        if c["kind"] == safety.CON_SCRIPTS:
            entry["has_scripts"] = True
        installed.append(entry)
        new_entries.append({"skill": c["skill"], "repo": c["repo"], "url": c["url"],
                            "commit": c["commit"], "path": str(dest), "kind": c["kind"],
                            "src_hash": c["src_hash"],
                            "hash": "" if dry_run else tree_hash(dest)})

    # Lo que instaló skill-router antes y ya no está en el plan se retira.
    removed: list[dict] = []
    chosen = {e["skill"] for e in new_entries}
    for name, old in prev_by_name.items():
        if name in chosen:
            continue
        r = _remove_entry(root, old, dry_run=dry_run)
        if r["removed"]:
            removed.append({"skill": name, "reason": "ya no está en el plan"})
        else:
            skipped.append({"skill": name, "repo": old.get("repo", "?"), "reason": r["reason"]})
            new_entries.append(old)

    after_chars = sum(len(c["fm_name"]) + len(c["description"]) for c in selected)
    token_estimate = {
        "before_chars": all_found_chars,
        "after_chars": after_chars,
        "before_tokens_est": _tokens(all_found_chars),
        "after_tokens_est": _tokens(after_chars),
        "before_skills": all_found,
        "after_skills": len(selected),
        "method": ("estimación chars/4 del name+description del frontmatter, que Claude Code "
                   "carga en el contexto en cada turno por cada skill instalada. 'before' = "
                   "instalar todas las skills de los repos del plan; 'after' = las instaladas. "
                   "El cuerpo de SKILL.md solo se carga al usar la skill y no se cuenta."),
    }

    if not dry_run:
        if new_entries or prev:
            _write_state(root, {
                "version": STATE_VERSION,
                "updated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "project": str(root),
                "allow_scripts": bool(allow_scripts),
                "max_skills": max_skills,
                "scenarios": list((plan or {}).get("scenarios") or []),
                "repos": repos_info,
                "installed": new_entries,
                "created_dirs": created_dirs,
            })

    return {
        "installed": installed,
        "skipped": skipped,
        "dedup_removed": dedup_removed,
        "removed": removed,
        "token_estimate": token_estimate,
        "state_path": str(state_path),
        "dry_run": bool(dry_run),
    }


def _remove_entry(root: Path, entry: dict, dry_run: bool = False, force: bool = False) -> dict:
    """Borra una skill registrada solo si está dentro de .claude/skills del
    proyecto y (salvo force) no se modificó desde que se instaló."""
    name = entry.get("skill", "")
    skills_dir = root / SKILLS_REL
    dest = skills_dir / name
    if not safety.valid_skill_name(name) or Path(entry.get("path", "")) != dest:
        return {"removed": False, "reason": "entrada de estado no válida; no se borra"}
    if dest.is_symlink() or not safety.is_within(dest, skills_dir) or _same_path(skills_dir, global_skills_dir()):
        return {"removed": False, "reason": "ruta fuera de .claude/skills del proyecto; no se borra"}
    if not dest.exists():
        return {"removed": True, "reason": "ya no existía"}
    if not force and entry.get("hash") and tree_hash(dest) != entry["hash"]:
        return {"removed": False, "reason": "modificada a mano desde la instalación; se conserva"}
    if not dry_run:
        shutil.rmtree(dest)
    return {"removed": True, "reason": ""}


def status(project_root) -> dict:
    """Estado de lo instalado por skill-router en el proyecto."""
    root = Path(project_root).expanduser().resolve()
    state = _read_state(root)
    out = {"project": str(root), "state_path": str(root / STATE_REL),
           "active": state is not None, "installed": [], "count": 0}
    if not state:
        return out
    for e in state.get("installed") or []:
        dest = Path(e.get("path", ""))
        present = dest.is_dir()
        out["installed"].append({
            "skill": e.get("skill"), "repo": e.get("repo"), "path": str(dest),
            "present": present,
            "modified": bool(present and e.get("hash") and tree_hash(dest) != e.get("hash")),
            "kind": e.get("kind"),
        })
    out["count"] = len(out["installed"])
    for k in ("updated", "scenarios", "repos", "allow_scripts", "max_skills"):
        out[k] = state.get(k)
    return out


def undo(project_root, force: bool = False) -> dict:
    """Deshace lo registrado en state.json. No borra nada que no esté en él ni
    copias modificadas a mano (salvo force=True)."""
    root = _check_project(project_root)
    state = _read_state(root)
    result = {"project": str(root), "removed": [], "kept": [], "state_removed": False}
    if state is None:
        result["message"] = "no hay nada que deshacer (no existe state.json)"
        return result
    kept_entries = []
    for e in state.get("installed") or []:
        r = _remove_entry(root, e, force=force)
        if r["removed"]:
            result["removed"].append(e.get("skill"))
        else:
            result["kept"].append({"skill": e.get("skill"), "reason": r["reason"]})
            kept_entries.append(e)
    if kept_entries:
        state["installed"] = kept_entries
        _write_state(root, state)
    else:
        try:
            (root / STATE_REL).unlink()
            result["state_removed"] = True
        except OSError:
            pass
        _rmdir_if_empty(root / STATE_REL.parent)
        # Solo las carpetas que creó skill-router, y solo si quedaron vacías.
        for rel in sorted(state.get("created_dirs") or [], key=lambda s: -len(s)):
            p = root / rel
            if p.parent == root or safety.is_within(p, root):
                _rmdir_if_empty(p)
    return result


def _rmdir_if_empty(p: Path) -> None:
    try:
        p.rmdir()
    except OSError:
        pass
