# MobBrain: contexto para Claude

Plugin de Minecraft (Paper) con un árbol de decisión que aprende cómo juega cada jugador y le ajusta los mobs. Lo armamos con Claude el 10/10/2026. Este archivo resume todo para seguir en una sesión nueva.

## Con quién trabajás

- Luciano, estudiante de Ingeniería de Sistemas en la UCB (Tarija, Bolivia). Le interesa mucho la IA.
- Hablale en español, informal y cercano, como entre amigos.
- Quiere aprender, no solo recibir código. Explicá el porqué de cada cambio y conectalo con lo que ve en la facu: en Estructuras de Datos (SIS-211) ya vio listas, pilas, colas y tablas hash, y ahora está con árboles binarios. Este plugin es justamente un árbol binario que aprende solo.
- Cuando algo falla en el server, pedile el texto o una captura de la consola.

## El server

- Paper 26.1.2 build 74 (`paper-26.1.2-74.jar`) en su laptop: Windows 10, Dell G5 5590, i7-9750H, 16 GB de RAM. Carpeta: `C:\Users\ESI\Desktop\minecraftserver`.
- Se prende con `iniciar-server-optimizado.bat` (flags de Aikar, `-Xms3G -Xmx4G`) y se apaga escribiendo `stop` en la consola, nunca cerrando la ventana. Él entra por `localhost` y los amigos por un túnel de Playit.gg. `online-mode=false`.
- JDK 25 (Temurin). A veces streamea con OBS en la misma laptop, así que el rendimiento importa.
- Plugins instalados:
  - **WorldReset** (de terceros): cuando alguien muere, regenera el mundo con una semilla nueva.
  - **DeathCounter** (hecho con Claude): contador de muertes en la tabla lateral, en `plugins/DeathCounter/deaths.yml`.
  - **LagFixer** (de terceros): optimiza mobs y entidades.
  - **MobSurge** (hecho con Claude): +33% de mobs hostiles y x1.5 de daño. **MobBrain lo reemplaza** (su nivel 3 hace lo mismo), así que hay que sacar `MobSurge.jar`. Si quedan los dos, MobBrain avisa en la consola.
- Compila sus plugins con GitHub Actions (un repo por plugin; el `.jar` sale en Artifacts) o con Maven local.

## Cómo funciona MobBrain

1. **Fotos.** Cada 15 s mira a cada jugador en supervivencia o aventura y guarda 12 rasgos (abajo).
2. **Etiquetas.** Si muere dentro de los 180 s siguientes, la foto queda con 1 (peligro); si no, con 0 (sobrevivió). No cuentan los `/kill` ni las muertes sin daño en los últimos 10 s o con causa KILL, CUSTOM o SUICIDE. Si se desconecta, sus fotos pendientes se descartan.
3. **Entrenamiento.** Cada 5 min (la primera vez a los 20 s) entrena un árbol CART en otro hilo: impureza de Gini, clases balanceadas (las muertes son raras y pesan más), profundidad máxima 4 y hoja mínima `max(6, n/60)`. Antes entrena una copia con el 80% y mide con el 20% restante. Necesita 150 fotos y 20 de peligro (unas 2 muertes); mientras tanto usa reglas fijas (`Cerebro.heuristica`).
4. **Nivel.** El árbol devuelve un riesgo de 0 a 1 de morir pronto (0,5 = jugador promedio, por los pesos balanceados). Se promedia con el anterior y se pasa a nivel con los cortes 0,20 / 0,38 / 0,62 / 0,80. Al cambiar, avisa en la barra de acción.
5. **Mobs.** En cada spawn natural de un mob hostil (`Enemy`) se usa el nivel del jugador más cercano (hasta 64 bloques): agrega o cancela mobs y les sube vida y velocidad. El daño que le hacen al jugador (flechas incluidas, PvP no) se multiplica. Si ya tiene 24 mobs cerca, no agrega más.
6. **Hordas.** En nivel 5, de noche, en el overworld y a cielo abierto, cada 150 s aparecen 3 a 5 zombies, esqueletos o arañas a 14–22 bloques, que lo atacan directo.

| Nivel | Nombre | Mobs extra | Vida mobs | Daño | Velocidad |
|---|---|---|---|---|---|
| 1 | Respiro | cancela el 30% | x1 | x1 | x1 |
| 2 | Suave | +10% | x1 | x1,25 | x1 |
| 3 | Normal | +33% | x1 | x1,5 | x1 |
| 4 | Difícil | +66% | x1,25 | x1,75 | x1,08 |
| 5 | Pesadilla | +100% | x1,5 | x2 | x1,15 |

Todo esto se ajusta en `config.yml`.

**Rasgos** (el orden es el formato de `datos.csv`): 0 `vida` · 1 `armadura` · 2 `hambre` · 3 `arma` (0 nada, 1 madera/oro, 2 piedra/cobre, 3 hierro/arco/ballesta/tridente, 4 diamante/maza, 5 netherite) · 4 `kills5` (hostiles matados en 5 min) · 5 `dano5` (daño recibido en 5 min) · 6 `mobsCerca` (16 bloques) · 7 `minutosVivo` · 8 `luz` · 9 `noche` · 10 `bajoTierra` (bloques bajo la superficie) · 11 `muertesHora` (de su historial).

## Archivos

- `src/main/java/com/luciano/mobbrain/Arbol.java`: el árbol CART (entrenar, predecir, explicar el camino, dibujar). No usa nada de Minecraft.
- `Cerebro.java`: guarda las fotos (máximo 20.000, se van las más viejas), entrena, predice, reglas fijas y CSV. Tampoco usa Minecraft.
- `MobBrain.java`: el plugin (eventos, fotos, niveles, spawns, daño, hordas, comandos y guardado).
- `src/main/resources/config.yml` y `plugin.yml` (comando `/ia`, alias `/mobbrain`; permiso `mobbrain.admin`, por defecto para op).
- `pruebas/`: prueba del árbol con jugadores simulados y prueba del plugin con un servidor falso.
- `herramientas/`: compilar y verificar sin Maven (ver abajo).

En el server, `plugins/MobBrain/` guarda `datos.csv` (fotos con su etiqueta), `arbol.txt` (el último árbol dibujado) y `jugadores.csv` (muertes y minutos por UUID). Se escriben cada 5 min, en `/ia reset` y al apagar.

Comandos: `/ia nivel [jugador]` (nivel y camino en el árbol), `/ia arbol`, `/ia stats`, `/ia entrenar` (admin), `/ia reset confirmar` (admin).

## Estado al 11/10/2026

- Versión 1.0.0 entregada como `MobBrain.jar` (bytecode Java 21, `api-version: "26.1"`).
- **Todavía no se probó en un server de verdad.** Primer paso: copiar el jar a `plugins`, sacar MobSurge, prender el server y buscar en la consola `MobBrain activado`. Después jugar un rato y probar `/ia nivel` y `/ia stats`.
- Lo que sí se verificó en la nube, sin acceso a Maven:
  - Las 127 clases, campos y métodos de la API que usa el bytecode existen con la firma exacta en Paper 26.1.2 y en 26.2 (Adventure 5.2), y ninguno está deprecado.
  - Prueba del árbol con 20.000 fotos simuladas: entrena en ~0,35 s, con 69% de precisión balanceada en datos que no vio.
  - Servidor falso, 43/43: etiquetado, `/kill` ignorado, entrenamiento, spawns, daño, flechas, PvP sin tocar, hordas, comandos, guardado y recarga.

## Para revisar cuando corra de verdad

- **WorldReset:** cuando alguien muere regenera el mundo y mueve a todos. A los que no murieron no se les reinicia `minutosVivo`. Y si los mata con `setHealth(0)` justo después de haber recibido daño, esa muerte podría contarse como real. Ver qué hace y ajustar `alMorirJugador` / `alRevivir`.
- **LagFixer** puede limitar o congelar mobs y comerse los extras. Si no aparecen, revisar su config.
- **Creepers:** la explosión cuenta como daño de mob, así que en nivel 5 pega x2 (MobSurge ya hacía x1,5).
- **Saltos de nivel:** si el riesgo queda justo en un corte, el nivel puede ir y venir y el aviso se repite. Se puede agregar histéresis.
- **Rendimiento:** cada 15 s hace un `getNearbyEntities` por jugador y el entrenamiento va en otro hilo. No debería pesar, pero conviene mirar el TPS mientras streamea.
- Un rasgo nuevo va al final de `Cerebro.RASGOS`. Al cargar, las filas viejas de `datos.csv` con otra cantidad de columnas se descartan.

## Ideas que quedaron

- Abrir `datos.csv` en Python (pandas y scikit-learn), entrenar un `DecisionTreeClassifier` o un random forest y compararlo con el árbol del plugin. Buen proyecto para su lado de IA.
- Un adivinador tipo Akinator: árbol binario de preguntas sí/no que agrega una rama nueva cada vez que se equivoca.
- Repasar con él tablas hash (Práctica 2 de SIS-211) y árboles binarios de búsqueda, usando este código como ejemplo.

## Compilar

- **GitHub Actions:** el código está en el repo `LucianoSaavedra/mobsurge`, rama `mobbrain`, carpeta `mobbrain/`. El workflow `.github/workflows/mobbrain.yml` compila con JDK 25 y Maven y deja `MobBrain.jar` en Artifacts.
- **Maven local:** `mvn clean package` → `target/MobBrain.jar`. El `pom.xml` usa `paper-api` `[26.1.2.build,)`, o sea la última versión publicada; el código compila igual contra 26.2.
- **Sin Maven** (si el repo de Paper está bloqueado, como pasó en la nube): `bash herramientas/verificar.sh` en Linux, macOS o WSL. Baja las fuentes de la API desde GitHub, genera stubs exactos, compila `target/MobBrain.jar`, verifica cada llamada a la API y corre las dos pruebas. Detalles en `herramientas/README.md`.

## Convenciones

- Código, comentarios y mensajes en español.
- `Arbol` y `Cerebro` no importan nada de Bukkit, así se prueban solos.
- Todo lo que toca Bukkit va en el hilo principal. El entrenamiento corre async sobre una copia de los datos; los archivos se arman en el hilo principal y se escriben async (con `.tmp` y move).
- Mensajes al jugador con códigos `&` a través de `LegacyComponentSerializer.legacyAmpersand()`.
- Después de cambiar algo, compilar y correr las pruebas (Maven o `herramientas/verificar.sh`) antes de pasarle el jar.
