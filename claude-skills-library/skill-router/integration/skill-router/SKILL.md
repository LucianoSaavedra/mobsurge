---
name: skill-router
description: Recomienda qué skills instaladas conviene usar para una tarea concreta, con puntuación 0-100 y motivo, marca las que sobran y detecta skills que se solapan o compiten entre sí. Úsala cuando el usuario pregunte "¿qué skills debería usar para esto?", "¿qué skill me sirve para…?", "¿qué skills tengo para X?", "¿tengo skills repetidas o que chocan?" o pida revisar conflictos entre sus skills.
argument-hint: "[tarea o mensaje] | --conflicts"
allowed-tools: Bash(python3 ${CLAUDE_SKILL_DIR}/run_router.py *)
---

# Skill Router bajo demanda

Ejecuta el Skill Router (un script local, solo Python 3 estándar) para decidir qué
skills encajan con una tarea y explicar por qué. Responde siempre en español.

Argumentos recibidos: $ARGUMENTS

Importante al ejecutar: lanza cada comando de abajo tal cual, en su propia
llamada a Bash, empezando por `python3 ${CLAUDE_SKILL_DIR}/run_router.py`. No
antepongas `cd` ni encadenes comandos con `&&`, `;` o `|`: solo la forma simple
está preaprobada y no pide permiso al usuario.

## 1. Recomendar skills para una tarea

1. Decide qué texto evaluar:
   - Si los argumentos traen una tarea, usa ese texto.
   - Si están vacíos, usa la petición más reciente del usuario a la que se refiere
     ("esto"). Si no está claro de qué tarea habla, pregúntaselo en una línea.
2. Ejecuta (pon el texto entre comillas simples; si contiene `'`, escríbela como `'\''`):

   ```bash
   python3 ${CLAUDE_SKILL_DIR}/run_router.py --json 'TEXTO DE LA TAREA'
   ```

   Sin más flags usa las skills de `~/.claude/skills`; si no hay, el catálogo
   `skills.json`. Flags útiles:
   - `--llm`: usa Claude como scorer (más preciso, tarda segundos). Úsalo si el
     usuario pide más precisión o si el resultado léxico no tiene sentido.
   - `--skills-dir RUTA`: otra carpeta de skills (por ejemplo `.claude/skills` del
     proyecto).
   - `--top N` y `--threshold N`: cuántas recomendar (def. 3) y puntuación mínima
     (def. 15).
3. Lee el JSON:
   - `recommended`: skills a usar, con `score` (0-100) y `matched` (términos que
     coincidieron; los que empiezan por `concept:` son conceptos, p. ej.
     `concept:estudio`; con `--llm` es una frase de motivo).
   - `noise`: skills que sobran para esta tarea.
   - `source` y `scorer`: de dónde salieron las skills y cómo se puntuó.
4. Responde con una lista corta: cada skill recomendada con su puntuación y el
   motivo en palabras normales, y una línea con las que sobran. Si `recommended`
   está vacío, dilo: probablemente no hace falta ninguna skill.
5. Usa tu criterio: el scorer por defecto se basa en palabras clave y puede
   equivocarse (sobre todo con descripciones en inglés). Si una recomendación no
   tiene sentido para la tarea, dilo y descártala en vez de seguirla a ciegas.

## 2. Detectar skills que se solapan (conflictos)

Si el usuario pregunta por skills repetidas, que compiten o que chocan, o pasa
`--conflicts`:

```bash
python3 ${CLAUDE_SKILL_DIR}/run_router.py --conflicts --json
```

Devuelve pares `a`/`b` con `similarity` (0-1, similitud de coseno de sus términos)
y `shared` (términos en común). Explica cada par y sugiere dejar activa solo una
cuando hagan lo mismo. Si la lista está vacía, no hay solapamientos por encima
del umbral.

## Si falla

- "No encuentro skill_router.py": la carpeta de la skill se copió en vez de
  enlazarse. Dile al usuario que la enlace con `ln -s` o que defina
  `SKILL_ROUTER_HOME=/ruta/a/skill-router`.
- "No encontre skills": pasa `--demo` para enseñar el funcionamiento con las 6
  skills de ejemplo, avisando de que no son las suyas.
