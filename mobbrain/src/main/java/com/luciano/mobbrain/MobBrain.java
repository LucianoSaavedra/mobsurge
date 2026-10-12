package com.luciano.mobbrain;

import net.kyori.adventure.text.Component;
import net.kyori.adventure.text.serializer.legacy.LegacyComponentSerializer;
import net.kyori.adventure.title.Title;
import org.bukkit.Bukkit;
import org.bukkit.Difficulty;
import org.bukkit.GameMode;
import org.bukkit.Location;
import org.bukkit.Material;
import org.bukkit.World;
import org.bukkit.attribute.Attribute;
import org.bukkit.attribute.AttributeInstance;
import org.bukkit.block.Block;
import org.bukkit.block.BlockFace;
import org.bukkit.command.Command;
import org.bukkit.command.CommandSender;
import org.bukkit.command.PluginCommand;
import org.bukkit.configuration.file.FileConfiguration;
import org.bukkit.entity.Enemy;
import org.bukkit.entity.Entity;
import org.bukkit.entity.EntityType;
import org.bukkit.entity.LivingEntity;
import org.bukkit.entity.Mob;
import org.bukkit.entity.Player;
import org.bukkit.entity.Projectile;
import org.bukkit.event.EventHandler;
import org.bukkit.event.EventPriority;
import org.bukkit.event.Listener;
import org.bukkit.event.entity.CreatureSpawnEvent;
import org.bukkit.event.entity.EntityDamageByEntityEvent;
import org.bukkit.event.entity.EntityDamageEvent;
import org.bukkit.event.entity.EntityDeathEvent;
import org.bukkit.event.entity.PlayerDeathEvent;
import org.bukkit.event.player.PlayerJoinEvent;
import org.bukkit.event.player.PlayerQuitEvent;
import org.bukkit.event.player.PlayerRespawnEvent;
import org.bukkit.plugin.java.JavaPlugin;

import java.io.BufferedReader;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Random;
import java.util.UUID;

/**
 * MobBrain: una IA (árbol de decisión) que aprende cómo juega cada jugador y
 * le ajusta los mobs. Cada 15 s le saca una "foto" a cada uno (vida, armadura,
 * arma, mobs cerca...). Si muere en los 3 minutos siguientes, esa foto queda
 * marcada como "peligro". Con esas fotos entrena un árbol que estima el riesgo
 * de morir pronto, y según ese riesgo elige un nivel del 1 al 5.
 */
public final class MobBrain extends JavaPlugin implements Listener {

    /** Qué hace cada nivel. */
    private record Nivel(String nombre, String color, double mobsExtra, double vida, double dano, double velocidad) {
    }

    private record Foto(long tiempo, double[] rasgos) {
    }

    /** Lo que la IA sabe de un jugador conectado. */
    private static final class Estado {
        final Deque<Foto> pendientes = new ArrayDeque<>();
        final Deque<Long> kills = new ArrayDeque<>();
        final Deque<double[]> danos = new ArrayDeque<>();
        long inicioVida = ahora();
        long ultimoDano;
        EntityDamageEvent.DamageCause ultimaCausa;
        double riesgo = 0.5;
        boolean tieneRiesgo;
        int nivel = 3;
        int mobsCerca;
        long ultimaHorda;
        double[] ultimaFoto;
        boolean saludado;
    }

    /** Historial guardado de cada jugador. */
    private static final class Historial {
        String nombre;
        int muertes;
        double minutos;
    }

    private static final long CINCO_MIN = 5 * 60_000L;

    /** Reloj (se puede cambiar en las pruebas para simular el paso del tiempo). */
    static java.util.function.LongSupplier reloj = System::currentTimeMillis;

    private static long ahora() {
        return reloj.getAsLong();
    }
    private static final Title.Times TIEMPOS_TITULO =
            Title.Times.times(Duration.ofMillis(300), Duration.ofMillis(2500), Duration.ofMillis(700));

    private final Nivel[] niveles = new Nivel[6];
    private final double[] cortes = new double[4];
    private final Map<UUID, Estado> estados = new HashMap<>();
    private final Map<UUID, Historial> historial = new HashMap<>();
    private final Random random = new Random();
    private final Object candadoArchivos = new Object();

    private Cerebro cerebro;
    private volatile boolean entrenando;

    private int segundosFoto;
    private int ventanaSeg;
    private int minutosEntreEntrenos;
    private int maxProf;
    private int minFotos;
    private int minPeligro;
    private int maxFotos;
    private boolean avisarCambios;
    private boolean bienvenida;
    private int topeMobsCerca;
    private boolean hordas;
    private int hordaNivel;
    private int hordaCadaSeg;
    private int hordaMin;
    private int hordaMax;

    // ================================================================ arranque

    @Override
    public void onEnable() {
        saveDefaultConfig();
        leerConfig();
        cerebro = new Cerebro(maxFotos);
        try {
            int n = cerebro.cargar(archivo("datos.csv"));
            if (n > 0) getLogger().info("Cargué " + n + " fotos guardadas (" + cerebro.muertesEnDatos() + " de peligro).");
        } catch (IOException e) {
            getLogger().warning("No pude leer datos.csv: " + e.getMessage());
        }
        cargarHistorial();

        getServer().getPluginManager().registerEvents(this, this);
        PluginCommand cmd = getCommand("ia");
        if (cmd != null) {
            cmd.setExecutor(this);
            cmd.setTabCompleter(this);
        }

        long cadaFoto = Math.max(5, segundosFoto) * 20L;
        getServer().getScheduler().runTaskTimer(this, this::sacarFotos, cadaFoto, cadaFoto);
        long cadaEntreno = Math.max(1, minutosEntreEntrenos) * 60L * 20L;
        getServer().getScheduler().runTaskTimer(this, () -> entrenar(null), 20L * 20, cadaEntreno);
        if (hordas) getServer().getScheduler().runTaskTimer(this, this::revisarHordas, 20L * 60, 20L * 20);

        for (Player p : Bukkit.getOnlinePlayers()) estado(p);
        if (getServer().getPluginManager().getPlugin("MobSurge") != null) {
            getLogger().warning("MobSurge también está activo y se suma a MobBrain (doble de mobs y daño). "
                    + "El nivel 3 de MobBrain ya es igual a MobSurge: conviene sacar MobSurge.jar de plugins.");
        }
        getLogger().info("MobBrain activado. Cada " + segundosFoto + " s miro a cada jugador; el árbol se entrena cada "
                + minutosEntreEntrenos + " min.");
    }

    @Override
    public void onDisable() {
        if (cerebro == null) return;
        // Las fotos que todavía no cumplieron la ventana no tienen etiqueta: se descartan.
        for (Estado e : estados.values()) e.pendientes.clear();
        guardarTodo(false);
    }

    private void leerConfig() {
        FileConfiguration c = getConfig();
        segundosFoto = Math.max(5, c.getInt("segundos-entre-fotos", 15));
        ventanaSeg = Math.max(segundosFoto, c.getInt("ventana-segundos", 180));
        minutosEntreEntrenos = Math.max(1, c.getInt("minutos-entre-entrenamientos", 5));
        maxProf = Math.max(1, Math.min(8, c.getInt("profundidad-maxima", 4)));
        minFotos = Math.max(50, c.getInt("minimo-fotos", 150));
        minPeligro = Math.max(5, c.getInt("minimo-fotos-de-peligro", 20));
        maxFotos = Math.max(500, c.getInt("maximo-fotos", 20000));
        avisarCambios = c.getBoolean("avisar-cambios", true);
        bienvenida = c.getBoolean("mensaje-bienvenida", true);
        topeMobsCerca = Math.max(1, c.getInt("tope-mobs-cerca", 24));

        cortes[0] = c.getDouble("cortes.nivel-5", 0.20);
        cortes[1] = c.getDouble("cortes.nivel-4", 0.38);
        cortes[2] = c.getDouble("cortes.nivel-3", 0.62);
        cortes[3] = c.getDouble("cortes.nivel-2", 0.80);

        Nivel[] def = {
                null,
                new Nivel("Respiro", "&a", -0.30, 1.0, 1.0, 1.0),
                new Nivel("Suave", "&2", 0.10, 1.0, 1.25, 1.0),
                new Nivel("Normal", "&e", 0.33, 1.0, 1.5, 1.0),
                new Nivel("Difícil", "&6", 0.66, 1.25, 1.75, 1.08),
                new Nivel("Pesadilla", "&c", 1.0, 1.5, 2.0, 1.15),
        };
        for (int i = 1; i <= 5; i++) {
            String b = "niveles." + i + ".";
            Nivel d = def[i];
            niveles[i] = new Nivel(
                    c.getString(b + "nombre", d.nombre()),
                    c.getString(b + "color", d.color()),
                    Math.max(-1.0, Math.min(3.0, c.getDouble(b + "mobs-extra", d.mobsExtra()))),
                    Math.max(0.1, Math.min(5.0, c.getDouble(b + "vida", d.vida()))),
                    Math.max(0.1, Math.min(5.0, c.getDouble(b + "dano", d.dano()))),
                    Math.max(0.5, Math.min(2.0, c.getDouble(b + "velocidad", d.velocidad()))));
        }

        hordas = c.getBoolean("hordas.activadas", true);
        hordaNivel = Math.max(1, Math.min(5, c.getInt("hordas.nivel-minimo", 5)));
        hordaCadaSeg = Math.max(30, c.getInt("hordas.cada-segundos", 150));
        hordaMin = Math.max(1, c.getInt("hordas.minimo", 3));
        hordaMax = Math.max(hordaMin, c.getInt("hordas.maximo", 5));
    }

    // ================================================================== fotos

    private boolean jugando(Player p) {
        if (p.isDead()) return false;
        GameMode gm = p.getGameMode();
        return gm == GameMode.SURVIVAL || gm == GameMode.ADVENTURE;
    }

    private Estado estado(Player p) {
        return estados.computeIfAbsent(p.getUniqueId(), id -> new Estado());
    }

    private Historial historial(Player p) {
        Historial h = historial.computeIfAbsent(p.getUniqueId(), id -> new Historial());
        h.nombre = p.getName();
        return h;
    }

    /** Cada pocos segundos: etiqueta fotos viejas, saca fotos nuevas y actualiza el nivel. */
    private void sacarFotos() {
        long ahora = ahora();
        long ventanaMs = ventanaSeg * 1000L;
        for (Player p : Bukkit.getOnlinePlayers()) {
            Estado e = estado(p);
            // Fotos que ya cumplieron la ventana sin morir: "sobrevivió" (0).
            while (!e.pendientes.isEmpty() && ahora - e.pendientes.peekFirst().tiempo() > ventanaMs) {
                cerebro.agregar(p.getName(), e.pendientes.removeFirst().rasgos(), 0);
            }
            if (!jugando(p)) continue;

            double[] x = rasgos(p, e, ahora);
            e.ultimaFoto = x;
            e.pendientes.addLast(new Foto(ahora, x));
            historial(p).minutos += segundosFoto / 60.0;

            double r = cerebro.riesgo(x);
            e.riesgo = e.tieneRiesgo ? 0.5 * e.riesgo + 0.5 * r : r;
            e.tieneRiesgo = true;
            int nuevo = nivelPara(e.riesgo);
            if (nuevo != e.nivel) {
                int viejo = e.nivel;
                e.nivel = nuevo;
                if (avisarCambios) avisarCambio(p, viejo, nuevo);
            }
        }
    }

    private double[] rasgos(Player p, Estado e, long ahora) {
        double[] x = new double[Cerebro.N];
        x[0] = Math.round(p.getHealth() * 10) / 10.0;
        AttributeInstance armadura = p.getAttribute(Attribute.ARMOR);
        x[1] = armadura != null ? Math.round(armadura.getValue()) : 0;
        x[2] = p.getFoodLevel();
        x[3] = nivelArma(p.getInventory().getItemInMainHand().getType());

        podar(e, ahora);
        x[4] = e.kills.size();
        double dano = 0;
        for (double[] d : e.danos) dano += d[1];
        x[5] = Math.round(dano * 10) / 10.0;

        int mobs = 0;
        for (Entity cerca : p.getNearbyEntities(16, 8, 16)) {
            if (cerca instanceof Enemy && !cerca.isDead()) mobs++;
        }
        e.mobsCerca = mobs;
        x[6] = mobs;
        x[7] = Math.round((ahora - e.inicioVida) / 6000.0) / 10.0;

        Location l = p.getLocation();
        World w = p.getWorld();
        x[8] = l.getBlock().getLightLevel();
        x[9] = esNoche(w) ? 1 : 0;
        x[10] = Math.max(0, Math.min(64, w.getHighestBlockYAt(l) - l.getBlockY()));
        Historial h = historial(p);
        x[11] = Math.round(h.muertes / (h.minutos / 60.0 + 1.0) * 100) / 100.0;
        return x;
    }

    private void podar(Estado e, long ahora) {
        while (!e.kills.isEmpty() && ahora - e.kills.peekFirst() > CINCO_MIN) e.kills.removeFirst();
        while (!e.danos.isEmpty() && ahora - (long) e.danos.peekFirst()[0] > CINCO_MIN) e.danos.removeFirst();
    }

    private static int nivelArma(Material m) {
        String n = m.name();
        if (n.equals("MACE")) return 4;
        if (n.equals("BOW") || n.equals("CROSSBOW") || n.equals("TRIDENT")) return 3;
        if (!(n.endsWith("_SWORD") || n.endsWith("_AXE") || n.endsWith("_SPEAR"))) return 0;
        if (n.startsWith("NETHERITE")) return 5;
        if (n.startsWith("DIAMOND")) return 4;
        if (n.startsWith("IRON")) return 3;
        if (n.startsWith("STONE") || n.startsWith("COPPER")) return 2;
        return 1;
    }

    private static boolean esNoche(World w) {
        if (w.getEnvironment() != World.Environment.NORMAL) return false;
        long t = w.getTime();
        return t >= 13000 && t < 23000;
    }

    private int nivelPara(double riesgo) {
        if (riesgo < cortes[0]) return 5;
        if (riesgo < cortes[1]) return 4;
        if (riesgo < cortes[2]) return 3;
        if (riesgo < cortes[3]) return 2;
        return 1;
    }

    private void avisarCambio(Player p, int viejo, int nuevo) {
        Nivel nv = niveles[nuevo];
        String flecha = nuevo > viejo ? "&c▲ más difícil" : "&a▼ te da un respiro";
        p.sendActionBar(texto("&b⚔ MobBrain: " + nv.color() + "Nivel " + nuevo + " · " + nv.nombre() + " &8| " + flecha));
    }

    // ================================================================ eventos

    @EventHandler
    public void alEntrar(PlayerJoinEvent ev) {
        Player p = ev.getPlayer();
        Estado e = estado(p);
        e.inicioVida = ahora();
        historial(p);
        if (bienvenida && !e.saludado) {
            e.saludado = true;
            msg(p, "&b⚔ MobBrain &7está mirando cómo jugás y va a ajustar los mobs. Usá &f/ia nivel &7para ver qué piensa de vos.");
        }
    }

    @EventHandler
    public void alSalir(PlayerQuitEvent ev) {
        // Si se va, no sabemos si iba a morir: esas fotos se descartan.
        estados.remove(ev.getPlayer().getUniqueId());
    }

    @EventHandler
    public void alRevivir(PlayerRespawnEvent ev) {
        Estado e = estado(ev.getPlayer());
        e.inicioVida = ahora();
    }

    @EventHandler(priority = EventPriority.MONITOR, ignoreCancelled = true)
    public void alRecibirDano(EntityDamageEvent ev) {
        if (!(ev.getEntity() instanceof Player p)) return;
        Estado e = estado(p);
        long ahora = ahora();
        e.ultimoDano = ahora;
        e.ultimaCausa = ev.getCause();
        e.danos.addLast(new double[]{ahora, ev.getFinalDamage()});
    }

    @EventHandler(priority = EventPriority.MONITOR)
    public void alMorirJugador(PlayerDeathEvent ev) {
        Player p = ev.getEntity();
        Estado e = estado(p);
        long ahora = ahora();
        EntityDamageEvent.DamageCause causa = e.ultimaCausa;
        // Muertes forzadas (/kill, plugins) no le enseñan nada a la IA.
        boolean real = ahora - e.ultimoDano <= 10_000 && causa != null
                && causa != EntityDamageEvent.DamageCause.KILL
                && causa != EntityDamageEvent.DamageCause.CUSTOM
                && causa != EntityDamageEvent.DamageCause.SUICIDE;
        if (real) {
            long ventanaMs = ventanaSeg * 1000L;
            for (Foto f : e.pendientes) {
                cerebro.agregar(p.getName(), f.rasgos(), ahora - f.tiempo() <= ventanaMs ? 1 : 0);
            }
            historial(p).muertes++;
        }
        e.pendientes.clear();
        e.kills.clear();
        e.danos.clear();
        e.tieneRiesgo = false;
        e.riesgo = 0.5;
    }

    @EventHandler(priority = EventPriority.MONITOR)
    public void alMorirMob(EntityDeathEvent ev) {
        LivingEntity muerto = ev.getEntity();
        if (!(muerto instanceof Enemy)) return;
        Player asesino = muerto.getKiller();
        if (asesino != null) estado(asesino).kills.addLast(ahora());
    }

    @EventHandler(priority = EventPriority.HIGH, ignoreCancelled = true)
    public void alAparecer(CreatureSpawnEvent ev) {
        if (ev.getSpawnReason() != CreatureSpawnEvent.SpawnReason.NATURAL) return;
        LivingEntity mob = ev.getEntity();
        if (!(mob instanceof Enemy)) return;
        Player cerca = jugadorMasCercano(mob.getLocation(), 64);
        if (cerca == null) return;
        Estado e = estado(cerca);
        Nivel nv = niveles[e.nivel];

        if (nv.mobsExtra() < 0 && random.nextDouble() < -nv.mobsExtra()) {
            ev.setCancelled(true);
            return;
        }
        potenciar(mob, e.nivel);
        if (nv.mobsExtra() <= 0 || e.mobsCerca >= topeMobsCerca) return;

        int extra = (int) Math.floor(nv.mobsExtra());
        if (random.nextDouble() < nv.mobsExtra() - extra) extra++;
        Location donde = mob.getLocation();
        for (int i = 0; i < extra; i++) {
            try {
                Entity nuevo = mob.getWorld().spawnEntity(donde, mob.getType(), CreatureSpawnEvent.SpawnReason.CUSTOM);
                if (nuevo instanceof LivingEntity vivo) potenciar(vivo, e.nivel);
            } catch (RuntimeException ignorado) {
                return; // algún mob raro que no se puede clonar: se deja como está
            }
        }
    }

    @EventHandler(priority = EventPriority.HIGH, ignoreCancelled = true)
    public void alPegar(EntityDamageByEntityEvent ev) {
        if (!(ev.getEntity() instanceof Player p) || !jugando(p)) return;
        Entity atacante = ev.getDamager();
        if (atacante instanceof Projectile proyectil && proyectil.getShooter() instanceof Enemy tirador) {
            atacante = tirador;
        }
        if (!(atacante instanceof Enemy)) return;
        double mult = niveles[estado(p).nivel].dano();
        if (mult != 1.0) ev.setDamage(ev.getDamage() * mult);
    }

    private Player jugadorMasCercano(Location loc, double radio) {
        Player mejor = null;
        double mejorDist = radio * radio;
        for (Player p : loc.getWorld().getPlayers()) {
            if (!jugando(p)) continue;
            double d = p.getLocation().distanceSquared(loc);
            if (d < mejorDist) {
                mejorDist = d;
                mejor = p;
            }
        }
        return mejor;
    }

    private void potenciar(LivingEntity mob, int nivel) {
        Nivel nv = niveles[nivel];
        if (nv.vida() != 1.0) {
            AttributeInstance vida = mob.getAttribute(Attribute.MAX_HEALTH);
            if (vida != null) {
                vida.setBaseValue(Math.max(1.0, vida.getBaseValue() * nv.vida()));
                mob.setHealth(Math.max(1.0, vida.getValue()));
            }
        }
        if (nv.velocidad() != 1.0) {
            AttributeInstance vel = mob.getAttribute(Attribute.MOVEMENT_SPEED);
            if (vel != null) vel.setBaseValue(vel.getBaseValue() * nv.velocidad());
        }
    }

    // ================================================================= hordas

    private void revisarHordas() {
        long ahora = ahora();
        for (Player p : Bukkit.getOnlinePlayers()) {
            if (!jugando(p)) continue;
            Estado e = estado(p);
            if (e.nivel < hordaNivel || ahora - e.ultimaHorda < hordaCadaSeg * 1000L) continue;
            if (e.mobsCerca >= topeMobsCerca) continue;
            World w = p.getWorld();
            if (!esNoche(w) || w.getDifficulty() == Difficulty.PEACEFUL) continue;
            Location l = p.getLocation();
            if (w.getHighestBlockYAt(l) - l.getBlockY() > 3) continue; // bajo techo o bajo tierra: zafa

            int cantidad = hordaMin + random.nextInt(hordaMax - hordaMin + 1);
            int hechos = 0;
            for (int intento = 0; intento < cantidad * 5 && hechos < cantidad; intento++) {
                double ang = random.nextDouble() * Math.PI * 2;
                double dist = 14 + random.nextDouble() * 8;
                int x = l.getBlockX() + (int) Math.round(Math.cos(ang) * dist);
                int z = l.getBlockZ() + (int) Math.round(Math.sin(ang) * dist);
                if (!w.isChunkLoaded(x >> 4, z >> 4)) continue;
                Block suelo = w.getHighestBlockAt(x, z);
                Material tipoSuelo = suelo.getType();
                if (!tipoSuelo.isSolid() || suelo.isLiquid() || tipoSuelo.name().endsWith("LEAVES")) continue;
                Block pies = suelo.getRelative(BlockFace.UP);
                Block cabeza = pies.getRelative(BlockFace.UP);
                if (!pies.isPassable() || pies.isLiquid() || !cabeza.isPassable() || cabeza.isLiquid()) continue;
                Location donde = pies.getLocation().add(0.5, 0, 0.5);
                Entity m = w.spawnEntity(donde, sortearMobHorda(), CreatureSpawnEvent.SpawnReason.CUSTOM);
                if (m instanceof LivingEntity vivo) potenciar(vivo, e.nivel);
                if (m instanceof Mob conCerebro) conCerebro.setTarget(p);
                hechos++;
            }
            if (hechos > 0) {
                e.ultimaHorda = ahora;
                p.showTitle(Title.title(texto("&4☠ ¡Horda!"), texto("&7La IA te nota demasiado cómodo..."), TIEMPOS_TITULO));
            }
        }
    }

    private EntityType sortearMobHorda() {
        double r = random.nextDouble();
        if (r < 0.6) return EntityType.ZOMBIE;
        if (r < 0.85) return EntityType.SKELETON;
        return EntityType.SPIDER;
    }

    // ============================================================ entrenamiento

    private void entrenar(CommandSender quien) {
        if (entrenando) {
            if (quien != null) msg(quien, "&eYa estoy entrenando, esperá un toque.");
            return;
        }
        Cerebro.Datos d = cerebro.copia();
        if (!Cerebro.alcanza(d, minFotos, minPeligro)) {
            if (quien != null) {
                msg(quien, "&eTodavía no me alcanza para entrenar: tengo &f" + d.size() + "&e fotos y &f" + d.muertes()
                        + "&e de peligro. Necesito &f" + minFotos + "&e fotos y &f" + minPeligro + "&e de peligro.");
                msg(quien, "&7Mientras tanto uso reglas fijas. Cada muerte de verdad me da varias fotos de peligro.");
            }
            guardarTodo(true);
            return;
        }
        entrenando = true;
        long semilla = random.nextLong();
        int prof = maxProf;
        getServer().getScheduler().runTaskAsynchronously(this, () -> {
            Cerebro.Modelo m = null;
            try {
                m = Cerebro.entrenar(d, prof, semilla);
            } catch (Throwable t) {
                getLogger().warning("Falló el entrenamiento: " + t);
            }
            final Cerebro.Modelo listo = m;
            if (!isEnabled()) return;
            getServer().getScheduler().runTask(this, () -> {
                entrenando = false;
                if (listo == null) return;
                cerebro.setModelo(listo);
                String resumen = String.format(Locale.ROOT,
                        "Árbol nuevo: %d fotos (%d de peligro), %d hojas, precisión %.0f%% (detecta %.0f%% de las muertes).",
                        listo.muestras, listo.muertes, listo.arbol.hojas(), listo.precision(), listo.aciertoMuertes);
                getLogger().info(resumen);
                if (quien != null) msg(quien, "&a" + resumen + " &7Mirá &f/ia arbol");
                guardarTodo(true);
            });
        });
    }

    // ================================================================ comandos

    @Override
    public boolean onCommand(CommandSender s, Command command, String label, String[] args) {
        String sub = args.length == 0 ? "ayuda" : args[0].toLowerCase(Locale.ROOT);
        switch (sub) {
            case "nivel" -> cmdNivel(s, args);
            case "arbol", "árbol" -> cmdArbol(s);
            case "stats", "datos" -> cmdStats(s);
            case "entrenar" -> {
                if (admin(s)) entrenar(s);
            }
            case "reset" -> cmdReset(s, args);
            default -> ayuda(s);
        }
        return true;
    }

    @Override
    public List<String> onTabComplete(CommandSender s, Command command, String alias, String[] args) {
        List<String> opciones = new ArrayList<>();
        if (args.length == 1) {
            opciones.addAll(List.of("nivel", "arbol", "stats", "entrenar", "reset", "ayuda"));
        } else if (args.length == 2 && args[0].equalsIgnoreCase("nivel")) {
            for (Player p : Bukkit.getOnlinePlayers()) opciones.add(p.getName());
        } else if (args.length == 2 && args[0].equalsIgnoreCase("reset")) {
            opciones.add("confirmar");
        }
        String pref = args.length == 0 ? "" : args[args.length - 1].toLowerCase(Locale.ROOT);
        opciones.removeIf(o -> !o.toLowerCase(Locale.ROOT).startsWith(pref));
        return opciones;
    }

    private boolean admin(CommandSender s) {
        if (s.hasPermission("mobbrain.admin")) return true;
        msg(s, "&cEso solo lo puede hacer un admin (permiso mobbrain.admin).");
        return false;
    }

    private void ayuda(CommandSender s) {
        msg(s, "&b⚔ MobBrain &7- una IA que aprende cómo jugás");
        msg(s, "&f/ia nivel [jugador] &7qué nivel te puso y por qué");
        msg(s, "&f/ia arbol &7ver el árbol de decisión que aprendió");
        msg(s, "&f/ia stats &7cuántos datos tiene y qué tan bien predice");
        msg(s, "&f/ia entrenar &7entrenar ya (admin)");
        msg(s, "&f/ia reset confirmar &7borrar todo lo aprendido (admin)");
    }

    private void cmdNivel(CommandSender s, String[] args) {
        Player obj = null;
        if (args.length >= 2) obj = Bukkit.getPlayerExact(args[1]);
        else if (s instanceof Player p) obj = p;
        if (obj == null) {
            msg(s, args.length >= 2 ? "&cNo encontré a ese jugador conectado." : "&cUsá /ia nivel <jugador>");
            return;
        }
        Estado e = estado(obj);
        Nivel nv = niveles[e.nivel];
        msg(s, "&b⚔ MobBrain &7sobre &f" + obj.getName() + "&7: " + nv.color() + "Nivel " + e.nivel + " · " + nv.nombre());
        msg(s, String.format(Locale.ROOT, "&7Riesgo de morir pronto: &f%d%% &8(50%% = jugador promedio)", Math.round(e.riesgo * 100)));
        msg(s, "&7Efecto: &f" + textoExtra(nv.mobsExtra()) + "&7, vida de mobs &fx" + Arbol.num(nv.vida())
                + "&7, daño &fx" + Arbol.num(nv.dano()) + "&7, velocidad &fx" + Arbol.num(nv.velocidad()));
        if (e.ultimaFoto == null) {
            msg(s, "&7Todavía no le saqué ninguna foto. Esperá " + segundosFoto + " segundos.");
            return;
        }
        msg(s, cerebro.modelo() == null ? "&7Por qué (reglas fijas, todavía aprendiendo):" : "&7Por qué (camino en el árbol):");
        for (String paso : cerebro.explicar(e.ultimaFoto)) msg(s, "&8 → &f" + paso);
    }

    private void cmdArbol(CommandSender s) {
        Cerebro.Modelo m = cerebro.modelo();
        if (m == null) {
            msg(s, "&eTodavía no hay árbol. Tengo &f" + cerebro.cantidad() + "&e fotos y &f" + cerebro.muertesEnDatos()
                    + "&e de peligro; necesito " + minFotos + " y " + minPeligro + ". Mientras tanto uso reglas fijas.");
            return;
        }
        msg(s, "&b⚔ Árbol de MobBrain &7(sí = la condición se cumple)");
        for (String linea : m.arbol.dibujar(this::etiquetaHoja)) msg(s, "&f" + linea);
        msg(s, "&7También está guardado en &fplugins/MobBrain/arbol.txt");
    }

    private void cmdStats(CommandSender s) {
        msg(s, "&b⚔ MobBrain &7- datos");
        msg(s, "&7Fotos guardadas: &f" + cerebro.cantidad() + " &7(de peligro: &f" + cerebro.muertesEnDatos() + "&7)");
        Cerebro.Modelo m = cerebro.modelo();
        if (m == null) {
            msg(s, "&7Modelo: &ereglas fijas &7(faltan datos para el árbol)");
        } else {
            long hace = (ahora() - m.cuando) / 60000;
            msg(s, String.format(Locale.ROOT, "&7Árbol: &f%d hojas&7, profundidad &f%d&7, entrenado hace &f%d min&7 con &f%d fotos",
                    m.arbol.hojas(), m.arbol.profundidad(), hace, m.muestras));
            msg(s, String.format(Locale.ROOT, "&7Validación (datos que no vio): detecta &f%.0f%%&7 de las muertes y acierta &f%.0f%%&7 cuando sobreviven",
                    m.aciertoMuertes, m.aciertoVivos));
            int[] uso = m.arbol.usoDeRasgos();
            StringBuilder sb = new StringBuilder();
            for (int i = 0; i < uso.length; i++) {
                if (uso[i] > 0) sb.append(sb.length() == 0 ? "" : ", ").append(Cerebro.RASGOS[i]);
            }
            msg(s, "&7Lo que más mira: &f" + (sb.length() == 0 ? "nada todavía" : sb));
        }
        for (Player p : Bukkit.getOnlinePlayers()) {
            Estado e = estado(p);
            Nivel nv = niveles[e.nivel];
            msg(s, "&8 - &f" + p.getName() + "&7: " + nv.color() + "Nivel " + e.nivel + " " + nv.nombre()
                    + String.format(Locale.ROOT, " &8(riesgo %d%%)", Math.round(e.riesgo * 100)));
        }
    }

    private void cmdReset(CommandSender s, String[] args) {
        if (!admin(s)) return;
        if (args.length < 2 || !args[1].equalsIgnoreCase("confirmar")) {
            msg(s, "&cEsto borra todo lo que aprendió la IA. Si estás seguro: &f/ia reset confirmar");
            return;
        }
        cerebro.borrarTodo();
        for (Estado e : estados.values()) e.pendientes.clear();
        guardarTodo(true);
        msg(s, "&aListo, la IA arranca de cero.");
    }

    private String etiquetaHoja(double riesgo) {
        int n = nivelPara(riesgo);
        return "Nivel " + n + " " + niveles[n].nombre();
    }

    private static String textoExtra(double extra) {
        if (extra < 0) return Math.round(-extra * 100) + "% menos mobs";
        if (extra == 0) return "mobs normales";
        return "+" + Math.round(extra * 100) + "% mobs";
    }

    // ================================================================ archivos

    private Path archivo(String nombre) {
        return getDataFolder().toPath().resolve(nombre);
    }

    private void guardarTodo(boolean enOtroHilo) {
        List<String> datos = cerebro.lineasCsv();
        List<String> jugadores = lineasHistorial();
        List<String> arbol = textoArbol();
        Runnable escribir = () -> {
            synchronized (candadoArchivos) {
                try {
                    Cerebro.escribir(archivo("datos.csv"), datos);
                    Cerebro.escribir(archivo("jugadores.csv"), jugadores);
                    Cerebro.escribir(archivo("arbol.txt"), arbol);
                } catch (IOException e) {
                    getLogger().warning("No pude guardar los datos: " + e.getMessage());
                }
            }
        };
        if (enOtroHilo) getServer().getScheduler().runTaskAsynchronously(this, escribir);
        else escribir.run();
    }

    private List<String> textoArbol() {
        List<String> out = new ArrayList<>();
        Cerebro.Modelo m = cerebro.modelo();
        out.add("MobBrain - árbol de decisión");
        out.add("Rasgos: " + String.join(", ", Cerebro.RASGOS));
        if (m == null) {
            out.add("Todavía no hay árbol: " + cerebro.cantidad() + " fotos, " + cerebro.muertesEnDatos() + " de peligro.");
            return out;
        }
        out.add(String.format(Locale.ROOT, "Entrenado con %d fotos (%d de peligro). Validación: detecta %.0f%% de las muertes, acierta %.0f%% de los vivos.",
                m.muestras, m.muertes, m.aciertoMuertes, m.aciertoVivos));
        out.add("");
        out.addAll(m.arbol.dibujar(this::etiquetaHoja));
        return out;
    }

    private List<String> lineasHistorial() {
        List<String> out = new ArrayList<>();
        out.add("uuid,nombre,muertes,minutos");
        for (Map.Entry<UUID, Historial> en : historial.entrySet()) {
            Historial h = en.getValue();
            String nombre = h.nombre == null ? "?" : h.nombre.replace(',', '_');
            out.add(en.getKey() + "," + nombre + "," + h.muertes + "," + String.format(Locale.ROOT, "%.2f", h.minutos));
        }
        return out;
    }

    private void cargarHistorial() {
        Path f = archivo("jugadores.csv");
        if (!Files.exists(f)) return;
        try (BufferedReader r = Files.newBufferedReader(f, StandardCharsets.UTF_8)) {
            String linea = r.readLine();
            while ((linea = r.readLine()) != null) {
                String[] p = linea.split(",");
                if (p.length != 4) continue;
                try {
                    Historial h = new Historial();
                    h.nombre = p[1];
                    h.muertes = Integer.parseInt(p[2]);
                    h.minutos = Double.parseDouble(p[3]);
                    historial.put(UUID.fromString(p[0]), h);
                } catch (IllegalArgumentException ignored) {
                    // fila rota
                }
            }
        } catch (IOException e) {
            getLogger().warning("No pude leer jugadores.csv: " + e.getMessage());
        }
    }

    // ================================================================= textos

    private static Component texto(String s) {
        return LegacyComponentSerializer.legacyAmpersand().deserialize(s);
    }

    private static void msg(CommandSender s, String texto) {
        s.sendMessage(texto(texto));
    }
}
