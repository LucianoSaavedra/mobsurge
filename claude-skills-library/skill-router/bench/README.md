# Benchmark held-out del Skill Router

`heldout.json` es un set de **60 casos independientes** para medir el Skill Router con mensajes que no se escribieron mirando su lógica. Complementa los 19 casos de `eval.py`, que se usaron durante el desarrollo y por eso tienden a premiar lo que el router ya sabe hacer.

## Cómo se construyó

- Lo escribió a mano un agente que **no abrió** `skill_router.py`, `concepts.py`, `llm_scorer.py` ni `eval.py`. Solo tenía el nombre y la descripción de las 6 skills del set de demo (`estudio-a-medida`, `marca-marketing`, `guiones-video`, `code-review`, `deep-research`, `debug`).
- Los mensajes imitan cómo escribe la gente en un chat: sobre todo en español, con algo de inglés, spanglish, faltas de ortografía y jerga.
- **33 de los 60 casos no comparten ninguna palabra con las descripciones ni con los nombres de las skills.** Para comprobarlo se pasa todo a minúsculas, se quitan las tildes y las palabras vacías (`de`, `en`, `con`…), y también se cuenta como coincidencia que dos palabras empiecen por los mismos 5 caracteres. Si solo se cuentan las palabras exactas, son 35 de 60.
- El `expected` de cada caso se puso con criterio justo: cuando había dudas razonables entre dos skills, se aceptan las dos.
- Se comprobó que no hay casos duplicados ni casi idénticos: ningún par de casos comparte un 30 % o más de sus palabras (índice de Jaccard).

## Formato

```json
{"version": 1, "cases": [{"query": "...", "expected": ["skill", ...], "kind": "..."}]}
```

- `expected` es la lista de skills aceptables. Cuenta como acierto top-1 si la primera recomendación es cualquiera de ellas, y como acierto top-3 si alguna aparece entre las recomendadas.
- `expected: []` marca un caso **negativo**: el acierto es que el router **no** recomiende ninguna skill.

## Distribución por tipo (`kind`)

| kind | casos | qué mide |
|---|---|---|
| `directo` | 10 | usa el vocabulario de la descripción de la skill |
| `sinonimo` | 14 | paráfrasis sin las palabras de la descripción ("prueba final", "échale un ojo", "clip vertical") |
| `multi` | 6 | dos intenciones en un mensaje; `expected` tiene 2 skills válidas |
| `ingles` | 6 | mensajes en inglés |
| `typo` | 6 | faltas de ortografía, abreviaturas y jerga ("nesesito un gion", "me peta el server") |
| `negativo` | 10 | charla y preguntas generales; 5 son trampas que comparten una palabra con una skill (`marca`, `fallo`, `error`, `escena`, `email`) sin necesitarla |
| `ambiguo` | 8 | mensajes vagos; `expected` recoge la mejor opción razonable (1 o 2 skills) |
| **total** | **60** | |

Veces que aparece cada skill en algún `expected`: estudio-a-medida 11, marca-marketing 12, guiones-video 9, code-review 9, deep-research 10, debug 9.

## Cómo ejecutarlo

Desde `claude-skills-library/skill-router/`, una vez que `eval.py` tenga la opción `--cases` (la añade el orquestador):

```bash
python3 eval.py --cases bench/heldout.json            # router por defecto
python3 eval.py --cases bench/heldout.json --compare  # baseline frente a conceptos
python3 eval.py --cases bench/heldout.json --llm      # incluye el scorer LLM
```

Para comprobar solo que el archivo es válido:

```bash
python3 -c "import json; d=json.load(open('bench/heldout.json')); print(len(d['cases']))"
```

## Reglas para mantener la independencia

1. **No ajustes el router mirando estos casos.** Si un caso falla, se arregla el router pensando en el problema general, no en la frase concreta. Si hace falta iterar, hay que crear antes un set nuevo de held-out.
2. **No cambies un `expected` para que salga un acierto.** Solo se corrige si la etiqueta estaba mal de verdad, y conviene apuntar el motivo en el commit.
3. Si se añaden casos, que los escriba alguien que no haya leído `concepts.py` ni el código del scorer.
4. Los resultados de este set deben darse **separados** de los 19 casos de desarrollo, y no mezclarse con ellos.
