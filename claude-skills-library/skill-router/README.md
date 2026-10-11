# 🧭 Skill Router (MVP)

Una IA que, según el **contexto del chat**, decide **qué skills de Claude Code conviene
usar en ese momento** — las rankea con puntuación y motivo, y marca las que son ruido.

Es el prototipo del proyecto descrito en [`../IDEA-SKILL-ROUTER.md`](../IDEA-SKILL-ROUTER.md).

## Por qué

Cuando tienes muchas skills instaladas, el modelo se satura y elige mal (más skills =
peor output). Falta un componente que **razone, puntúe y explique** qué usar para *este*
mensaje, y que avise de lo que sobra.

## Qué hace hoy (MVP honesto)

- Lee tus skills desde `~/.claude/skills/` (frontmatter de cada `SKILL.md`) o desde el
  catálogo `skills.json`.
- Dado tu mensaje, **puntúa cada skill** (0–100) por relevancia.
- **Recomienda** las mejores, marca el **ruido** y **explica** el motivo (términos que
  coincidieron).
- Sin dependencias (solo Python 3 estándar) → corre en cualquier laptop.

## Uso

```bash
cd skill-router

python3 skill_router.py --demo "quiero preparar mi examen de calculo"
python3 skill_router.py --catalog ../skills.json "guion para un reel"
python3 skill_router.py --skills-dir ~/.claude/skills "revisa este PR"
python3 skill_router.py --json "..."     # salida JSON (para integrar)
```

Sin flags, intenta `~/.claude/skills`; si no hay, usa el `skills.json` vecino; si
tampoco, cae a skills de demo.

## Medición (clave para la demo / premio)

```bash
python3 eval.py          # precisión top-1 y top-3 sobre casos etiquetados
python3 eval.py --json
```

Un benchmark con casos `mensaje → skill correcta`. Da un número claro de antes/después
para enseñar en una presentación.

> **Honestidad sobre los resultados:** el baseline actual es de coincidencia de
> palabras clave ponderada (tipo IDF). Con el set de demo da 100%, pero es un set
> pequeño y favorable; **no** prueba calidad general. El valor real se vería con más
> casos, skills reales y comparando contra un scorer más fuerte.

## Cómo se decide la puntuación (transparente)

1. Se tokeniza el mensaje y el `nombre + descripción` de cada skill (minúsculas, sin
   acentos, sin stopwords ES/EN).
2. Cada término pesa por **IDF** (los términos raros e informativos pesan más que los
   comunes).
3. `score = 100 × (IDF de términos que coinciden) / (IDF de todos los términos del
   mensaje)`.
4. Se recomienda si supera el umbral (`--threshold`, def. 15) y entra en el top
   (`--top`, def. 3). El resto es "ruido".

## Siguientes pasos (hoja de ruta)

- **Scorer por embeddings** — capturar significado, no solo palabras (sinónimos).
- **Scorer LLM** — pedir a Claude que elija y justifique (modo de alta calidad).
- **Detección de conflictos** — skills que compiten o se solapan.
- **Aprendizaje** — ajustar según lo que al usuario le funciona.
- **Integración** — exponerlo como hook/slash-command dentro de Claude Code.

El diseño deja el *scorer* aislado para enchufar cualquiera de estos sin reescribir el
resto.
