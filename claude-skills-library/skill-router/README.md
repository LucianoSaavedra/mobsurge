# Skill Router

Decide, en cada mensaje, qué skill de Claude Code conviene usar (o ninguna), con
puntuación y motivo. Es el núcleo del plugin **Skill Router**, que además instala
pocas skills curadas en cada proyecto de forma automática.

- **Guía para usuarios (instalar el plugin una vez y olvidarse):** [`../GUIA-DE-USO.md`](../GUIA-DE-USO.md)
- **Pitch, resultados medidos y demo:** [`PITCH.md`](PITCH.md)
- **Integración en Claude Code (hook y skill):** [`integration/README.md`](integration/README.md)
- **Benchmark independiente:** [`bench/README.md`](bench/README.md)

## Estado actual

- Estable, con las suites de tests del núcleo y de la integración en verde.
- **Modelo por defecto del scorer LLM: Claude Haiku** (`claude-haiku-5-5`; se
  cambia con `SKILL_ROUTER_MODEL`).
- El hook `integration/hooks/route_prompt.py` tiene tres modos
  (`SKILL_ROUTER_HOOK_MODE=lexical|cascade|llm`). Usado a mano, el script usa
  `lexical` por defecto; **el plugin lo lanza en `llm`** salvo que definas la variable.

## Resultados medidos

Held-out (`bench/heldout.json`, 60 casos), top-1 / negativos:

| Scorer | Top-1 | Negativos |
|---|---|---|
| Léxico | 36.7% | 80% |
| Léxico + conceptos | 43.3% | 50% |
| Cascada (Haiku si el léxico duda) | 98.3% | 100% |
| LLM Opus 5.5 | 100% | 100% |
| **LLM Haiku 5.5** | **100%** | **100%** |

- Haiku acierta 60/60 y es el más barato: por eso es el modo por defecto del plugin.
- La cascada solo evita el 8% de las llamadas.
- La capa de conceptos, que en el set de desarrollo (19 casos) sube el top-1 de
  63.2% a 94.7%, **no aguanta en el held-out**: mejora poco el top-1 y baja los
  negativos de 80% a 50%. Ese set de 19 casos es de desarrollo, no de evaluación.

Coste por consulta con Haiku vía `claude -p`: ~1.570 tokens de entrada, $0.00035 y
~2 s (antes ~3.080 tokens; −49% de tokens y −42% de coste gracias a
`CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`). Equivale a ~7% de un turno mínimo de
Claude Code (~23.400 tokens). Detalle en [`PITCH.md`](PITCH.md#5-economía-de-tokens).

Limitaciones: los casos del held-out los escribió un agente Claude y los puntúa
Claude (posible sesgo compartido); solo se probó con 6 skills de demo.

## Uso desde la terminal

```bash
cd skill-router

python3 skill_router.py --demo "quiero preparar mi examen de calculo"
python3 skill_router.py --catalog ../skills.json "guion para un reel"
python3 skill_router.py --skills-dir ~/.claude/skills "revisa este PR"
python3 skill_router.py --demo --llm "cuentame un chiste corto"   # scorer LLM (Haiku)
python3 skill_router.py --conflicts --catalog ../skills.json      # skills que se solapan
python3 skill_router.py --json "..."                              # salida JSON
```

Sin flags intenta `~/.claude/skills`; si no hay, usa el `skills.json` vecino; si
tampoco, las skills de demo.

## Cómo funciona

- **Léxico** (`skill_router.py`): tokeniza mensaje y `name + description` (sin
  tildes ni palabras vacías ES/EN), pondera por IDF y explica con los términos
  que coincidieron.
- **Conceptos** (`concepts.py`): sinónimos curados que expanden mensaje y skills;
  se apagan con `--no-concepts`.
- **LLM** (`llm_scorer.py`): Claude elige y justifica en una frase. Usa el SDK si hay
  `ANTHROPIC_API_KEY` y el paquete `anthropic`, o `claude -p`. Si falla, cae al léxico.
  Caché opcional con `SKILL_ROUTER_CACHE`.
- **Cascada** (`cascade.py`): léxico primero y LLM solo si el léxico duda.
- **Conflictos** (`--conflicts`): similitud de coseno entre perfiles de skills.
- **Benchmark** (`eval.py`): `--compare`, `--llm`, `--cascade`, `--model`,
  `--llm-models`, `--cases`.

## Medición y tests

```bash
python3 eval.py --compare                                   # set de desarrollo (19)
python3 eval.py --cases bench/heldout.json --compare        # held-out (60), sin red
python3 eval.py --cases bench/heldout.json --compare --llm --model haiku

python3 -m unittest discover -s tests
python3 -m unittest discover -s integration/tests
```

## Hoja de ruta

Hecho:

- ✅ Router léxico con IDF y motivo explicable.
- ✅ Capa de conceptos (útil en desarrollo; no mejora el held-out de forma clara).
- ✅ Detección de conflictos.
- ✅ Scorer LLM, con Haiku por defecto.
- ✅ Cascada léxico → LLM.
- ✅ Benchmark independiente de 60 casos (`bench/heldout.json`).
- ✅ Reducción de tokens por consulta (−49%).
- ✅ Integración: hook `UserPromptSubmit` con modos y guardia `SKILL_ROUTER_CHILD`, y skill bajo demanda.
- ✅ Plugin de Claude Code con autopilot (detectar, planificar, instalar por proyecto, deduplicar, estimar ahorro, `status`, `undo`): ver [`../GUIA-DE-USO.md`](../GUIA-DE-USO.md).

Pendiente:

- ⏳ Prueba con 30-50 skills reales (descripciones largas, en inglés).
- ⏳ Casos de evaluación escritos por personas (quitar el sesgo compartido).
- ⏳ A/B de calidad final: mismas tareas con y sin router.
- ⏳ Backend SDK con API key como opción (base fija ~310 tokens frente a ~1.070 del CLI).
- ⏳ Aprendizaje según lo que el usuario acepta o descarta.
