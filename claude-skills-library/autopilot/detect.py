"""C1. Perfil rápido de un proyecto (solo biblioteca estándar).

profile_project(root) -> {"root", "languages": {lang: n}, "frameworks": [...],
                          "signals": [...], "summary"}

Recorre el árbol ignorando directorios pesados u ocultos, con límites de
archivos, profundidad y tiempo, para quedarse siempre por debajo de 1 s.

Señales emitidas (las usa plan.py y casan con "signals" de skills.json):
  lang:<lenguaje>     hay código de ese lenguaje
  fw:<framework>      framework/herramienta detectada (maven, react, remotion...)
  file:<marcador>     archivo marcador presente (pom.xml, package.json...)
  kind:code           proyecto con código fuente
  kind:data           datos/ciencia de datos (csv, notebooks, pandas...)
  kind:documents      documentos ofimáticos (pdf, docx, pptx, xlsx)
  kind:content        mayoritariamente texto/markdown, poco código
  kind:skills-dev     el repo contiene skills propias (SKILL.md)
  topic:<tema>        marketing, social, study (por palabras clave; solo en
                      proyectos de contenido o sin código)
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

IGNORED_DIRS = {".git", "node_modules", "venv", ".venv", "build", "dist",
                "target", "__pycache__", ".hg", ".svn", ".idea", ".vscode",
                ".mypy_cache", ".pytest_cache", ".tox", ".next", ".gradle",
                "out", "coverage", "site-packages", "vendor",
                # datos de prueba: no describen el proyecto (y evitan que los
                # fixtures de este mismo paquete contaminen el perfil)
                "fixtures", "testdata", "__fixtures__"}
MAX_FILES = 4000      # archivos considerados como máximo
MAX_DEPTH = 8         # profundidad máxima bajo la raíz
TIME_BUDGET = 0.6     # segundos; se corta el recorrido al superarlo
MAX_TEXT_FILES = 12   # .md/.txt leídos para buscar temas
TEXT_BYTES = 4096     # bytes leídos de cada uno

EXT_LANG = {
    ".py": "python", ".java": "java", ".kt": "kotlin", ".kts": "kotlin",
    ".scala": "scala", ".groovy": "groovy", ".js": "javascript",
    ".mjs": "javascript", ".cjs": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".go": "go", ".rs": "rust",
    ".rb": "ruby", ".php": "php", ".cs": "csharp", ".c": "c", ".h": "c",
    ".cpp": "cpp", ".cc": "cpp", ".hpp": "cpp", ".swift": "swift",
    ".m": "objective-c", ".dart": "dart", ".lua": "lua", ".r": "r",
    ".sh": "shell", ".bash": "shell", ".ps1": "powershell", ".sql": "sql",
    ".vue": "vue", ".svelte": "svelte", ".ex": "elixir", ".exs": "elixir",
    ".ipynb": "python",
}
DATA_EXT = {".csv", ".tsv", ".parquet", ".ipynb", ".feather", ".arrow"}
DOC_EXT = {".pdf", ".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls", ".odt"}
TEXT_EXT = {".md", ".mdx", ".txt", ".rst"}

# Marcadores: nombre de archivo -> (frameworks, lenguaje implícito)
MARKERS = {
    "pom.xml": (["maven"], "java"),
    "build.gradle": (["gradle"], "java"),
    "build.gradle.kts": (["gradle"], "kotlin"),
    "package.json": (["node"], "javascript"),
    "requirements.txt": ([], "python"),
    "pyproject.toml": ([], "python"),
    "setup.py": ([], "python"),
    "Pipfile": ([], "python"),
    "environment.yml": (["conda"], "python"),
    "Cargo.toml": (["cargo"], "rust"),
    "go.mod": (["go-modules"], "go"),
    "Gemfile": (["bundler"], "ruby"),
    "composer.json": (["composer"], "php"),
    "Dockerfile": (["docker"], None),
    "docker-compose.yml": (["docker"], None),
    "remotion.config.ts": (["remotion"], "typescript"),
    "remotion.config.js": (["remotion"], "javascript"),
    "next.config.js": (["next"], "javascript"),
    "next.config.mjs": (["next"], "javascript"),
    "vite.config.ts": (["vite"], "typescript"),
    "vite.config.js": (["vite"], "javascript"),
    "angular.json": (["angular"], "typescript"),
    "manage.py": (["django"], "python"),
}
MANIFESTS = {"package.json", "requirements.txt", "pyproject.toml", "Pipfile",
             "environment.yml", "pom.xml", "build.gradle", "build.gradle.kts"}

# Dependencias -> framework
NODE_DEPS = {"react": "react", "next": "next", "vue": "vue", "svelte": "svelte",
             "@angular/core": "angular", "express": "express",
             "remotion": "remotion", "@remotion/cli": "remotion",
             "electron": "electron", "react-native": "react-native",
             "astro": "astro", "vite": "vite", "typescript": "typescript-tooling"}
PY_DEPS = {"pandas": "pandas", "numpy": "numpy", "scikit-learn": "scikit-learn",
           "sklearn": "scikit-learn", "torch": "pytorch",
           "tensorflow": "tensorflow", "matplotlib": "matplotlib",
           "jupyter": "jupyter", "polars": "polars", "django": "django",
           "flask": "flask", "fastapi": "fastapi", "streamlit": "streamlit",
           "seaborn": "seaborn", "scipy": "scipy", "openpyxl": "openpyxl"}
JVM_DEPS = {"spring-boot": "spring", "paper-api": "paper", "spigot-api": "spigot",
            "junit": "junit", "lombok": "lombok"}
DATA_FW = {"pandas", "numpy", "scikit-learn", "pytorch", "tensorflow",
           "matplotlib", "jupyter", "polars", "seaborn", "scipy", "streamlit"}

# Temas por palabras clave (en nombres de archivo y en texto de .md/.txt)
TOPICS = {
    "marketing": ["marketing", "seo", "copywriting", "campaña", "campana",
                  "campaign", "landing", "newsletter", "conversion",
                  "conversión", "branding", "marca", "funnel", "embudo",
                  "lead", "anuncio", "ads"],
    "social": ["instagram", "tiktok", "reels", "reel", "shorts", "linkedin",
               "redes sociales", "social media", "hashtag", "youtube"],
    "study": ["apuntes", "temario", "examen", "exam", "asignatura", "lecture",
              "syllabus", "flashcards", "estudio", "tema1", "tema 1", "curso"],
}
TOPIC_MIN_HITS = 3  # apariciones mínimas para emitir topic:<tema>
_WORD_RE = {t: re.compile(r"(?<![a-z0-9áéíóúñ])(" + "|".join(re.escape(w) for w in ws)
                          + r")(?![a-z0-9áéíóúñ])")
            for t, ws in TOPICS.items()}

LANG_LABEL = {"python": "Python", "java": "Java", "javascript": "JavaScript",
              "typescript": "TypeScript", "kotlin": "Kotlin", "go": "Go",
              "rust": "Rust", "ruby": "Ruby", "php": "PHP", "csharp": "C#",
              "cpp": "C++", "shell": "Shell"}
NON_PRIMARY_LANGS = {"shell", "powershell", "sql"}  # no definen el tipo de proyecto


def _read_head(path: str, limit: int = TEXT_BYTES) -> str:
    try:
        with open(path, "rb") as fh:
            return fh.read(limit).decode("utf-8", "replace")
    except OSError:
        return ""


def _deps_from_manifest(name: str, path: str) -> set[str]:
    text = _read_head(path, 65536)
    found: set[str] = set()
    if name == "package.json":
        try:
            data = json.loads(text)
        except ValueError:
            data = {}
        deps: set[str] = set()
        if isinstance(data, dict):
            for key in ("dependencies", "devDependencies", "peerDependencies"):
                if isinstance(data.get(key), dict):
                    deps.update(data[key])
        for dep, fw in NODE_DEPS.items():
            if dep in deps:
                found.add(fw)
        if any(d.startswith("@remotion/") for d in deps):
            found.add("remotion")
        return found
    low = text.lower()
    table = JVM_DEPS if name in ("pom.xml", "build.gradle", "build.gradle.kts") else PY_DEPS
    for dep, fw in table.items():
        if re.search(r"(?<![a-z0-9_-])" + re.escape(dep) + r"(?![a-z0-9_])", low):
            found.add(fw)
    return found


def profile_project(root) -> dict:
    start = time.monotonic()
    root_path = Path(root).expanduser().resolve()
    languages: dict[str, int] = {}
    frameworks: set[str] = set()
    markers: set[str] = set()
    counts = {"data": 0, "docs": 0, "text": 0, "skill_md": 0, "files": 0}
    topic_hits = {t: 0 for t in TOPICS}
    text_files: list[str] = []
    truncated = False

    stack = [(str(root_path), 0)] if root_path.is_dir() else []
    while stack:
        if counts["files"] >= MAX_FILES or time.monotonic() - start > TIME_BUDGET:
            truncated = True
            break
        current, depth = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            name = entry.name
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError:
                continue
            if is_dir:
                if name in IGNORED_DIRS or name.startswith(".") or name.endswith(".egg-info"):
                    continue
                if depth + 1 <= MAX_DEPTH:
                    stack.append((entry.path, depth + 1))
                low_dir = name.lower()
                for t, rx in _WORD_RE.items():
                    if rx.search(low_dir.replace("-", " ").replace("_", " ")):
                        topic_hits[t] += 1
                continue
            if not entry.is_file(follow_symlinks=False):
                continue
            counts["files"] += 1
            if counts["files"] > MAX_FILES:
                truncated = True
                break
            ext = os.path.splitext(name)[1].lower()
            lang = EXT_LANG.get(ext)
            if lang:
                languages[lang] = languages.get(lang, 0) + 1
            if ext in DATA_EXT:
                counts["data"] += 1
            elif ext in DOC_EXT:
                counts["docs"] += 1
            elif ext in TEXT_EXT:
                counts["text"] += 1
                if name == "SKILL.md":
                    counts["skill_md"] += 1
                elif len(text_files) < MAX_TEXT_FILES:
                    text_files.append(entry.path)
            if name in MARKERS:
                markers.add(name)
                fws, mlang = MARKERS[name]
                frameworks.update(fws)
                if mlang and mlang not in languages:
                    languages[mlang] = 0  # marcador sin fuentes aún
                if name in MANIFESTS:
                    frameworks.update(_deps_from_manifest(name, entry.path))
            stem = os.path.splitext(name)[0].lower().replace("-", " ").replace("_", " ")
            for t, rx in _WORD_RE.items():
                if rx.search(stem):
                    topic_hits[t] += 1

    # Temas en el texto de unos pocos .md/.txt (README primero).
    text_files.sort(key=lambda p: (0 if os.path.basename(p).lower().startswith("readme") else 1, p))
    for path in text_files:
        if time.monotonic() - start > TIME_BUDGET + 0.2:
            truncated = True
            break
        low = _read_head(path).lower()
        for t, rx in _WORD_RE.items():
            topic_hits[t] += len(rx.findall(low))

    languages = {k: v for k, v in languages.items() if v > 0 or k in
                 {MARKERS[m][1] for m in markers}}
    signals: set[str] = set()
    for lang in languages:
        signals.add("lang:" + lang)
    for fw in frameworks:
        signals.add("fw:" + fw)
    for m in markers:
        signals.add("file:" + m)

    code_files = sum(n for l, n in languages.items() if l not in NON_PRIMARY_LANGS)
    has_build = bool(markers & set(MANIFESTS) | markers & {"Cargo.toml", "go.mod", "Gemfile", "composer.json"})
    if code_files >= 1 or has_build:
        signals.add("kind:code")
    if counts["data"] >= 2 or (frameworks & DATA_FW):
        signals.add("kind:data")
    if counts["docs"] >= 2:
        signals.add("kind:documents")
    if counts["text"] + counts["docs"] >= 3 and counts["text"] + counts["docs"] > code_files:
        signals.add("kind:content")
    if counts["skill_md"] >= 1:
        signals.add("kind:skills-dev")
    # Los temas solo cuentan en proyectos de contenido (o sin código): en un
    # repo de código, que el README hable de "marketing" no lo convierte en
    # un proyecto de marketing.
    topics_apply = "kind:content" in signals or "kind:code" not in signals
    for t, hits in topic_hits.items():
        if topics_apply and hits >= TOPIC_MIN_HITS:
            signals.add("topic:" + t)
    if truncated:
        signals.add("scan:truncated")

    profile = {
        "root": str(root_path),
        "languages": dict(sorted(languages.items(), key=lambda kv: (-kv[1], kv[0]))),
        "frameworks": sorted(frameworks),
        "signals": sorted(signals),
        "summary": "",
        "files_scanned": min(counts["files"], MAX_FILES),
        "elapsed_s": round(time.monotonic() - start, 3),
    }
    profile["summary"] = _summary(profile)
    return profile


def _summary(p: dict) -> str:
    if not p["signals"] or p["files_scanned"] == 0:
        return "Proyecto vacío o sin señales reconocibles."
    parts = []
    langs = [l for l, n in p["languages"].items() if n > 0 and l not in NON_PRIMARY_LANGS]
    if langs:
        parts.append("código " + ", ".join(
            f"{LANG_LABEL.get(l, l)} ({p['languages'][l]})" for l in langs[:3]))
    fws = [f for f in p["frameworks"] if f not in ("node", "docker", "typescript-tooling")]
    if fws:
        parts.append("con " + ", ".join(fws[:5]))
    kinds = {"kind:data": "datos", "kind:documents": "documentos",
             "kind:content": "contenido/texto", "kind:skills-dev": "skills propias"}
    extra = [v for k, v in kinds.items() if k in p["signals"]]
    topics = {"topic:marketing": "marketing", "topic:social": "redes sociales",
              "topic:study": "estudio"}
    extra += ["tema " + v for k, v in topics.items() if k in p["signals"]]
    if extra:
        parts.extend(extra)
    text = "Proyecto: " + "; ".join(parts) if parts else "Proyecto con pocas señales."
    if "scan:truncated" in p["signals"]:
        text += " (escaneo parcial: proyecto grande)"
    return text + "."


if __name__ == "__main__":
    import sys
    print(json.dumps(profile_project(sys.argv[1] if len(sys.argv) > 1 else "."),
                     ensure_ascii=False, indent=2))
