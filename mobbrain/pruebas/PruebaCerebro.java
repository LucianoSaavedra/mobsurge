import com.luciano.mobbrain.Cerebro;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayDeque;
import java.util.Deque;
import java.util.List;
import java.util.Random;

/**
 * Simula jugadores con distinta habilidad, genera fotos cada 15 s con la misma
 * regla de etiquetado del plugin (murió dentro de 180 s = 1) y entrena.
 */
public class PruebaCerebro {
    static int fallas = 0;

    static void check(boolean ok, String msg) {
        System.out.println((ok ? "  OK   " : "  FALLA ") + msg);
        if (!ok) fallas++;
    }

    public static void main(String[] args) throws Exception {
        Random r = new Random(7);
        Cerebro c = new Cerebro(20000);
        int vidas = 0;
        int ventana = 12; // 12 fotos de 15 s = 180 s
        String[] nombres = {"luciano", "joco", "pibe3", "pibe4"};
        double[] habilidad = {0.85, 0.35, 0.6, 0.15};

        while (c.cantidad() < 20000) {
            int p = r.nextInt(nombres.length);
            double hab = habilidad[p];
            vidas++;
            Deque<double[]> pendientes = new ArrayDeque<>();
            double vida = 20, armadura = hab * 15 * r.nextDouble(), arma = Math.min(5, Math.round(hab * 5 * r.nextDouble() + 1));
            int kills = 0;
            for (int t = 0; t < 400; t++) {
                boolean noche = (t / 40) % 2 == 1;
                double mobs = Math.max(0, r.nextGaussian() * 2 + (noche ? 4 : 1));
                double dano = Math.max(0, mobs * (1.2 - hab) * 2 + r.nextGaussian() * 2);
                vida = Math.max(1, Math.min(20, vida - dano * 0.25 + 3 * hab + r.nextGaussian()));
                if (r.nextDouble() < hab * 0.5) kills++;
                double luz = noche ? r.nextInt(6) : 8 + r.nextInt(8);
                double[] x = {
                        Math.round(vida), Math.round(armadura), 10 + r.nextInt(11), arma,
                        Math.min(kills, 15), Math.round(dano), Math.round(mobs), t * 0.25,
                        luz, noche ? 1 : 0, r.nextDouble() < 0.3 ? r.nextInt(40) : 0, 0
                };
                pendientes.addLast(x);
                // probabilidad real de morir en esta foto
                double pm = 0.002 + (vida < 8 ? 0.05 : 0) + (mobs >= 5 ? 0.03 : 0)
                        + (armadura < 4 ? 0.02 : 0) - (hab * 0.02);
                if (r.nextDouble() < Math.max(0.001, pm)) {
                    // murió: las últimas 'ventana' fotos son peligro
                    int i = 0, total = pendientes.size();
                    for (double[] f : pendientes) {
                        c.agregar(nombres[p], f, (total - i) <= ventana ? 1 : 0);
                        i++;
                    }
                    pendientes.clear();
                    break;
                }
                while (pendientes.size() > ventana) c.agregar(nombres[p], pendientes.removeFirst(), 0);
            }
            for (double[] f : pendientes) c.agregar(nombres[p], f, 0);
        }

        Cerebro.Datos d = c.copia();
        System.out.println("vidas simuladas: " + vidas + ", fotos: " + d.size() + ", fotos de peligro: " + d.muertes());
        check(Cerebro.alcanza(d, 100, 20), "alcanza para entrenar");

        long t0 = System.nanoTime();
        Cerebro.Modelo m = Cerebro.entrenar(d, 4, 42);
        long ms = (System.nanoTime() - t0) / 1_000_000;
        System.out.println("entrenó en " + ms + " ms, hojas=" + m.arbol.hojas() + ", profundidad=" + m.arbol.profundidad());
        System.out.printf("validación: detecta %.0f%% de las muertes, acierta %.0f%% de los vivos, precisión %.0f%%%n",
                m.aciertoMuertes, m.aciertoVivos, m.precision());
        check(ms < 3000, "entrenamiento rápido (" + ms + " ms)");
        check(m.precision() > 60, "aprende algo mejor que tirar una moneda");
        check(m.arbol.profundidad() <= 4, "respeta la profundidad máxima");

        int[] uso = m.arbol.usoDeRasgos();
        StringBuilder usados = new StringBuilder();
        for (int i = 0; i < uso.length; i++) if (uso[i] > 0) usados.append(Cerebro.RASGOS[i]).append("x").append(uso[i]).append(' ');
        System.out.println("rasgos usados: " + usados);
        check(uso[0] > 0 || uso[6] > 0, "usa vida o mobsCerca (los que causan muertes)");

        c.setModelo(m);
        double[] fuerte = {20, 18, 18, 5, 8, 0, 0, 30, 12, 0, 0, 0};
        double[] debil = {4, 0, 8, 1, 0, 12, 7, 5, 2, 1, 0, 0};
        double rf = c.riesgo(fuerte), rd = c.riesgo(debil);
        System.out.printf("riesgo jugador fuerte=%.2f, débil=%.2f%n", rf, rd);
        check(rf < rd, "el débil tiene más riesgo que el fuerte");

        System.out.println("camino del débil:");
        for (String s : c.explicar(debil)) System.out.println("    " + s);
        System.out.println("árbol:");
        List<String> dib = m.arbol.dibujar(v -> v < 0.2 ? "Nivel 5" : v < 0.38 ? "Nivel 4" : v < 0.62 ? "Nivel 3" : v < 0.8 ? "Nivel 2" : "Nivel 1");
        for (String s : dib) System.out.println("    " + s);

        // CSV ida y vuelta
        Path tmp = Files.createTempDirectory("mb").resolve("datos.csv");
        Cerebro.escribir(tmp, c.lineasCsv());
        Cerebro c2 = new Cerebro(20000);
        int leidas = c2.cargar(tmp);
        check(leidas == c.cantidad() && c2.muertesEnDatos() == c.muertesEnDatos(), "CSV ida y vuelta (" + leidas + " filas)");
        System.out.println("primeras líneas CSV:");
        Files.readAllLines(tmp).stream().limit(3).forEach(l -> System.out.println("    " + l));

        // tope de muestras
        Cerebro chico = new Cerebro(100);
        for (int i = 0; i < 250; i++) chico.agregar("x", fuerte, i % 10 == 0 ? 1 : 0);
        check(chico.cantidad() == 100 && chico.muertesEnDatos() == 10, "respeta el máximo de muestras y cuenta bien las muertes");

        // heurística sin modelo
        Cerebro vacio = new Cerebro(1000);
        check(vacio.riesgo(debil) > vacio.riesgo(fuerte), "reglas fijas: débil > fuerte sin modelo");
        System.out.println("reglas fijas para el débil: " + vacio.explicar(debil));

        // árbol con datos de una sola clase no rompe
        Cerebro todos0 = new Cerebro(1000);
        for (int i = 0; i < 200; i++) todos0.agregar("x", fuerte, 0);
        check(!Cerebro.alcanza(todos0.copia(), 100, 20), "no entrena sin muertes");

        System.out.println(fallas == 0 ? "TODO OK" : ("FALLAS: " + fallas));
        System.exit(fallas == 0 ? 0 : 1);
    }
}
