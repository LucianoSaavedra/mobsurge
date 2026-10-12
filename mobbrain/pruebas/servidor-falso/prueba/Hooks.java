package prueba;

import net.kyori.adventure.text.TextComponent;
import net.kyori.adventure.text.serializer.legacy.LegacyComponentSerializer;
import org.bukkit.entity.Player;

import java.io.File;
import java.util.ArrayList;
import java.util.List;
import java.util.logging.Logger;

/** Lo que usan los stubs parcheados para las pruebas. */
public final class Hooks {
    public static org.bukkit.Server server;
    public static File carpeta;
    public static org.bukkit.configuration.file.FileConfiguration config;
    public static final List<Player> jugadores = new ArrayList<>();
    public static final Logger LOG = Logger.getLogger("MobBrain");

    public static org.bukkit.attribute.Attribute attr(String nombre) {
        return Fake.crear(nombre, null, org.bukkit.attribute.Attribute.class);
    }

    public static Player buscar(String nombre) {
        for (Player p : jugadores) if (p.getName().equals(nombre)) return p;
        return null;
    }

    /** Serializador falso: el "componente" es un proxy cuyo toString es el texto. */
    public static LegacyComponentSerializer legacy() {
        Fake[] f = new Fake[1];
        LegacyComponentSerializer s = Fake.crear("legacy", f, LegacyComponentSerializer.class);
        f[0].conF("deserialize", a -> {
            TextComponent c = Fake.crear(String.valueOf(a[0]), null, TextComponent.class);
            return c;
        });
        return s;
    }
}
