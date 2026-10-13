"""C2. Plan determinista: qué repos del catálogo curado instalar en un proyecto.

make_plan(profile, catalog_path, max_repos=3) -> {
    "scenarios": [...], "repos": [{"name", "url", "scenario", "trust", "reason",
                                   "skills"?}],
    "skipped": [{"name", "reason"}], "token_budget_note"}

Reglas:
- Solo repos de skills.json (catálogo curado). Nada fuera de él.
- Cada escenario declara "signals": {señal: peso}. Puntuación = suma de pesos
  de las señales presentes en el perfil (signals + lang:/fw: derivados).
- Entra un escenario si puntúa >= MIN_SCORE y >= RELATIVE_CUT * mejor puntuación
  (así una señal genérica como kind:code no arrastra repos en un proyecto
  claramente de vídeo, p.ej.).
- Menos repos antes que más: un repo por escenario (el de mayor confianza),
  se descartan colecciones grandes (tag "coleccion-grande"), se deduplica por
  URL y se corta en max_repos.
- Cada repo del catálogo declara "skills": la lista acotada de skills del
  escenario; se copia a la entrada del plan (install.py solo instala esas).
  Lista vacía -> el repo no se instala (va a skipped), nunca el repo entero.
  Si dos escenarios eligen el mismo repo, sus listas se unen.
- Sin señales -> plan vacío, nunca "todo".
"""
from __future__ import annotations

import json
from pathlib import Path

MIN_SCORE = 2
RELATIVE_CUT = 0.6
BIG_COLLECTION_TAG = "coleccion-grande"
TRUST_ORDER = {"oficial": 0, "comunidad-recomendada": 2, "comunidad-popular": 3,
               "comunidad": 4}


def trust_rank(trust: str) -> int:
    trust = str(trust or "")
    if trust in TRUST_ORDER:
        return TRUST_ORDER[trust]
    if trust.startswith("oficial"):  # p.ej. "oficial-remotion"
        return 1
    return 5


def _profile_signals(profile: dict) -> set[str]:
    sig = set(profile.get("signals") or [])
    sig.update("lang:" + l for l in (profile.get("languages") or {}))
    sig.update("fw:" + f for f in (profile.get("frameworks") or []))
    return sig


def _load_scenarios(catalog_path) -> dict:
    data = json.loads(Path(catalog_path).expanduser().read_text(encoding="utf-8"))
    scenarios = data.get("scenarios") if isinstance(data, dict) else None
    return scenarios if isinstance(scenarios, dict) else {}


def make_plan(profile: dict, catalog_path, max_repos: int = 3) -> dict:
    scenarios = _load_scenarios(catalog_path)
    present = _profile_signals(profile)

    scored = []  # (score, key, matched)
    for key, sc in scenarios.items():
        if not isinstance(sc, dict):
            continue
        weights = sc.get("signals") or {}
        if not isinstance(weights, dict):
            continue
        matched = sorted(s for s in weights if s in present and _num(weights[s]) > 0)
        score = sum(_num(weights[s]) for s in matched)
        if score > 0:
            scored.append((score, key, matched))
    scored.sort(key=lambda t: (-t[0], t[1]))
    top = scored[0][0] if scored else 0
    cut = max(MIN_SCORE, RELATIVE_CUT * top)
    chosen = [t for t in scored if t[0] >= cut]

    repos: list[dict] = []
    skipped: list[dict] = []
    seen_urls: dict[str, str] = {}
    repos_by_url: dict[str, dict] = {}
    for score, key, matched in chosen:
        sc = scenarios[key]
        candidates = [r for r in (sc.get("repos") or [])
                      if isinstance(r, dict) and r.get("name") and r.get("url")]
        if not candidates:
            skipped.append({"name": key, "reason": "el escenario no tiene repos instalables (solo pasos manuales)"})
            continue
        candidates.sort(key=lambda r: (BIG_COLLECTION_TAG in (r.get("tags") or []),
                                       trust_rank(r.get("trust")), str(r["name"])))
        picked = False
        for r in candidates:
            name, url = str(r["name"]), str(r["url"])
            if BIG_COLLECTION_TAG in (r.get("tags") or []):
                skipped.append({"name": name, "reason": "colección grande: demasiadas skills para instalar automáticamente (instálala a mano si la quieres)"})
                continue
            if picked:
                skipped.append({"name": name, "reason": f"alternativa en '{key}': ya se eligió una opción de igual o mayor confianza"})
                continue
            only = _skill_list(r)
            if only == []:
                # Lista vacía = ninguna skill segura y relevante: no se instala el
                # repo entero (eso metería skills ajenas al escenario).
                skipped.append({"name": name, "reason": f"sin skills seguras y relevantes para '{key}' (lista \"skills\" vacía en el catálogo): no se instala el repo entero"})
                continue
            picked = True
            if url in seen_urls:
                prev = repos_by_url[url]
                extra = [x for x in (only or []) if x not in prev.get("skills", [])]
                if "skills" in prev and extra:
                    prev["skills"] = prev["skills"] + extra
                    skipped.append({"name": name, "reason": f"mismo repo que '{seen_urls[url]}', ya incluido; se añaden sus skills: {', '.join(extra)}"})
                else:
                    skipped.append({"name": name, "reason": f"mismo repo que '{seen_urls[url]}', ya incluido"})
                continue
            if len(repos) >= max_repos:
                skipped.append({"name": name, "reason": f"límite de {max_repos} repos alcanzado (menos repos = menos tokens)"})
                continue
            seen_urls[url] = name
            entry = {"name": name, "url": url, "scenario": key,
                     "trust": str(r.get("trust") or "desconocido"),
                     "reason": f"escenario '{sc.get('title') or key}' (puntuación {_fmt(score)}; señales: {', '.join(matched)})"}
            if only is not None:
                entry["skills"] = list(only)
            repos_by_url[url] = entry
            repos.append(entry)

    if repos:
        note = (f"{len(repos)} repo(s) de {len(scenarios)} escenarios del catálogo. Cada skill instalada "
                "añade su nombre y descripción al contexto en cada turno; por eso se eligen pocos repos "
                "y la instalación limita el número de skills. La estimación real de tokens la da la "
                "instalación (token_estimate).")
    elif chosen:
        note = ("Hay escenarios que encajan, pero ninguno tiene skills seguras y relevantes que "
                "instalar automáticamente (ver skipped): no se instala nada (0 tokens extra).")
    else:
        note = ("Sin señales suficientes: no se instala nada (0 tokens extra). Puedes elegir "
                "escenarios a mano con install.sh.")
    return {"scenarios": [k for _, k, _ in chosen],
            "scenario_scores": {k: (int(s) if float(s).is_integer() else s) for s, k, _ in scored},
            "repos": repos, "skipped": skipped, "token_budget_note": note}


def _skill_list(repo: dict):
    """Lista "skills" del catálogo, limpia y sin duplicados (orden del catálogo).
    None si el repo no la declara (catálogos antiguos: se instala lo que pase
    los filtros de seguridad); [] si la declara vacía o sin nombres válidos."""
    raw = repo.get("skills")
    if not isinstance(raw, list):
        return None
    out: list[str] = []
    for x in raw:
        if isinstance(x, str) and x.strip() and x.strip() not in out:
            out.append(x.strip())
    return out


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _fmt(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else f"{x:g}"


if __name__ == "__main__":
    import sys
    from .detect import profile_project
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    catalog = Path(__file__).resolve().parent.parent / "skills.json"
    print(json.dumps(make_plan(profile_project(root), catalog), ensure_ascii=False, indent=2))
