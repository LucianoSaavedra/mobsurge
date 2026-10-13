# Guía de uso: Skill Router como plugin de Claude Code

Esta guía es para cualquier persona que use Claude Code, programe o no. Explica
qué hace el plugin, cómo se activa **una sola vez** y qué pasa después en cada
proyecto.

> Marcas que verás en esta guía:
> **[no verificado aquí]** quiere decir que el comando sale de la documentación
> oficial de Claude Code y de su `--help`, pero no se ha ejecutado de principio a
> fin en esta máquina. Si algo no responde como se describe, mira la
> [sección de problemas](#si-algo-no-funciona).

---

## 1. Qué hace

Claude Code puede usar "skills": carpetas con instrucciones para tareas concretas
(revisar código, hacer vídeos con Remotion, escribir textos de marketing...). Hay
cientos publicadas, pero instalar muchas a la vez es contraproducente: ocupan
contexto en cada mensaje y Claude elige peor.

El plugin **Skill Router** hace dos cosas solo, sin que tengas que pedírselo:

1. **Al abrir una sesión en un proyecto nuevo**, mira qué tipo de proyecto es
   (lenguajes, frameworks, si es de contenido o de código), elige **pocas**
   skills de un catálogo revisado a mano (`skills.json`) y las copia **solo en ese
   proyecto**, en `<proyecto>/.claude/skills/`. Quita las repetidas y te dice
   cuánto contexto (tokens) ahorras frente a instalarlo todo.
2. **En cada mensaje que escribes**, decide si alguna de tus skills encaja con lo
   que pides y, si una encaja con claridad, le pasa a Claude una nota de una línea
   con la recomendación. Si ninguna encaja, no añade nada.

Lo que **nunca** hace:

- Instalar nada que no esté en el catálogo curado.
- Instalar skills que traen programas ejecutables (scripts `.py`, `.sh`, `.js`,
  archivos con permiso de ejecución...), salvo que tú lo permitas expresamente.
- Tocar tus skills globales de `~/.claude/skills`. Solo trabaja en
  `<proyecto>/.claude/skills` y en lo que él mismo instaló.
- Hacer algo que no se pueda deshacer: todo tiene `undo`.

---

## 2. Requisitos

- **Claude Code** instalado y con sesión iniciada (el comando `claude` funciona en
  tu terminal).
- **Python 3** (no hace falta instalar paquetes extra).
- **git** (para descargar las skills del catálogo la primera vez).
- Conexión a internet la primera vez que se configura cada tipo de proyecto
  (después, las fuentes quedan en caché en `~/.cache/skill-router/sources`).
- Este repositorio descargado en tu equipo. En los ejemplos está en
  `/home/user/mobsurge`; cambia esa ruta por la tuya.

> Las sesiones de Claude Code en la nube (claude.ai/code) **no cargan** los plugins
> que instalas en tu equipo. Este plugin es para tu Claude Code local.

---

## 3. Instalación (una sola vez)

Ejecuta esto en tu terminal (fuera de Claude Code). **[no verificado aquí]**

```bash
# 1. Comprobar que el plugin está bien formado (debe decir "Validation passed")
claude plugin validate /home/user/mobsurge/claude-skills-library

# 2. Añadir el repositorio como "marketplace" (tienda local de plugins)
claude plugin marketplace add /home/user/mobsurge
#    Debe responder algo como:
#    Successfully added marketplace: mobsurge-skills (declared in user settings)

# 3. Instalar el plugin para TODOS tus proyectos (ámbito "user", el de por defecto)
claude plugin install skill-router@mobsurge-skills

# 4. Comprobar
claude plugin list               # skill-router debe aparecer como "enabled"
claude plugin details skill-router
```

Atajo de un solo comando (añade el marketplace si falta) **[no verificado aquí]**:

```bash
claude plugin install skill-router --marketplace /home/user/mobsurge
```

Desde **dentro** de una sesión de Claude Code también se puede:

```
/plugin marketplace add /home/user/mobsurge
/plugin install skill-router@mobsurge-skills
```

Notas:

- El nombre `mobsurge-skills` sale de `/home/user/mobsurge/.claude-plugin/marketplace.json`.
  Si el repositorio está en GitHub, en el paso 2 puedes usar `owner/repo` en vez
  de la ruta.
- Como el marketplace es una carpeta local, Claude Code lee los archivos del
  plugin directamente de ella: si actualizas el repositorio, los cambios se aplican
  al abrir una sesión nueva o con `/reload-plugins`.
- **No uses** `claude plugin init` para esto: crea archivos en `~/.claude/skills`,
  justo lo que este plugin se compromete a no tocar.
- Ámbitos de instalación: `user` (todos tus proyectos; es lo que buscamos),
  `project` (queda en el `.claude/settings.json` del repositorio y cada
  colaborador lo instala igualmente) y `local` (solo tú y solo ese repositorio).

---

## 4. Qué pasa en cada proyecto nuevo

1. Abres Claude Code en la carpeta del proyecto.
2. Al arrancar la sesión, el plugin comprueba si el proyecto ya está configurado.
   Si no lo está, lanza la configuración **en segundo plano** (no te hace esperar)
   y deja un registro en `<proyecto>/.claude/skill-router/setup.log`.
3. La configuración:
   - **detecta** el tipo de proyecto (tarda milisegundos; ignora `.git`,
     `node_modules`, entornos virtuales, `build`, `dist`, etc.);
   - **elige** como mucho 3 repositorios del catálogo, prefiriendo los de
     confianza oficial y siempre menos antes que más;
   - **copia** como mucho 12 skills en `<proyecto>/.claude/skills/` (copia, no
     enlace), saltándose las que traen scripts;
   - **quita duplicados** (skills casi iguales de repos distintos);
   - guarda qué hizo en `<proyecto>/.claude/skill-router/state.json` y calcula el
     **ahorro estimado de tokens**.
4. Las skills nuevas se activan en la **siguiente sesión** de ese proyecto (si la
   carpeta `.claude/skills` no existía al empezar la sesión, Claude Code no la
   vigila hasta que reinicias).

Si el proyecto no da pistas claras (por ejemplo, una carpeta vacía), el plan sale
vacío y no se instala nada: 0 tokens extra.

No se activa en tu carpeta personal (`$HOME`), en `/` ni dentro de `~/.claude`.

**Sobre el ahorro de tokens que te muestra:** es una estimación (caracteres / 4 del
nombre y la descripción de cada skill, que es lo que Claude Code carga en cada
turno). Compara instalar todas las skills de los repos elegidos con instalar solo
las que el plugin dejó.

---

## 5. Modos del router (la recomendación en cada mensaje)

| Modo | Qué usa | Acierto medido (held-out, 60 casos: top-1 / negativos) | Coste |
|---|---|---|---|
| `llm` (**por defecto en el plugin**) | Claude Haiku vía `claude -p` | 100% / 100% | ~1.570 tokens de entrada, ~$0.00035 y ~2 s por mensaje |
| `cascade` | léxico y, si duda, Haiku | 98.3% / 100% | casi lo mismo que `llm`: solo evita el 8% de las llamadas |
| `lexical` | palabras clave, en tu equipo | 36.7% / 80% | gratis e instantáneo |

"Negativos" son mensajes en los que **no** debe recomendarse ninguna skill (por
ejemplo, una charla sin relación con tus skills).

¿Cuánto es ~1.570 tokens? Un turno mínimo de Claude Code (decir "hola" en una
carpeta vacía) ya carga unos 23.400 tokens de contexto. La consulta del router es
como un 7% de ese mínimo, y en conversaciones reales, menos.

### Cómo cambiar de modo

El modo se lee de la variable de entorno `SKILL_ROUTER_HOOK_MODE`. Si no la
defines, el plugin usa `llm`.

Para una sesión:

```bash
SKILL_ROUTER_HOOK_MODE=lexical claude
```

Para siempre, añádela a tu perfil de shell (`~/.bashrc`, `~/.zshrc`...):

```bash
export SKILL_ROUTER_HOOK_MODE=lexical   # o cascade, o llm
```

o al bloque `"env"` de tu `~/.claude/settings.json`:

```json
{ "env": { "SKILL_ROUTER_HOOK_MODE": "lexical" } }
```

Recomendación: deja `llm` si quieres la mejor elección; usa `lexical` si no
quieres gastar nada de cuota o trabajas sin conexión.

---

## 6. Comandos útiles

Dentro de Claude Code (los añade el plugin):

| Comando | Para qué |
|---|---|
| `/skill-router:status` | Qué se instaló en este proyecto, si lo has modificado y el ahorro estimado. |
| `/skill-router:plan` | Qué se instalaría, sin tocar nada. |
| `/skill-router:setup` | Configurar o reconfigurar a mano. Admite `--dry-run` (simular) y `--allow-scripts` (permitir skills con scripts; úsalo solo si has revisado el código). |
| `/skill-router:undo` | Retirar lo instalado y no reinstalar en este proyecto. Admite `--force` (borrar también copias que editaste a mano) y `--purge` (borrar también la marca y el registro). |

Lo mismo desde la terminal, sin Claude Code:

```bash
cd /home/user/mobsurge/claude-skills-library
python3 -m autopilot.cli plan   --project /ruta/a/tu/proyecto
python3 -m autopilot.cli setup  --project /ruta/a/tu/proyecto --dry-run
python3 -m autopilot.cli status --project /ruta/a/tu/proyecto
python3 -m autopilot.cli undo   --project /ruta/a/tu/proyecto
# añade --json a cualquiera para una salida legible por programas
```

Sobre `undo`:

- Retira solo lo que instaló el plugin. Las copias que hayas editado a mano se
  **conservan** salvo que uses `--force`.
- Sin `--purge` deja una marca para que el plugin no vuelva a instalar en ese
  proyecto al abrir la siguiente sesión. `/skill-router:setup` lo reactiva.
- Nunca toca `~/.claude/skills`.

---

## 7. Cómo desactivarlo

De menos a más:

| Quiero... | Cómo |
|---|---|
| Quitar las skills de **un** proyecto | `/skill-router:undo` |
| Que no instale nada solo, pero seguir con la recomendación | variable `SKILL_ROUTER_AUTOPILOT=0` |
| Quitar solo la nota por mensaje | variable `SKILL_ROUTER_HOOK_OFF=1` |
| Apagar el plugin entero | `claude plugin disable skill-router` (o desde `/plugin`) **[no verificado aquí]** |
| Desinstalarlo | `claude plugin uninstall skill-router` **[no verificado aquí]** |
| Quitar también el marketplace | `claude plugin marketplace remove mobsurge-skills` (desinstala sus plugins) **[no verificado aquí]** |

Desinstalar el plugin **no** borra las skills que ya copió a tus proyectos: para
eso, ejecuta antes `/skill-router:undo` en cada proyecto (o
`python3 -m autopilot.cli undo --project ...`). Las fuentes descargadas están en
`~/.cache/skill-router/sources` y se pueden borrar sin riesgo.

---

## 8. Dónde rinde mejor y dónde aporta poco

**Rinde mejor:**

- Proyectos con un tipo claro: una app Java/Maven, un proyecto Node/React, un
  análisis de datos en Python, un proyecto de vídeo con Remotion o una carpeta de
  contenido de marketing. Ahí el plan elige 1 o 2 repos adecuados.
- Cuando tenías la tentación de instalar colecciones enteras: el plugin instala
  pocas y te dice cuánto contexto ahorras.
- En modo `llm`, con mensajes escritos con tus propias palabras (sinónimos,
  frases largas): es donde el modo léxico falla y Haiku acierta.

**Aporta poco:**

- Carpetas vacías o sin pistas: no instala nada (lo cual también es correcto).
- Si ya tienes muchas skills globales en `~/.claude/skills`: el plugin no las
  toca, así que no reduce ese contexto.
- Sesiones en la nube (claude.ai/code): no cargan el plugin.
- En modo `lexical` con mensajes que no usan las palabras de la skill: acierta el
  36.7% en el benchmark.
- Temas que el catálogo curado no cubre: solo instala lo que hay en `skills.json`.

---

## 9. Preguntas frecuentes

**¿Cuánto me cuesta?**
En modo `llm`, una consulta a Haiku por mensaje: ~1.570 tokens de entrada,
~$0.00035 y ~2 s, medido con `claude -p`. En modo `lexical`, nada.

**¿Por qué Haiku y no un modelo más grande?**
En el benchmark de 60 casos, Haiku acertó lo mismo que Opus (100% / 100%) y es
más barato y rápido.

**¿Es fiable ese 100%?**
Es lo medido, con matices: los 60 casos los escribió un agente de Claude y los
puntúa Claude (puede haber un sesgo compartido), y solo se probó con 6 skills de
demo. Falta la prueba con 30-50 skills reales.

**¿Puede instalar algo peligroso?**
Solo instala del catálogo curado y salta las skills con scripts ejecutables salvo
que pongas `--allow-scripts`. Aun así son repos de terceros: revisa lo que
instalas.

**¿Y mis skills personales?**
No se tocan. Todo ocurre en `<proyecto>/.claude/skills` y en el estado
`<proyecto>/.claude/skill-router/`.

**¿Debo subir `.claude/skills` a git?**
Es decisión tuya. Si lo subes, tus compañeros verán las mismas skills aunque no
tengan el plugin.

**He editado una skill instalada, ¿se pierde si deshago?**
No: `undo` conserva las copias modificadas salvo que uses `--force`.
`/skill-router:status` te dice cuáles cambiaste.

**Instalé el plugin pero no veo las skills.**
Abre una sesión nueva en el proyecto: la configuración corre en segundo plano y
las skills se cargan en la siguiente sesión. Revisa
`<proyecto>/.claude/skill-router/setup.log` o ejecuta `/skill-router:status`.

**¿Puedo ver qué haría antes de que instale nada?**
Sí: `/skill-router:plan` o `/skill-router:setup --dry-run`. Si prefieres que no
instale nada solo, define `SKILL_ROUTER_AUTOPILOT=0` y usa `/skill-router:setup`
cuando quieras.

---

## Si algo no funciona

- `claude plugin ...` no existe o da error: actualiza Claude Code; dentro de una
  sesión, prueba `/plugin`. Los comandos de la sección 3 están tomados de la
  documentación (code.claude.com/docs/en/plugin-marketplaces y /docs/en/plugins)
  y del `--help` del CLI.
- Para probar el plugin sin instalarlo (solo esa sesión):
  `claude --plugin-dir /home/user/mobsurge/claude-skills-library`.
- No aparece la nota del router: comprueba que no tienes `SKILL_ROUTER_HOOK_OFF=1`
  y que la sesión ve skills instaladas. Con `SKILL_ROUTER_HOOK_DEBUG=1` el hook
  escribe diagnóstico.
- La configuración no arranca: mira `setup.log` y que `git` y `python3` estén en
  el `PATH`.

Más detalle técnico: [README de la librería](README.md) ·
[README del router](skill-router/README.md) · [Pitch y resultados](skill-router/PITCH.md).
