#!/usr/bin/env python3
"""
Skill Router (MVP) — recomienda qué skills de Claude Code conviene usar según el
mensaje/contexto del momento.

Dado (1) un conjunto de skills (leídas de ~/.claude/skills o de skills.json) y
(2) el mensaje del usuario, puntúa cada skill por relevancia, recomienda las mejores,
marca las que son "ruido" para ese turno y explica por qué.

Diseño honesto del MVP:
- Baseline transparente y sin dependencias: ponderación tipo IDF sobre
  nombre+descripción de cada skill. Explicable y reproducible.
- Punto de extensión claro para enchufar embeddings o un LLM como "scorer".

Uso:
    python3 skill_router.py "quiero preparar mi examen de cálculo"
    python3 skill_router.py --skills-dir ~/.claude/skills "revisa este PR"
    python3 skill_router.py --catalog ../skills.json "guion para un reel"
    python3 skill_router.py --json "..."     # salida JSON
    python3 skill_router.py --demo           # usa skills de ejemplo

Sin --skills-dir ni --catalog intenta ~/.claude/skills; si no tiene skills, usa el
skills.json vecino; si tampoco, cae a un set de demo.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from concepts import expand_tokens

# --- Stopwords ES/EN basicas (no exhaustivo, suficiente para el baseline) ----
STOPWORDS = set("""
a al algo ante antes como con contra de del desde donde durante e el ella ellas
ello ellos en entre era eran es esa ese eso esta este esto hacer hasta la las le
les lo los mas me mi mis mucho muy no nos o os para pero por porque que se sea ser
si sin sobre su sus te tu tus un una uno unos y ya
and are as at be but by for from has have in into is it its of on or that the this
to was were will with your you my me i we our
quiero necesito ayuda hazme dame crea crear
""".split())

# Tokens de 2 letras que sí informan (siglas técnicas). El resto de tokens de
# menos de 3 letras se descarta porque casi siempre son ruido ("de", "la", "mi").
SHORT_TOKENS = frozenset({
    "pr", "ia", "ai", "ui", "ux", "qa", "ci", "cd", "db", "bd", "js", "ts",
    "ml", "cv", "2d", "3d",
})


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def tokenize(text: str) -> list[str]:
    text = strip_accents((text or "").lower())
    tokens = re.findall(r"[a-z0-9]+", text)
    return [t for t in tokens
            if (len(t) > 2 or t in SHORT_TOKENS) and t not in STOPWORDS]


@dataclass
class Skill:
    name: str
    description: str
    scenario: str = ""
    source: str = ""
    terms: dict[str, int] = field(default_factory=dict)  # term -> frecuencia

    def build_terms(self, use_concepts: bool = True) -> None:
        name = self.name or ""
        # `or ""`: una skill sin descripción (None) no debe aportar el token "none"
        blob = f"{name} {name} {self.scenario or ''} {self.description or ''}"
        toks = tokenize(blob)
        if use_concepts:
            toks = expand_tokens(toks)
        self.terms = {}
        for t in toks:
            self.terms[t] = self.terms.get(t, 0) + 1


# --- Carga de skills ---------------------------------------------------------
_FM_KEY = re.compile(r"^([A-Za-z0-9_-]+)\s*:\s*(.*)$")
_FM_BLOCK = re.compile(r"^[|>][+-]?[0-9]?[+-]?\s*(#.*)?$")
_DESC_FALLBACK_MAX = 1024  # mismo límite que Claude Code para `description`


def _unquote(v: str) -> str:
    """Quita comillas YAML que envuelven todo el valor (sin tocar las internas)."""
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
        if v[0] == "'":
            return v[1:-1].replace("''", "'")
        try:
            out = json.loads(v)  # YAML con comillas dobles ~ string JSON
            if isinstance(out, str):
                return out
        except ValueError:
            pass
        return v[1:-1].replace('\\"', '"')
    # escalar plano: en YAML, " #" inicia un comentario
    return re.sub(r"\s+#.*$", "", v)


def _first_paragraph(lines: list[str]) -> str:
    """Primer párrafo de texto del cuerpo (sin títulos, código ni comentarios).

    Es lo que Claude Code usa como descripción cuando el frontmatter no la trae.
    """
    para: list[str] = []
    in_code = False
    for line in lines:
        s = line.strip()
        if s.startswith(("```", "~~~")):
            if para:
                break
            in_code = not in_code
            continue
        if in_code:
            continue
        if not s or s.startswith(("#", "<!--")) or s in ("---", "***", "___"):
            if para:
                break
            continue
        para.append(s)
    return " ".join(para)[:_DESC_FALLBACK_MAX]


def parse_frontmatter(skill_md: Path) -> tuple[str, str]:
    """Extrae name y description del frontmatter YAML sin dependencias.

    - Solo lee claves de primer nivel (una `name:` anidada en `metadata:` no pisa
      el nombre de la skill).
    - Soporta valores entre comillas, bloques `>`/`|` y escalares planos que
      continúan en líneas indentadas.
    - Sin `name` usa el nombre de la carpeta; sin `description` usa el primer
      párrafo del cuerpo (igual que Claude Code).
    - Tolera BOM, CRLF y bytes no UTF-8. Si no se puede leer, devuelve ("", "").
    """
    skill_md = Path(skill_md)
    try:
        text = skill_md.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return ("", "")
    lines = text.splitlines()
    dir_name = skill_md.parent.name
    name, desc = "", ""

    if lines and lines[0].strip() == "---":
        end = next((j for j in range(1, len(lines)) if lines[j].strip() in ("---", "...")), None)
        fm_lines = lines[1:end] if end is not None else lines[1:]
        body = lines[end + 1:] if end is not None else []
        i = 0
        while i < len(fm_lines):
            m = _FM_KEY.match(fm_lines[i])  # sin indentación = clave de primer nivel
            i += 1
            if not m:
                continue
            key, val = m.group(1).lower(), m.group(2).strip()
            cont: list[str] = []
            while i < len(fm_lines) and (fm_lines[i][:1] in (" ", "\t") or not fm_lines[i].strip()):
                cont.append(fm_lines[i].strip())
                i += 1
            if key not in ("name", "description"):
                continue
            if _FM_BLOCK.match(val):
                value = " ".join(x for x in cont if x)
            else:
                value = _unquote(" ".join([val] + [x for x in cont if x]).strip())
            value = " ".join(value.split())
            if key == "name":
                name = value
            else:
                desc = value
    else:
        body = lines

    if not desc:
        desc = _first_paragraph(body)
    return (name or dir_name, desc)


# Carpetas que nunca son skills instaladas: `_sources` es donde install.sh clona
# los repos; cada skill que contienen ya se enlaza arriba, en ~/.claude/skills/<x>.
_SKIP_DIRS = frozenset({"_sources", "node_modules", "__pycache__"})


def load_from_dir(skills_dir: Path, max_depth: int = 3) -> list[Skill]:
    """Carga las skills (carpetas con SKILL.md) que hay bajo `skills_dir`.

    - Busca hasta `max_depth` niveles: 1 es el layout estándar
      (~/.claude/skills/<skill>/SKILL.md); 3 cubre también colecciones anidadas
      como ~/.claude/skills/synced/<id>/<skill>/SKILL.md.
    - No entra dentro de una skill (sus subcarpetas son recursos, no skills).
    - Ignora carpetas ocultas y `_sources`: install.sh clona ahí los repos y enlaza
      cada skill arriba, así que leerla duplicaría skills (y sumaría las que no se
      enlazaron, que Claude Code tampoco carga). Si apuntas --skills-dir
      directamente a una carpeta dentro de _sources, esa sí se lee.
    - Dos enlaces a la misma carpeta real cuentan una sola vez.
    - Si `skills_dir` es en sí una skill (tiene SKILL.md), devuelve solo esa.
    """
    skills_dir = Path(skills_dir).expanduser()
    if not skills_dir.is_dir():
        return []
    skills: list[Skill] = []
    seen: set[Path] = set()

    def add(skill_dir: Path) -> None:
        skill_md = skill_dir / "SKILL.md"
        try:
            real = skill_md.resolve()
        except OSError:
            real = skill_md
        if real in seen:
            return
        seen.add(real)
        name, desc = parse_frontmatter(skill_md)
        skills.append(Skill(name=name or skill_dir.name, description=desc,
                            source=str(skill_dir)))

    if (skills_dir / "SKILL.md").is_file():
        add(skills_dir)
        return skills

    def walk(d: Path, depth: int) -> None:
        try:
            entries = sorted(d.iterdir(), key=lambda p: p.name)
        except OSError:
            return
        for entry in entries:
            if entry.name.startswith(".") or entry.name in _SKIP_DIRS:
                continue
            try:
                if not entry.is_dir():  # sigue enlaces simbólicos
                    continue
                if (entry / "SKILL.md").is_file():
                    add(entry)
                elif depth < max_depth:
                    walk(entry, depth + 1)
            except OSError:
                continue  # enlace roto o sin permisos: se salta

    walk(skills_dir, 1)
    return skills


SCENARIO_SEP = " · "


def load_from_catalog(catalog: Path) -> list[Skill]:
    """Carga los repos del catálogo skills.json como skills.

    Un mismo repo puede aparecer en varios escenarios (p.ej. anthropics-skills en
    "base" y en "estudio"). Se deduplica por nombre: una sola skill con todos sus
    escenarios (unidos por " · ") y sus descripciones distintas concatenadas. La
    fuente es la URL de la primera aparición.
    """
    try:
        data = json.loads(Path(catalog).read_text(encoding="utf-8"))
    except (OSError, ValueError):  # ValueError cubre JSON y UTF-8 inválidos
        return []
    scenarios = data.get("scenarios") if isinstance(data, dict) else None
    if not isinstance(scenarios, dict):
        return []

    merged: dict[str, dict] = {}  # nombre -> {titles, abouts, url}
    for key, sc in scenarios.items():
        if not isinstance(sc, dict):
            continue
        title = str(sc.get("title") or key).strip()
        repos = sc.get("repos") or []
        if not isinstance(repos, list):
            continue
        for repo in repos:
            if not isinstance(repo, dict):
                continue
            name = str(repo.get("name") or key).strip()
            about = " ".join(str(repo.get("about") or "").split())
            entry = merged.setdefault(name, {"titles": [], "abouts": [],
                                             "url": str(repo.get("url") or "catalog")})
            if title and title not in entry["titles"]:
                entry["titles"].append(title)
            if about and about not in entry["abouts"]:
                entry["abouts"].append(about)

    return [Skill(name=name,
                  description=" ".join(e["abouts"]),
                  scenario=SCENARIO_SEP.join(e["titles"]),
                  source=e["url"])
            for name, e in merged.items()]


def demo_skills() -> list[Skill]:
    return [
        Skill("estudio-a-medida", "material de estudio resumenes fichas flashcards examen simulacro repaso segun rubrica calificaciones", "Estudio"),
        Skill("marca-marketing", "copywriting posts redes campanas email landing seo conversion voz de marca", "Marketing"),
        Skill("guiones-video", "guion video corto reel tiktok short gancho escena texto en pantalla cta viral", "Video"),
        Skill("code-review", "revisar codigo bugs seguridad rendimiento pull request diff inyeccion errores", "Ingenieria"),
        Skill("deep-research", "investigacion multiples fuentes mercado comparar tendencias sintesis informe", "Investigacion"),
        Skill("debug", "reproducir aislar diagnosticar error stack trace fallo bug", "Ingenieria"),
    ]


# --- Scoring (baseline IDF) --------------------------------------------------
def score_skills(skills: list[Skill], query: str, use_concepts: bool = True) -> list[dict]:
    """Puntúa cada skill (0-100) contra el mensaje.

    score = 100 × IDF(términos del mensaje que la skill contiene) / IDF(todos los
    términos del mensaje). Si el mensaje no deja ningún término útil tras
    tokenizar (vacío, solo stopwords o signos), todas puntúan 0.
    """
    for s in skills:
        s.build_terms(use_concepts=use_concepts)
    n = max(1, len(skills))
    # document frequency por termino
    df: dict[str, int] = {}
    for s in skills:
        for t in s.terms:
            df[t] = df.get(t, 0) + 1

    def idf(t: str) -> float:
        return math.log((n + 1) / (df.get(t, 0) + 1)) + 1.0

    q_terms = tokenize(query)
    if use_concepts:
        q_terms = expand_tokens(q_terms)
    q_unique = list(dict.fromkeys(q_terms))
    total_idf = sum(idf(t) for t in q_unique)

    results = []
    for s in skills:
        matched = [t for t in q_unique if t in s.terms]
        score = round(100 * sum(idf(t) for t in matched) / total_idf) if total_idf else 0
        results.append({
            "name": s.name,
            "scenario": s.scenario,
            "score": score,
            "matched": matched,
            "source": s.source,
        })
    results.sort(key=lambda r: (-r["score"], r["name"]))
    return results


def detect_conflicts(skills: list[Skill], threshold: float = 0.35,
                     use_concepts: bool = True) -> list[dict]:
    """Detecta pares de skills que se solapan (perfiles de términos parecidos).

    Usa similitud de coseno sobre los vectores de términos+conceptos. Pares por
    encima del umbral son candidatos a redundancia/competencia: tener ambas activas
    confunde al router. Un mismo objeto Skill repetido en la lista no se compara
    consigo mismo.
    """
    for s in skills:
        s.build_terms(use_concepts=use_concepts)

    def cosine(a: dict[str, int], b: dict[str, int]) -> float:
        common = set(a) & set(b)
        if not common:
            return 0.0
        dot = sum(a[t] * b[t] for t in common)
        na = math.sqrt(sum(v * v for v in a.values()))
        nb = math.sqrt(sum(v * v for v in b.values()))
        return dot / (na * nb) if na and nb else 0.0

    pairs = []
    for i in range(len(skills)):
        for j in range(i + 1, len(skills)):
            if skills[i] is skills[j]:
                continue
            sim = cosine(skills[i].terms, skills[j].terms)
            if sim >= threshold:
                shared = sorted(set(skills[i].terms) & set(skills[j].terms))
                pairs.append({
                    "a": skills[i].name, "b": skills[j].name,
                    "similarity": round(sim, 2),
                    "shared": _pretty(shared),
                })
    pairs.sort(key=lambda p: -p["similarity"])
    return pairs


def _pretty(matched: list[str]) -> list[str]:
    """Convierte tokens de concepto a algo legible para el 'motivo'."""
    out = []
    for m in matched:
        if m.startswith("concept:"):
            out.append(f"~{m.split(':', 1)[1]}")
        else:
            out.append(m)
    return out


def classify(results: list[dict], top: int, threshold: int) -> tuple[list[dict], list[dict]]:
    """Separa recomendadas (score >= threshold, máx. `top`) del ruido (< threshold).

    Una skill con score 0 nunca se recomienda, aunque threshold sea 0: no coincidió
    en nada. Las que superan el umbral pero quedan fuera del top no son ni
    recomendadas ni ruido (son relevantes, solo que hay mejores).
    """
    top = max(0, int(top))
    recommended = [r for r in results if r["score"] > 0 and r["score"] >= threshold][:top]
    rec_ids = {id(r) for r in recommended}
    noise = [r for r in results
             if id(r) not in rec_ids and (r["score"] < threshold or r["score"] <= 0)]
    return recommended, noise


# --- CLI ---------------------------------------------------------------------
def _default_skills() -> tuple[list[Skill], str]:
    default_dir = Path.home() / ".claude" / "skills"
    default_catalog = Path(__file__).resolve().parent.parent / "skills.json"
    skills = load_from_dir(default_dir)
    if skills:
        return skills, f"dir:{default_dir}"
    if default_catalog.exists():
        skills = load_from_catalog(default_catalog)
        if skills:
            return skills, f"catalog:{default_catalog}"
    return demo_skills(), "demo"


def main(argv: list[str]) -> int:
    try:  # consolas no UTF-8 (p.ej. Windows cp1252): no reventar por los emojis
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

    p = argparse.ArgumentParser(description="Skill Router MVP: recomienda skills por contexto.")
    p.add_argument("query", nargs="*", help="mensaje/contexto del usuario")
    p.add_argument("--skills-dir", type=Path, help="carpeta de skills (SKILL.md por subcarpeta)")
    p.add_argument("--catalog", type=Path, help="skills.json del catalogo")
    p.add_argument("--demo", action="store_true", help="usa skills de ejemplo")
    p.add_argument("--no-concepts", action="store_true", help="apaga la capa de conceptos (baseline)")
    p.add_argument("--conflicts", action="store_true", help="lista skills que se solapan (sin query)")
    p.add_argument("--llm", action="store_true",
                   help="usa Claude como scorer (SDK con API key o CLI `claude`); si no hay, cae al léxico")
    p.add_argument("--top", type=int, default=3, help="max recomendadas (def 3)")
    p.add_argument("--threshold", type=int, default=15, help="score minimo para recomendar (def 15)")
    p.add_argument("--json", action="store_true", help="salida JSON")
    args = p.parse_args(argv)

    query = " ".join(args.query).strip()
    if not query and not args.conflicts:
        p.error("falta el mensaje. Ej: skill_router.py 'preparar examen de calculo'")
    if args.top < 1:
        p.error("--top debe ser 1 o más")

    # Resolucion de fuente de skills
    if args.demo:
        skills, src_label = demo_skills(), "demo"
    elif args.skills_dir:
        skills, src_label = load_from_dir(args.skills_dir), f"dir:{args.skills_dir}"
    elif args.catalog:
        skills, src_label = load_from_catalog(args.catalog), f"catalog:{args.catalog}"
    else:
        skills, src_label = _default_skills()

    if not skills:
        print("No encontre skills. Usa --demo, --skills-dir o --catalog.", file=sys.stderr)
        return 1

    if args.conflicts:
        pairs = detect_conflicts(skills, use_concepts=not args.no_concepts)
        if args.json:
            print(json.dumps({"source": src_label, "conflicts": pairs}, ensure_ascii=False, indent=2))
            return 0
        print(f"\n⚠️  Conflictos / solapamientos entre skills  ({src_label}, {len(skills)} skills)\n")
        if not pairs:
            print("   Ninguno por encima del umbral. 👍\n")
            return 0
        for p_ in pairs:
            print(f"   {p_['similarity']:.2f}  {p_['a']}  ⇄  {p_['b']}")
            print(f"         comparten: {', '.join(p_['shared'][:10])}")
        print("\n   Sugerencia: si dos compiten siempre para lo mismo, deja una activa.\n")
        return 0

    scorer = "léxico+conceptos" if not args.no_concepts else "léxico"
    results = None
    if args.llm:
        from llm_scorer import llm_score, LLMUnavailable, available_backend, last_call_info
        try:
            results = llm_score(skills, query)
            scorer = f"LLM ({last_call_info.get('backend') or available_backend()}"
            scorer += ", caché)" if last_call_info.get("cached") else ")"
        except LLMUnavailable as e:
            print(f"⚠️  Scorer LLM no disponible ({e}); uso el léxico.", file=sys.stderr)
    if results is None:
        results = score_skills(skills, query, use_concepts=not args.no_concepts)
    recommended, noise = classify(results, args.top, args.threshold)

    if args.json:
        print(json.dumps({
            "query": query,
            "source": src_label,
            "scorer": scorer,
            "recommended": recommended,
            "noise": noise,
        }, ensure_ascii=False, indent=2))
        return 0

    shown_query = " ".join(query.split())  # un mensaje multilínea se muestra en una línea
    print(f"\n🧭 Skill Router — contexto: \"{shown_query}\"")
    print(f"   fuente de skills: {src_label}  ({len(skills)} skills)  ·  scorer: {scorer}\n")
    if recommended:
        print("✅ Usa estas ahora:")
        for r in recommended:
            motivo = ", ".join(_pretty(r["matched"])) if r["matched"] else "coincidencia general"
            etq = f" [{r['scenario']}]" if r["scenario"] else ""
            print(f"   {r['score']:3d}%  {r['name']}{etq}")
            print(f"         motivo: {motivo}")
    else:
        print("🤔 Ninguna skill supera el umbral; quizas no necesitas skill para esto.")
    if noise:
        print("\n💤 Ruido para este turno (no las uses ahora):")
        print("   " + ", ".join(f"{r['name']} ({r['score']}%)" for r in noise))
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
