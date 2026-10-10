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

## Siguiente acción esperada

Ayúdame a crear esa skill de guiones con `skill-creator` y guárdala en `~/.claude/skills/`.
