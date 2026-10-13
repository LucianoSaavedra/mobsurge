# Claude Skills Library 📚

Una **librería de skills de Claude Code** organizada por escenario, para que elijas
fácilmente qué instalar según lo que necesites: ingeniería, IA, estudio, video viral,
marketing y redes, y estudio de mercado.

> Pensado para aplicarse en **tu propio Claude** (tu laptop, con tu `~/.claude`).
> En una sesión en la nube las instalaciones son temporales; por eso esto está
> versionado en git: lo bajas y lo corres donde los cambios se quedan.

## 🚀 Recomendado: el plugin Skill Router (automático)

Se instala **una sola vez** como plugin de Claude Code y, desde ahí, en cada
proyecto: detecta el tipo de proyecto, instala **solo en ese proyecto**
(`<proyecto>/.claude/skills`) unas pocas skills de este catálogo, sin duplicados ni
scripts ejecutables, informa del ahorro de tokens y, en cada mensaje, recomienda la
skill adecuada (por defecto con Claude Haiku). Todo se deshace y nunca toca
`~/.claude/skills`.

```bash
git clone https://github.com/LucianoSaavedra/mobsurge.git ~/mobsurge
cd ~/mobsurge && git checkout claude/skills-repos-research-k788cw
claude plugin marketplace add ~/mobsurge
claude plugin install skill-router@mobsurge-skills
```

Cuando el PR #1 esté en `main`: `/plugin install skill-router --marketplace LucianoSaavedra/mobsurge`.

- 👉 **Cómo instalarlo y usarlo:** [`GUIA-DE-USO.md`](./GUIA-DE-USO.md)
- 📊 **Resultados medidos y pitch:** [`skill-router/PITCH.md`](./skill-router/PITCH.md)
- 🧭 **Detalle técnico del router:** [`skill-router/README.md`](./skill-router/README.md)

El instalador manual de abajo (`install.sh`) sigue disponible si prefieres
instalar escenarios completos a mano en `~/.claude/skills`.

## ⚡ Uso rápido (instalador manual)

```bash
cd claude-skills-library
chmod +x install.sh

./install.sh            # menú interactivo: elige escenarios por número
./install.sh --list     # ver escenarios disponibles
./install.sh base video_viral marketing   # instalar directo
./install.sh --all      # instalar todo (no recomendado de golpe)
```

El instalador clona cada repo en `~/.claude/skills/_sources/`, detecta las carpetas
con `SKILL.md` y las enlaza en `~/.claude/skills/`. Luego **reinicia Claude Code**.

> **Windows:** usa WSL o Git Bash para correr `install.sh`.

## 💻 Pasos en la laptop (paso a paso)

1. **Baja esta rama:**
   ```bash
   git fetch origin claude/skills-repos-research-k788cw
   git checkout claude/skills-repos-research-k788cw
   ```
2. **Entra a la librería y da permisos al instalador:**
   ```bash
   cd claude-skills-library
   chmod +x install.sh
   ```
3. **Instala pocos escenarios primero** (empieza por 1–2, no todo):
   ```bash
   ./install.sh base
   # cuando ya funcione, suma lo que quieras:
   ./install.sh video_viral marketing
   ```
4. **Activa las plantillas a medida** y rellénalas:
   ```bash
   cp -r templates/estudio-a-medida ~/.claude/skills/
   cp -r templates/marca-marketing ~/.claude/skills/
   # edita cada SKILL.md buscando los marcadores <<RELLENA: ...>>
   ```
5. **Reinicia Claude Code** para que cargue las skills nuevas.
6. **Verifica** que aparezcan: abre Claude Code y comprueba que las skills estén listadas; si no, revisa que existan en `~/.claude/skills/`.
7. **Crea la skill de guiones de video** conmigo usando `skill-creator` (ver abajo).

### Para quitar algo
Borra el enlace de la skill en `~/.claude/skills/` (y su fuente en `~/.claude/skills/_sources/`).

### Siguiente paso: skill de guiones de video
Cuando estés en la laptop, escríbeme para armarla juntos con `skill-creator`,
ajustada a tu nicho y plataformas. Ten a mano: tu nicho, en qué plataformas publicas,
y 2–3 videos de referencia que te gusten.

## 🗂️ Escenarios (la librería)

| Escenario | Qué incluye |
|---|---|
| `base` | Colecciones oficiales y comunitarias grandes |
| `ingenieria` | Metodología, TDD, agentes, revisión de código |
| `ia_productividad` | Crear tus propias skills + MCPs de contexto/memoria |
| `estudio` | Material de estudio (docx/pdf/pptx) + investigación |
| `video_viral` | Video programático con Remotion (Reels/Shorts/TikTok) |
| `marketing` | Conversión, copywriting, SEO, analytics, growth |
| `redes_empresas` | Contenido social y gestión |
| `estudio_mercado` | Análisis de mercado y síntesis |

El detalle de cada repo (URL, nivel de confianza, descripción) está en
[`skills.json`](./skills.json), que es la **fuente de datos** de la librería. Para
añadir o quitar skills, edita ese archivo y las listas equivalentes en `install.sh`.

## 🧩 Plantillas a medida (`templates/`)

Dos skills propias listas para rellenar (busca los marcadores `<<RELLENA: ...>>`):

- **`templates/estudio-a-medida/`** — material de estudio según tu rúbrica y prioridades de nota.
- **`templates/marca-marketing/`** — contenido y campañas en la voz de tu marca.

Para activarlas, copia la carpeta a tus skills:

```bash
cp -r templates/estudio-a-medida ~/.claude/skills/
cp -r templates/marca-marketing ~/.claude/skills/
```

Luego edita el `SKILL.md` de cada una con tus datos y reinicia Claude Code.

## 🔌 Extras que NO son skills (se instalan aparte)

Algunas herramientas muy útiles son **servidores MCP**, no skills de carpeta:

- **Context7** — mete documentación de APIs con versión al contexto (menos APIs inventadas).
- **Claude Mem** — memoria persistente entre sesiones.

Se añaden con el gestor de MCP de Claude Code (`/mcp` o tu `settings.json`), siguiendo
el README de cada proyecto. `deep-research` suele venir ya incluida en tu entorno.

## ⚠️ Antes de instalar

- **Instala selectivamente.** Reportes de la comunidad muestran que meter muchas skills
  a la vez *empeora* las respuestas. Empieza por 1–2 escenarios.
- **Revisa el código.** Son repos de terceros. Mira qué hacen antes de confiar en ellos,
  sobre todo si traen scripts.
- **Confianza.** En `skills.json` cada repo tiene un campo `trust` (`oficial`,
  `comunidad-popular`, `comunidad`). Prioriza lo oficial.

## 📍 Estado y hoja de ruta

Estado actual: el router es estable y Haiku es su modelo por defecto; las suites de
tests del núcleo, de la integración y del autopilot están en verde.

| Pieza | Estado |
|---|---|
| Catálogo curado `skills.json` (con `trust`, señales y tags) | ✅ Hecho |
| Instalador manual `install.sh` | ✅ Hecho |
| Skill Router: léxico, conceptos, LLM (Haiku), cascada, conflictos, benchmark | ✅ Hecho |
| Hook `UserPromptSubmit` y skill bajo demanda | ✅ Hecho |
| Plugin con autopilot (detectar, planificar, instalar por proyecto, deduplicar, ahorro, `status`/`undo`) | ✅ Hecho |
| Benchmark held-out de 60 casos: Haiku 100% top-1 / 100% negativos | ✅ Medido |
| Prueba con 30-50 skills reales | ⏳ Pendiente |
| Casos de evaluación escritos por personas (el held-out lo escribió y puntúa Claude) | ⏳ Pendiente |
| A/B de calidad final con y sin router | ⏳ Pendiente |

Cifras y limitaciones completas en [`skill-router/PITCH.md`](./skill-router/PITCH.md).

## 🔎 Fuentes

- [Firecrawl — 14 Best Claude Code Skills](https://www.firecrawl.dev/blog/best-claude-code-skills)
- [Analytics Vidhya — Top repos (1000+ skills)](https://www.analyticsvidhya.com/blog/2026/03/github-repositories-to-get-free-claude-code-skills/)
- [designrevision — Awesome Claude Code Skills](https://designrevision.com/blog/awesome-claude-code-skills)
- [buildthisnow — Best Skills 2026](https://www.buildthisnow.com/blog/guide/mechanics/best-claude-code-skills-2026)
- [Star History — BehiSecc/awesome-claude-skills](https://www.star-history.com/behisecc/awesome-claude-skills)
- [marketingskills (fork con .skill listos)](https://github.com/mysticaltech/marketingskills)
- [width.ai — Claude skills for marketing](https://www.width.ai/post/claude-skills-for-marketing)
- [Nemo — Remotion Agent Skills](https://www.nemovideo.com/blog/remotion-agent-skills-claude-code-guide)
- [wshuyi/remotion-video-skill](https://gittrend.io/repo/wshuyi/remotion-video-skill)
