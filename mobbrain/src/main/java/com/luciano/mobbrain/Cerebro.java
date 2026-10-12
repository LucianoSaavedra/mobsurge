package com.luciano.mobbrain;

import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.List;
import java.util.Locale;
import java.util.Random;

/**
 * El "cerebro": guarda las fotos etiquetadas, entrena el árbol y predice el
 * riesgo de cada jugador. No depende de Bukkit.
 */
public final class Cerebro {

    /** Rasgos que la IA mira de cada jugador (el orden importa). */
    public static final String[] RASGOS = {
            "vida",          // 0  corazones x2 (0-20)
            "armadura",      // 1  puntos de armadura
            "hambre",        // 2  comida (0-20)
            "arma",          // 3  0 nada, 1 madera/oro, 2 piedra, 3 hierro/arco, 4 diamante, 5 netherite
            "kills5",        // 4  mobs hostiles matados en los últimos 5 min
            "dano5",         // 5  daño recibido en los últimos 5 min
            "mobsCerca",     // 6  mobs hostiles a menos de 16 bloques
            "minutosVivo",   // 7  minutos desde que entró o revivió
            "luz",           // 8  luz donde está parado (0-15)
            "noche",         // 9  1 si es de noche en el overworld
            "bajoTierra",    // 10 bloques debajo de la superficie
            "muertesHora",   // 11 muertes por hora de juego (historial)
    };

    public static final int N = RASGOS.length;

    /** Un modelo entrenado más sus métricas de validación. */
    public static final class Modelo {
        public final Arbol arbol;
        public final int muestras;
        public final int muertes;
        /** % de muertes que el árbol detecta en datos que no vio (validación). */
        public final double aciertoMuertes;
        /** % de veces que sobrevivió y el árbol también lo dijo. */
        public final double aciertoVivos;
        public final long cuando;

        Modelo(Arbol arbol, int muestras, int muertes, double aciertoMuertes, double aciertoVivos, long cuando) {
            this.arbol = arbol;
            this.muestras = muestras;
            this.muertes = muertes;
            this.aciertoMuertes = aciertoMuertes;
            this.aciertoVivos = aciertoVivos;
            this.cuando = cuando;
        }

        /** Precisión balanceada: promedio de los dos aciertos. */
        public double precision() {
            return (aciertoMuertes + aciertoVivos) / 2.0;
        }
    }

    /** Copia inmutable de los datos para entrenar en otro hilo. */
    public static final class Datos {
        final double[][] x;
        final int[] y;

        Datos(double[][] x, int[] y) {
            this.x = x;
            this.y = y;
        }

        public int muertes() {
            int p = 0;
            for (int v : y) p += v;
            return p;
        }

        public int size() {
            return y.length;
        }
    }

    private static final class Fila {
        final String jugador;
        final double[] x;
        final int y;

        Fila(String jugador, double[] x, int y) {
            this.jugador = jugador;
            this.x = x;
            this.y = y;
        }
    }

    private final Deque<Fila> filas = new ArrayDeque<>();
    private int maxMuestras;
    private int muertesEnDatos;
    private volatile Modelo modelo;

    public Cerebro(int maxMuestras) {
        this.maxMuestras = Math.max(100, maxMuestras);
    }

    public void setMaxMuestras(int max) {
        this.maxMuestras = Math.max(100, max);
        recortar();
    }

    // ------------------------------------------------------------------ datos

    public void agregar(String jugador, double[] x, int murio) {
        filas.addLast(new Fila(jugador, x.clone(), murio));
        muertesEnDatos += murio;
        recortar();
    }

    private void recortar() {
        while (filas.size() > maxMuestras) {
            muertesEnDatos -= filas.removeFirst().y;
        }
    }

    public int cantidad() {
        return filas.size();
    }

    public int muertesEnDatos() {
        return muertesEnDatos;
    }

    public void borrarTodo() {
        filas.clear();
        muertesEnDatos = 0;
        modelo = null;
    }

    public Datos copia() {
        double[][] x = new double[filas.size()][];
        int[] y = new int[filas.size()];
        int i = 0;
        for (Fila f : filas) {
            x[i] = f.x;
            y[i] = f.y;
            i++;
        }
        return new Datos(x, y);
    }

    // ----------------------------------------------------------- entrenamiento

    /** ¿Hay suficientes datos para entrenar? */
    public static boolean alcanza(Datos d, int minMuestras, int minMuertes) {
        int m = d.muertes();
        return d.size() >= minMuestras && m >= minMuertes && d.size() - m >= minMuertes;
    }

    /**
     * Entrena un modelo nuevo. Es puro (no toca el estado), así que se puede
     * llamar desde otro hilo. Primero mide qué tan bien predice usando un 20%
     * de datos que el árbol no ve, y después entrena con todo.
     */
    public static Modelo entrenar(Datos d, int maxProf, long semilla) {
        int n = d.size();
        int minHoja = Math.max(6, n / 60);

        // Validación: 80% entrena, 20% prueba.
        Integer[] orden = new Integer[n];
        for (int i = 0; i < n; i++) orden[i] = i;
        Random r = new Random(semilla);
        for (int i = n - 1; i > 0; i--) {
            int j = r.nextInt(i + 1);
            Integer t = orden[i];
            orden[i] = orden[j];
            orden[j] = t;
        }
        int corte = (int) Math.round(n * 0.8);
        double[][] xe = new double[corte][];
        int[] ye = new int[corte];
        for (int i = 0; i < corte; i++) {
            xe[i] = d.x[orden[i]];
            ye[i] = d.y[orden[i]];
        }
        Arbol prueba = Arbol.entrenar(xe, ye, RASGOS, maxProf, Math.max(4, corte / 60));
        int vp = 0, fn = 0, vn = 0, fp = 0;
        for (int i = corte; i < n; i++) {
            int k = orden[i];
            boolean dice = prueba.riesgo(d.x[k]) >= 0.5;
            if (d.y[k] == 1) {
                if (dice) vp++;
                else fn++;
            } else {
                if (dice) fp++;
                else vn++;
            }
        }
        double aciertoMuertes = vp + fn == 0 ? 0 : 100.0 * vp / (vp + fn);
        double aciertoVivos = vn + fp == 0 ? 0 : 100.0 * vn / (vn + fp);

        Arbol arbol = Arbol.entrenar(d.x, d.y, RASGOS, maxProf, minHoja);
        return new Modelo(arbol, n, d.muertes(), aciertoMuertes, aciertoVivos, System.currentTimeMillis());
    }

    public void setModelo(Modelo m) {
        this.modelo = m;
    }

    public Modelo modelo() {
        return modelo;
    }

    // -------------------------------------------------------------- predicción

    /** Riesgo de 0 a 1 de morir pronto (0.5 = jugador promedio). */
    public double riesgo(double[] x) {
        Modelo m = modelo;
        return m != null ? m.arbol.riesgo(x) : heuristica(x, null);
    }

    public List<String> explicar(double[] x) {
        Modelo m = modelo;
        if (m != null) return m.arbol.explicar(x);
        List<String> pasos = new ArrayList<>();
        double r = heuristica(x, pasos);
        pasos.add(String.format(Locale.ROOT, "reglas fijas (todavía aprendiendo): riesgo %d%%", Math.round(r * 100)));
        return pasos;
    }

    /**
     * Reglas a mano que se usan mientras no hay datos suficientes para el árbol.
     * Si {@code pasos} no es null, anota qué reglas se usaron.
     */
    static double heuristica(double[] x, List<String> pasos) {
        double r = 0.5;
        r += regla(x[0] < 8, 0.25, "vida baja", pasos);
        r += regla(x[0] >= 16, -0.10, "vida alta", pasos);
        r += regla(x[1] < 4, 0.10, "casi sin armadura", pasos);
        r += regla(x[1] >= 12, -0.15, "buena armadura", pasos);
        r += regla(x[3] <= 1, 0.10, "arma floja", pasos);
        r += regla(x[3] >= 4, -0.10, "arma fuerte", pasos);
        r += regla(x[6] >= 4, 0.15, "muchos mobs cerca", pasos);
        r += regla(x[4] >= 5 && x[0] >= 12, -0.15, "viene matando todo", pasos);
        r += regla(x[5] >= 10, 0.10, "recibió mucho daño", pasos);
        return Math.max(0.02, Math.min(0.98, r));
    }

    private static double regla(boolean cumple, double delta, String texto, List<String> pasos) {
        if (!cumple) return 0;
        if (pasos != null) pasos.add(texto + (delta > 0 ? " (+" : " (") + Math.round(delta * 100) + "%)");
        return delta;
    }

    // --------------------------------------------------------------------- CSV

    public static String cabeceraCsv() {
        return "jugador," + String.join(",", RASGOS) + ",murio";
    }

    /** Arma las líneas del CSV. Llamar desde el hilo principal; escribirlas puede ir en otro hilo. */
    public List<String> lineasCsv() {
        List<String> out = new ArrayList<>(filas.size() + 1);
        out.add(cabeceraCsv());
        StringBuilder sb = new StringBuilder();
        for (Fila f : filas) {
            sb.setLength(0);
            sb.append(f.jugador.replace(',', '_'));
            for (double v : f.x) sb.append(',').append(Arbol.num(v));
            sb.append(',').append(f.y);
            out.add(sb.toString());
        }
        return out;
    }

    public static void escribir(Path archivo, List<String> lineas) throws IOException {
        Files.createDirectories(archivo.getParent());
        Path tmp = archivo.resolveSibling(archivo.getFileName() + ".tmp");
        try (BufferedWriter w = Files.newBufferedWriter(tmp, StandardCharsets.UTF_8)) {
            for (String l : lineas) {
                w.write(l);
                w.newLine();
            }
        }
        Files.move(tmp, archivo, StandardCopyOption.REPLACE_EXISTING);
    }

    /** Carga datos guardados. Devuelve cuántas filas leyó. */
    public int cargar(Path archivo) throws IOException {
        if (!Files.exists(archivo)) return 0;
        int leidas = 0;
        try (BufferedReader r = Files.newBufferedReader(archivo, StandardCharsets.UTF_8)) {
            String linea = r.readLine(); // cabecera
            if (linea == null) return 0;
            while ((linea = r.readLine()) != null) {
                String[] p = linea.split(",");
                if (p.length != N + 2) continue;
                try {
                    double[] x = new double[N];
                    for (int i = 0; i < N; i++) x[i] = Double.parseDouble(p[i + 1]);
                    int y = Integer.parseInt(p[N + 1].trim());
                    agregar(p[0], x, y == 1 ? 1 : 0);
                    leidas++;
                } catch (NumberFormatException ignored) {
                    // fila rota: se saltea
                }
            }
        }
        return leidas;
    }
}
