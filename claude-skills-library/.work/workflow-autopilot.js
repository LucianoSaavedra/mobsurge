export const meta = {
  name: 'skill-router-autopilot',
  description: 'Build the one-time Claude Code plugin + autopilot that profiles each project, auto-installs curated skills, dedupes, reports token savings and routes prompts',
  phases: [
    { title: 'Spec', detail: 'verify plugin/hook/skill contracts against official docs' },
    { title: 'Build', detail: 'detector+planner, installer+safety, plugin packaging (parallel, disjoint files)' },
    { title: 'E2E', detail: 'end-to-end test on fixture projects and this repo' },
  ],
}

const LIB = '/home/user/mobsurge/claude-skills-library'
const ROUTER = `${LIB}/skill-router`
const AP = `${LIB}/autopilot`

const COMMON = `
Proyecto: "Skill Router", en ${LIB} (repo git /home/user/mobsurge, rama claude/skills-repos-research-k788cw). Ya existen:
- ${LIB}/skills.json: catálogo curado de repos de skills por escenario, con campo trust.
- ${LIB}/install.sh: instalador manual por escenarios.
- ${ROUTER}/: router que recomienda skills por mensaje: skill_router.py, concepts.py, llm_scorer.py, eval.py. También integration/hooks/route_prompt.py, un hook UserPromptSubmit.
OJO: OTRO workflow está editando ahora mismo ${ROUTER} (núcleo, llm_scorer, eval e integration/). NO toques nada dentro de ${ROUTER}. Usa solo su API pública estable: load_from_dir, load_from_catalog, score_skills, classify, detect_conflicts, y el script integration/hooks/route_prompt.py tal cual.

OBJETIVO: el usuario activa el sistema UNA sola vez, como plugin de Claude Code, y desde ahí todo es automático en cada proyecto:
1. La primera vez que abre un proyecto, se detecta el tipo de proyecto.
2. Se eligen las mejores skills del catálogo curado.
3. Se instalan solo en ese proyecto (.claude/skills/).
4. Se eliminan los duplicados.
5. Se informa el ahorro de tokens.
6. En cada mensaje, el router recomienda qué skill usar.
Prioridades: automatización máxima, ahorro de tokens y que el rendimiento no baje.

Protecciones de seguridad que se mantienen siempre:
(a) Solo se instalan repos del catálogo curado, nunca URLs arbitrarias.
(b) Las skills con scripts ejecutables (cualquier archivo que no sea texto o markdown, o con bit de ejecución, o .py/.sh/.js, etc.) se saltan salvo opción explícita allow_scripts.
(c) Todo es deshacible con un comando.
(d) Nunca se tocan las skills globales del usuario (~/.claude/skills). Solo se gestiona <proyecto>/.claude/skills y lo que el propio sistema instaló.

Reglas para todos los agentes:
- Toca solo tus archivos.
- Nada de git commit, push ni cambios de estado de git.
- Solo Python 3 estándar.
- Textos para el usuario en español.
- No inventes resultados: ejecuta y reporta la salida real.
- Temporales en /tmp/autopilot-<rol>/; bórralos al acabar.

Contratos (respétalos exactamente):
C1. autopilot/detect.py: profile_project(root) -> dict con:
    - "root": str
    - "languages": {lang: n_archivos}
    - "frameworks": [str]
    - "signals": [str]   (p.ej. "java", "maven", "web", "docs", "marketing-content", "data", "video-content", "tests")
    - "summary": str     (una línea)
    Debe ser rápido (<1 s en repos medianos): ignora .git, node_modules, venv, build, dist, target, y limita el número de archivos escaneados.
C2. autopilot/plan.py: make_plan(profile, catalog_path, max_repos=3) -> dict con:
    - "scenarios": [str]
    - "repos": [{"name", "url", "scenario", "trust", "reason"}]
    - "skipped": [{"name", "reason"}]
    - "token_budget_note": str
    Usa las señales del perfil. Prefiere trust oficial, y menos repos antes que más (ahorro de tokens). Debe ser determinista.
C3. autopilot/install.py:
    - apply_plan(plan, project_root, allow_scripts=False, dry_run=False, sources_dir=None, max_skills=12) -> dict con:
        "installed": [{"skill", "repo", "path"}]
        "skipped": [{"skill", "repo", "reason"}]
        "dedup_removed": [{"skill", "kept", "similarity"}]
        "token_estimate": {"before_chars", "after_chars", "before_tokens_est", "after_tokens_est", "method"}
        "state_path": str
    - status(project_root) -> dict
    - undo(project_root) -> dict
    El estado se guarda en <project>/.claude/skill-router/state.json. Las fuentes se clonan en una caché compartida (por defecto ~/.cache/skill-router/sources, configurable con sources_dir o SKILL_ROUTER_SOURCES) con git clone --depth 1. Las skills se COPIAN, no se enlazan, en <project>/.claude/skills/<nombre>, y se marcan como gestionadas en el estado. Es idempotente. El ahorro de tokens se estima con chars/4 sobre name+description, y se etiqueta como estimación en "method".
C4. autopilot/cli.py, ejecutable como "python3 -m autopilot.cli <cmd>" desde ${LIB}, o por ruta. Subcomandos:
    - setup [--project DIR] [--auto] [--allow-scripts] [--dry-run] [--json]
    - status [--project DIR] [--json]
    - undo [--project DIR]
    - plan [--project DIR] [--json]
    Usa C1-C3. Salida legible en español, o JSON con --json.`

const SPEC_SCHEMA = {
  type: 'object',
  properties: {
    plugin_layout: { type: 'string', description: 'estructura exacta de archivos de un plugin y de un marketplace, con los campos obligatorios de plugin.json y marketplace.json, citando la documentación' },
    hooks_in_plugins: { type: 'string', description: 'cómo declara hooks un plugin (hooks/hooks.json o en plugin.json), variable de ruta del plugin (p.ej. ${CLAUDE_PLUGIN_ROOT}), timeouts' },
    session_start: { type: 'string', description: 'contrato de SessionStart: campos de entrada (cwd, source...), cómo inyectar contexto, timeout, si puede lanzar procesos en segundo plano' },
    user_prompt_submit: { type: 'string', description: 'contrato de UserPromptSubmit resumido' },
    skills_discovery: { type: 'string', description: 'dónde descubre skills Claude Code (proyecto, usuario, plugins), precedencia, si las skills instaladas a mitad de sesión se ven sin reiniciar, y si existe alguna forma oficial de desactivar skills' },
    commands: { type: 'string', description: 'cómo define un plugin slash commands (commands/*.md), frontmatter y cómo ejecutar un script desde un comando' },
    install_flow: { type: 'string', description: 'comandos exactos para que el usuario añada el marketplace desde un repo de GitHub e instale el plugin' },
    sources: { type: 'array', items: { type: 'string' }, description: 'URLs consultadas' },
    uncertainties: { type: 'array', items: { type: 'string' } },
  },
  required: ['plugin_layout', 'hooks_in_plugins', 'session_start', 'skills_discovery', 'commands', 'install_flow', 'sources', 'uncertainties'],
}

const BUILD = {
  type: 'object',
  properties: {
    files: { type: 'array', items: { type: 'string' } },
    summary: { type: 'string', description: 'qué hiciste y qué verificaste ejecutando, con salidas reales' },
    test_output: { type: 'string' },
    issues: { type: 'array', items: { type: 'string' } },
  },
  required: ['files', 'summary', 'test_output', 'issues'],
}

const SPEC = `${COMMON}
TU ROL: verificar en la documentación OFICIAL de Claude Code cómo se construye todo esto, ANTES de que se escriba código.
Usa WebFetch (sigue las redirecciones) sobre https://code.claude.com/docs/en/plugins, https://code.claude.com/docs/en/plugins-reference, https://code.claude.com/docs/en/plugin-marketplaces, https://code.claude.com/docs/en/hooks, https://code.claude.com/docs/en/skills y https://code.claude.com/docs/en/slash-commands. Si alguna URL no existe, busca la equivalente desde el índice de la documentación.
Si hay un CLI \`claude\` instalado, puedes contrastar con \`claude --help\` y \`claude plugin --help\`. No crees archivos en el repo.
Devuelve un spec preciso con ejemplos JSON mínimos válidos. Cita la fuente de cada afirmación. Marca como incertidumbre lo que no esté documentado: no lo inventes.`

const DETECT = (spec) => `${COMMON}
SPEC verificado de la documentación: ${JSON.stringify(spec).slice(0, 6000)}
TU ROL: detector y planificador. Archivos tuyos:
- ${AP}/__init__.py
- ${AP}/detect.py
- ${AP}/plan.py
- ${AP}/tests/test_detect.py
- ${AP}/tests/test_plan.py
- ${AP}/tests/fixtures/ (proyectos de ejemplo mínimos)
- ${LIB}/skills.json
En skills.json puedes AÑADIR campos a cada repo, p.ej. "signals" (señales de proyecto que lo justifican) y "tags". No borres ni renombres campos existentes: install.sh y el router los leen.
Implementa C1 y C2. Las fixtures deben cubrir al menos: Java/Maven (como este repo, ${'/home/user/mobsurge'}, que tiene pom.xml), web Node/React, Python con data/notebooks, un repo de contenido/marketing (markdown, posts), un repo de vídeo (Remotion o guiones) y un repo vacío.
El plan para un repo vacío o sin señales claras debe ser mínimo: solo lo base o nada, nunca "todo".
Tests con unittest: cd ${LIB} && python3 -m unittest discover -s autopilot/tests -p "test_[dp]*.py" -v. Mide el tiempo de profile_project sobre /home/user/mobsurge.`

const INSTALL = (spec) => `${COMMON}
SPEC verificado de la documentación: ${JSON.stringify(spec).slice(0, 6000)}
TU ROL: instalador con seguridad. Archivos tuyos:
- ${AP}/install.py
- ${AP}/safety.py
- ${AP}/tests/test_install.py
- ${AP}/tests/test_safety.py
Implementa C3. El detector (C1/C2) lo escribe otro agente a la vez: en tus tests construye planes a mano con el formato de C2.
Detalles:
- Escaneo de seguridad (safety.py): clasifica cada carpeta de skill como "solo-instrucciones" o "con-scripts", y explica por qué. Rechaza symlinks que salgan de la carpeta y rutas con "..".
- Deduplicación: usa detect_conflicts del router (importa ${ROUTER} vía sys.path; API estable). Entre skills casi duplicadas conserva la de mayor trust y luego la de descripción más completa, y regístralo en dedup_removed.
- max_skills limita el total instalado (ahorro de tokens).
- Idempotencia: ejecutarlo dos veces no duplica nada.
- undo borra solo lo que el sistema instaló, según state.json.
- Tests SIN red: crea repos git locales en /tmp con skills de ejemplo (unas solo-instrucciones, otras con scripts, duplicadas) y usa sus URLs file://. Con un monkeypatch del catálogo o pasando el plan directamente.
- Haz UNA prueba real con red clonando https://github.com/anthropics/skills en un sources_dir temporal y una instalación dry_run, para comprobar que la ruta real funciona. Si el proxy lo bloquea, repórtalo.
Tests: cd ${LIB} && python3 -m unittest discover -s autopilot/tests -p "test_[is]*.py" -v`

const PLUGIN = (spec) => `${COMMON}
SPEC verificado de la documentación (síguelo al pie de la letra en formatos y rutas): ${JSON.stringify(spec).slice(0, 9000)}
TU ROL: empaquetado como plugin y orquestación automática. Archivos tuyos:
- ${AP}/cli.py (C4)
- ${AP}/bootstrap.py
- ${AP}/tests/test_cli.py
- ${AP}/tests/test_bootstrap.py
- la estructura del plugin con raíz ${LIB}: .claude-plugin/plugin.json, hooks/hooks.json (o lo que diga el spec), commands/*.md, skills/<nombre>/SKILL.md para la skill del plugin
- el marketplace en la raíz del repo: /home/user/mobsurge/.claude-plugin/marketplace.json, apuntando al plugin en ./claude-skills-library
Los módulos detect/plan/install los escriben otros agentes a la vez: programa contra los contratos C1-C3 y en tus tests simúlalos.
Comportamiento:
- bootstrap.py es el hook SessionStart. Lee el JSON de stdin y toma el cwd del proyecto.
  · Si el proyecto ya está configurado (existe state.json), sale rápido y añade como contexto, como mucho, una línea muy corta con las skills activas. Pocos tokens.
  · Si no está configurado, lanza en SEGUNDO PLANO y desacoplado "cli setup --auto --project <cwd>", con log en <project>/.claude/skill-router/setup.log, y devuelve enseguida un contexto breve: "Skill Router está configurando las skills de este proyecto; estarán activas en la próxima sesión". Ajústalo a lo que diga el spec sobre cuándo se descubren las skills.
  · Nunca bloquea ni rompe el arranque: exit 0 y todas las excepciones capturadas.
  · Si la variable SKILL_ROUTER_CHILD=1 está definida, no hace nada (guardia anti-recursión).
  · Si la variable SKILL_ROUTER_AUTOPILOT=0 está definida, no hace nada (desactivado).
  · Usa un lock para que no haya dos setups a la vez.
- hooks.json: registra SessionStart -> bootstrap.py y UserPromptSubmit -> ${'${CLAUDE_PLUGIN_ROOT}'}/skill-router/integration/hooks/route_prompt.py (usa la variable de ruta del plugin que diga el spec). No modifiques route_prompt.py.
- commands: /skill-router:setup, /skill-router:status, /skill-router:undo, /skill-router:plan (o el esquema de nombres que diga el spec). Cada uno ejecuta el cli correspondiente.
- La skill del plugin explica a Claude cuándo consultar el router y el autopilot.
- Valida que los JSON sean válidos y que las rutas que referencian existan. Si el CLI de claude tiene un validador de plugins (p.ej. "claude plugin validate"), úsalo.
Tests: cd ${LIB} && python3 -m unittest discover -s autopilot/tests -p "test_[cb]*.py" -v. Prueba bootstrap por stdin: proyecto nuevo, proyecto configurado, JSON malformado, SKILL_ROUTER_CHILD=1 y SKILL_ROUTER_AUTOPILOT=0.`

const E2E = (ctx) => `${COMMON}
TU ROL: prueba de punta a punta y revisión adversarial del autopilot. Los agentes de construcción terminaron. Sus resúmenes, que pueden estar equivocados: ${JSON.stringify(ctx).slice(0, 10000)}
NO edites el código: solo reporta. Puedes crear proyectos de prueba en /tmp.
1. Ejecuta todas las suites: cd ${LIB} && python3 -m unittest discover -s autopilot/tests -v
2. E2E sin red: copia una fixture a /tmp, define HOME y SKILL_ROUTER_SOURCES en directorios temporales, y ejecuta cli plan, setup --auto (si hace falta red, usa repos file:// como hacen los tests), status y undo. Comprueba que undo deja el proyecto igual que antes (diff de árbol).
3. E2E con red, una sola vez: cli plan sobre /home/user/mobsurge (Java/Maven). Luego setup --auto --dry-run apuntando a una COPIA del repo en /tmp. Nunca sobre el repo real.
4. bootstrap por stdin: tiempo de respuesta (debe ser <1 s), que no bloquee, que el lock funcione si se lanzan 2 a la vez, las guardias de entorno, y que el contexto inyectado sea breve. Cuenta sus caracteres.
5. Plugin: JSON válidos, rutas existentes y coherencia con la documentación oficial (WebFetch https://code.claude.com/docs/en/plugins-reference si hace falta).
6. Seguridad: ¿se puede instalar algo fuera del catálogo? ¿Una skill con scripts se cuela? ¿Path traversal en los nombres de skill? ¿undo puede borrar algo que no instaló?
Reporta los hallazgos verificados con severidad y un fix concreto.`

const E2E_SCHEMA = {
  type: 'object',
  properties: {
    suites: { type: 'string' },
    e2e: { type: 'string', description: 'resultados reales de los pasos 2-5' },
    findings: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          severity: { type: 'string', enum: ['critical', 'high', 'medium', 'low'] },
          file: { type: 'string' },
          title: { type: 'string' },
          detail: { type: 'string' },
          fix: { type: 'string' },
        },
        required: ['severity', 'file', 'title', 'detail', 'fix'],
      },
    },
  },
  required: ['suites', 'e2e', 'findings'],
}

phase('Spec')
const spec = await agent(SPEC, { label: 'spec:docs', phase: 'Spec', schema: SPEC_SCHEMA })
if (!spec) { log('Sin spec verificado: abortando para no construir sobre suposiciones'); return { spec: null } }

phase('Build')
const [detect, install, plugin] = await parallel([
  () => agent(DETECT(spec), { label: 'build:detect+plan', phase: 'Build', schema: BUILD }),
  () => agent(INSTALL(spec), { label: 'build:install+safety', phase: 'Build', schema: BUILD }),
  () => agent(PLUGIN(spec), { label: 'build:plugin+bootstrap', phase: 'Build', schema: BUILD }),
])

phase('E2E')
const e2e = await agent(E2E({ detect, install, plugin }), { label: 'e2e:adversarial', phase: 'E2E', schema: E2E_SCHEMA })
return { spec, detect, install, plugin, e2e }
