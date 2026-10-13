# Benchmark held-out del Skill Router

`heldout.json` es un set de **60 casos independientes** para medir el Skill Router con mensajes que no se escribieron mirando su lógica. Complementa los 19 casos de `eval.py`, que se usaron durante el desarrollo y por eso tienden a premiar lo que el router ya sabe hacer.

## Cómo se construyó

- Lo escribió a mano un agente que **no abrió** `skill_router.py`, `concepts.py`, `llm_scorer.py` ni `eval.py`. Solo tenía el nombre y la descripción de las 6 skills del set de demo (`estudio-a-medida`, `marca-marketing`, `guiones-video`, `code-review`, `deep-research`, `debug`).
- Los mensajes imitan cómo escribe la gente en un chat: sobre todo en español, con algo de inglés, spanglish, faltas de ortografía y jerga.
- **33 de los 60 casos no comparten ninguna palabra con las descripciones ni con los nombres de las skills.** Para comprobarlo se pasa todo a minúsculas, se quitan las tildes y las palabras vacías (`de`, `en`, `con`…), y también se cuenta como coincidencia que dos palabras empiecen por los mismos 5 caracteres. Si solo se cuentan las palabras exactas, son 35 de 60.
- El `expected` de cada caso se puso con criterio justo: cuando había dudas razonables entre dos skills, se aceptan las dos.
- Se comprobó que no hay casos duplicados ni casi idénticos: ningún par de casos comparte un 30 % o más de sus palabras (índice de Jaccard). Dentro del held-out el máximo es 0,14. La comparación con el set inline de `eval.py` está en la sección «Independencia frente al set inline».

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
| `multi` | 6 | dos intenciones en un mensaje; `expected` tiene 2 skills válidas. Top-1/top-3 miden «al menos una»; **multi completa** mide «las dos» |
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

## Qué informa `eval.py`

Las métricas principales siguen siendo **top-1 y top-3 con el umbral por defecto (15)**, porque es lo que hace el producto. Además:

- **Empates en el #1.** Si varias recomendadas comparten la puntuación máxima, el top-1 vale `|esperadas ∩ empatadas| / |empatadas|`: lo que se acertaría de media con un desempate al azar. Así el orden alfabético de los nombres nunca decide un acierto, y renombrar una skill no cambia ninguna cifra. Se aplica lo mismo en el corte del top-3 cuando un empate lo cruza. La tabla muestra cuántos casos tuvieron empate (`empates en #1`).
- **Diagnóstico** (clave `diagnostics` en `--json`; no sustituye a top-1/top-3):
  - `hit@1 ranking`: en los positivos, si la esperada es la #1 por puntuación bruta (>0), sin aplicar el umbral. Separa «ordena bien» de «se atreve a recomendar».
  - `MRR`: en los positivos, 1/posición del primer esperado en el ranking bruto (0 si no puntúa).
  - `falsos positivos`: % de negativos en los que se recomendó algo.
  - `multi completa`: en los casos `multi`, % en que **todas** las esperadas están entre las recomendadas.
  - `precisión recom.`: de todo lo recomendado, qué parte está en `expected`. Junto con `recom. por caso` evita premiar que se recomiende de más.
  - Causas de fallo: `sin_senal` (todo a 0), `abstuvo_con_1_correcta` (la #1 era la buena pero quedó bajo el umbral), `empate_en_1`, `ranking_incorrecto`, `falso_positivo` y `error_llm`.
- **Economía** (scorers con LLM): llamadas al LLM (en la cascada, % evitado), tokens de entrada y salida totales y por consulta, coste, latencia media y aciertos top-1 por cada 1000 tokens. Si el backend no informa un dato, sale `n/d` (`null` en `--json`).
- `--threshold` y `--top` existen **solo para diagnóstico**. No sirven para elegir el umbral mirando este set (regla 1). Si se publica un barrido, que incluya baseline y +conceptos juntos: en el baseline, bajar el umbral sí empeora los negativos.

```bash
python3 eval.py --compare --llm --cascade --llm-models opus,haiku   # una sola tabla
```

## Set de estrés (`stress.json`)

`make_stress.py` genera `stress.json` a partir de `heldout.json` de forma determinista. Cada caso aparece 3 veces con **las mismas etiquetas**: (a) con un traceback o log pegado debajo, (b) con una frase de contexto neutra y larga delante y (c) con un texto pegado dentro de `<pasted_content>`. Mide si el router recomienda por la petición y no por lo que la rodea. Como deriva del held-out, hereda sus reglas: no se ajusta nada mirándolo.

```bash
python3 bench/make_stress.py
python3 eval.py --compare --cases bench/stress.json
```

El archivo lleva `"derived_from": "heldout.json"`, así que `eval.py` no avisa de que sus variantes se parecen entre sí, porque es algo intencionado.

## Independencia frente al set inline

Se midió el índice de Jaccard de cada caso del held-out con su caso más parecido de los 19 de `eval.py`. Para calcularlo se usan los términos útiles de `tokenize(normalize_query(...))`, que son los mismos que ve el router:

- Media 0,07, mediana 0,00. Solo **1 de 60** pasa de 0,3 y 7 pasan de 0,2.
- Pares más parecidos:
  - 0,50: «tengo este stack trace y no sé de dónde sale el error, ayúdame a diagnosticarlo» ~ «tengo un stack trace, ayudame a diagnosticar el error» (`directo`).
  - 0,29: «dame ideas de publicaciones para Instagram de mi peluquería…» ~ «ideas de contenido para instagram sobre mi negocio».
  - 0,27: «revisa este pull request, me preocupa que haya inyección SQL…» ~ «revisa este pull request por bugs de seguridad».
  - 0,25: «prepárame un simulacro de examen de historia según la rúbrica…» ~ «necesito un simulacro con mi rubrica».
- También se repiten plantillas sin coincidir mucho en palabras: «mañana tengo la prueba…», «un guion para un reel…» y «clip vertical».

Conclusión: el held-out es independiente en lo esencial, pero no del todo. El caso del stack trace es casi un duplicado de un caso de desarrollo, y unos pocos `directo` y `sinonimo` comparten plantilla con él. Su efecto en las cifras es pequeño (son casos fáciles), pero esas cifras de `directo` hay que leerlas como algo optimistas. Cuando se rehaga el held-out, conviene reescribir el caso del stack trace. `eval.py --cases` avisa por stderr de cualquier caso con Jaccard ≥ 0,3 frente al set inline, o frente a otro caso del mismo archivo (incluidos los duplicados exactos, que hoy cuentan dos veces).
