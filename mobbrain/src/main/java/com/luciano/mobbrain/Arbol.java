package com.luciano.mobbrain;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Locale;

/**
 * Árbol de decisión binario (algoritmo CART con impureza de Gini).
 *
 * Cada nodo interno hace una pregunta del tipo "¿rasgo >= umbral?".
 * Si la respuesta es sí se baja por la rama {@code si}, si no por {@code no}.
 * Las hojas guardan el "riesgo": qué tan seguido murieron los jugadores que
 * llegaron a esa hoja, con las clases balanceadas (0.5 = jugador promedio).
 *
 * No depende de Bukkit, así se puede probar solo.
 */
public final class Arbol {

    /** Un nodo del árbol. Si {@code rasgo < 0} es una hoja. */
    public static final class Nodo {
        int rasgo = -1;
        double umbral;
        Nodo si;
        Nodo no;
        /** Riesgo balanceado (0 a 1) de las muestras que llegan acá. */
        double riesgo;
        /** Cantidad de muestras y cuántas terminaron en muerte. */
        int n;
        int muertes;

        boolean esHoja() {
            return rasgo < 0;
        }
    }

    private final Nodo raiz;
    private final String[] nombres;
    private final int hojas;
    private final int profundidad;

    private Arbol(Nodo raiz, String[] nombres) {
        this.raiz = raiz;
        this.nombres = nombres;
        this.hojas = contarHojas(raiz);
        this.profundidad = medirProfundidad(raiz);
    }

    public int hojas() {
        return hojas;
    }

    public int profundidad() {
        return profundidad;
    }

    // ------------------------------------------------------------ entrenamiento

    /**
     * Entrena un árbol.
     *
     * @param x        filas de rasgos
     * @param y        1 si el jugador murió en la ventana, 0 si sobrevivió
     * @param nombres  nombre de cada rasgo (para imprimir)
     * @param maxProf  profundidad máxima
     * @param minHoja  mínimo de muestras por hoja
     */
    public static Arbol entrenar(double[][] x, int[] y, String[] nombres, int maxProf, int minHoja) {
        int n = x.length;
        int pos = 0;
        for (int v : y) pos += v;
        int neg = n - pos;
        // Pesos balanceados: las muertes son raras, así que cada una pesa más.
        double wPos = pos == 0 ? 0 : n / (2.0 * pos);
        double wNeg = neg == 0 ? 0 : n / (2.0 * neg);
        double[] w = new double[n];
        for (int i = 0; i < n; i++) w[i] = y[i] == 1 ? wPos : wNeg;
        int[] idx = new int[n];
        for (int i = 0; i < n; i++) idx[i] = i;
        Nodo raiz = construir(x, y, w, idx, 0, maxProf, Math.max(1, minHoja));
        return new Arbol(raiz, nombres.clone());
    }

    private static Nodo construir(double[][] x, int[] y, double[] w, int[] idx, int prof, int maxProf, int minHoja) {
        Nodo nodo = new Nodo();
        double wTot = 0;
        double wPos = 0;
        int muertes = 0;
        for (int i : idx) {
            wTot += w[i];
            if (y[i] == 1) {
                wPos += w[i];
                muertes++;
            }
        }
        nodo.n = idx.length;
        nodo.muertes = muertes;
        nodo.riesgo = wTot > 0 ? wPos / wTot : 0.5;

        if (prof >= maxProf || idx.length < 2 * minHoja || muertes == 0 || muertes == idx.length) {
            return nodo;
        }

        double impPadre = gini(wPos, wTot);
        double mejorGanancia = 1e-7 * wTot;
        int mejorRasgo = -1;
        double mejorUmbral = 0;
        int rasgos = x[idx[0]].length;
        Integer[] orden = new Integer[idx.length];

        for (int f = 0; f < rasgos; f++) {
            for (int k = 0; k < idx.length; k++) orden[k] = idx[k];
            final int ff = f;
            Arrays.sort(orden, (a, b) -> Double.compare(x[a][ff], x[b][ff]));
            double izqTot = 0;
            double izqPos = 0;
            for (int k = 0; k < orden.length - 1; k++) {
                int i = orden[k];
                izqTot += w[i];
                if (y[i] == 1) izqPos += w[i];
                int cantIzq = k + 1;
                int cantDer = orden.length - cantIzq;
                if (cantIzq < minHoja || cantDer < minHoja) continue;
                double v = x[i][f];
                double siguiente = x[orden[k + 1]][f];
                if (siguiente <= v) continue; // mismo valor: no se puede cortar acá
                double ganancia = impPadre - gini(izqPos, izqTot) - gini(wPos - izqPos, wTot - izqTot);
                if (ganancia > mejorGanancia) {
                    mejorGanancia = ganancia;
                    mejorRasgo = f;
                    mejorUmbral = (v + siguiente) / 2.0;
                }
            }
        }

        if (mejorRasgo < 0) return nodo;

        int cantSi = 0;
        for (int i : idx) if (x[i][mejorRasgo] >= mejorUmbral) cantSi++;
        int[] si = new int[cantSi];
        int[] no = new int[idx.length - cantSi];
        int a = 0;
        int b = 0;
        for (int i : idx) {
            if (x[i][mejorRasgo] >= mejorUmbral) si[a++] = i;
            else no[b++] = i;
        }
        nodo.rasgo = mejorRasgo;
        nodo.umbral = mejorUmbral;
        nodo.si = construir(x, y, w, si, prof + 1, maxProf, minHoja);
        nodo.no = construir(x, y, w, no, prof + 1, maxProf, minHoja);
        return nodo;
    }

    /** Impureza de Gini ponderada: W * 2p(1-p). */
    private static double gini(double wPos, double wTot) {
        if (wTot <= 0) return 0;
        double p = wPos / wTot;
        return wTot * 2.0 * p * (1.0 - p);
    }

    // ------------------------------------------------------------- predicción

    private Nodo hoja(double[] x) {
        Nodo n = raiz;
        while (!n.esHoja()) n = x[n.rasgo] >= n.umbral ? n.si : n.no;
        return n;
    }

    /** Riesgo balanceado de 0 a 1 (0.5 = como un jugador promedio). */
    public double riesgo(double[] x) {
        return hoja(x).riesgo;
    }

    /** El camino de preguntas que recorrió el árbol para esta foto. */
    public List<String> explicar(double[] x) {
        List<String> pasos = new ArrayList<>();
        Nodo n = raiz;
        while (!n.esHoja()) {
            boolean si = x[n.rasgo] >= n.umbral;
            pasos.add(String.format(Locale.ROOT, "%s = %s %s %s",
                    nombres[n.rasgo], num(x[n.rasgo]), si ? ">=" : "<", num(n.umbral)));
            n = si ? n.si : n.no;
        }
        pasos.add(String.format(Locale.ROOT, "hoja: murieron %d de %d (riesgo %d%%)",
                n.muertes, n.n, Math.round(n.riesgo * 100)));
        return pasos;
    }

    /** Dibuja el árbol en líneas de texto. */
    public List<String> dibujar(java.util.function.DoubleFunction<String> etiquetaHoja) {
        List<String> out = new ArrayList<>();
        dibujar(raiz, "", "", out, etiquetaHoja);
        return out;
    }

    private void dibujar(Nodo n, String prefijo, String rama, List<String> out,
                         java.util.function.DoubleFunction<String> etiquetaHoja) {
        if (n.esHoja()) {
            out.add(prefijo + rama + String.format(Locale.ROOT, "riesgo %d%% (%d fotos, %d muertes) -> %s",
                    Math.round(n.riesgo * 100), n.n, n.muertes, etiquetaHoja.apply(n.riesgo)));
            return;
        }
        out.add(prefijo + rama + "¿" + nombres[n.rasgo] + " >= " + num(n.umbral) + "?");
        String sub = prefijo + (rama.isEmpty() ? "" : "   ");
        dibujar(n.si, sub, "sí: ", out, etiquetaHoja);
        dibujar(n.no, sub, "no: ", out, etiquetaHoja);
    }

    /** Cuántas veces el árbol usa cada rasgo para preguntar. */
    public int[] usoDeRasgos() {
        int[] uso = new int[nombres.length];
        contarUso(raiz, uso);
        return uso;
    }

    private static void contarUso(Nodo n, int[] uso) {
        if (n.esHoja()) return;
        uso[n.rasgo]++;
        contarUso(n.si, uso);
        contarUso(n.no, uso);
    }

    private static int contarHojas(Nodo n) {
        return n.esHoja() ? 1 : contarHojas(n.si) + contarHojas(n.no);
    }

    private static int medirProfundidad(Nodo n) {
        return n.esHoja() ? 0 : 1 + Math.max(medirProfundidad(n.si), medirProfundidad(n.no));
    }

    static String num(double v) {
        if (v == Math.rint(v)) return String.valueOf((long) v);
        return String.format(Locale.ROOT, "%.1f", v);
    }
}
