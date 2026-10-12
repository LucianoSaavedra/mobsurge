import re, shutil, sys
from pathlib import Path
src = Path(sys.argv[1]); dst = Path(sys.argv[2])
shutil.rmtree(dst, ignore_errors=True); shutil.copytree(src, dst)
THROW = '{ throw new UnsupportedOperationException(); }'
def patch(rel, pairs):
    f = dst / rel; s = f.read_text()
    for sig, body in pairs:
        line = next((l for l in s.splitlines() if sig in l and THROW in l), None)
        if line is None: sys.exit(f'no encontré {sig} en {rel}')
        s = s.replace(line, line.replace(THROW, body), 1)
    f.write_text(s)
patch('org/bukkit/plugin/java/JavaPlugin.java', [
    (' getDataFolder()', '{ return prueba.Hooks.carpeta; }'),
    (' getServer()', '{ return prueba.Hooks.server; }'),
    (' isEnabled()', '{ return true; }'),
    (' getConfig()', '{ return prueba.Hooks.config; }'),
    (' saveDefaultConfig()', '{ }'),
    (' getCommand(', '{ return null; }'),
    (' getLogger()', '{ return prueba.Hooks.LOG; }'),
])
patch('org/bukkit/Bukkit.java', [
    (' getOnlinePlayers()', '{ return prueba.Hooks.jugadores; }'),
    (' getPlayerExact(', '{ return prueba.Hooks.buscar(arg0); }'),
])
patch('net/kyori/adventure/text/serializer/legacy/LegacyComponentSerializer.java', [(' legacyAmpersand()', '{ return prueba.Hooks.legacy(); }')])
patch('net/kyori/adventure/title/Title.java', [
    ('net.kyori.adventure.title.Title.Times arg2)', '{ return null; }'),
    (' times(java.time.Duration', '{ return null; }'),
])
patch('org/bukkit/Material.java', [(' isSolid()', '{ String n = name(); return !n.endsWith("AIR") && !n.contains("WATER") && !n.contains("LAVA"); }')])
f = dst / 'org/bukkit/attribute/Attribute.java'; s = f.read_text()
for name, key in [('ARMOR', 'armor'), ('MAX_HEALTH', 'max_health'), ('MOVEMENT_SPEED', 'movement_speed')]:
    s, n = re.subn(rf'(org\.bukkit\.attribute\.Attribute {name}) = null;', rf'\1 = prueba.Hooks.attr("{key}");', s)
    if n != 1: sys.exit('no pude parchear ' + name)
f.write_text(s)
def unfinal(rel, sig):
    f = dst / rel; s = f.read_text()
    line = next(l for l in s.splitlines() if sig in l)
    s = s.replace(line, line.replace('final ', '', 1)); f.write_text(s)
unfinal('org/bukkit/event/entity/EntityDamageEvent.java', ' getFinalDamage()')
unfinal('org/bukkit/event/player/PlayerEvent.java', ' getPlayer()')
print('stubs de prueba listos')
