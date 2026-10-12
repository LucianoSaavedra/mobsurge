# MobBrain

Plugin para Paper 26.1 que usa un **árbol de decisión** para aprender cómo juega cada jugador y le ajusta los mobs: al que la tiene clara le manda más y más fuertes, al que la está pasando mal le da un respiro.

## Instalar

1. Copiá `MobBrain.jar` a la carpeta `plugins` del server.
2. **Sacá `MobSurge.jar`**: el nivel 3 de MobBrain ya hace lo mismo (+33% de mobs, x1.5 de daño) y si dejás los dos se suman.
3. Reiniciá el server. Se crea `plugins/MobBrain/config.yml`.

## Cómo piensa la IA

1. **Fotos:** cada 15 segundos mira a cada jugador: vida, armadura, arma, hambre, mobs que mató y daño que recibió en los últimos 5 minutos, mobs cerca, luz, si es de noche, si está bajo tierra, cuánto lleva vivo y cuántas veces suele morir.
2. **Etiquetas:** si el jugador muere dentro de los 3 minutos siguientes, esa foto queda marcada como *peligro*; si no, como *sobrevivió*. Los `/kill` y las muertes forzadas por plugins no cuentan.
3. **Entrenamiento:** cada 5 minutos entrena un árbol (algoritmo CART con Gini, igual que el árbol binario de la facu pero que aprende solo qué preguntar). Mide qué tan bien predice con un 20% de datos que no usó para entrenar.
4. **Nivel:** el árbol da un *riesgo* de morir pronto (50% = jugador promedio). Poco riesgo = nivel alto.

Mientras no hay datos suficientes (150 fotos y 20 de peligro, unas 2 muertes) usa reglas fijas.

| Nivel | Nombre | Mobs | Vida mobs | Daño | Velocidad |
|---|---|---|---|---|---|
| 1 | Respiro | 30% menos | x1 | x1 | x1 |
| 2 | Suave | +10% | x1 | x1.25 | x1 |
| 3 | Normal | +33% | x1 | x1.5 | x1 |
| 4 | Difícil | +66% | x1.25 | x1.75 | x1.08 |
| 5 | Pesadilla | +100% | x1.5 | x2 | x1.15 |

En nivel 5, de noche y a cielo abierto, cada 2 minutos y medio le cae una **horda** que va directo a él.

## Comandos

- `/ia nivel [jugador]`: qué nivel te puso y el camino de preguntas que siguió el árbol.
- `/ia arbol`: el árbol completo que aprendió.
- `/ia stats`: cuántos datos tiene y qué tan bien predice.
- `/ia entrenar`: entrenar ya (admin).
- `/ia reset confirmar`: borrar todo lo aprendido (admin).

## Archivos

- `datos.csv`: todas las fotos con su etiqueta. Se puede abrir en Excel o en Python (`pandas.read_csv`) para entrenar tus propios modelos.
- `arbol.txt`: el último árbol entrenado.
- `jugadores.csv`: muertes y minutos jugados de cada uno.

## Compilar

Igual que tus otros plugins: subí esta carpeta a un repo de GitHub y Actions arma el `.jar`, o con Maven y JDK 25: `mvn clean package` (queda en `target/MobBrain.jar`).

El código está en `src/main/java/com/luciano/mobbrain/`:
- `Arbol.java`: el árbol de decisión (sin nada de Minecraft).
- `Cerebro.java`: guarda las fotos, entrena y predice.
- `MobBrain.java`: el plugin (eventos, mobs, hordas, comandos).

Además, `pruebas/` tiene las pruebas sin Minecraft y `herramientas/` sirve para compilar y verificar sin Maven. `CLAUDE.md` es el contexto completo para seguir con Claude Code.
