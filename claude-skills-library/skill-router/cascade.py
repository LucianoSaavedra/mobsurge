"""
Cascada léxico → LLM: llama al LLM solo cuando el léxico no basta.

Cada llamada al LLM gasta cuota de tu plan (o de tu API key) y tarda segundos; el
scorer léxico+conceptos es gratis y tarda milisegundos. La cascada puntúa primero
con el léxico y solo pide ayuda al LLM cuando la decisión léxica no es fiable:

1. Léxico seguro de una skill: el #1 tiene score >= high Y le saca al #2 al menos
   `margin` puntos → se devuelve el resultado léxico, sin LLM.
2. Léxico seguro de que "nada aplica": ninguna skill coincide en nada (score 0) Y el
   mensaje tiene como mucho `short` términos útiles ("ok, sigue", "gracias",
   "qué hora es") → se devuelve el resultado léxico (todo a 0), sin LLM.
   OJO: un score 0 a secas NO basta. Con skills descritas en inglés y mensajes en
   español ("cancela mi suscripción", "hoja de cálculo con las ventas") el léxico
   da 0 aunque haya una skill clarísima; esos casos son justo para el LLM.
3. Cualquier otro caso → llm_scorer.llm_score().

Cómo se eligieron los valores por defecto (high=60, margin=20, short=2)
-----------------------------------------------------------------------
Nunca con bench/heldout.json (es el set independiente). Se usaron:
- el set inline de eval.py (19 casos, 6 skills de demo), y
- 60 casos propios (30 directos en inglés, 15 en español, 15 negativos o de
  seguimiento de conversación) sobre las 62 skills reales instaladas en la máquina
  de desarrollo (~/.claude/skills, /mnt/skills y plugins), con descripciones
  reales en inglés.
Se barrió high ∈ {35, 40, 50, 60, 70} × margin ∈ {10, 20, 30} × cuatro reglas de
"nada aplica" (nunca, score 0, score 0 con ≤1 término, score 0 con ≤2 términos),
midiendo cuántas llamadas se evitan y cuántas de las decisiones tomadas sin LLM son
erróneas. Resultado con high=60, margin=20, short=2 (sobre el léxico de entonces):
- inline: evita 8 de 19 llamadas (42 %), 0 errores;
- propios: evita 31 de 60 llamadas (52 %), 0 errores.
high 50 o menos ya mete errores en el set propio (p. ej. "build an MCP server"
→ product-self-knowledge con 57 puntos); margin 10 y 20 dan lo mismo y se eligió
20 por prudencia; "score 0" sin limitar la longitud del mensaje evita más llamadas
(72 %) pero comete 9 errores (todos los casos en español).
Si cambia la forma de puntuar del léxico, hay que repetir este barrido:
lexical_decision() es una función pura pensada para eso.
"""

from __future__ import annotations

import time

import skill_router as sr

DEFAULT_HIGH = 60
DEFAULT_MARGIN = 20
DEFAULT_SHORT = 2  # términos útiles máximos de un mensaje "sin nada que enrutar"

# Claves de `usage` (las mismas que llm_scorer.llm_score más las de la cascada).
_LLM_USAGE_KEYS = ("backend", "model", "input_tokens", "output_tokens", "cache_read_tokens",
                   "cost_usd", "latency_s", "cached")


def _content_terms(query: str) -> int:
    """Términos útiles distintos del mensaje (tras quitar lo pegado, si se puede)."""
    normalize = getattr(sr, "normalize_query", None)
    text = normalize(query) if callable(normalize) else query
    return len(set(sr.tokenize(text)))


def lexical_decision(results: list[dict], n_terms: int, high: int = DEFAULT_HIGH,
                     margin: int = DEFAULT_MARGIN, short: int = DEFAULT_SHORT) -> str:
    """Decide con el resultado léxico (ordenado) si hace falta el LLM.

    Devuelve "lexical" (el léxico basta: hay una skill clara), "none" (el léxico
    basta: no aplica ninguna) o "llm" (hay que preguntar al LLM).
    """
    top = results[0]["score"] if results else 0
    second = results[1]["score"] if len(results) > 1 else 0
    if top >= high and top - second >= margin:
        return "lexical"
    if top <= 0 and n_terms <= short:
        return "none"
    return "llm"


def cascade_score(skills, query: str, high: int = DEFAULT_HIGH, margin: int = DEFAULT_MARGIN,
                  model: str | None = None, usage: dict | None = None, *,
                  short: int = DEFAULT_SHORT, backend: str | None = None,
                  use_concepts: bool = True, fallback: bool = True) -> tuple[list[dict], bool]:
    """Puntúa con el léxico y, solo si no es fiable, con el LLM.

    Devuelve (resultados, used_llm). Los resultados tienen la forma de
    score_skills(). used_llm=True si se usó la respuesta del LLM (aunque viniera de
    SKILL_ROUTER_CACHE; mira usage["cached"]).

    usage: si es un dict, se rellena con las claves de llm_score (backend, model,
    input_tokens, output_tokens, cache_read_tokens, cost_usd, latency_s, cached) más
    "decision" ("lexical", "none", "llm" o "llm_error"). Sin LLM, tokens y coste
    valen 0. latency_s es el tiempo total de la cascada.
    fallback: si el LLM no está disponible o falla, devuelve el resultado léxico
    (decision="llm_error", usage["error"] con el motivo) en vez de lanzar
    LLMUnavailable.
    """
    t0 = time.monotonic()
    info: dict = dict.fromkeys(_LLM_USAGE_KEYS)
    info.update(input_tokens=0, output_tokens=0, cache_read_tokens=0, cost_usd=0.0,
                cached=False)
    try:
        lexical = sr.score_skills(skills, query, use_concepts=use_concepts)
        decision = lexical_decision(lexical, _content_terms(query), high, margin, short)
        info["decision"] = decision
        if decision != "llm":
            return lexical, False

        import llm_scorer  # tardío: el camino barato no lo necesita
        llm_usage: dict = {}
        try:
            results = llm_scorer.llm_score(skills, query, backend=backend, model=model,
                                           usage=llm_usage)
        except llm_scorer.LLMUnavailable as e:
            info.update({k: llm_usage.get(k, info.get(k)) for k in _LLM_USAGE_KEYS})
            info["decision"] = "llm_error"
            info["error"] = str(e)
            if not fallback:
                raise
            return lexical, False
        info.update({k: llm_usage.get(k) for k in _LLM_USAGE_KEYS})
        return results, True
    finally:
        info["latency_s"] = round(time.monotonic() - t0, 3)
        if isinstance(usage, dict):
            usage.update(info)
