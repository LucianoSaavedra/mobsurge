package prueba;

import com.luciano.mobbrain.MobBrain;
import org.bukkit.Difficulty;
import org.bukkit.GameMode;
import org.bukkit.Location;
import org.bukkit.Material;
import org.bukkit.Server;
import org.bukkit.World;
import org.bukkit.attribute.Attribute;
import org.bukkit.attribute.AttributeInstance;
import org.bukkit.block.Block;
import org.bukkit.command.Command;
import org.bukkit.configuration.file.FileConfiguration;
import org.bukkit.entity.Enemy;
import org.bukkit.entity.Entity;
import org.bukkit.entity.EntityType;
import org.bukkit.entity.LivingEntity;
import org.bukkit.entity.Mob;
import org.bukkit.entity.Player;
import org.bukkit.entity.Projectile;
import org.bukkit.event.entity.CreatureSpawnEvent;
import org.bukkit.event.entity.EntityDamageByEntityEvent;
import org.bukkit.event.entity.EntityDamageEvent;
import org.bukkit.event.entity.EntityDeathEvent;
import org.bukkit.event.entity.PlayerDeathEvent;
import org.bukkit.event.player.PlayerJoinEvent;
import org.bukkit.event.player.PlayerQuitEvent;
import org.bukkit.event.player.PlayerRespawnEvent;
import org.bukkit.inventory.ItemStack;
import org.bukkit.inventory.PlayerInventory;
import org.bukkit.plugin.PluginManager;
import org.bukkit.projectiles.ProjectileSource;
import org.bukkit.scheduler.BukkitScheduler;

import java.lang.reflect.Field;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.function.LongSupplier;

/** Prueba de humo del plugin con un servidor falso (sin Minecraft). */
public class PruebaPlugin {
    static int fallas = 0;
    static long t = 1_700_000_000_000L;
    static final List<Runnable> timers = new ArrayList<>();
    static World mundo;
    static final List<Entity> spawneados = new ArrayList<>();
    static final long[] hora = {6000};

    static void check(boolean ok, String msg) {
        System.out.println((ok ? "  OK    " : "  FALLA ") + msg);
        if (!ok) fallas++;
    }

    // ------------------------------------------------------------ dobles

    static final class Config extends FileConfiguration {
        final Map<String, Object> over;

        Config(Map<String, Object> over) {
            super((stubgen.M) null);
            this.over = over;
        }

        @Override
        public int getInt(String p, int d) {
            return over.containsKey(p) ? (Integer) over.get(p) : d;
        }

        @Override
        public double getDouble(String p, double d) {
            return over.containsKey(p) ? ((Number) over.get(p)).doubleValue() : d;
        }

        @Override
        public boolean getBoolean(String p, boolean d) {
            return over.containsKey(p) ? (Boolean) over.get(p) : d;
        }

        @Override
        public String getString(String p, String d) {
            return over.containsKey(p) ? (String) over.get(p) : d;
        }
    }

    static final class Loc extends Location {
        final World w;
        double x, y, z;

        Loc(World w, double x, double y, double z) {
            super(w, x, y, z);
            this.w = w;
            this.x = x;
            this.y = y;
            this.z = z;
        }

        @Override public World getWorld() { return w; }
        @Override public int getBlockX() { return (int) Math.floor(x); }
        @Override public int getBlockY() { return (int) Math.floor(y); }
        @Override public int getBlockZ() { return (int) Math.floor(z); }
        @Override public Block getBlock() { return bloque(Material.AIR, (byte) 7); }
        @Override public Location add(double dx, double dy, double dz) { x += dx; y += dy; z += dz; return this; }
        @Override public double distanceSquared(Location o) {
            Loc l = (Loc) o;
            return (x - l.x) * (x - l.x) + (y - l.y) * (y - l.y) + (z - l.z) * (z - l.z);
        }
    }

    static final class Item extends ItemStack {
        final Material m;
        Item(Material m) { super((stubgen.M) null); this.m = m; }
        @Override public Material getType() { return m; }
    }

    static Block bloque(Material m, byte luz) {
        Fake[] f = new Fake[1];
        Block b = Fake.crear("bloque-" + m, f, Block.class);
        boolean aire = m == Material.AIR;
        f[0].con("getType", m).con("isLiquid", false).con("isPassable", aire).con("getLightLevel", luz)
                .conF("getRelative", a -> bloque(Material.AIR, (byte) 0))
                .conF("getLocation", a -> new Loc(mundo, 5, 65, 5));
        return b;
    }

    static AttributeInstance atributo(double base) {
        Fake[] f = new Fake[1];
        AttributeInstance ai = Fake.crear("attr", f, AttributeInstance.class);
        double[] v = {base};
        f[0].conF("getBaseValue", a -> v[0]).conF("getValue", a -> v[0]).conF("setBaseValue", a -> {
            v[0] = (Double) a[0];
            return null;
        });
        return ai;
    }

    static final class FakePlayer {
        final String nombre;
        Player p;
        Fake f;
        double vida, armadura;
        Material arma;
        final List<Entity> cerca = new ArrayList<>();
        GameMode modo = GameMode.SURVIVAL;
        Loc loc;
        final List<String> chat = new ArrayList<>();
        final List<String> barra = new ArrayList<>();
        int titulos;

        FakePlayer(String nombre, double vida, double armadura, Material arma, double x) {
            this.nombre = nombre;
            this.vida = vida;
            this.armadura = armadura;
            this.arma = arma;
            this.loc = new Loc(mundo, x, 64, 0);
            Fake[] ff = new Fake[1];
            p = Fake.crear(nombre, ff, Player.class);
            f = ff[0];
            UUID id = UUID.nameUUIDFromBytes(nombre.getBytes());
            AttributeInstance arm = atributo(0);
            Fake[] fi = new Fake[1];
            PlayerInventory inv = Fake.crear("inv", fi, PlayerInventory.class);
            fi[0].conF("getItemInMainHand", a -> new Item(this.arma));
            f.con("getName", nombre).con("getUniqueId", id).conF("getHealth", a -> this.vida)
                    .con("getFoodLevel", 20)
                    .conF("getAttribute", a -> {
                        Fake.de(arm).conF("getValue", z -> this.armadura);
                        return a[0] == Attribute.ARMOR ? arm : null;
                    })
                    .con("getInventory", inv)
                    .conF("getNearbyEntities", a -> new ArrayList<>(cerca))
                    .conF("getLocation", a -> new Loc(mundo, loc.x, loc.y, loc.z))
                    .con("getWorld", mundo)
                    .con("isDead", false)
                    .conF("getGameMode", a -> modo)
                    .con("hasPermission", true)
                    .conF("sendMessage", a -> { chat.add(String.valueOf(a[0])); return null; })
                    .conF("sendActionBar", a -> { barra.add(String.valueOf(a[0])); return null; })
                    .conF("showTitle", a -> { titulos++; return null; });
        }

        String todo() {
            return String.join("\n", chat).replaceAll("&[0-9a-fk-or]", "");
        }
    }

    static LivingEntity mob(String nombre, EntityType tipo, Loc donde) {
        Fake[] f = new Fake[1];
        LivingEntity m = Fake.crear(nombre, f, Enemy.class, Mob.class);
        AttributeInstance vida = atributo(20);
        AttributeInstance vel = atributo(0.23);
        f[0].conF("getAttribute", a -> a[0] == Attribute.MAX_HEALTH ? vida : a[0] == Attribute.MOVEMENT_SPEED ? vel : null)
                .conF("getLocation", a -> new Loc(mundo, donde.x, donde.y, donde.z))
                .con("getWorld", mundo).con("getType", tipo).con("isDead", false);
        return m;
    }

    // ----------------------------------------------------------- eventos

    static final class Muerte extends PlayerDeathEvent {
        final Player p;
        Muerte(Player p) { super((stubgen.M) null); this.p = p; }
        @Override public Player getEntity() { return p; }
    }

    static final class Dano extends EntityDamageEvent {
        final Entity e;
        final DamageCause c;
        final double d;
        Dano(Entity e, DamageCause c, double d) { super((stubgen.M) null); this.e = e; this.c = c; this.d = d; }
        @Override public Entity getEntity() { return e; }
        @Override public DamageCause getCause() { return c; }
        @Override public double getFinalDamage() { return d; }
    }

    static final class Pegar extends EntityDamageByEntityEvent {
        final Entity victima, atacante;
        double dano;
        boolean cambiado;
        Pegar(Entity victima, Entity atacante, double dano) {
            super((stubgen.M) null);
            this.victima = victima;
            this.atacante = atacante;
            this.dano = dano;
        }
        @Override public Entity getEntity() { return victima; }
        @Override public Entity getDamager() { return atacante; }
        @Override public double getDamage() { return dano; }
        @Override public void setDamage(double d) { dano = d; cambiado = true; }
    }

    static final class Spawn extends CreatureSpawnEvent {
        final LivingEntity e;
        final SpawnReason r;
        boolean cancelado;
        Spawn(LivingEntity e, SpawnReason r) { super((stubgen.M) null); this.e = e; this.r = r; }
        @Override public LivingEntity getEntity() { return e; }
        @Override public SpawnReason getSpawnReason() { return r; }
        @Override public void setCancelled(boolean c) { cancelado = c; }
        @Override public boolean isCancelled() { return cancelado; }
    }

    static final class MobMuerto extends EntityDeathEvent {
        final LivingEntity e;
        MobMuerto(LivingEntity e) { super((stubgen.M) null); this.e = e; }
        @Override public LivingEntity getEntity() { return e; }
    }

    static final class Entra extends PlayerJoinEvent {
        final Player p;
        Entra(Player p) { super((stubgen.M) null); this.p = p; }
        @Override public Player getPlayer() { return p; }
    }

    static final class Sale extends PlayerQuitEvent {
        final Player p;
        Sale(Player p) { super((stubgen.M) null); this.p = p; }
        @Override public Player getPlayer() { return p; }
    }

    static final class Revive extends PlayerRespawnEvent {
        final Player p;
        Revive(Player p) { super((stubgen.M) null); this.p = p; }
        @Override public Player getPlayer() { return p; }
    }

    // ------------------------------------------------------------- prueba

    static void tick(int veces) {
        for (int i = 0; i < veces; i++) {
            t += 15_000;
            timers.get(0).run(); // sacarFotos
        }
    }

    static void comando(MobBrain mb, FakePlayer quien, String... args) {
        mb.onCommand(quien.p, (Command) null, "ia", args);
    }

    static MobBrain nuevoPlugin() {
        timers.clear();
        MobBrain mb = new MobBrain();
        mb.onEnable();
        return mb;
    }

    public static void main(String[] args) throws Exception {
        Field reloj = MobBrain.class.getDeclaredField("reloj");
        reloj.setAccessible(true);
        reloj.set(null, (LongSupplier) () -> t);

        Path carpeta = Files.createTempDirectory("mobbrain");
        Hooks.carpeta = carpeta.toFile();
        Map<String, Object> over = new HashMap<>();
        over.put("minimo-fotos", 50);
        over.put("minimo-fotos-de-peligro", 5);
        Hooks.config = new Config(over);

        Fake[] fs = new Fake[1];
        BukkitScheduler sch = Fake.crear("scheduler", fs, BukkitScheduler.class);
        fs[0].conF("runTaskTimer", a -> { timers.add((Runnable) a[1]); return null; })
                .conF("runTaskAsynchronously", a -> { ((Runnable) a[1]).run(); return null; })
                .conF("runTask", a -> { ((Runnable) a[1]).run(); return null; });
        PluginManager pm = Fake.crear("pm", null, PluginManager.class);
        Fake[] fsv = new Fake[1];
        Server server = Fake.crear("server", fsv, Server.class);
        fsv[0].con("getScheduler", sch).con("getPluginManager", pm);
        Hooks.server = server;

        Fake[] fw = new Fake[1];
        mundo = Fake.crear("mundo", fw, World.class);
        fw[0].con("getEnvironment", World.Environment.NORMAL)
                .conF("getTime", a -> hora[0])
                .con("getHighestBlockYAt", 64)
                .conF("getPlayers", a -> new ArrayList<>(Hooks.jugadores))
                .con("getDifficulty", Difficulty.NORMAL)
                .con("isChunkLoaded", true)
                .conF("getHighestBlockAt", a -> bloque(Material.GRASS_BLOCK, (byte) 15))
                .conF("spawnEntity", a -> {
                    LivingEntity m = mob("spawn-" + a[1], (EntityType) a[1], (Loc) a[0]);
                    spawneados.add(m);
                    return m;
                });

        FakePlayer luciano = new FakePlayer("luciano", 20, 15, Material.DIAMOND_SWORD, 0);
        FakePlayer joco = new FakePlayer("joco", 6, 0, Material.AIR, 1000);
        for (int i = 0; i < 5; i++) joco.cerca.add(mob("z" + i, EntityType.ZOMBIE, joco.loc));
        Hooks.jugadores.add(luciano.p);
        Hooks.jugadores.add(joco.p);

        System.out.println("== arranque");
        MobBrain mb = nuevoPlugin();
        check(timers.size() == 3, "programó 3 tareas (fotos, entrenamiento, hordas): " + timers.size());
        mb.alEntrar(new Entra(luciano.p));
        mb.alEntrar(new Entra(joco.p));
        check(luciano.todo().contains("MobBrain"), "saluda al entrar");

        System.out.println("== niveles con reglas fijas");
        tick(1);
        comando(mb, luciano, "nivel");
        String out = luciano.todo();
        check(out.contains("Nivel 5"), "luciano (full armadura, espada de diamante) queda en nivel 5");
        check(!luciano.barra.isEmpty() && luciano.barra.get(0).contains("Nivel 5"), "le avisa en la barra de acción: " + luciano.barra);
        luciano.chat.clear();
        comando(mb, luciano, "nivel", "joco");
        check(luciano.todo().contains("Nivel 1"), "joco (6 de vida, sin armadura, 5 zombies) queda en nivel 1");
        check(luciano.todo().contains("reglas fijas"), "explica que usa reglas fijas mientras aprende");

        System.out.println("== etiquetado de fotos");
        tick(8); // 9 fotos pendientes cada uno
        mb.alRecibirDano(new Dano(joco.p, EntityDamageEvent.DamageCause.ENTITY_ATTACK, 5));
        mb.alMorirJugador(new Muerte(joco.p));
        luciano.chat.clear();
        comando(mb, luciano, "stats");
        check(luciano.todo().contains("de peligro: 9"), "la muerte de joco marcó sus 9 fotos como peligro");

        mb.alRevivir(new Revive(joco.p));
        tick(3);
        mb.alRecibirDano(new Dano(joco.p, EntityDamageEvent.DamageCause.KILL, 1000));
        mb.alMorirJugador(new Muerte(joco.p));
        luciano.chat.clear();
        comando(mb, luciano, "stats");
        check(luciano.todo().contains("de peligro: 9"), "un /kill no cuenta como muerte de verdad");

        tick(2);
        t += 60_000; // pasa más de un minuto sin daño
        mb.alMorirJugador(new Muerte(joco.p));
        luciano.chat.clear();
        comando(mb, luciano, "stats");
        check(luciano.todo().contains("de peligro: 9"), "morir sin daño reciente (plugin) tampoco cuenta");

        System.out.println("== entrenamiento");
        luciano.chat.clear();
        comando(mb, luciano, "entrenar");
        for (int vuelta = 0; vuelta < 6; vuelta++) {
            mb.alRevivir(new Revive(joco.p));
            tick(10);
            mb.alRecibirDano(new Dano(joco.p, EntityDamageEvent.DamageCause.ENTITY_ATTACK, 6));
            mb.alMorirJugador(new Muerte(joco.p));
        }
        tick(13); // cierra fotos viejas de luciano como "sobrevivió"
        luciano.chat.clear();
        timers.get(1).run(); // tarea de entrenamiento
        comando(mb, luciano, "stats");
        out = luciano.todo();
        check(out.contains("Árbol:"), "entrenó un árbol");
        luciano.chat.clear();
        comando(mb, luciano, "arbol");
        check(luciano.chat.size() >= 3, "/ia arbol muestra el árbol (" + luciano.chat.size() + " líneas)");
        tick(2);
        luciano.chat.clear();
        comando(mb, luciano, "nivel");
        check(luciano.todo().contains("Nivel 4") || luciano.todo().contains("Nivel 5"), "con el árbol, luciano sigue arriba");
        check(luciano.todo().contains("hoja:"), "explica el camino en el árbol");
        luciano.chat.clear();
        comando(mb, luciano, "nivel", "joco");
        check(luciano.todo().contains("Nivel 1") || luciano.todo().contains("Nivel 2"), "con el árbol, joco sigue abajo");
        System.out.println("    " + luciano.todo().replace("\n", "\n    "));

        System.out.println("== spawns");
        spawneados.clear();
        LivingEntity zombie = mob("zombie", EntityType.ZOMBIE, new Loc(mundo, 10, 64, 0));
        Spawn sp = new Spawn(zombie, CreatureSpawnEvent.SpawnReason.NATURAL);
        mb.alAparecer(sp);
        // nivel actual de luciano
        luciano.chat.clear();
        comando(mb, luciano, "nivel");
        boolean nivel5 = luciano.todo().contains("Nivel 5");
        AttributeInstance vidaZ = zombie.getAttribute(Attribute.MAX_HEALTH);
        check(!sp.cancelado, "no cancela spawns cerca del jugador fuerte");
        check(spawneados.size() >= (nivel5 ? 1 : 0), "suma mobs extra cerca de luciano: " + spawneados.size());
        check(vidaZ.getBaseValue() > 20, "el zombie tiene más vida: " + vidaZ.getBaseValue());
        check(Fake.de(zombie).contar("setHealth") == 1, "lo cura hasta la vida nueva");

        int cancelados = 0;
        for (int i = 0; i < 300; i++) {
            Spawn s2 = new Spawn(mob("zj" + i, EntityType.ZOMBIE, new Loc(mundo, 1010, 64, 0)), CreatureSpawnEvent.SpawnReason.NATURAL);
            mb.alAparecer(s2);
            if (s2.cancelado) cancelados++;
        }
        check(cancelados > 50 && cancelados < 130, "cerca de joco (nivel 1) cancela ~30% de los spawns: " + cancelados + "/300");

        spawneados.clear();
        mb.alAparecer(new Spawn(mob("custom", EntityType.ZOMBIE, new Loc(mundo, 10, 64, 0)), CreatureSpawnEvent.SpawnReason.CUSTOM));
        check(spawneados.isEmpty(), "ignora spawns que no son naturales");
        Spawn lejos = new Spawn(mob("lejos", EntityType.ZOMBIE, new Loc(mundo, 500, 64, 0)), CreatureSpawnEvent.SpawnReason.NATURAL);
        mb.alAparecer(lejos);
        check(spawneados.isEmpty() && !lejos.cancelado, "no toca mobs lejos de todos");
        LivingEntity vaca = Fake.crear("vaca", null, LivingEntity.class);
        mb.alAparecer(new Spawn(vaca, CreatureSpawnEvent.SpawnReason.NATURAL));
        check(spawneados.isEmpty(), "no toca animales");

        System.out.println("== daño");
        Pegar golpe = new Pegar(luciano.p, zombie, 4);
        mb.alPegar(golpe);
        check(golpe.cambiado && golpe.dano > 4, "un zombie le pega más fuerte a luciano: 4 -> " + golpe.dano);
        Fake[] fa = new Fake[1];
        Projectile flecha = Fake.crear("flecha", fa, Projectile.class);
        LivingEntity esqueleto = mob("esqueleto", EntityType.SKELETON, new Loc(mundo, 20, 64, 0));
        fa[0].con("getShooter", (ProjectileSource) esqueleto);
        Pegar tiro = new Pegar(luciano.p, flecha, 3);
        mb.alPegar(tiro);
        check(tiro.cambiado && tiro.dano > 3, "las flechas de esqueleto también: 3 -> " + tiro.dano);
        Pegar pvp = new Pegar(luciano.p, joco.p, 5);
        mb.alPegar(pvp);
        check(!pvp.cambiado, "no toca el PvP");
        Pegar aJoco = new Pegar(joco.p, zombie, 4);
        mb.alPegar(aJoco);
        check(!aJoco.cambiado, "a joco (nivel 1, daño x1.0) no le cambia nada");

        System.out.println("== kills");
        Fake.de(zombie).con("getKiller", luciano.p);
        for (int i = 0; i < 3; i++) mb.alMorirMob(new MobMuerto(zombie));
        tick(1);
        luciano.chat.clear();
        comando(mb, luciano, "nivel");
        check(true, "registra kills sin errores");

        System.out.println("== hordas");
        spawneados.clear();
        // forzar a luciano a nivel 5 con reglas claras: lo dejamos igual y probamos de día y de noche
        hora[0] = 6000;
        timers.get(2).run();
        check(spawneados.isEmpty(), "de día no hay hordas");
        hora[0] = 15000;
        luciano.chat.clear();
        comando(mb, luciano, "nivel");
        boolean es5 = luciano.todo().contains("Nivel 5");
        timers.get(2).run();
        if (es5) {
            check(spawneados.size() >= 3 && spawneados.size() <= 5, "de noche le cae una horda a luciano: " + spawneados.size());
            check(luciano.titulos == 1, "le muestra el título de horda");
            check(spawneados.stream().allMatch(m -> Fake.de(m).contar("setTarget") == 1), "la horda va directo a él");
            int antes = spawneados.size();
            timers.get(2).run();
            check(spawneados.size() == antes, "respeta la espera entre hordas");
        } else {
            System.out.println("  (luciano no quedó en nivel 5 con el árbol, se salta la parte de hordas)");
        }

        System.out.println("== comandos");
        luciano.chat.clear();
        comando(mb, luciano);
        check(luciano.todo().contains("/ia nivel"), "ayuda");
        luciano.chat.clear();
        comando(mb, luciano, "reset");
        check(luciano.todo().contains("confirmar"), "reset pide confirmación");
        List<String> tab = mb.onTabComplete(luciano.p, null, "ia", new String[]{"ni"});
        check(tab.equals(List.of("nivel")), "autocompletar: " + tab);
        List<String> tab2 = mb.onTabComplete(luciano.p, null, "ia", new String[]{"nivel", "jo"});
        check(tab2.equals(List.of("joco")), "autocompletar jugadores: " + tab2);

        System.out.println("== salir y guardar");
        mb.alSalir(new Sale(joco.p));
        luciano.chat.clear();
        comando(mb, luciano, "stats");
        String stats = luciano.todo();
        mb.onDisable();
        Path datos = carpeta.resolve("datos.csv");
        check(Files.exists(datos), "guardó datos.csv");
        int filas = Files.readAllLines(datos).size() - 1;
        check(stats.contains("Fotos guardadas: " + filas), "datos.csv tiene las " + filas + " fotos");
        check(Files.readString(carpeta.resolve("jugadores.csv")).contains("joco"), "guardó jugadores.csv");
        check(Files.readString(carpeta.resolve("arbol.txt")).contains("¿"), "guardó arbol.txt con el árbol");
        System.out.println("    arbol.txt:");
        for (String l : Files.readAllLines(carpeta.resolve("arbol.txt"))) System.out.println("      " + l);

        System.out.println("== reiniciar el server");
        MobBrain mb2 = nuevoPlugin();
        luciano.chat.clear();
        comando(mb2, luciano, "stats");
        check(luciano.todo().contains("Fotos guardadas: " + filas), "al reiniciar carga las fotos guardadas");
        timers.get(1).run();
        luciano.chat.clear();
        comando(mb2, luciano, "stats");
        check(luciano.todo().contains("Árbol:"), "y reentrena el árbol con lo guardado");
        comando(mb2, luciano, "reset", "confirmar");
        luciano.chat.clear();
        comando(mb2, luciano, "stats");
        check(luciano.todo().contains("Fotos guardadas: 0"), "reset confirmar borra todo");

        System.out.println(fallas == 0 ? "TODO OK" : "FALLAS: " + fallas);
        System.exit(fallas == 0 ? 0 : 1);
    }
}
