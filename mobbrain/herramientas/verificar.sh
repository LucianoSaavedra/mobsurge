#!/usr/bin/env bash
# Compila y verifica MobBrain SIN Maven, contra las fuentes reales de la API de Paper.
# Sirve cuando no hay acceso a repo.papermc.io ni a Maven Central (pasó en la nube).
# Con Maven o GitHub Actions no hace falta: usá `mvn clean package`.
#
# Uso (Linux, macOS o WSL):   bash herramientas/verificar.sh
# Necesita: git, JDK 21 o más nuevo (javac, java, jar, javap) y python3.
# Opcional: PAPER_RAMA=ver/26.2 para verificar contra otra versión de Paper.
#           TRABAJO=/otra/carpeta para la carpeta temporal (por defecto .verificar/).
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
H="$RAIZ/herramientas"
P="$RAIZ/pruebas"
W="${TRABAJO:-$RAIZ/.verificar}"
PAPER_RAMA="${PAPER_RAMA:-ver/26.1.2}"
PAPER_DIR="paper-${PAPER_RAMA//\//-}"
PY="$(command -v python3 || command -v python)"
mkdir -p "$W/deps"
W="$(cd "$W" && pwd)"

clonar() { # repo etiqueta carpeta
  if [ ! -d "$3" ]; then
    git -c advice.detachedHead=false clone --quiet --depth 1 --filter=blob:none --branch "$2" "https://github.com/$1" "$3"
  fi
}

echo "== 1/6 Fuentes de la API de Paper ($PAPER_RAMA) desde GitHub (solo la primera vez)"
cd "$W/deps"
if [ ! -d "$PAPER_DIR/paper-api" ]; then
  rm -rf "$PAPER_DIR"
  git clone --quiet --filter=blob:none --no-checkout --depth 1 --branch "$PAPER_RAMA" https://github.com/PaperMC/Paper "$PAPER_DIR"
  git -C "$PAPER_DIR" sparse-checkout init --cone
  git -C "$PAPER_DIR" sparse-checkout set paper-api
  git -C "$PAPER_DIR" -c advice.detachedHead=false checkout --quiet "$PAPER_RAMA"
fi
GK="$PAPER_DIR/paper-api/build.gradle.kts"
ADV="$(sed -n 's/.*adventureVersion = "\([^"]*\)".*/\1/p' "$GK" | head -1)"
ANOT="$(sed -n 's/.*annotationsVersion = "\([^"]*\)".*/\1/p' "$GK" | head -1)"
clonar KyoriPowered/adventure "v$ADV" "adventure-$ADV"
clonar KyoriPowered/examination v1.3.0 examination
clonar KyoriPowered/option v1.1.0 option
clonar jspecify/jspecify v1.0.0 jspecify
clonar JetBrains/java-annotations "$ANOT" "java-annotations-$ANOT"
find . -name module-info.java -delete
SP="$PAPER_DIR/paper-api/src/main/java:$PAPER_DIR/paper-api/src/generated/java"
for d in $(find "adventure-$ADV" -type d -path '*/src/main/java' | grep -v -E 'configurate|annotation-processors|nbt|ansi|extra-kotlin' | sort); do
  SP="$SP:$d"
done
SP="$SP:examination/api/src/main/java:examination/string/src/main/java:option/src/main/java:jspecify/src/main/java:java-annotations-$ANOT/src/jvmMain/java"
echo "   Paper: $PAPER_RAMA · Adventure $ADV · JetBrains annotations $ANOT"

procesar() { # modo opciones... (corre el procesador sobre las fuentes reales, sin compilar cuerpos)
  local modo="$1"; shift
  (cd "$W/deps" && javac -proc:only -implicit:none -nowarn -Xmaxerrs 1 \
      -processorpath "$W/procesador" -processor StubGen -Astubgen.mode="$modo" "$@" \
      -sourcepath "$SP" -d "$W/nada" "$W/Dummy.java") > "$W/$modo.log" 2>&1 || true
}

echo "== 2/6 Stubs exactos de la API"
rm -rf "$W/procesador" "$W/stubs" "$W/stubs-cls" "$W/nada"
mkdir -p "$W/procesador" "$W/stubs" "$W/stubs-cls" "$W/nada"
javac -nowarn -d "$W/procesador" "$H/StubGen.java"
printf 'public class Dummy {}\n' > "$W/Dummy.java"
procesar gen -Astubgen.types="$H/tipos.txt" -Astubgen.out="$W/stubs"
if [ ! -f "$W/stubs/org/bukkit/Bukkit.java" ]; then
  echo "No se generaron los stubs. Log:"; cat "$W/gen.log"; exit 1
fi
if grep -q '^MISSING' "$W/stubs/gen-report.txt"; then
  echo "Tipos que no existen en esta versión:"; grep '^MISSING' "$W/stubs/gen-report.txt"; exit 1
fi
javac -nowarn -encoding UTF-8 --release 21 -d "$W/stubs-cls" $(find "$W/stubs" -name '*.java')

echo "== 3/6 Compilar el plugin -> target/MobBrain.jar"
rm -rf "$W/clases" && mkdir -p "$W/clases" "$RAIZ/target"
javac -encoding UTF-8 --release 21 -Xlint:all -Xlint:-processing -cp "$W/stubs-cls" -d "$W/clases" $(find "$RAIZ/src/main/java" -name '*.java')
cp "$RAIZ/src/main/resources/"* "$W/clases/"
rm -f "$RAIZ/target/MobBrain.jar"
jar --create --file "$RAIZ/target/MobBrain.jar" -C "$W/clases" .

echo "== 4/6 Verificar cada llamada a la API contra las fuentes reales"
"$PY" "$H/refs.py" "$W/clases" "$W/refs.txt"
procesar verify -Astubgen.refs="$W/refs.txt" -Astubgen.out="$W/verificacion.txt"
if [ ! -f "$W/verificacion.txt" ]; then
  echo "No corrió la verificación. Log:"; cat "$W/verify.log"; exit 1
fi
head -1 "$W/verificacion.txt"
if grep -q '^FAIL ' "$W/verificacion.txt"; then
  grep '^FAIL ' "$W/verificacion.txt"; exit 1
fi
grep -E 'DEPRECATED|ApiStatus' "$W/verificacion.txt" || true

echo "== 5/6 Prueba del árbol (jugadores simulados, sin Minecraft)"
rm -rf "$W/prueba-arbol" && mkdir -p "$W/prueba-arbol"
javac -nowarn -encoding UTF-8 -d "$W/prueba-arbol" \
  "$RAIZ/src/main/java/com/luciano/mobbrain/Arbol.java" "$RAIZ/src/main/java/com/luciano/mobbrain/Cerebro.java" "$P/PruebaCerebro.java"
java -Dfile.encoding=UTF-8 -Dstdout.encoding=UTF-8 -cp "$W/prueba-arbol" PruebaCerebro > "$W/prueba-arbol.txt" 2>&1 \
  || { cat "$W/prueba-arbol.txt"; exit 1; }
grep -E 'FALLA|validación|TODO OK' "$W/prueba-arbol.txt"

echo "== 6/6 Prueba del plugin con un servidor falso"
"$PY" "$P/servidor-falso/parchear_stubs.py" "$W/stubs" "$W/stubs-prueba"
rm -rf "$W/prueba-servidor" && mkdir -p "$W/prueba-servidor"
javac -nowarn -encoding UTF-8 --release 21 -d "$W/prueba-servidor" \
  $(find "$W/stubs-prueba" -name '*.java') $(find "$P/servidor-falso" -name '*.java') $(find "$RAIZ/src/main/java" -name '*.java')
java -Dfile.encoding=UTF-8 -Dstdout.encoding=UTF-8 -cp "$W/prueba-servidor" prueba.PruebaPlugin > "$W/prueba-servidor.txt" 2>&1 \
  || { cat "$W/prueba-servidor.txt"; exit 1; }
echo "   $(grep -c '^  OK' "$W/prueba-servidor.txt") pruebas OK"
grep -E 'FALLA|TODO OK' "$W/prueba-servidor.txt"

echo
echo "Listo: target/MobBrain.jar compilado y verificado."
