# Herramientas: compilar y verificar sin Maven

Solo hacen falta si no hay acceso al repo de Paper (`repo.papermc.io`) ni a Maven Central, como pasó cuando se armó el plugin en la nube. Con acceso, alcanza con Maven o GitHub Actions.

```
bash herramientas/verificar.sh
```

Funciona en Linux, macOS o WSL y necesita git, JDK 21 o más nuevo y python3. Tarda unos 30 segundos, más la primera descarga.

Qué hace:

1. Clona desde GitHub solo `paper-api` de PaperMC/Paper (rama `ver/26.1.2`, o la que pongas en `PAPER_RAMA=ver/26.2`) y las fuentes de Adventure, examination, option, jspecify y las anotaciones de JetBrains, en las versiones que pide ese `build.gradle.kts`.
2. `StubGen.java` es un procesador de anotaciones que corre con `javac -proc:only` sobre esas fuentes. Así solo resuelve declaraciones y no compila cuerpos, por eso no hacen falta Guava ni las demás dependencias. Escribe stubs con las firmas exactas de los tipos de `tipos.txt`.
3. Compila el plugin contra los stubs y arma `target/MobBrain.jar`.
4. `refs.py` saca del bytecode cada clase, campo y método de la API que usa el plugin. Después `StubGen` (modo `verify`) chequea cada uno contra las fuentes reales con las reglas de la JVM: que exista con el descriptor exacto, si es estático o no, si es de interfaz o de clase. También marca lo deprecado.
5. Corre `pruebas/PruebaCerebro.java`: el árbol con 20.000 fotos de jugadores simulados.
6. Parchea los stubs con `pruebas/servidor-falso/parchear_stubs.py` y corre `PruebaPlugin`, un servidor falso con jugadores, mobs y eventos hechos con proxies.

Si el plugin empieza a usar una clase nueva de la API, agregala a `tipos.txt` (y sus superclases o interfaces si el compilador las pide).

Probado el 11/10/2026 contra `ver/26.1.2` y `ver/26.2`: 127 referencias OK y 43/43 pruebas en las dos.
