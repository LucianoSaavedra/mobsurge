---
description: Muestra qué skills instaló el Skill Router en este proyecto, si se modificaron y el ahorro estimado de tokens
---

Ejecuta, en una sola llamada a Bash y sin `cd` delante:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/autopilot/cli.py" status --project "$(pwd)"
```

Resume la salida en español en pocas líneas, sin añadir datos que no estén en ella.
Si no hay nada instalado, sugiere `/skill-router:plan` para ver qué se instalaría.
