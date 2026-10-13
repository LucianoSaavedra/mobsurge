"""Comprobaciones de seguridad para instalar skills de repos curados.

Clasifica cada carpeta de skill como:
  - "solo-instrucciones": solo texto (markdown/datos), sin nada ejecutable.
  - "con-scripts": contiene algo ejecutable o no textual (bit de ejecución,
    .py/.sh/.js..., shebang, binarios). Se salta salvo allow_scripts.
  - "rechazada": nunca se instala (symlinks que salen de la carpeta o a
    directorios, rutas con "..", nombre de skill inválido, demasiado grande).

Solo usa la biblioteca estándar. No ejecuta nada de la skill.
"""
from __future__ import annotations

import os
import re
import stat
from pathlib import Path

SOLO_INSTRUCCIONES = "solo-instrucciones"
CON_SCRIPTS = "con-scripts"
RECHAZADA = "rechazada"

# Nombre de carpeta de skill: letras, dígitos, guion, guion bajo y punto
# interior; empieza por letra o dígito; máx. 64 (límite de Claude Code).
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

# Texto que Claude solo LEE (no ejecuta).
TEXT_EXTS = frozenset({
    ".md", ".markdown", ".mdx", ".txt", ".rst", ".adoc",
    ".json", ".jsonl", ".yaml", ".yml", ".toml", ".csv", ".tsv", ".ini",
    ".cfg", ".conf", ".xml", ".xsd",
})
TEXT_NAMES = frozenset({"license", "licence", "notice", "readme", "authors",
                        "changelog", "copying", ".gitignore", ".gitattributes"})

# Extensiones que se consideran código (aunque sean texto).
SCRIPT_EXTS = frozenset({
    ".py", ".pyw", ".pyc", ".sh", ".bash", ".zsh", ".fish", ".ksh", ".csh",
    ".js", ".mjs", ".cjs", ".ts", ".mts", ".cts", ".jsx", ".tsx",
    ".rb", ".pl", ".pm", ".php", ".lua", ".r", ".jl", ".go", ".rs", ".java",
    ".kt", ".swift", ".c", ".cc", ".cpp", ".h", ".hpp", ".cs", ".scala",
    ".ps1", ".psm1", ".bat", ".cmd", ".vbs", ".exe", ".dll", ".so", ".dylib",
    ".bin", ".jar", ".wasm", ".html", ".htm", ".svg", ".applescript",
    ".scpt", ".command", ".app", ".msi", ".deb", ".rpm",
})

MAX_FILES = 2000
MAX_BYTES = 50 * 1024 * 1024
_SNIFF = 4096


def valid_skill_name(name: str) -> bool:
    """True si `name` es un nombre de carpeta de skill seguro."""
    if not isinstance(name, str) or not _NAME_RE.match(name):
        return False
    return ".." not in name and not name.endswith(".")


def has_dotdot(path: str) -> bool:
    """True si la ruta tiene un componente ".." (o es absoluta)."""
    p = str(path).replace("\\", "/")
    return p.startswith("/") or any(part == ".." for part in p.split("/"))


def is_within(child: Path, parent: Path) -> bool:
    try:
        Path(child).resolve().relative_to(Path(parent).resolve())
        return True
    except (ValueError, OSError):
        return False


def _is_text_file(path: Path) -> tuple[bool, str]:
    """(es_texto, motivo_si_no). Mira extensión, shebang y bytes."""
    name = path.name.lower()
    ext = path.suffix.lower()
    if ext in SCRIPT_EXTS:
        return False, f"archivo de código ({ext})"
    try:
        with open(path, "rb") as fh:
            head = fh.read(_SNIFF)
    except OSError as e:
        return False, f"no se puede leer ({e.strerror or e})"
    if head.startswith(b"#!"):
        return False, "tiene shebang (#!)"
    if ext not in TEXT_EXTS and name not in TEXT_NAMES and ext != "":
        return False, f"tipo de archivo no textual ({ext})"
    if b"\x00" in head:
        return False, "contenido binario"
    try:
        head.decode("utf-8")
    except UnicodeDecodeError as e:
        # Un corte a mitad de un carácter multibyte al final no es binario.
        if e.start < len(head) - 4:
            return False, "no es texto UTF-8"
    return True, ""


def classify_skill(skill_dir: Path, name: str | None = None) -> dict:
    """Clasifica una carpeta de skill sin seguir symlinks ni ejecutar nada.

    Devuelve {"kind", "reasons": [str], "files": [rutas relativas],
    "bytes": n}. `kind` es SOLO_INSTRUCCIONES, CON_SCRIPTS o RECHAZADA.
    """
    skill_dir = Path(skill_dir)
    name = skill_dir.name if name is None else name
    rejected: list[str] = []
    scripts: list[str] = []
    files: list[str] = []
    total = 0

    if not valid_skill_name(name):
        rejected.append(f"nombre de skill inválido: {name!r}")
    if skill_dir.is_symlink():
        rejected.append("la carpeta de la skill es un enlace simbólico")
    if not (skill_dir / "SKILL.md").is_file() or (skill_dir / "SKILL.md").is_symlink():
        rejected.append("falta SKILL.md (o es un enlace simbólico)")
    if rejected:
        return {"kind": RECHAZADA, "reasons": rejected, "files": [], "bytes": 0}

    root = skill_dir.resolve()
    for dirpath, dirnames, filenames in os.walk(skill_dir, followlinks=False):
        dp = Path(dirpath)
        # .git dentro de la skill no se copia ni se evalúa
        dirnames[:] = sorted(d for d in dirnames if d != ".git")
        for d in list(dirnames):
            full = dp / d
            rel = full.relative_to(skill_dir).as_posix()
            if has_dotdot(rel):
                rejected.append(f"ruta con '..': {rel}")
            if full.is_symlink():
                dirnames.remove(d)
                target_inside = is_within(full, root)
                rejected.append(
                    f"enlace simbólico a directorio: {rel}"
                    + ("" if target_inside else " (sale de la carpeta)"))
        for f in sorted(filenames):
            full = dp / f
            rel = full.relative_to(skill_dir).as_posix()
            if has_dotdot(rel) or "\\" in f:
                rejected.append(f"ruta no válida: {rel}")
                continue
            if full.is_symlink():
                if not is_within(full, root):
                    rejected.append(f"enlace simbólico que sale de la carpeta: {rel}")
                    continue
                if not full.resolve().is_file():
                    rejected.append(f"enlace simbólico roto o especial: {rel}")
                    continue
            try:
                st = full.stat()  # sigue el enlace (ya validado como interno)
            except OSError:
                rejected.append(f"no se puede leer: {rel}")
                continue
            if not stat.S_ISREG(st.st_mode):
                rejected.append(f"archivo especial (no regular): {rel}")
                continue
            files.append(rel)
            total += st.st_size
            if st.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
                scripts.append(f"{rel}: tiene bit de ejecución")
                continue
            ok, why = _is_text_file(full)
            if not ok:
                scripts.append(f"{rel}: {why}")
        if len(files) > MAX_FILES:
            rejected.append(f"demasiados archivos (> {MAX_FILES})")
            break
    if total > MAX_BYTES:
        rejected.append(f"demasiado grande ({total} bytes > {MAX_BYTES})")

    if rejected:
        kind, reasons = RECHAZADA, rejected
    elif scripts:
        kind, reasons = CON_SCRIPTS, scripts
    else:
        kind, reasons = SOLO_INSTRUCCIONES, ["solo archivos de texto/markdown"]
    return {"kind": kind, "reasons": reasons, "files": files, "bytes": total}


def summarize_reasons(reasons: list[str], limit: int = 3) -> str:
    """Motivos en una línea legible."""
    head = "; ".join(reasons[:limit])
    extra = len(reasons) - limit
    return head + (f" (+{extra} más)" if extra > 0 else "")
