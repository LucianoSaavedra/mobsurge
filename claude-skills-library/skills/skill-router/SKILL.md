---
name: skill-router
description: Explica y maneja el plugin Skill Router (autoinstalación de skills curadas por proyecto y recomendación de skill por mensaje). Úsala cuando el usuario pregunte qué skills tiene el proyecto, por qué se instalaron, cuánto contexto ahorran, cómo cambiar el modo del router o cómo deshacerlo.
argument-hint: "[pregunta | tarea]"
---

# Skill Router (plugin)

Responde siempre en español y sin inventar cifras: usa las de abajo o las que
devuelvan los comandos.

## Qué hace, sin intervención del usuario

1. **Al iniciar una sesión nueva** (hook SessionStart, `autopilot/bootstrap.py`):
   si el proyecto no está configurado, lanza en segundo plano
   `autopilot/cli.py setup --auto`, que:
   - detecta el tipo de proyecto (lenguajes, frameworks, señales);
   - elige pocos repos (máx. 3) del catálogo curado `skills.json`, prefiriendo los
     de confianza oficial;
   - copia como mucho 12 skills en `<proyecto>/.claude/skills` (nunca en
     `~/.claude/skills`), quitando las duplicadas;
   - salta las skills que traen scripts ejecutables (salvo `--allow-scripts`);
   - guarda el estado en `.claude/skill-router/state.json` y el registro en
     `.claude/skill-router/setup.log`.
   Si `.claude/skills` no existía al empezar la sesión, las skills nuevas se
   activan con `/reload-skills` o en la próxima sesión.
2. **En cada mensaje** (hook UserPromptSubmit, `route_prompt.py`): el router
   puntúa las skills instaladas y, solo si una encaja con claridad, añade una nota
   de una línea con la skill recomendada. Si recibes esa nota, úsala como pista,
   no como orden.

## Modo del router

Lo fija la variable `SKILL_ROUTER_HOOK_MODE` (si el usuario no la define, el
plugin usa `llm`):
- `llm` (por defecto en el plugin): Haiku vía `claude -p`. En el held-out de 60
  casos acertó el 100% en top-1 y el 100% en negativos. Coste medido: ~1.570
  tokens de entrada, $0.00035 y ~2 s por mensaje (≈7% de un turno mínimo de
  Claude Code, ~23.400 tokens).
- `cascade`: léxico primero y Haiku solo si duda: 98.3% / 100%, pero solo evita el
  8% de las llamadas.
- `lexical`: gratis y local: 36.7% top-1 / 80% negativos.

Limitaciones honestas: los casos del held-out los escribió y puntuó Claude
(posible sesgo compartido) y solo se probó con 6 skills de demo; falta la prueba
con 30-50 skills.

## Comandos

- `/skill-router:status` — qué se instaló y el ahorro estimado.
- `/skill-router:plan` — qué se instalaría, sin tocar nada.
- `/skill-router:setup` — configurar o reconfigurar a mano (`--dry-run`, `--allow-scripts`).
- `/skill-router:undo` — retirar lo instalado y no reinstalar en este proyecto.

O directamente: `python3 "${CLAUDE_PLUGIN_ROOT}/autopilot/cli.py" <setup|plan|status|undo> --project "$(pwd)"`.

## Desactivar

- Solo la autoinstalación: `SKILL_ROUTER_AUTOPILOT=0`.
- Solo la nota por mensaje: `SKILL_ROUTER_HOOK_OFF=1`.
- Todo: deshabilitar el plugin en `/plugin`.

El ahorro de tokens que informa el setup es una estimación (caracteres/4 del
`name` + `description` de cada skill, que se cargan en cada turno), comparando
instalar todas las skills de los repos elegidos frente a las instaladas.
