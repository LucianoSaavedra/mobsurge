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

## Capa de conceptos (semántica ligera)

El baseline por palabras clave falla con sinónimos: "repasar para la prueba" no
coincide con una skill que dice "examen". La capa de conceptos (`concepts.py`) mapea
familias de términos a un concepto canónico (estudio, video, marketing, redes,
revisión de código, depuración, investigación) y expande mensaje y skills con sus
conceptos antes de puntuar. Es un recurso curado y auditable, sin modelos ni
dependencias. Se puede apagar con `--no-concepts` para comparar.

## Medición (clave para la demo / premio)

```bash
python3 eval.py             # tabla detallada (con conceptos)
python3 eval.py --compare   # baseline vs conceptos (el número clave)
python3 eval.py --json
```

El benchmark tiene 19 casos de tres tipos: **directos**, **sinónimos** (donde los
conceptos ayudan) y **negativos** (no debe activarse ninguna skill).

**Resultado medido (set de demo):**

| Scorer        | Top-1  | Top-3  |
|---------------|--------|--------|
| baseline      | 63.2%  | 63.2%  |
| **+conceptos**| 84.2%  | 89.5%  |

La capa de conceptos mejora el top-1 en **+21 puntos**. Ese antes/después es lo que se
enseña en la demo.

> **Honestidad sobre los resultados:** es un set pequeño y hecho por nosotros, así que
> estos números orientan pero **no** prueban calidad general. Hay fallos reales que
> dejamos a la vista a propósito: un caso de estudio que se queda bajo el umbral, un
> falso positivo ("chiste corto" → video por la palabra "corto"), y skills que a veces
> aparecen juntas en top-3. El valor real se vería con más casos, skills reales y un
> scorer más fuerte (embeddings/LLM).

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

- ✅ **Capa de conceptos** — sinónimos por recurso curado (+21 pts top-1). *Hecho.*
- **Scorer por embeddings** — capturar significado automáticamente, sin listas a mano.
- **Scorer LLM** — pedir a Claude que elija y justifique (modo de alta calidad).
- **Detección de conflictos** — skills que compiten o se solapan.
- **Aprendizaje** — ajustar según lo que al usuario le funciona.
- **Integración** — exponerlo como hook/slash-command dentro de Claude Code.

El diseño deja el *scorer* aislado para enchufar cualquiera de estos sin reescribir el
resto.
