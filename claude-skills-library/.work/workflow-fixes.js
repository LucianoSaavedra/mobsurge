export const meta = {
  name: 'skill-router-fixes',
  description: 'Apply the 27 confirmed review findings with file-owned fixers (core, llm, eval, integration) and verify each suite',
  phases: [
    { title: 'Fix', detail: 'core -> integration chained; llm and eval in parallel' },
    { title: 'Check', detail: 'independent regression check of the whole tree' },
  ],
}

const ROOT = '/home/user/mobsurge/claude-skills-library/skill-router'
const FINDINGS = '/tmp/review_findings.json'

const COMMON = `
Proyecto "Skill Router" en ${ROOT} (repo git /home/user/mobsurge). Recomienda qué skills de Claude Code usar según el mensaje del usuario.
Una revisión adversarial dejó hallazgos CONFIRMADOS en ${FINDINGS} (JSON con la lista "confirmed"; cada hallazgo tiene id, file, severity, title, detail, evidence, fix y verdict). Si verdict.better_fix existe, SUPERA al fix original: el escéptico demostró con datos que el original era peor. Lee los hallazgos que te tocan COMPLETOS antes de tocar código.

Reglas para todos:
- Varios agentes editan a la vez: toca SOLO tus archivos.
- No hagas commit, push ni cambios de estado de git.
- Solo Python estándar.
- No ajustes umbrales ni parámetros mirando bench/heldout.json: es el set independiente y se contaminaría. Para desarrollar usa el set inline de eval.py, casos que construyas tú en /tmp o las skills reales que haya en la máquina (~/.claude/skills, /mnt/skills si existe). El held-out solo se mide al final, y eso lo hace el orquestador.
- Nada de llamadas reales al LLM (claude -p), salvo que tu rol diga lo contrario.
- Cada arreglo lleva un test que fallaría sin él. No debilites tests existentes. Los 3 tests que fallan hoy (version true, comentario tras comillas, comentario HTML) deben pasar arreglando el CÓDIGO, no el test. Si de verdad crees que un test está mal, explícalo en issues.
- Español en textos para el usuario.
- Al terminar ejecuta tus suites y reporta la salida real.

Contratos entre módulos (respétalos exactamente; otros agentes los usan):
1. skill_router.normalize_query(text: str) -> str. Quita lo pegado que no es la petición (bloques \`\`\`...\`\`\`, contenido entre marcadores <pasted_content ...>...</pasted_content>, líneas de traceback/log, líneas con sangría de código, líneas dominadas por símbolos). Si no queda texto útil, devuelve el original.
2. skill_router.score_skills(skills, query, use_concepts=True, normalize=True): con normalize=True aplica normalize_query antes de tokenizar.
3. skill_router.classify(results, top, threshold, min_matched=1): una skill solo se recomienda si además tiene al menos min_matched coincidencias distintas en "matched". Los tokens "concept:x" cuentan como coincidencia, pero el agente del núcleo puede decidir que no cuenten por sí solos si lo justifica. Con el valor por defecto se mantiene el comportamiento actual.
4. Guardia anti-recursión: cuando llm_scorer lanza el subproceso \`claude\`, pone en su entorno SKILL_ROUTER_CHILD=1. Si el hook ve SKILL_ROUTER_CHILD=1 en su entorno, sale en silencio con exit 0 sin hacer nada.
5. Uso de tokens: llm_scorer.llm_score(skills, query, backend=None, model=None, usage=None). Si usage es un dict, se rellena con {"backend", "model", "input_tokens", "output_tokens", "cache_read_tokens", "cost_usd", "latency_s", "cached"}. Usa None en los campos que el backend no informe; nunca los inventes. cached=True significa que vino de SKILL_ROUTER_CACHE y no se gastaron tokens.
6. Cascada: nuevo módulo cascade.py con cascade_score(skills, query, high=..., margin=..., model=None, usage=None) -> (results, used_llm: bool). Primero puntúa con el léxico+conceptos. Si la decisión léxica es confiable (top >= high y ventaja sobre el #2 >= margin, o un criterio claro y justificado para "nada aplica"), devuelve el resultado léxico sin llamar al LLM. Si no, llama a llm_score. Los valores por defecto de high y margin se fijan sobre el set inline o casos propios, nunca sobre el held-out.

OBJETIVO DEL PROYECTO (tenlo presente): además de recomendar bien, el router existe para AHORRAR TOKENS y hacer que el plan Pro de Claude rinda más. Cada llamada LLM gasta cuota. Se mide en: llamadas al LLM evitadas, tokens por consulta, aciertos por token y latencia. El modo LLM no puede ser el único camino: el camino barato (léxico/cascada) tiene que ser bueno y estar medido.

API pública que no puede romperse (solo se pueden añadir kwargs opcionales): tokenize, strip_accents, Skill, parse_frontmatter, load_from_dir, load_from_catalog, demo_skills, score_skills, classify, detect_conflicts, main; llm_scorer: llm_score, available_backend, build_prompt, LLMUnavailable, _extract_json, _call_cli, _call_sdk.`

const RESULT = {
  type: 'object',
  properties: {
    fixed: { type: 'array', items: { type: 'string' }, description: 'ids de hallazgos resueltos, con una línea de cómo' },
    not_fixed: { type: 'array', items: { type: 'string' }, description: 'ids no resueltos y por qué' },
    files: { type: 'array', items: { type: 'string' } },
    test_output: { type: 'string', description: 'últimas líneas reales de tus suites' },
    measurements: { type: 'string', description: 'mediciones reales antes/después (inline, /tmp, skills reales) — nunca held-out' },
    issues: { type: 'array', items: { type: 'string' } },
  },
  required: ['fixed', 'not_fixed', 'files', 'test_output', 'issues'],
}

const CORE = `${COMMON}
TU ROL: núcleo léxico. Archivos tuyos: ${ROOT}/skill_router.py, ${ROOT}/concepts.py, ${ROOT}/tests/test_router.py, ${ROOT}/tests/test_concepts.py, ${ROOT}/tests/test_cli.py (nuevo), ${ROOT}/tests/_support.py.
Hallazgos que te tocan: core-1, core-2, core-7, core-8 y la parte de skill_router.py de tests-1 (tests de las flags del CLI que usa el guion de demo: --demo, --catalog, --skills-dir, --json, --no-concepts, --conflicts, --llm con backend simulado/ausente, --top, --threshold).
- core-1: implementa normalize_query (contrato 1) e intégralo en score_skills (contrato 2). Sigue verdict.better_fix. Además añade classify(min_matched) (contrato 3). No apliques el fix de "solo términos conocidos". El tope K del denominador es opcional: aplícalo solo si lo justificas con datos que no salgan del held-out.
- core-2: quita de concepts.py los disparadores genéricos que meten dominio falso en descripciones reales en inglés (p.ej. request, email, copy, test; revisa el hallazgo). Arregla que "estudio" pertenezca a dos conceptos. Mide con las skills reales de la máquina antes y después.
- core-7, core-8: arregla parse_frontmatter y _first_paragraph para que pasen los 2 tests que fallan.
- CLI: añade --cascade (usa cascade.cascade_score del contrato 6; si cascade.py aún no existe o falla, cae al léxico con aviso) y --model <id> (se pasa al LLM). Cuando se use el LLM, muestra en la salida los tokens y la latencia del contrato 5, y si hubo caché. Añade los tests de estas flags en tests/test_cli.py, con backends simulados.
Verifica: python3 -m unittest discover -s tests -v (todo verde; los tests de eval.py son de otro agente, avisa si alguno falla por tus cambios), python3 eval.py --compare (set inline: reporta los números; pueden cambiar, explica por qué) y casos largos con traceback pegado, sobre demo y sobre skills reales.`

const INTEG = (core) => `${COMMON}
TU ROL: integración. El agente del núcleo ya terminó. Resumen: ${core ? JSON.stringify(core).slice(0, 3500) : '(no disponible: lee skill_router.py)'}
Archivos tuyos: ${ROOT}/integration/ completo (hooks/route_prompt.py, skill-router/SKILL.md, skill-router/run_router.py, settings.example.json, README.md, tests/).
Hallazgos que te tocan: int-1, int-2, int-5, int-7, tests-2, tests-3, tests-4. La parte de documentación de docs-2 en integration/README.md también es tuya.
- int-1: añade al hook un filtro de precisión. Debe ser conservador: es mejor no inyectar nada que inyectar algo equivocado. Usa classify(min_matched=...) y/o margen frente al #2. Mide la tasa de inyecciones equivocadas con las skills reales de la máquina, usando mensajes que construyas tú (nunca bench/heldout.json), y reporta antes/después.
- int-2: usa normalize_query ANTES de recortar, y recorta de forma que se conserve lo que el usuario tecleó.
- int-5, int-7: que hook y skill descubran las mismas skills (proyecto, subiendo hasta la raíz git, más usuario), sin contarse a sí mismas.
- Guardia anti-recursión del contrato 4 en el hook, con su test.
- tests-2/3/4: tests aislados (cwd y HOME temporales), que comprueben stderr y que fallen si se intenta lanzar un \`claude\` real (por ejemplo con un shim en PATH que falle).
Verifica el contrato del hook UserPromptSubmit contra https://code.claude.com/docs/en/hooks si cambias la forma de salida.
Ejecuta: python3 -m unittest discover -s integration/tests -v y pruebas manuales por stdin (estudio, video, negativo, traceback pegado, JSON malformado, SKILL_ROUTER_CHILD=1).`

const LLM = `${COMMON}
TU ROL: scorer LLM. Archivos tuyos: ${ROOT}/llm_scorer.py, ${ROOT}/tests/test_llm_scorer.py.
Hallazgos que te tocan: core-3, core-4, core-5, core-6, int-3, int-4, int-6 y la parte de código de docs-2.
- Decodifica la salida del CLI como UTF-8 de forma explícita.
- Pasa el prompt por stdin, no por argv (evita E2BIG).
- Aplica SKILL_ROUTER_LLM_TIMEOUT también al SDK, sin reintentos largos.
- Lanza \`claude -p\` lo más ligero y aislado posible: sin herramientas, sin MCP, sin hooks del usuario, sin persistir la sesión. Usa SOLO flags que existan de verdad: comprúebalas con \`claude --help\`. Pon SKILL_ROUTER_CHILD=1 en el entorno del hijo (contrato 4).
- Mantén el modelo configurable (SKILL_ROUTER_MODEL, por defecto claude-opus-5-5) y la caché SKILL_ROUTER_CACHE con escritura atómica.
Puedes hacer como mucho 3 llamadas reales a \`claude\` para comprobar que las flags elegidas funcionan (devuelve JSON válido, no usa herramientas). Todos los tests usan backends simulados.
- Contrato 5: obtén el uso REAL de tokens. En el CLI usa --output-format json y lee los campos de uso, coste y resultado que de verdad devuelva (compruébalo con una llamada real; no supongas los nombres de los campos). En el SDK, msg.usage. Mide la latencia.
- Parámetro model (contrato 5): si no viene, usa SKILL_ROUTER_MODEL o claude-opus-5-5. Comprueba con una llamada real que claude-haiku-5-5 funciona como modelo del router.
- Prompt compacto para gastar menos: solo nombre + descripción recortada a una longitud razonable por skill (configurable), instrucciones breves y salida JSON mínima. Reporta el tamaño del prompt antes/después (caracteres y tokens reales de input de una llamada).
- La clave de la caché incluye el modelo.
- Crea cascade.py (contrato 6) con sus tests en tests/test_cascade.py usando backends simulados. Fija high y margin por defecto con el set inline de eval.py o casos propios, nunca con bench/heldout.json, y documenta en el docstring cómo los elegiste.
Archivos adicionales tuyos: ${ROOT}/cascade.py y ${ROOT}/tests/test_cascade.py.
Llamadas reales a claude permitidas: hasta 6 en total (flags, formato json, haiku).
Ejecuta: python3 -m unittest tests.test_llm_scorer tests.test_cascade -v (desde ${ROOT}).`

const EVAL = `${COMMON}
TU ROL: evaluador y benchmark. Archivos tuyos: ${ROOT}/eval.py, ${ROOT}/tests/test_eval.py, ${ROOT}/bench/ (salvo heldout.json, que NO se modifica; puedes añadir archivos nuevos).
Hallazgos que te tocan: bench-1, bench-2, bench-3, bench-4, bench-5 y la parte de eval.py de tests-1.
- bench-1: además de la precisión con umbral, reporta una métrica de ranking que no dependa del umbral (por ejemplo, hit@1 del ranking bruto y MRR sobre los casos positivos) y la tasa de falsos positivos en negativos.
- bench-2: los empates en el #1 no pueden contar como acierto por orden alfabético. Cuéntalos como fallo o con crédito fraccionario, documéntalo y hazlo determinista.
- bench-3: para los casos multi añade una métrica "completa" (todas las intenciones esperadas aparecen en las recomendadas).
- Añade una métrica de sobre-recomendación: de las skills recomendadas, cuántas no están en expected (precisión de las recomendaciones). El LLM suele recomendar 2-3.
- bench-4: version debe ser el entero 1 exactamente (ni bool ni float).
- bench-5: mide la similitud del held-out con el set inline y documenta el resultado en bench/README.md, sin tocar heldout.json.
- Nuevo set de estrés derivado mecánicamente: bench/make_stress.py genera bench/stress.json a partir de los casos de heldout.json con la misma etiqueta, añadiendo (a) un traceback o log pegado, (b) una frase de contexto neutra larga, (c) el texto dentro de un bloque <pasted_content>. Es una transformación determinista y sin juicio, así que no contamina el held-out. eval.py debe poder evaluarlo con --cases.
Mantén el formato JSON de --json compatible: solo añade campos.
- Economía de tokens: añade --cascade (usa cascade.cascade_score del contrato 6) y --model <id>. Para cada scorer que use el LLM, reporta número de llamadas al LLM (y % evitadas en cascada), tokens de entrada/salida totales y por consulta, coste si lo hay, latencia media y "aciertos top-1 por cada 1000 tokens". Usa el dict usage del contrato 5; si un campo viene None, muéstralo como "n/d", nunca lo inventes. Inclúyelo también en --json. Con --compare --llm --cascade debe salir una tabla única: baseline, +conceptos, cascada y LLM.
- Puedes añadir --llm-models opus,haiku (o ids completos) para comparar modelos en una sola ejecución.
Ejecuta: python3 -m unittest tests.test_eval -v, python3 eval.py --compare y python3 eval.py --compare --cases bench/stress.json (sin --llm; para --llm/--cascade usa tests con backend simulado).`

const CHECK = (r) => `${COMMON}
TU ROL: comprobación final de regresiones. Los 4 agentes de arreglos terminaron. Resúmenes (pueden equivocarse): ${JSON.stringify(r).slice(0, 9000)}
NO edites nada. Ejecuta y reporta la salida real de:
1. cd ${ROOT} && python3 -m unittest discover -s tests -v  y  python3 -m unittest discover -s integration/tests -v
2. python3 eval.py --compare ; python3 eval.py --compare --cases bench/stress.json  (sin --llm)
3. Hook por stdin: estudio, video, "hola", traceback pegado, JSON malformado y SKILL_ROUTER_CHILD=1.
4. Comprueba que los contratos 1-6 se cumplen (para 5 y 6 usa backends simulados; no hagas llamadas reales) y que la API pública sigue intacta: importa cada nombre.
5. git -C /home/user/mobsurge status --porcelain: ¿hay archivos basura (pycache, /tmp, caches) que no deberían commitearse?
Reporta cualquier regresión o incoherencia entre agentes (p.ej. el hook usa un kwarg que el núcleo no implementó).`

const CHECK_SCHEMA = {
  type: 'object',
  properties: {
    suites: { type: 'string', description: 'resultado real de las suites (n tests, fallos)' },
    eval_inline: { type: 'string' },
    eval_stress: { type: 'string' },
    hook_checks: { type: 'string' },
    problems: { type: 'array', items: { type: 'string' } },
  },
  required: ['suites', 'eval_inline', 'eval_stress', 'hook_checks', 'problems'],
}

phase('Fix')
const done = await parallel([
  () => agent(CORE, { label: 'fix:core', phase: 'Fix', schema: RESULT })
          .then(core => agent(INTEG(core), { label: 'fix:integration', phase: 'Fix', schema: RESULT })
          .then(integ => ({ core, integ }))),
  () => agent(LLM, { label: 'fix:llm', phase: 'Fix', schema: RESULT }),
  () => agent(EVAL, { label: 'fix:eval', phase: 'Fix', schema: RESULT }),
])
const [ci, llm, ev] = done
const summary = { core: ci ? ci.core : null, integration: ci ? ci.integ : null, llm, eval: ev }

phase('Check')
const check = await agent(CHECK(summary), { label: 'check:regressions', phase: 'Check', schema: CHECK_SCHEMA })
return { ...summary, check }
