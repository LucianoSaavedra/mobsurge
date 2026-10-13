---
description: Muestra, sin instalar nada, qué tipo de proyecto se detecta y qué repos del catálogo curado se elegirían
---

Ejecuta, en una sola llamada a Bash y sin `cd` delante:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/autopilot/cli.py" plan --project "$(pwd)"
```

Resume en español: tipo detectado, escenarios, repos elegidos (con su nivel de
confianza) y descartados. Para aplicarlo: `/skill-router:setup` (o
`/skill-router:setup --dry-run` para simular la instalación).
