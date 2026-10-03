package com.example.mobsurge;

import org.bukkit.Location;
import org.bukkit.entity.Entity;
import org.bukkit.entity.LivingEntity;
import org.bukkit.entity.Monster;
import org.bukkit.entity.Player;
import org.bukkit.event.EventHandler;
import org.bukkit.event.Listener;
import org.bukkit.event.entity.CreatureSpawnEvent;
import org.bukkit.event.entity.EntityDamageByEntityEvent;
import org.bukkit.plugin.java.JavaPlugin;

import java.util.Random;

public class MobSurge extends JavaPlugin implements Listener {

    // Probabilidad extra de que aparezca un mob adicional junto a cada spawn natural.
    // 0.33 = 33% más mobs hostiles que en el juego normal.
    private static final double EXTRA_SPAWN_CHANCE = 0.33;

    // Multiplicador de daño que hacen los mobs hostiles a los jugadores.
    // 1.5 = 50% más de daño que lo normal.
    private static final double DAMAGE_MULTIPLIER = 1.5;

    private final Random random = new Random();
    private boolean spawningExtra = false; // evita que el spawn extra dispare otro spawn extra (bucle infinito)

    @Override
    public void onEnable() {
        getServer().getPluginManager().registerEvents(this, this);
        getLogger().info("MobSurge activado. +33% de mobs hostiles y +50% de daño de mobs.");
    }

    @EventHandler
    public void onCreatureSpawn(CreatureSpawnEvent event) {
        if (spawningExtra) return;
        if (event.isCancelled()) return;

        // Solo aplica a spawns naturales (no a los que genera este mismo plugin,
        // ni a los invocados por comandos, spawners, etc.)
        if (event.getSpawnReason() != CreatureSpawnEvent.SpawnReason.NATURAL) return;

        Entity entity = event.getEntity();
        if (!(entity instanceof Monster)) return; // solo mobs hostiles (zombies, esqueletos, creepers, etc.)

        if (random.nextDouble() < EXTRA_SPAWN_CHANCE) {
            Location loc = entity.getLocation();
            spawningExtra = true;
            try {
                loc.getWorld().spawnEntity(loc, entity.getType());
            } finally {
                spawningExtra = false;
            }
        }
    }

    @EventHandler
    public void onEntityDamageByEntity(EntityDamageByEntityEvent event) {
        if (!(event.getEntity() instanceof Player)) return;
        if (!(event.getDamager() instanceof Monster)) return;

        // Multiplica el daño que el mob hostil le hace al jugador
        event.setDamage(event.getDamage() * DAMAGE_MULTIPLIER);
    }
}
