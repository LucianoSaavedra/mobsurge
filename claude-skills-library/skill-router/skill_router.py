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

Sin --skills-dir ni --catalog intenta ~/.claude/skills; si no existe, usa el
skills.json vecino; si tampoco, cae a un set de demo.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

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


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def tokenize(text: str) -> list[str]:
    text = strip_accents(text.lower())
    tokens = re.findall(r"[a-z0-9]+", text)
    return [t for t in tokens if len(t) > 2 and t not in STOPWORDS]


@dataclass
class Skill:
    name: str
    description: str
    scenario: str = ""
    source: str = ""
    terms: dict[str, int] = field(default_factory=dict)  # term -> frecuencia

    def build_terms(self) -> None:
        blob = f"{self.name} {self.name} {self.scenario} {self.description}"
        self.terms = {}
        for t in tokenize(blob):
            self.terms[t] = self.terms.get(t, 0) + 1


# --- Carga de skills ---------------------------------------------------------
def parse_frontmatter(skill_md: Path) -> tuple[str, str]:
    """Extrae name y description del frontmatter YAML sin dependencias."""
    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError:
        return ("", "")
    if not text.startswith("---"):
        return (skill_md.parent.name, "")
    end = text.find("\n---", 3)
    fm = text[3:end] if end != -1 else text[3:]
    name, desc = skill_md.parent.name, ""
    # description puede ser multilinea con ">-"
    lines = fm.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r"\s*name\s*:\s*(.+)", line)
        if m:
            name = m.group(1).strip().strip("'\"")
        m = re.match(r"\s*description\s*:\s*(.*)", line)
        if m:
            val = m.group(1).strip()
            if val in (">", ">-", "|", "|-", ""):
                block = []
                i += 1
                while i < len(lines) and (lines[i].startswith((" ", "\t")) or not lines[i].strip()):
                    block.append(lines[i].strip())
                    i += 1
                desc = " ".join(x for x in block if x)
                continue
            else:
                desc = val.strip("'\"")
        i += 1
    return (name, desc)


def load_from_dir(skills_dir: Path) -> list[Skill]:
    skills = []
    if not skills_dir.is_dir():
        return skills
    for skill_md in sorted(skills_dir.glob("*/SKILL.md")):
        if "_sources" in skill_md.parts:
            # evita duplicar: las de _sources se enlazan arriba
            pass
        name, desc = parse_frontmatter(skill_md)
        skills.append(Skill(name=name, description=desc, source=str(skill_md.parent)))
    return skills


def load_from_catalog(catalog: Path) -> list[Skill]:
    skills = []
    try:
        data = json.loads(catalog.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return skills
    for key, sc in data.get("scenarios", {}).items():
        title = sc.get("title", key)
        for repo in sc.get("repos", []):
            skills.append(Skill(
                name=repo.get("name", key),
                description=repo.get("about", ""),
                scenario=title,
                source=repo.get("url", "catalog"),
            ))
    return skills


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
def score_skills(skills: list[Skill], query: str) -> list[dict]:
    for s in skills:
        if not s.terms:
            s.build_terms()
    n = max(1, len(skills))
    # document frequency por termino
    df: dict[str, int] = {}
    for s in skills:
        for t in s.terms:
            df[t] = df.get(t, 0) + 1

    def idf(t: str) -> float:
        return math.log((n + 1) / (df.get(t, 0) + 1)) + 1.0

    q_terms = tokenize(query)
    q_unique = list(dict.fromkeys(q_terms))
    total_idf = sum(idf(t) for t in q_unique) or 1.0

    results = []
    for s in skills:
        matched = [t for t in q_unique if t in s.terms]
        gained = sum(idf(t) for t in matched)
        score = round(100 * gained / total_idf)
        results.append({
            "name": s.name,
            "scenario": s.scenario,
            "score": score,
            "matched": matched,
            "source": s.source,
        })
    results.sort(key=lambda r: (-r["score"], r["name"]))
    return results


def classify(results: list[dict], top: int, threshold: int) -> tuple[list[dict], list[dict]]:
    recommended = [r for r in results if r["score"] >= threshold][:top]
    rec_names = {r["name"] for r in recommended}
    noise = [r for r in results if r["name"] not in rec_names and r["score"] < threshold]
    return recommended, noise


# --- CLI ---------------------------------------------------------------------
def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Skill Router MVP: recomienda skills por contexto.")
    p.add_argument("query", nargs="*", help="mensaje/contexto del usuario")
    p.add_argument("--skills-dir", type=Path, help="carpeta de skills (SKILL.md por subcarpeta)")
    p.add_argument("--catalog", type=Path, help="skills.json del catalogo")
    p.add_argument("--demo", action="store_true", help="usa skills de ejemplo")
    p.add_argument("--top", type=int, default=3, help="max recomendadas (def 3)")
    p.add_argument("--threshold", type=int, default=15, help="score minimo para recomendar (def 15)")
    p.add_argument("--json", action="store_true", help="salida JSON")
    args = p.parse_args(argv)

    query = " ".join(args.query).strip()
    if not query:
        p.error("falta el mensaje. Ej: skill_router.py 'preparar examen de calculo'")

    # Resolucion de fuente de skills
    skills: list[Skill] = []
    src_label = ""
    if args.demo:
        skills, src_label = demo_skills(), "demo"
    elif args.skills_dir:
        skills, src_label = load_from_dir(args.skills_dir), f"dir:{args.skills_dir}"
    elif args.catalog:
        skills, src_label = load_from_catalog(args.catalog), f"catalog:{args.catalog}"
    else:
        default_dir = Path.home() / ".claude" / "skills"
        default_catalog = Path(__file__).resolve().parent.parent / "skills.json"
        if default_dir.is_dir() and any(default_dir.glob("*/SKILL.md")):
            skills, src_label = load_from_dir(default_dir), f"dir:{default_dir}"
        elif default_catalog.exists():
            skills, src_label = load_from_catalog(default_catalog), f"catalog:{default_catalog}"
        else:
            skills, src_label = demo_skills(), "demo"

    if not skills:
        print("No encontre skills. Usa --demo, --skills-dir o --catalog.", file=sys.stderr)
        return 1

    results = score_skills(skills, query)
    recommended, noise = classify(results, args.top, args.threshold)

    if args.json:
        print(json.dumps({
            "query": query,
            "source": src_label,
            "recommended": recommended,
            "noise": noise,
        }, ensure_ascii=False, indent=2))
        return 0

    print(f"\n🧭 Skill Router — contexto: \"{query}\"")
    print(f"   fuente de skills: {src_label}  ({len(skills)} skills)\n")
    if recommended:
        print("✅ Usa estas ahora:")
        for r in recommended:
            motivo = ", ".join(r["matched"]) if r["matched"] else "coincidencia general"
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
