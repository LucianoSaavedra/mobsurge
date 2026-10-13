---
description: Retira las skills que instaló el Skill Router en este proyecto y desactiva su autoinstalación aquí
argument-hint: "[--purge] [--force]"
---

Antes de ejecutar nada, confirma con el usuario que quiere deshacer, salvo que ya
lo haya pedido explícitamente en este mismo mensaje. Luego ejecuta, en una sola
llamada a Bash y sin `cd` delante:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/autopilot/cli.py" undo --project "$(pwd)" $ARGUMENTS
```

Explica en español qué se retiró y qué se conservó (las copias modificadas a mano
se conservan salvo `--force`). Sin `--purge` queda una marca para que el
autopilot no reinstale en la próxima sesión; `/skill-router:setup` lo reactiva.
Nunca se tocan las skills globales de `~/.claude/skills`.
