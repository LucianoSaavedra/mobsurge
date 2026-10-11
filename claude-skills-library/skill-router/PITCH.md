# Skill Router: pitch y guion de demo

Documento para presentar el proyecto a un concurso. Todos los números que aparecen
aquí salen de ejecutar el código de esta carpeta (o se calculan a partir de ellos,
como los intervalos de confianza); la sección
[Cómo reproducir](#9-cómo-reproducir-los-resultados) explica cómo obtenerlos. Lo que
todavía no está medido se marca como **PENDIENTE**.

---

## 1. Elevator pitch

La comunidad avisa de que, cuantas más skills instalas en Claude Code, peor elige; y
hoy esa decisión ocurre dentro del modelo, sin puntuación, sin motivo y sin avisarte
de que tienes dos skills que hacen lo mismo. **Skill Router** es una capa que, en
cada mensaje, puntúa tus skills de 0 a 100, recomienda las que encajan, explica por
qué y marca el resto como ruido. Tiene tres niveles (palabras clave explicables, sinónimos curados y
un LLM que justifica su elección), detecta skills que se solapan y se engancha a
Claude Code como hook. Y viene con su propio benchmark, para que cada mejora se pueda
medir en vez de prometer.

---

## 2. El problema

**Más skills no es mejor.** El [README de la librería](../README.md) recoge un aviso
de la comunidad: instalar muchas skills a la vez empeora las respuestas, así que lo
prudente es empezar por uno o dos escenarios. Es un aviso de la comunidad, no una
medición nuestra; medirlo con y sin router está en la hoja de ruta.

**El mecanismo nativo es una caja negra.** Claude Code decide qué skill usar a partir
del `name` y la `description` de cada una. Con pocas skills suele bastar, pero:

- **No muestra puntuación ni motivo.** No sabes por qué eligió una skill o por qué no
  usó otra, así que no puedes corregir una mala descripción con datos.
- **No avisa de solapamientos.** Si dos skills cubren lo mismo, compiten en silencio.
- **No se puede medir desde fuera.** No hay un número que diga si añadir una skill
  ha empeorado la elección de las demás.

**Los solapamientos existen en catálogos reales.** El propio catálogo de esta
librería (`skills.json`, 9 entradas) tiene dos repos de video con Remotion que el
detector de conflictos marca con similitud 0.69 (ver la demo, paso 4).

---

## 3. La solución

```
mensaje del usuario + skills instaladas (name + description)
              |
              v
  Nivel 1  Léxico con IDF ........... rápido, sin dependencias, explica
           qué palabras coincidieron
  Nivel 2  + Conceptos .............. sinónimos curados: "prueba" y
           "repasar" activan el concepto "estudio"
  Nivel 3  LLM (Claude) ............. elige y justifica con una frase;
           si falla, cae al léxico
              |
              v
  recomendadas (score 0-100 + motivo)  |  ruido  |  conflictos entre skills
```

- **Nivel 1: léxico explicable** (`skill_router.py`). Tokeniza mensaje y skills
  (sin tildes ni palabras vacías en español e inglés) y pondera cada término por IDF:
  las palabras raras pesan más que las comunes. El motivo es literal: los términos
  que coincidieron.
- **Nivel 2: conceptos** (`concepts.py`). Un recurso curado que agrupa sinónimos en
  conceptos (estudio, video, marketing, redes, revisión de código, depuración,
  investigación). Es auditable y se puede apagar con `--no-concepts` para comparar.
- **Nivel 3: LLM que justifica** (`llm_scorer.py`, flag `--llm`). Le pide a Claude
  que elija las skills y dé un motivo en lenguaje natural. Usa el SDK de Anthropic si
  hay `ANTHROPIC_API_KEY` o el CLI `claude -p` si Claude Code está instalado; si no
  hay ninguno o la llamada falla, el router sigue funcionando con el léxico. Tiene
  caché opcional en disco (`SKILL_ROUTER_CACHE`) para demos y benchmarks
  reproducibles.
- **Detección de conflictos** (`--conflicts`). Compara cada par de skills por
  similitud de coseno de sus términos y conceptos, y lista las parejas que se
  solapan, con los términos que comparten.
- **Integración con Claude Code** (`integration/`):
  - un **hook `UserPromptSubmit`** (`integration/hooks/route_prompt.py`) que, antes de
    que Claude procese cada mensaje, añade una nota corta con las skills que encajan,
    su puntuación y el motivo. Si ninguna encaja no añade nada; nunca bloquea el
    mensaje; no interviene cuando el usuario escribe un `/comando`; el LLM va apagado
    por defecto en el hook y se activa con `SKILL_ROUTER_HOOK_LLM=1`. Ejemplo de
    configuración en `integration/settings.example.json`.
  - una **skill bajo demanda** (`integration/skill-router/SKILL.md`) para preguntar
    "¿qué skills debería usar para esto?" o "¿tengo skills repetidas?".
- **Diseño modular.** Los tres scorers devuelven la misma estructura, así que la
  clasificación, la salida, el hook y el benchmark no cambian al cambiar de scorer.

Requisitos: solo Python 3 estándar para los niveles 1 y 2. El nivel 3 necesita el CLI
de Claude Code con sesión iniciada, o `ANTHROPIC_API_KEY` más el paquete `anthropic`.

---

## 4. Resultados medidos (set de desarrollo, 19 casos)

`eval.py` trae 19 casos escritos a mano: 9 **directos** (usan las palabras de la
skill), 7 de **sinónimos** (dicen lo mismo con otras palabras) y 3 **negativos** (no
debe recomendarse ninguna skill). Se puntúa contra las 6 skills de demo.

| Scorer          | Top-1  | Top-3  |
|-----------------|--------|--------|
| baseline (léxico) | 63.2% | 63.2% |
| + conceptos     | 84.2%  | 89.5%  |
| LLM             | 100%   | 100%   |

Por tipo de caso (top-1 / top-3):

| Tipo      | n | baseline      | + conceptos   | LLM           |
|-----------|---|---------------|---------------|---------------|
| directo   | 9 | 100.0 / 100.0 | 100.0 / 100.0 | 100.0 / 100.0 |
| sinónimo  | 7 | 14.3 / 14.3   | 71.4 / 85.7   | 100.0 / 100.0 |
| negativo  | 3 | 66.7 / 66.7   | 66.7 / 66.7   | 100.0 / 100.0 |

Lectura:

- Con palabras exactas el router acierta todo lo directo, pero falla 6 de 7 sinónimos.
- La capa de conceptos sube el top-1 en **+21 puntos**, casi todo en sinónimos.
- El LLM acierta los 19 casos, incluidos los negativos donde el léxico se equivoca
  (por ejemplo, "cuéntame un chiste corto" activa la skill de video por la palabra
  "corto").
- **Coste:** la comparativa léxica completa (19 casos, dos scorers) tarda unos 0.1 s.
  La versión con LLM tardó 102 y 106 s en dos ejecuciones (19 llamadas por el CLI
  `claude -p`), es decir, unos 5-6 s por mensaje. Una consulta suelta con `--llm`
  tardó entre 5 y 6 s.

### Por qué estos 19 casos no bastan

- **Son pocos.** Con 19 casos, el intervalo de confianza al 95 % (Wilson) es muy
  ancho: el 84.2 % de los conceptos es compatible con algo entre el 62 % y el 95 %, y
  el 100 % del LLM con algo entre el 83 % y el 100 %. Los intervalos de conceptos y LLM
  se solapan, así que este set no permite afirmar cuánto mejor es el LLM.
- **Los escribió el mismo equipo que escribió el router.** Los casos y la capa de
  conceptos se hicieron a la vez, así que es probable que los conceptos estén
  ajustados a estas frases.
- **El LLM lo satura.** Un 100 % no deja margen para ver si una mejora del LLM ayuda
  o perjudica.
- **Las skills de demo son fáciles.** Son 6 skills con descripciones cortas y en
  español, escritas por nosotros. Las skills reales suelen tener descripciones largas
  y en inglés.

Por eso hay un segundo benchmark, independiente.

---

## 5. Benchmark independiente (held-out)

> **PENDIENTE.** Esta sección se rellenará con los números reales al ejecutar el
> benchmark. No hay que copiar aquí cifras del set de 19 casos.

El set `bench/heldout.json` tiene **60 casos** escritos por un agente que **no leyó
el código** del router (ni `skill_router.py`, ni `concepts.py`, ni `llm_scorer.py`,
ni `eval.py`); solo conocía el nombre y la descripción de las 6 skills de demo. Imita
cómo escribe la gente en un chat: español, inglés, spanglish, faltas de ortografía y
jerga. Según `bench/README.md`, 33 de los 60 casos no comparten ninguna palabra con
las descripciones ni con los nombres de las skills. Las reglas para mantenerlo
independiente están en ese mismo README (no ajustar el router mirando estos casos,
no cambiar un `expected` para que salga un acierto).

Resultado global (60 casos):

| Scorer          | Top-1 | Top-3 |
|-----------------|-------|-------|
| baseline (léxico) |     |       |
| + conceptos     |       |       |
| LLM             |       |       |

Por tipo de caso (top-1 / top-3):

| Tipo      | n  | baseline | + conceptos | LLM |
|-----------|----|----------|-------------|-----|
| directo   | 10 |          |             |     |
| sinónimo  | 14 |          |             |     |
| multi     | 6  |          |             |     |
| inglés    | 6  |          |             |     |
| typo      | 6  |          |             |     |
| negativo  | 10 |          |             |     |
| ambiguo   | 8  |          |             |     |

Fecha de la medición: PENDIENTE · Commit: PENDIENTE · Modelo del LLM: PENDIENTE

Comando:

```bash
python3 eval.py --cases bench/heldout.json --compare --llm
```

---

## 6. Guion de demo (3 minutos)

Todos los comandos se ejecutan desde `claude-skills-library/skill-router/` y usan las 6
skills de demo (`--demo`), salvo el paso de conflictos, que usa el catálogo real.

### Antes de salir al escenario

```bash
cd claude-skills-library/skill-router
export SKILL_ROUTER_CACHE=/tmp/skill-router-cache   # caché del LLM
python3 eval.py --compare --llm                     # calienta la caché (~2 min)
python3 skill_router.py --demo --llm "cuentame un chiste corto"
python3 skill_router.py --demo --llm "mañana tengo la prueba, necesito repasar"
```

Con la caché caliente, las llamadas al LLM de la demo responden al instante con la
misma respuesta que se obtuvo al calentarla, y la salida indica `LLM (cli, caché)`.
Si el público pregunta, se explica que es una respuesta grabada del modelo, no una
llamada en directo. Sin caché, cada llamada tarda unos 5-6 s.

La caché depende del backend (CLI o SDK), del modelo y del texto exacto: hay que
calentarla en la misma máquina y con los mismos comandos que se van a enseñar.

Plan B: si no hay red ni CLI de Claude, `--llm` cae al léxico y lo dice en la cabecera
(`scorer: léxico+conceptos`). Los pasos 1, 2, 4 y 6, la primera línea del paso 3 y
`eval.py --compare` sin `--llm` no necesitan red.

### Paso 0 · El problema (0:00-0:20, sin comandos)

> "Claude Code elige qué skill usar leyendo sus descripciones. Con dos o tres skills va
> bien. Con veinte, elige peor, y no te dice por qué. Skill Router hace esa decisión
> visible: puntúa, explica y avisa de lo que sobra."

### Paso 1 · Recomendar y explicar (0:20-0:45)

```bash
python3 skill_router.py --demo "quiero preparar mi examen de calculo"
```

Qué se ve: `estudio-a-medida` con 43 %, motivo `examen, ~estudio`; las otras 5 skills
aparecen como ruido con 0 %.

> "No es una caja negra: el motivo son las palabras que coincidieron. La tilde (~)
> marca un concepto, no una palabra literal. Y las otras cinco skills quedan marcadas
> como ruido para este turno."

### Paso 2 · Sinónimos: antes y después (0:45-1:10)

```bash
python3 skill_router.py --demo --no-concepts "mañana tengo la prueba, necesito repasar"
python3 skill_router.py --demo "mañana tengo la prueba, necesito repasar"
```

Qué se ve: sin conceptos, ninguna skill supera el umbral. Con conceptos,
`estudio-a-medida` con 16 %, motivo `~estudio`.

> "La persona no dijo 'examen', dijo 'prueba' y 'repasar'. Las palabras exactas no lo
> pillan; la capa de conceptos sí. Ojo: 16 sobre 100 apenas pasa el umbral de 15. Por
> eso existe el tercer nivel."

### Paso 3 · El LLM corrige y justifica (1:10-1:40)

```bash
python3 skill_router.py --demo "cuentame un chiste corto"
python3 skill_router.py --demo --llm "cuentame un chiste corto"
python3 skill_router.py --demo --llm "mañana tengo la prueba, necesito repasar"
```

Qué se ve: el léxico recomienda `guiones-video` (28 %, motivo `corto`), un falso
positivo. Con `--llm`, ninguna skill. En la tercera, `estudio-a-medida` con una frase
de motivo (en nuestra ejecución: 95 %, "El usuario tiene una prueba mañana y necesita
material de repaso como resúmenes, fichas o un simulacro").

> "El léxico cae en la trampa de la palabra 'corto'. El LLM entiende que es charla y no
> recomienda nada. Y cuando sí recomienda, lo justifica en una frase. Si no hay LLM
> disponible, el router no se rompe: vuelve al léxico."

Nota: la puntuación y la frase del LLM pueden variar de una ejecución a otra.

### Paso 4 · Conflictos entre skills (1:40-2:00)

```bash
python3 skill_router.py --conflicts --catalog ../skills.json
```

Qué se ve: `remotion-skills ⇄ remotion-video-skill` con 0.69 (comparten video, reels,
remotion, shorts, tiktok...), y dos parejas más débiles (0.46 y 0.38).

> "Esto es nuestro catálogo real, no uno de juguete. Dos repos hacen casi lo mismo y
> compiten por los mismos mensajes. El router lo detecta y sugiere dejar uno activo."

### Paso 5 · Se puede medir (2:00-2:30)

```bash
python3 eval.py --compare --llm
```

(Con la caché caliente responde al instante. Sin LLM, usar `python3 eval.py --compare`,
que muestra solo baseline y conceptos.)

Qué se ve: baseline 63.2 % / 63.2 %, conceptos 84.2 % / 89.5 %, LLM 100 % / 100 %, y
el desglose por tipo: los sinónimos pasan de 14.3 % a 71.4 % y a 100 %.

> "Cada nivel se mide. Pero lo decimos claro: son 19 casos escritos por nosotros, y el
> LLM los satura. Por eso tenemos un benchmark independiente de 60 casos escritos por
> alguien que no vio el código." (Si ya hay números del held-out, enseñarlos aquí.)

### Paso 6 · Dentro de Claude Code (2:30-2:50)

```bash
SKILL_ROUTER_DEMO=1 python3 integration/hooks/route_prompt.py --prompt "guion para un reel" --texto
```

Qué se ve: la nota que el hook le pasaría a Claude: `guiones-video` con relevancia
100/100, motivo `guion, reel, ~video`, y las demás como ruido.

> "Esto es lo que Claude recibe antes de cada mensaje cuando el router está instalado
> como hook. Si nada encaja, no añade nada; y nunca bloquea al usuario."

### Cierre (2:50-3:00)

> "Skill Router convierte una decisión invisible en una decisión puntuada, explicada y
> medible. El siguiente paso es medir si eso mejora el resultado final de Claude, no
> solo la elección de skill."

---

## 7. Diferenciación

| | Listas tipo awesome-claude-skills | Trigger nativo por `description` | Skill Router |
|---|---|---|---|
| Para qué sirve | Descubrir e instalar skills | Elegir skill dentro del modelo | Elegir skill para este mensaje, fuera del modelo |
| Decide por mensaje | No | Sí | Sí |
| Puntuación visible | No | No | Sí (0-100) |
| Motivo de la elección | No | No | Sí (términos o frase del LLM) |
| Marca lo que sobra | No | No | Sí (ruido por turno) |
| Detecta skills que se solapan | No | No | Sí (`--conflicts`) |
| Benchmark para medir cambios | No | No | Sí (`eval.py`) |
| Coste | Ninguno | Incluido | Léxico: casi nulo; LLM: una llamada por mensaje |

No compite con ninguno de los dos, se apoya en ellos:

- Las **listas** (como las colecciones `awesome` que ya instala esta librería) dicen
  qué skills existen. El router decide cuál usar ahora entre las que ya instalaste.
- El **trigger nativo** sigue funcionando. El router lee las mismas `description` y le
  añade a Claude una pista explicada; Claude decide al final. Además, como el router
  puntúa las descripciones, sirve para detectar descripciones flojas o duplicadas.

---

## 8. Limitaciones y hoja de ruta

### Limitaciones (hoy)

- **Medición pequeña.** Solo hay números del set de 19 casos; el held-out está
  PENDIENTE.
- **No hemos medido el resultado final.** Medimos si el router elige la skill
  correcta, no si las respuestas de Claude mejoran con el router. Esa es la prueba que
  de verdad importa y no está hecha.
- **El léxico se equivoca con palabras sueltas.** "Chiste corto" acaba en la skill de
  video. Las puntuaciones no están calibradas: un 16 % puede ser un acierto y el
  umbral (15) es fijo.
- **Los conceptos son una lista hecha a mano**, en español y para 7 dominios. Una
  skill de otro tema no se beneficia hasta que alguien amplía la lista.
- **Skills de demo fáciles.** Descripciones cortas, en español y escritas por
  nosotros. Con skills reales (largas, en inglés) es de esperar que el léxico rinda
  peor; no lo hemos medido, y la propia skill del router ya avisa de ello.
- **El LLM cuesta.** Unos 5-6 s por mensaje en nuestras pruebas con el CLI, una
  llamada por mensaje, respuestas que pueden variar entre ejecuciones y dependencia de
  red y credenciales. Por eso en el hook va apagado por defecto.
- **Los conflictos miden vocabulario, no comportamiento.** Dos skills que usan
  palabras genéricas parecidas ("oficial", "repo", "skill") pueden salir como pareja
  sin hacer lo mismo.
- **Recomienda, no gestiona.** No activa ni desactiva skills, así que hoy no reduce el
  contexto que ocupan las descripciones de todas las skills instaladas.
- **No aprende** todavía de lo que le funciona a cada usuario.

### Hoja de ruta

1. Publicar los números del held-out (sección 5) y mantenerlo separado del set de
   desarrollo.
2. Prueba A/B de calidad: las mismas tareas con todas las skills frente a las que elige
   el router, evaluadas con una rúbrica, y midiendo también tokens.
3. Modo híbrido: léxico primero y LLM solo cuando el léxico duda (puntuación cerca del
   umbral o empate), para bajar latencia y coste.
4. Scorer por embeddings, para captar sinónimos sin mantener listas a mano.
5. Calibrar el umbral y las puntuaciones con datos.
6. Un set de prueba con skills reales instaladas (descripciones largas y en inglés).
7. Aprendizaje a partir de lo que el usuario acepta o descarta.
8. Pasar de recomendar a gestionar: activar o desactivar skills por proyecto o por
   turno.

---

## 9. Cómo reproducir los resultados

Requisitos: Python 3 (probado con 3.13), sin paquetes externos. Para el LLM, el CLI
`claude` con sesión iniciada, o `ANTHROPIC_API_KEY` y el paquete `anthropic`.

```bash
cd claude-skills-library/skill-router

# Set de desarrollo (19 casos): baseline frente a conceptos. No necesita red.
python3 eval.py --compare
#   esperado: baseline 63.2% / 63.2%, +conceptos 84.2% / 89.5%

# Lo mismo con el scorer LLM (19 llamadas, ~2 minutos sin caché)
python3 eval.py --compare --llm
#   medido: LLM 100% / 100%

# Tabla detallada caso a caso, o salida JSON
python3 eval.py
python3 eval.py --json

# Benchmark independiente (60 casos)
python3 eval.py --cases bench/heldout.json --compare
python3 eval.py --cases bench/heldout.json --compare --llm

# Comandos de la demo
python3 skill_router.py --demo "quiero preparar mi examen de calculo"
python3 skill_router.py --demo --llm "cuentame un chiste corto"
python3 skill_router.py --conflicts --catalog ../skills.json

# Hook de Claude Code, probado a mano y con sus tests
SKILL_ROUTER_DEMO=1 python3 integration/hooks/route_prompt.py --prompt "guion para un reel" --texto
python3 -m unittest discover -s integration/tests
```

Notas para reproducir:

- Los resultados léxicos (baseline y conceptos) son deterministas: deberían salir
  idénticos en cualquier máquina con el mismo código.
- Los del LLM pueden variar entre ejecuciones. El 100 % / 100 % del set de 19 casos
  se repitió en dos ejecuciones más, hechas al escribir este documento (octubre de
  2026) con el modelo por defecto (`claude-opus-5-5`, vía CLI). Se puede cambiar de
  modelo con `SKILL_ROUTER_MODEL`.
- Para repetir una medición del LLM sin volver a llamar al modelo, define
  `SKILL_ROUTER_CACHE=/una/carpeta`: guarda cada respuesta y la reutiliza.
- Si cambias `concepts.py`, `skill_router.py` o los casos, los números del set de 19
  casos cambian: vuelve a ejecutar `eval.py --compare` y actualiza este documento.
