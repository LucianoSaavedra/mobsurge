# Skill Router dentro de Claude Code

Esta carpeta conecta el Skill Router con Claude Code para que funcione en cada
mensaje, sin que tengas que ejecutarlo a mano. Tiene dos piezas:

1. **Hook automático** (`hooks/route_prompt.py`). Cada vez que envías un mensaje,
   Claude Code ejecuta el hook antes de que Claude lo lea. El hook puntúa tus
   skills contra el mensaje y, si alguna encaja, le pasa a Claude una nota con las
   skills recomendadas para ese turno, el motivo y las que sobran. Si ninguna
   encaja, el hook no imprime nada y Claude no recibe ninguna nota.
2. **Skill bajo demanda** (`skill-router/SKILL.md`). Sirve para preguntarle a
   Claude "¿qué skills debería usar para esto?" o "¿tengo skills que se
   solapan?". Claude ejecuta el router y te explica el resultado.

Esto es lo que Claude recibe en un turno con recomendación. Es la salida real del
hook con las skills de demo y el mensaje "quiero preparar mi examen de cálculo
con flashcards y un simulacro":

```text
Skill Router: sugerencia automática para este turno (scorer léxico+conceptos; fuente: skills de demo, 6 skills).
Skills que encajan con este mensaje:
- estudio-a-medida [Estudio]: relevancia 60/100; motivo: examen, flashcards, simulacro, ~estudio
Ruido para este turno (no hace falta cargarlas): code-review, debug, deep-research, guiones-video, marca-marketing.
Es una pista orientativa basada en palabras clave: si no encaja con lo que pide el usuario, ignórala. No hace falta mencionarla.
```

La nota no aparece en tu pantalla. Claude Code se la pasa a Claude como un
recordatorio de sistema.

## Archivos

| Archivo | Para qué sirve |
|---|---|
| `hooks/route_prompt.py` | Hook `UserPromptSubmit` (solo Python 3 estándar) |
| `settings.example.json` | Fragmento de `settings.json` que registra el hook |
| `skill-router/SKILL.md` | Skill `/skill-router` para usar el router bajo demanda |
| `skill-router/run_router.py` | Lanzador que encuentra `skill_router.py` desde la skill |
| `tests/test_route_prompt.py` | 31 pruebas del hook y del lanzador (`unittest`) |

## El contrato del hook (comprobado en la documentación oficial)

Fuente: <https://code.claude.com/docs/en/hooks>, consultada el 11-10-2026 desde
Claude Code 2.1.296.

**Entrada.** Claude Code pasa por stdin un objeto JSON con los campos comunes
(`session_id`, `transcript_path`, `cwd`, `permission_mode`, `hook_event_name`)
y el campo propio del evento, `prompt`. Este es el ejemplo de la documentación:

```json
{
  "session_id": "abc123",
  "transcript_path": "/Users/.../.claude/projects/.../00893aaf-19fa-41d2-8238-13269b9b3ca0.jsonl",
  "cwd": "/Users/...",
  "permission_mode": "default",
  "hook_event_name": "UserPromptSubmit",
  "prompt": "Write a function to calculate the factorial of a number"
}
```

El hook usa tres de esos campos:

- `prompt`: el texto que se puntúa. La documentación avisa de que el texto pegado
  llega expandido entre una línea `<pasted_content id="…">` y otra
  `</pasted_content id="…">`. El hook borra esas dos líneas y conserva el texto.
- `hook_event_name`: si llega y no es `UserPromptSubmit`, el hook no hace nada.
- `cwd`: para encontrar también las skills del proyecto (`<cwd>/.claude/skills`).
  También usa la variable de entorno `CLAUDE_PROJECT_DIR`, que según la
  documentación es "the project root where the session started".

**Salida.** Hay dos formas de añadir contexto cuando el hook sale con código 0.
Según la documentación:

> * **Plain text stdout**: Claude Code adds stdout it treats as plain text to Claude's context
> * **JSON with `additionalContext`**: use the JSON format below for more control. The `additionalContext` field is added as context
>
> Neither channel produces a visible transcript entry. Plain stdout and the `additionalContext` value are each injected as a system reminder that starts with the hook's name; Claude reads both.

Este hook usa la forma JSON. Si hay recomendación, imprime un solo objeto JSON y
nada más:

```json
{"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": "Skill Router: ..."}}
```

La documentación pide que stdout contenga solo ese objeto: "Print nothing but the
JSON object to stdout". El JSON se escribe con caracteres no ASCII escapados
(`\u00e1` en vez de `á`), así la salida es válida aunque la consola no use UTF-8.

**Reglas del contrato que el hook respeta:**

- **Nunca bloquea.** El código 2 y `"decision": "block"` rechazan el prompt. El
  hook no usa ninguno de los dos y sale siempre con 0. Si algo falla (JSON roto,
  router no encontrado, error inesperado), escribe una línea en stderr y sale con
  0. Según la documentación, "Claude never sees stderr from a hook that exits 0".
- **Silencio si no hay recomendación.** Con stdout vacío, Claude no recibe nada.
- **Tamaño.** `additionalContext` tiene un tope de 10.000 caracteres. Por encima,
  Claude Code guarda el texto en un archivo y a Claude solo le pasa la ruta y un
  avance. El hook recorta su nota a 2.000 caracteres y lista como máximo 8 skills
  de ruido. Las notas medidas en las pruebas ocupan entre 485 y 760 caracteres.
- **Tiempo.** En `UserPromptSubmit` el `timeout` por defecto es 30 s. Si se
  agota, la documentación dice que el hook "is canceled and its output, including
  any `additionalContext`, is discarded". En modo léxico el hook tarda de media
  entre 37 y 69 ms (ver [Rendimiento medido](#rendimiento-medido)).
- **Sin `matcher`.** `UserPromptSubmit` no admite matcher: el hook se ejecuta en
  todos los mensajes.

## Instalación paso a paso en tu laptop (macOS o Linux)

**Requisitos:** Claude Code y `python3`. Se probó con Python 3.13.16 y Claude
Code 2.1.296. No hace falta instalar paquetes.

**1. Anota la ruta absoluta del router.**

```bash
cd /ruta/donde/clonaste/claude-skills-library/skill-router
pwd   # p.ej. /Users/ana/code/claude-skills-library/skill-router
```

En los pasos siguientes, `ROUTER` es esa ruta.

**2. Prueba el hook a mano.** Así compruebas que Python y el router funcionan
antes de tocar Claude Code:

```bash
echo '{"hook_event_name":"UserPromptSubmit","prompt":"guion para un reel de tiktok"}' \
  | SKILL_ROUTER_DEMO=1 python3 ROUTER/integration/hooks/route_prompt.py
```

Tiene que imprimir un JSON con `hookSpecificOutput`. Para leer la nota sin
escapes, usa `--prompt` y `--texto`:

```bash
python3 ROUTER/integration/hooks/route_prompt.py --prompt "revisa este pull request" --texto
```

Sin `SKILL_ROUTER_DEMO=1`, el hook usa tus skills reales de `~/.claude/skills`.
Si con ellas no imprime nada, ninguna skill superó el umbral para ese mensaje.

**3. Registra el hook.** Abre `~/.claude/settings.json` (o créalo) y añade el
bloque `hooks` de [`settings.example.json`](settings.example.json). Cambia el
marcador `/RUTA/ABSOLUTA/A/claude-skills-library/skill-router` por tu `ROUTER`:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"/Users/ana/code/claude-skills-library/skill-router/integration/hooks/route_prompt.py\"",
            "timeout": 30,
            "statusMessage": "Skill Router: eligiendo skills..."
          }
        ]
      }
    ]
  }
}
```

- Si tu `settings.json` ya tiene una clave `hooks`, añade la entrada
  `UserPromptSubmit` dentro de ella en vez de crear otra clave `hooks`. Si ya
  tienes una lista `UserPromptSubmit`, añade el objeto a esa lista.
- Usa la ruta absoluta entre comillas. Claude Code ejecuta el comando con
  `sh -c`, y `~` no se expande dentro de comillas.
- Para activarlo en un solo proyecto, pon el bloque en `.claude/settings.json`
  (compartido con el equipo) o en `.claude/settings.local.json` (solo tú).
- Según la documentación, Claude Code detecta solo los cambios en los hooks de
  `settings.json`. Si no ves el hook, reinicia Claude Code.

**4. Instala la skill bajo demanda.** Enlázala; no la copies, porque así el
lanzador encuentra el router siguiendo el enlace:

```bash
mkdir -p ~/.claude/skills
ln -s "ROUTER/integration/skill-router" ~/.claude/skills/skill-router
```

Si prefieres copiarla, define `SKILL_ROUTER_HOME=ROUTER` (ver
[Configuración](#configuración)). Reinicia Claude Code para que cargue la skill.

La skill trae `allowed-tools: Bash(python3 ${CLAUDE_SKILL_DIR}/run_router.py *)`,
así que ejecutar el router no pide permiso. Lo que sí puede pedirlo es invocar la
skill. En la prueba con `claude -p`, la primera invocación se denegó hasta pasar
`--allowedTools "Skill(skill-router)"`. En modo interactivo, Claude Code te lo
pregunta. Para no tener que aprobarlo cada vez, añade la regla a tu
`settings.json`:

```json
{ "permissions": { "allow": ["Skill(skill-router)"] } }
```

**5. Comprueba que funciona.**

- Escribe `/hooks` en Claude Code. Es un visor de solo lectura y debe listar el
  hook `UserPromptSubmit` de tu configuración de usuario.
- Arranca con `claude --debug-file /tmp/cc-debug.txt`, envía un mensaje que encaje
  con alguna de tus skills y busca en el log:

  ```bash
  grep -E "Hooks: UserPromptSubmit|provided additionalContext" /tmp/cc-debug.txt
  ```

  Si el hook recomendó algo, verás líneas como estas (salida real de la prueba):

  ```text
  Hooks: UserPromptSubmit [python3 ".../integration/hooks/route_prompt.py"] finished with status 0 (90ms)
  Hook UserPromptSubmit (Skill Router: eligiendo skills...) provided additionalContext (485 chars)
  ```

- Pregunta a Claude: "¿qué skills debería usar para preparar un examen?". En las
  pruebas, Claude eligió la skill `skill-router` por su descripción, sin que se
  la nombraran.

**Windows.** Claude Code ejecuta los hooks con Git Bash, o con PowerShell si Git
Bash no está instalado. Si tu Python se llama `python` en vez de `python3`,
cámbialo en `command`. En `SKILL_ROUTER_SKILLS_DIR` las rutas se separan con `;`.
Esto no se ha probado en Windows.

## Cómo probarlo

**Pruebas automáticas.** No usan tus skills reales: ponen `HOME` en una carpeta
temporal.

```bash
cd ROUTER
python3 integration/tests/test_route_prompt.py -v
```

Resultado real: `Ran 31 tests ... OK`. Las pruebas cubren estos casos:

- Estudio, video, negativo, JSON malformado y stdin vacío.
- JSON que no es un objeto, prompt ausente, otro evento y comandos `/...`.
- UTF-8 inválido.
- Guardia de recursión e interruptor `OFF`.
- Router no encontrado.
- Umbral y top configurables.
- Skills personales, del proyecto, de una carpeta explícita, del catálogo, o
  ninguna; nombres repetidos cuentan una vez.
- La autoexclusión de `skill-router`.
- El scorer LLM, con un `claude` falso y sin backend.
- El lanzador de la skill, incluido el caso de enlace simbólico.

**Pruebas a mano.** Estas son las salidas reales con las skills de demo
(`SKILL_ROUTER_DEMO=1`):

| Entrada por stdin | Exit | Salida |
|---|---|---|
| `"prompt": "quiero preparar mi examen de cálculo con flashcards y un simulacro"` | 0 | JSON. Recomienda `estudio-a-medida` (60/100; motivo: examen, flashcards, simulacro, ~estudio) |
| `"prompt": "mañana tengo la prueba de biología, ayúdame a repasar"` | 0 | Nada. `estudio-a-medida` saca 11/100, por debajo del umbral de 15 |
| `"prompt": "escríbeme un guion para un reel de tiktok con buen gancho"` | 0 | JSON. Recomienda `guiones-video` (66/100; motivo: guion, reel, tiktok, gancho, ~video) |
| `"prompt": "hola"` | 0 | Nada |
| `{"prompt": "examen de calculo", ` (JSON malformado) | 0 | Nada. En stderr: `[skill-router hook] error ignorado: JSONDecodeError: ...` |
| stdin vacío | 0 | Nada |

**De principio a fin dentro de Claude Code.** Se lanzó un `claude -p` anidado
(Claude Code 2.1.296) con el hook registrado mediante `--settings` y un log de
depuración. El mensaje era "Necesito un guion para un reel de tiktok…":

- El log registró `finished with status 0 (90ms)` y `provided additionalContext
  (485 chars)`.
- Al preguntarle, Claude copió literalmente la nota con `guiones-video`.

En una segunda ejecución, `SKILL_ROUTER_DEMO=1` se pasó solo por la clave `env`
de los settings. El hook la recibió: Claude respondió que se recomendaba
`guiones-video`.

**La skill de principio a fin.** Se enlazó `skill-router/` en el `.claude/skills`
de un proyecto temporal y se lanzó `claude -p` con
`--allowedTools "Skill(skill-router)"`. Las preguntas fueron "¿Qué skills debería
usar para revisar un pull request por bugs de seguridad? ¿Y tengo skills que se
solapen?":

- Claude eligió la skill por su descripción.
- Ejecutó `run_router.py --json '…'` y `run_router.py --conflicts --json`. Las dos
  llamadas se aprobaron solas gracias a `allowed-tools` (`permission_denials: []`).
- Contestó en español. Descartó la recomendación léxica errónea (ver
  [Limitaciones](#limitaciones-leer-antes-de-fiarse)) y explicó los solapamientos.

En un intento anterior, Claude antepuso `cd … &&` al comando. Esa forma no está
preaprobada y se denegó. Por eso `SKILL.md` ahora pide lanzar cada comando por
separado y en su forma simple.

## Configuración

Todas las variables son opcionales. Puedes exportarlas antes de lanzar `claude` o
ponerlas en la clave `env` de `settings.json`. La segunda forma se comprobó de
principio a fin.

| Variable | Efecto |
|---|---|
| `SKILL_ROUTER_HOOK_OFF=1` | Apaga el hook sin tocar la configuración de hooks |
| `SKILL_ROUTER_HOOK_THRESHOLD` | Puntuación mínima (0-100) para recomendar. Por defecto, 15, igual que el CLI |
| `SKILL_ROUTER_HOOK_TOP` | Máximo de skills recomendadas (1-10). Por defecto, 3 |
| `SKILL_ROUTER_HOOK_LLM=1` | Usa Claude como scorer: el SDK con `ANTHROPIC_API_KEY`, o si no `claude -p`. Si falla, vuelve al léxico |
| `SKILL_ROUTER_LLM_TIMEOUT` | Segundos máximos de la llamada LLM. En el hook, 20 por defecto |
| `SKILL_ROUTER_SKILLS_DIR` | Carpeta o carpetas de skills, separadas por `:` (`;` en Windows). Sustituye a `~/.claude/skills` y a las del proyecto |
| `SKILL_ROUTER_CATALOG` | Usa ese `skills.json` en vez de las skills instaladas |
| `SKILL_ROUTER_DEMO=1` | Usa las 6 skills de demo (para probar) |
| `SKILL_ROUTER_HOME` | Carpeta del router, si moviste o copiaste el hook o la skill |
| `SKILL_ROUTER_HOOK_DEBUG=1` | Escribe en stderr el diagnóstico y los tiempos de carga, de puntuación y total |

**De dónde saca las skills el hook**, en orden:

1. `SKILL_ROUTER_DEMO`, `SKILL_ROUTER_CATALOG` o `SKILL_ROUTER_SKILLS_DIR`, si
   están definidas.
2. Si no, `~/.claude/skills` más `.claude/skills` del proyecto (de
   `CLAUDE_PROJECT_DIR` y de `cwd`), sin repetir nombres.
3. Si no hay ninguna skill, el catálogo `claude-skills-library/skills.json`. En
   ese caso la nota avisa de que son colecciones del catálogo y de que quizá no
   estén instaladas.

El hook nunca recomienda la skill `skill-router`, porque ya hace su trabajo.

**Modo LLM.** Es más preciso, pero más lento. En el set de 19 casos de `eval.py`,
el núcleo midió un 100 % de acierto top-1 con el LLM, frente al 84,2 % de
léxico+conceptos. Esa medición es del núcleo, no de este hook. Aquí se probó una
vez con `claude -p`: tardó 5,9 s y recomendó `estudio-a-medida` (95/100) para
"mañana tengo la prueba de biología, ayúdame a repasar", un mensaje con el que el
modo léxico no recomienda nada.

- Consume una llamada al modelo por cada mensaje.
- Mantén `SKILL_ROUTER_LLM_TIMEOUT` por debajo del `timeout` del hook. Si no, el
  hook se cancela y la nota se pierde.
- Para evitar bucles, el hook pone `SKILL_ROUTER_HOOK_ACTIVE=1` antes de llamar a
  `claude -p`. El Claude hijo ejecutaría este mismo hook, y el hook sale en
  silencio si ve esa variable. Una prueba con un `claude` falso comprueba que el
  proceso hijo hereda la variable.

## Cómo desactivarlo

- **Del todo:** borra la entrada `UserPromptSubmit` de tu `settings.json`. Para
  quitar la skill, borra el enlace: `rm ~/.claude/skills/skill-router`.
- **Sin borrar nada:** define `SKILL_ROUTER_HOOK_OFF=1` (en tu shell o en `env`
  de `settings.json`). El hook seguirá ejecutándose, pero sale al instante sin
  imprimir nada.
- **Todos los hooks a la vez:** pon `"disableAllHooks": true` en tu settings.
- **Solo para una sesión:** `claude --settings '{"disableAllHooks": true}'`. Según
  la documentación, esto tiene prioridad sobre los settings de proyecto y
  locales.

## Rendimiento medido

Se hicieron dos tandas de 30 ejecuciones por caso. Cada ejecución es un proceso
aparte, igual que lo lanza Claude Code, en el contenedor de desarrollo con Python
3.13. La tabla da el rango de las medias de las dos tandas y el máximo de todas
las ejecuciones:

| Caso | Media | Máximo |
|---|---|---|
| Recomienda (demo, 6 skills) | 56–69 ms | 92 ms |
| Sin recomendación (`hola` y estudio por sinónimos, demo) | 63–65 ms | 103 ms |
| JSON malformado o stdin vacío (sale antes de cargar el router) | 37–39 ms | 50 ms |
| Skills reales de `~/.claude/skills` (14 skills) | 59–66 ms | 79 ms |
| Catálogo `skills.json` (9 entradas) | 65 ms | 78 ms |

Unos 38 ms de cada ejecución son el arranque de Python y la lectura del evento:
es lo que tarda el caso de JSON malformado, que sale antes de cargar el router.
Importar el router, cargar las skills y puntuarlas añade entre 15 y 25 ms,
según `SKILL_ROUTER_HOOK_DEBUG=1`. Dentro de Claude Code, el log registró 90 y
103 ms en total.

## Limitaciones (leer antes de fiarse)

- **El scorer por defecto es léxico.** Compara palabras y una lista curada de
  sinónimos, así que se equivoca. Ejemplos reales en el contenedor de pruebas,
  con 14 skills reales que tienen descripciones en inglés:
  - "revisa este pull request por bugs de seguridad" recomendó `computer-use`,
    `google-workspace` y `morning` (24/100 cada una). La causa es la palabra
    `request`, que aparece en sus descripciones. Ninguna de las tres sirve para
    esa tarea.
  - "haz un informe en pdf con una tabla de excel" recomendó `google-workspace`
    (27/100; motivo: pdf, excel) y `skill-creator` (15/100, por el concepto
    `~investigacion`). En cambio, dejó como ruido la skill `pdf`, que era la que
    encajaba.

  Por eso la nota dice que es una pista orientativa y que Claude la ignore si no
  encaja. Para más precisión, usa `SKILL_ROUTER_HOOK_LLM=1`.
- **Los mensajes largos bajan la puntuación.** La puntuación es la fracción del
  peso del mensaje que coincide con la skill. Con las skills de demo, "escríbeme
  un guion para un reel de tiktok con buen gancho" saca 66/100. El mismo mensaje
  con una pregunta extra añadida bajó a 18/100. Un mensaje largo puede quedar por
  debajo del umbral aunque la skill encaje.
- **Recomienda, pero no activa ni desactiva skills.** Claude Code sigue listando
  todas tus skills en su contexto. El hook solo añade la nota, así que no ahorra
  tokens: los suma (entre 485 y 760 caracteres por nota en las pruebas).
- **Solo ve las skills de `~/.claude/skills` y `.claude/skills` del proyecto.**
  No ve las skills de plugins, las integradas ni las gestionadas por la
  organización. Tampoco lee `disable-model-invocation`, así que puede sugerir una
  skill que Claude no puede cargar por sí mismo.
- **También se ejecuta en turnos que no escribiste tú.** Según la documentación,
  `UserPromptSubmit` se ejecuta también cuando se dispara una tarea programada
  (incluido `/loop`), cuando un subagente en segundo plano informa de su
  resultado y cuando llega un mensaje de otra sesión. La documentación no da
  ningún campo para distinguir esos turnos, así que el hook también puntúa ese
  texto.
- **Los comandos `/algo` se ignoran a propósito.** En ese caso ya elegiste el
  comando o la skill.
- **Con el catálogo de reserva recomienda colecciones (repos), no skills
  instaladas.** La nota lo avisa.
- **La ruta de la skill no debe tener espacios.** `allowed-tools` usa
  `${CLAUDE_SKILL_DIR}` sin comillas, igual que el ejemplo de la documentación. Si
  la ruta tiene espacios, Claude Code te pedirá permiso para ejecutar el comando.
- **No se probó en Windows ni en macOS.** Las pruebas se hicieron en Linux.
