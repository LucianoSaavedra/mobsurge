# Skill Router: pitch y guion de demo

Documento para presentar el proyecto. Las cifras de resultados salen de ejecutar
el código de esta carpeta y se citan tal cual se midieron, sin redondear al alza.
Lo que no está medido se marca como **PENDIENTE**. La sección
[Cómo reproducir](#8-cómo-reproducir) explica cómo obtenerlas.

---

## 1. Elevator pitch

Instalar muchas skills en Claude Code sale caro: ocupan contexto en cada turno y
el modelo elige peor. **Skill Router** se activa una sola vez como plugin y, a
partir de ahí, en cada proyecto:

1. detecta qué tipo de proyecto es;
2. instala **solo en ese proyecto** unas pocas skills de un catálogo curado,
   sin duplicados y sin scripts ejecutables;
3. te dice cuánto contexto ahorras;
4. en cada mensaje, recomienda la skill que encaja (o ninguna).

La historia en una línea: **Claude Haiku acierta 60 de 60 casos de un benchmark
independiente y su consulta cuesta ~1.570 tokens, como un 7% de un turno mínimo
de Claude Code.**

---

## 2. El problema

- **Más skills no es mejor.** La comunidad avisa de que instalar muchas skills a
  la vez empeora las respuestas (es un aviso, no una medición nuestra).
- **Las skills globales viajan a todos los proyectos.** Lo que instalas en
  `~/.claude/skills` carga su nombre y descripción en cada turno, aunque ese
  proyecto no lo necesite.
- **La elección nativa es una caja negra.** Claude Code elige por `description`,
  sin puntuación, sin motivo y sin avisar de skills que se solapan.

---

## 3. La solución

```
Plugin (se instala una vez, ámbito user)
 │
 ├─ SessionStart ─► autopilot (en segundo plano, solo si el proyecto no está configurado)
 │                   detect.py  → tipo de proyecto (milisegundos)
 │                   plan.py    → ≤3 repos del catálogo curado, prefiere "oficial"
 │                   install.py → ≤12 skills COPIADAS en <proyecto>/.claude/skills,
 │                                deduplicadas, sin scripts, con estado y undo
 │
 └─ UserPromptSubmit ─► route_prompt.py (en cada mensaje)
                        modo llm (por defecto): Haiku elige y justifica
                        modo cascade: léxico y, si duda, Haiku
                        modo lexical: palabras clave + conceptos, gratis
                        → nota de una línea, o nada si ninguna encaja
```

Protecciones que se mantienen siempre:

- solo se instala desde el catálogo curado (`skills.json`, con campo `trust`);
- las skills con scripts ejecutables se saltan salvo `--allow-scripts`;
- todo se deshace (`/skill-router:undo`, o desinstalar el plugin);
- nunca se tocan las skills globales del usuario (`~/.claude/skills`).

---

## 4. Resultados medidos

### 4.1 Benchmark independiente (held-out, `bench/heldout.json`, 60 casos)

| Scorer | Top-1 | Negativos |
|---|---|---|
| Léxico | 36.7% | 80% |
| Léxico + conceptos | 43.3% | 50% |
| Cascada (léxico → Haiku si duda) | 98.3% | 100% |
| LLM Opus 5.5 | 100% | 100% |
| **LLM Haiku 5.5 (por defecto)** | **100%** | **100%** |

"Negativos" = casos en los que lo correcto es no recomendar nada.

Lectura honesta:

- **La capa de conceptos no es la historia.** En el set de desarrollo subía mucho
  (ver 4.2), pero en el held-out apenas mejora el top-1 y empeora los negativos
  (80% → 50%): convierte palabras sueltas en falsos positivos.
- **La historia es Haiku:** acierta lo mismo que Opus (60/60) y es el modelo
  más barato. Por eso es el modo por defecto del plugin.
- **La cascada no compensa:** casi iguala a Haiku (98.3% / 100%), pero solo evita
  el 8% de las llamadas. El ahorro es mínimo y se pierde algo de acierto.
- El modo léxico sigue disponible como opción gratuita y sin red, sabiendo que
  acierta bastante menos.

Nota: la medición de Opus se hizo con el código anterior, sin registro de uso de
tokens.

### 4.2 Set de desarrollo (19 casos inline en `eval.py`)

| Scorer | Top-1 |
|---|---|
| Léxico | 63.2% |
| Léxico + conceptos | 94.7% |

Este set se usó para desarrollar la capa de conceptos, así que **no es una
evaluación**: sirve para detectar regresiones. El contraste con el held-out
(43.3% con conceptos) es justo el motivo por el que no lo usamos como argumento.

---

## 5. Economía de tokens

Coste por consulta del router en modo `llm` (Haiku vía `claude -p`):

| | Tokens de entrada | Coste | Latencia |
|---|---|---|---|
| Antes | ~3.080 | — | — |
| Con `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` | ~1.570 | $0.00035 | ~2 s |

- Eso es **−49% de tokens y −42% de coste** por consulta.
- El CLI tiene una base fija de ~1.070 tokens por llamada que no se cachea. Con
  SDK + API key serían ~310 (no es el camino por defecto: el plugin usa la sesión
  de Claude Code que ya tienes).
- **Proporción:** un turno MÍNIMO de Claude Code (decir "hola" sin proyecto) carga
  ~23.400 tokens de contexto. La consulta del router equivale a **~7%** de ese
  mínimo; en conversaciones reales, a menos.

A eso se suma el ahorro por instalar pocas skills en el proyecto en lugar de
colecciones enteras globales: el setup lo estima en cada proyecto (caracteres / 4
del `name` + `description` de cada skill, que se cargan en cada turno) y lo
muestra en `/skill-router:status`. Es una estimación por proyecto, no una cifra
global.

---

## 6. Guion de demo (3 minutos)

Desde `claude-skills-library/`. Los pasos 1, 3 y 4 no necesitan red; el 2 clona
las fuentes en la caché (`~/.cache/skill-router/sources`) la primera vez.

**Paso 0 · El problema (sin comandos).**
> "Cuantas más skills instalas, más contexto gastas en cada turno y peor elige
> Claude. Y las globales viajan a todos tus proyectos."

**Paso 1 · Detectar y planificar.**

```bash
python3 -m autopilot.cli plan --project autopilot/tests/fixtures/video_remotion
python3 -m autopilot.cli plan --project autopilot/tests/fixtures/content_marketing
```

Qué se ve: un proyecto Remotion recibe un solo repo de vídeo (y el alternativo se
descarta por ser una alternativa del mismo escenario); uno de marketing recibe el repo de marketing.

**Paso 2 · Instalar en seco, con ahorro estimado.**

```bash
python3 -m autopilot.cli setup --project autopilot/tests/fixtures/video_remotion --dry-run
```

Qué se ve: qué skills se copiarían, cuáles se saltan (scripts) y la estimación de
tokens antes/después. No se escribe nada en el proyecto.

**Paso 3 · Recomendación gratis (modo léxico).**

```bash
cd skill-router
SKILL_ROUTER_DEMO=1 python3 integration/hooks/route_prompt.py --prompt "guion para un reel" --texto
```

Qué se ve: la nota de una línea que Claude recibiría antes del mensaje.

**Paso 4 · Se puede medir.**

```bash
python3 eval.py --cases bench/heldout.json --compare
```

Qué se ve: léxico 36.7% / 80% y con conceptos 43.3% / 50%. Luego se enseña la
tabla de 4.1 (medida con Haiku): 100% / 100%.
> "No vendemos la capa que se ve bonita en nuestro set de desarrollo. Vendemos lo
> que aguanta un benchmark que no usamos para desarrollar: Haiku, 60 de 60."

**Cierre.**
> "Se activa una vez. En cada proyecto instala poco, lo justo, y lo puedes
> deshacer. En cada mensaje te dice qué skill usar por ~7% de un turno mínimo."

---

## 7. Limitaciones y hoja de ruta

### Limitaciones (hoy)

- **Posible sesgo compartido:** los casos del held-out los escribió un agente
  Claude y los puntúa Claude.
- **Pocas skills:** solo se probó con 6 skills de demo. **Falta la prueba con
  30-50 skills** (PENDIENTE).
- **El set de 19 casos es de desarrollo**, no de evaluación.
- **No medimos el resultado final:** medimos si el router elige bien, no si las
  respuestas de Claude mejoran con él (PENDIENTE).
- **El modo `llm` cuesta** una llamada por mensaje (~1.570 tokens, ~2 s) y
  necesita red y la sesión de Claude Code.
- **Sesiones en la nube** (claude.ai/code) no cargan plugins locales.

### Hoja de ruta

Hecho:

- Router léxico + conceptos, detección de conflictos, scorer LLM, cascada y
  benchmark (`eval.py`, held-out de 60 casos).
- Haiku como modelo por defecto; reducción de tokens por consulta (−49%).
- Hook `UserPromptSubmit` con modos `lexical | cascade | llm` y guardia anti-recursión.
- Plugin con autopilot: detección, plan, instalación por proyecto, deduplicación,
  estimación de ahorro, `status` y `undo`. Suites de tests en verde.

Pendiente:

1. Prueba con 30-50 skills reales (descripciones largas, en inglés).
2. Casos de evaluación escritos por personas, para quitar el sesgo compartido.
3. Prueba A/B de calidad final: mismas tareas con y sin router, con rúbrica y
   tokens.
4. Backend SDK con API key como opción para bajar la base fija por llamada.
5. Aprendizaje a partir de lo que el usuario acepta o descarta.

---

## 8. Cómo reproducir

```bash
cd claude-skills-library/skill-router

# Set de desarrollo (19 casos), sin red
python3 eval.py --compare

# Held-out (60 casos), léxico y conceptos, sin red
python3 eval.py --cases bench/heldout.json --compare

# Held-out con LLM y cascada (una llamada a Claude por caso)
python3 eval.py --cases bench/heldout.json --compare --llm --model haiku
python3 eval.py --cases bench/heldout.json --compare --cascade --model haiku

# Tests
python3 -m unittest discover -s tests
python3 -m unittest discover -s integration/tests
cd .. && python3 -m unittest discover -s autopilot/tests
```

- Los resultados léxicos son deterministas.
- Los del LLM pueden variar entre ejecuciones; `SKILL_ROUTER_CACHE=/carpeta`
  guarda las respuestas para repetir una medición sin volver a llamar al modelo.
- El modelo se cambia con `SKILL_ROUTER_MODEL` (por defecto, `claude-haiku-5-5`).
