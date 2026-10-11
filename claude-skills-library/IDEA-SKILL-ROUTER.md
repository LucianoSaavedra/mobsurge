# 💡 Idea de proyecto: Skill Router

Una IA que, en cada turno del chat, mira el contexto y decide **qué skills conviene
usar en ese momento** (y cuáles no). Pensado como posible proyecto para concurso.

## El problema

- Claude Code ya activa skills por su `description`, pero cuando hay **muchas**
  instaladas, el modelo se satura y elige mal.
- Reportes de la comunidad: instalar muchas skills **empeora** el output.
- No hay un componente que **razone, puntúe y explique** qué skill usar para *este*
  mensaje concreto, ni que detecte skills que **sobran o chocan** entre sí.

## La propuesta

**Skill Router:** dado (1) el contexto del chat y (2) las skills instaladas, produce:

1. **Ranking de relevancia** — puntúa cada skill para el turno actual.
2. **Recomendación** — "usa estas 2; el resto, ignóralas ahora".
3. **Detección de ruido/conflictos** — avisa si hay skills redundantes o que compiten.
4. **Explicación** — por qué eligió cada una (transparencia).
5. *(Avanzado)* **Aprendizaje** — ajusta según lo que al usuario le funciona.

## Por qué puede competir

- Resuelve un dolor **real y medible** (más skills = peor resultado).
- Tiene **IA de verdad** (un router que razona), no solo un instalador.
- **Medible**: comparar calidad del output con y sin el router → gráfico para la demo.
- Ángulo atractivo: **"IA que gestiona a la IA"** (meta-problema).

## Base que ya tenemos

- `skills.json` — catálogo con escenarios, descripciones y nivel de confianza.
- `install.sh` — instala y enlaza skills en `~/.claude/skills/`.
  El Router se monta **encima** de esto; no empezamos de cero.

## Prototipo mínimo (primer paso realista)

Un comando/skill que, dado el mensaje del usuario:
- Lee el catálogo de skills instaladas (nombre + descripción).
- Devuelve: lista rankeada "usa esto ahora" con **puntuación** y **motivo** de cada una.
- Marca las que considera ruido para este turno.

Esto solo ya es una demo convincente. Después se añade detección de conflictos y
aprendizaje.

## Cómo se mediría

- Elegir N tareas de prueba (ingeniería, estudio, marketing...).
- Correr cada una: (a) con todas las skills activas, (b) con las que elige el Router.
- Comparar calidad (rúbrica simple) y tokens. Mostrar antes/después.

## Preguntas abiertas para cuando retomemos

- ¿Router como skill, como hook, o como MCP?
- ¿Puntuación por el propio modelo, por embeddings, o híbrido?
- ¿Alcance del primer prototipo: solo recomendar, o también activar/desactivar?

## Siguiente acción

En la laptop: elegir forma del prototipo (skill/hook/MCP) y construir la versión
"solo recomienda" sobre `skills.json`.
