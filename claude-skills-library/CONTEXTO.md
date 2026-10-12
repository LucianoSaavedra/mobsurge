# CONTEXTO PARA CONTINUAR (Claude Code)

Pega este archivo (o su contenido) en un chat nuevo para retomar el trabajo.

**Modelo:** quiero trabajar con Opus 5.5.

**Repo:** `LucianoSaavedra/mobsurge` (proyecto Java/Maven).
**Rama de trabajo:** `claude/skills-repos-research-k788cw` — todo lo de abajo ya está commiteado y subido ahí.

## Qué estamos haciendo

Armé una "librería de skills de Claude Code" para instalar fácil por escenario en mi
laptop (donde los cambios persisten; en una sesión en la nube son temporales). Quité
la parte de hacking/seguridad a propósito.

## Lo que ya existe en la rama, en `claude-skills-library/`

- `README.md` — catálogo + sección "Pasos en la laptop" (paso a paso) + fuentes.
- `skills.json` — datos de la librería (repos por escenario, nivel de confianza).
- `install.sh` — instalador interactivo: clona repos, detecta carpetas con `SKILL.md`
  y las enlaza en `~/.claude/skills/`.
  Uso: `./install.sh --list`, `./install.sh base video_viral marketing`, `./install.sh --all`.
- `templates/estudio-a-medida/SKILL.md` — plantilla de estudio según rúbrica (marcadores `<<RELLENA>>`).
- `templates/marca-marketing/SKILL.md` — plantilla de marketing con voz de marca.

## Escenarios disponibles

`base`, `ingenieria`, `ia_productividad`, `estudio`, `video_viral`, `marketing`,
`redes_empresas`, `estudio_mercado`.

## Pendiente (lo próximo)

Crear juntos una skill de **guiones de video viral** usando `skill-creator`, ajustada
a mi nicho y plataformas. (Intenté dejarla de plantilla antes pero quedó a medias y la
borré; mejor hacerla bien con skill-creator.)

Para esa skill tendré a mano: mi nicho, en qué plataformas publico, y 2–3 videos de
referencia que me gusten.

## Proyecto para premio: Skill Router (YA EMPEZADO ✅)

Decidimos construir un proyecto realista para concurso: una IA que, según el contexto
del chat, decide **qué skills conviene usar en el momento**. Idea en
`IDEA-SKILL-ROUTER.md`.

**Ya está construido y funciona** en `claude-skills-library/skill-router/`:
- `skill_router.py` — lee skills (de `~/.claude/skills` o del `skills.json`), y dado un
  mensaje **rankea, recomienda, marca ruido y explica el motivo**. Sin dependencias.
  Uso: `python3 skill_router.py --demo "guion para un reel"`.
- `eval.py` — benchmark con casos etiquetados (top-1/top-3). Da **100%** en el set de
  demo (set pequeño y favorable; es punto de partida, no prueba definitiva).
- `README.md` — qué hace, cómo medir y hoja de ruta.

**Añadido después (funciona y medido):**
- `concepts.py` — capa de conceptos/sinónimos. Mejora el benchmark de **63.2% → 84.2%
  top-1 (+21 pts)**. Se apaga con `--no-concepts`.
- Benchmark ampliado a 19 casos (directos, sinónimos, negativos) con
  `python3 eval.py --compare` (baseline vs conceptos).
- Detección de conflictos: `python3 skill_router.py --conflicts` lista skills que se
  solapan (coseno). Detecta p.ej. remotion-skills ⇄ remotion-video-skill (0.69).

**Estado honesto:** el scorer sigue siendo léxico (palabras + conceptos curados).
Explicable y medible, pero el salto a "ganador" necesita un scorer semántico.

**Siguientes pasos para la versión premiable (hoja de ruta):**
1. ✅ Capa de conceptos (sinónimos). HECHO.
2. ✅ Detección de conflictos. HECHO.
3. Scorer por **embeddings** (significado automático). PENDIENTE — necesita descargar
   un modelo; mejor en la laptop o con dependencias instalables.
4. Scorer **LLM** (que Claude elija y justifique). PENDIENTE — necesita
   `ANTHROPIC_API_KEY` o correr en la laptop con la sesión de Claude autenticada.
5. **Aprendizaje** de preferencias del usuario. PENDIENTE.
6. **Integración** en Claude Code como hook o slash-command. PENDIENTE.
El diseño deja el *scorer* aislado para enchufar lo anterior sin reescribir el resto.

## Otra tarea pendiente

Crear la skill de guiones de video con `skill-creator` en la laptop (persistente),
ajustada a mi nicho y plataformas. Tendré a mano: nicho, plataformas donde publico, y
2–3 videos de referencia.

## Estado del repo

Todo subido a la rama `claude/skills-repos-research-k788cw`. **PR #1** abierto hacia
`main`: https://github.com/LucianoSaavedra/mobsurge/pull/1

## Siguiente acción esperada

Retomar el Skill Router: elegir e implementar el scorer por embeddings o LLM (paso 1 o
2 de la hoja de ruta) y ampliar el benchmark con más casos y skills reales.

## ⏸️ Pausa (trabajo a medias, SIN REVISAR)

Se lanzó un workflow de ultracode (6 agentes) y se detuvo a mitad para ahorrar tokens.
Lo que alcanzó a hacerse quedó commiteado como WIP, **sin revisión adversarial**:
- Núcleo endurecido (skill_router.py, llm_scorer.py, concepts.py, eval.py): eval.py
  tiene `--cases` y desglose por tipo; números léxicos sin cambios (63.2% / 84.2%).
- Posiblemente incompletos: `bench/` (benchmark independiente), `tests/`,
  `integration/` (hook UserPromptSubmit + skill) y `PITCH.md`.

**Al retomar:**
1. Revisar qué quedó completo en bench/, tests/, integration/ y PITCH.md.
2. Correr tests: `cd skill-router && python3 -m unittest discover -s tests -v`.
3. Correr el benchmark independiente con los 3 scorers:
   `python3 eval.py --compare --llm --cases bench/heldout.json`.
4. Revisión adversarial del diff, meter los números reales en PITCH.md, commit.

## ⏸️ Pausa 2 (workflows detenidos a mitad, SIN VERIFICAR)

Medido antes de la pausa (benchmark independiente `bench/heldout.json`, 60 casos, top-1):
léxico 35% · +conceptos 40% · LLM Opus 5.5 100%. Resultado completo: `.work/heldout_llm_opus.json`.

Se detuvieron dos workflows a mitad; sus cambios parciales están commiteados como WIP:
1. **Arreglos + ahorro de tokens** (`.work/workflow-fixes.js`): aplica los 27 hallazgos
   confirmados de la revisión adversarial (`.work/review_findings.json`) y añade cascada
   (`cascade.py`), medición real de tokens, `--model` (Haiku) y métricas nuevas.
2. **Autopilot + plugin** (`.work/workflow-autopilot.js`): plugin de Claude Code que se
   instala una vez; por proyecto detecta el tipo, instala skills curadas sin scripts,
   deduplica, informa del ahorro de tokens y enruta cada mensaje.

Criterio de aceptación acordado: una config barata (cascada/Haiku) solo es la opción por
defecto si consigue top-1 ≥ 97% y negativos ≥ 90% en el held-out, con ahorro medido.

**Al retomar:** revisar qué dejó cada workflow (`git diff` desde `fd9b963`), relanzar
los dos workflows desde `.work/` (los agentes deben partir del estado actual), pasar las
suites, medir Opus vs Haiku vs cascada en el held-out y escribir `GUIA-DE-USO.md`.
