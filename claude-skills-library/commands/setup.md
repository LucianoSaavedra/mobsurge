---
description: Detecta el tipo de proyecto e instala solo en .claude/skills unas pocas skills del catálogo curado (deduplicadas), con estimación del ahorro de tokens
argument-hint: "[--dry-run] [--allow-scripts]"
---

Configura el Skill Router en este proyecto. Responde en español.

1. Ejecuta, en una sola llamada a Bash y sin `cd` delante:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/autopilot/cli.py" setup --yes --project "$(pwd)" $ARGUMENTS
   ```

2. Resume el resultado en pocas líneas: tipo de proyecto detectado, skills
   instaladas (y de qué repo), las saltadas con su motivo (las que traen scripts
   ejecutables se saltan salvo `--allow-scripts`), duplicados retirados y el ahorro
   estimado de tokens por turno, tal como lo da el comando (no inventes cifras).
3. Si `.claude/skills` no existía al empezar la sesión, indica que las skills se
   activan con `/reload-skills` o en la próxima sesión.
4. Recuerda que todo se deshace con `/skill-router:undo`. No toca `~/.claude/skills`.
