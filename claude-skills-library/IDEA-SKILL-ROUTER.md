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
- `install.sh` — instala y enlaza skills en `~/.claude/skills/` (instalador manual).
- `skill-router/` — el router ya construido, estable y con tests.
- `autopilot/` + plugin — instalación automática por proyecto.

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

## Decisiones tomadas (estado actual)

Las preguntas abiertas del principio ya tienen respuesta:

- **¿Skill, hook o MCP?** Plugin de Claude Code que combina un hook
  `SessionStart` (autoinstalación por proyecto), un hook `UserPromptSubmit`
  (recomendación por mensaje), comandos `/skill-router:*` y una skill bajo demanda.
- **¿Puntuación por modelo, embeddings o híbrido?** Por modelo: Claude Haiku es el
  modo por defecto. En el held-out de 60 casos acierta 100% top-1 y 100% en
  negativos, igual que Opus; el léxico (36.7% / 80%) queda como modo gratuito y la
  cascada (98.3% / 100%) solo evita el 8% de las llamadas.
- **¿Solo recomendar, o también activar?** Las dos cosas, por proyecto: instala
  pocas skills curadas en `<proyecto>/.claude/skills` (deshacible, sin tocar
  `~/.claude/skills`) y recomienda una por mensaje.

Coste medido de la recomendación con Haiku: ~1.570 tokens de entrada, $0.00035 y
~2 s por mensaje, ~7% de un turno mínimo de Claude Code (~23.400 tokens).

## Siguiente acción

- Prueba con 30-50 skills reales (hasta ahora, 6 skills de demo).
- Casos de evaluación escritos por personas (el held-out lo escribió y puntúa Claude).
- La medición de calidad final descrita arriba (con y sin router), que sigue
  pendiente.

Más: [guía de uso](GUIA-DE-USO.md) · [pitch y resultados](skill-router/PITCH.md) ·
[README del router](skill-router/README.md).
