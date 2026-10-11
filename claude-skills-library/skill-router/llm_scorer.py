"""
Scorer LLM: le pide a Claude que elija qué skills usar para un mensaje y que
justifique cada elección. Es el modo de "alta calidad" del Skill Router.

Backends, en orden de preferencia:
1. "sdk" — SDK oficial `anthropic` con ANTHROPIC_API_KEY.
2. "cli" — el CLI de Claude Code (`claude -p`), que usa la sesión ya autenticada
   (funciona en tu laptop con Claude Code instalado y logueado).
Si no hay ninguno, se lanza LLMUnavailable y el router cae al scorer léxico.

La salida tiene la misma forma que score_skills() para que el resto del router
(clasificación, impresión, benchmark) no cambie.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

DEFAULT_MODEL = os.environ.get("SKILL_ROUTER_MODEL", "claude-opus-5-5")
CLI_TIMEOUT_S = int(os.environ.get("SKILL_ROUTER_LLM_TIMEOUT", "120"))


class LLMUnavailable(RuntimeError):
    """No hay backend LLM disponible o la llamada falló."""


def available_backend() -> str | None:
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            import anthropic  # noqa: F401
            return "sdk"
        except ImportError:
            pass
    if shutil.which("claude"):
        return "cli"
    return None


def build_prompt(query: str, skills) -> str:
    catalog = "\n".join(
        f"- {s.name}: {s.description or '(sin descripción)'}" for s in skills
    )
    return (
        "Eres el router de skills de Claude Code. Dado el mensaje de un usuario y "
        "la lista de skills instaladas, decide cuáles conviene usar AHORA.\n\n"
        "Reglas:\n"
        "- Puntúa solo las skills realmente útiles para este mensaje (0-100).\n"
        "- Si ninguna aplica (saludos, charla, preguntas generales), devuelve "
        "recommended vacío.\n"
        "- Usa exactamente los nombres de la lista.\n"
        "- reason: una frase corta en español explicando por qué.\n\n"
        f"Skills instaladas:\n{catalog}\n\n"
        f"Mensaje del usuario: \"{query}\"\n\n"
        "Responde SOLO con JSON válido, sin texto extra, con esta forma:\n"
        '{"recommended": [{"name": "...", "score": 0, "reason": "..."}]}'
    )


def _call_sdk(prompt: str) -> str:
    import anthropic
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=DEFAULT_MODEL,
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


def _call_cli(prompt: str) -> str:
    try:
        proc = subprocess.run(
            ["claude", "-p", prompt, "--output-format", "text", "--model", DEFAULT_MODEL],
            capture_output=True, text=True, timeout=CLI_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise LLMUnavailable(f"fallo al invocar claude CLI: {e}") from e
    if proc.returncode != 0:
        raise LLMUnavailable(f"claude CLI salió con código {proc.returncode}: {proc.stderr.strip()[:200]}")
    return proc.stdout


def _extract_json(text: str) -> dict:
    """Extrae el primer objeto JSON del texto (tolera ```json ... ``` y ruido)."""
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise LLMUnavailable("la respuesta del LLM no contenía JSON")
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise LLMUnavailable(f"JSON inválido del LLM: {e}") from e


def llm_score(skills, query: str, backend: str | None = None) -> list[dict]:
    """Puntúa skills con un LLM. Devuelve la misma forma que score_skills()."""
    backend = backend or available_backend()
    if backend is None:
        raise LLMUnavailable("no hay ANTHROPIC_API_KEY+SDK ni CLI `claude` disponible")

    prompt = build_prompt(query, skills)
    raw = _call_sdk(prompt) if backend == "sdk" else _call_cli(prompt)
    data = _extract_json(raw)

    by_name = {s.name: s for s in skills}
    chosen: dict[str, dict] = {}
    for item in data.get("recommended", []) or []:
        name = str(item.get("name", "")).strip()
        if name not in by_name or name in chosen:
            continue  # ignora nombres inventados o duplicados
        try:
            score = max(0, min(100, int(round(float(item.get("score", 0))))))
        except (TypeError, ValueError):
            score = 0
        chosen[name] = {
            "name": name,
            "scenario": by_name[name].scenario,
            "score": score,
            "matched": [str(item.get("reason", "")).strip() or "elegida por el LLM"],
            "source": by_name[name].source,
        }

    results = list(chosen.values())
    for s in skills:  # el resto queda con 0 (ruido)
        if s.name not in chosen:
            results.append({"name": s.name, "scenario": s.scenario, "score": 0,
                            "matched": [], "source": s.source})
    results.sort(key=lambda r: (-r["score"], r["name"]))
    return results
