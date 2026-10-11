"""
Capa de conceptos (semántica ligera, sin modelos ni dependencias).

El baseline por palabras clave falla cuando el usuario y la skill usan sinónimos
distintos ("repasar para la prueba" vs "examen"). Aquí mapeamos familias de
términos a un concepto canónico; al puntuar, tanto el mensaje como las skills se
expanden con sus conceptos, así los sinónimos coinciden.

Es un recurso curado y explícito (no mágico): fácil de auditar y de ampliar.
Todos los términos van sin acentos y en minúscula (igual que el tokenizador).

Los verbos aparecen en infinitivo y también en imperativo ("revisa", "investiga",
"depura"...), porque así es como se le suelen pedir las cosas a un asistente.
"""

from __future__ import annotations

# concepto canónico -> términos de superficie que lo activan
CONCEPTS: dict[str, list[str]] = {
    "estudio": [
        "estudiar", "estudia", "estudio", "examen", "prueba", "parcial", "final",
        "repasar", "repasa", "repaso", "apuntes", "materia", "tarea", "deberes",
        "nota", "notas", "calificacion", "calificaciones", "flashcards", "fichas",
        "resumen", "resumenes", "simulacro", "rubrica", "aprender", "aprende",
        "temario", "test", "quiz", "universidad", "clase",
    ],
    "video": [
        "video", "videos", "reel", "reels", "short", "shorts", "tiktok", "guion",
        "guiones", "clip", "viral", "gancho", "escena", "escenas", "cta",
        "edicion", "grabar", "graba", "montaje", "youtube", "vertical",
    ],
    "marketing": [
        "marketing", "copy", "copywriting", "publicidad", "anuncio", "anuncios",
        "campana", "campanas", "seo", "conversion", "embudo", "growth",
        "posicionamiento", "branding", "marca", "email", "newsletter", "landing",
        "ventas", "cliente", "clientes",
    ],
    "redes": [
        "redes", "red", "social", "instagram", "facebook", "twitter", "linkedin",
        "publicacion", "publicaciones", "post", "posts", "seguidores",
        "engagement", "comunidad", "contenido",
    ],
    "revision_codigo": [
        "revisar", "revisa", "revisame", "revision", "review", "pull", "request",
        "pr", "prs", "diff", "bug", "bugs", "seguridad", "vulnerabilidad",
        "inyeccion", "rendimiento", "refactor", "calidad", "lint", "merge",
        "commit",
    ],
    "depuracion": [
        "debug", "depurar", "depura", "diagnosticar", "diagnostica", "reproducir",
        "reproduce", "stack", "trace", "traceback", "fallo", "crash", "excepcion",
        "error", "errores", "roto",
    ],
    "investigacion": [
        "investigar", "investiga", "investigacion", "mercado", "tendencias",
        "tendencia", "comparar", "compara", "comparativa", "fuentes", "informe",
        "reporte", "analisis", "analiza", "competencia", "benchmark", "estudio",
    ],
}

# término de superficie -> conceptos a los que pertenece (reverso, precomputado)
_TERM_TO_CONCEPTS: dict[str, list[str]] = {}
for _concept, _terms in CONCEPTS.items():
    for _t in _terms:
        _lst = _TERM_TO_CONCEPTS.setdefault(_t, [])
        if _concept not in _lst:  # un término repetido no duplica el concepto
            _lst.append(_concept)


def concepts_for(term: str) -> list[str]:
    """Conceptos canónicos a los que pertenece un término (vacío si ninguno)."""
    return _TERM_TO_CONCEPTS.get(term, [])


def expand_tokens(tokens: list[str]) -> list[str]:
    """Devuelve los tokens más un token 'concept:<x>' por cada concepto activado."""
    out = list(tokens)
    for t in tokens:
        for c in concepts_for(t):
            out.append(f"concept:{c}")
    return out
