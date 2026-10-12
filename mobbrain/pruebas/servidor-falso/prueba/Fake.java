package prueba;

import java.lang.reflect.InvocationHandler;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.function.Function;

/** Objeto falso genérico: responde lo que le configures y anota cada llamada. */
public final class Fake implements InvocationHandler {
    public final String nombre;
    public final Map<String, Function<Object[], Object>> respuestas = new HashMap<>();
    public final List<String> llamadas = new ArrayList<>();
    public Object proxy;

    private Fake(String nombre) {
        this.nombre = nombre;
    }

    @SuppressWarnings("unchecked")
    public static <T> T crear(String nombre, Fake[] salida, Class<?>... interfaces) {
        Fake f = new Fake(nombre);
        f.proxy = Proxy.newProxyInstance(Fake.class.getClassLoader(), interfaces, f);
        if (salida != null) salida[0] = f;
        return (T) f.proxy;
    }

    public static Fake de(Object proxy) {
        return (Fake) Proxy.getInvocationHandler(proxy);
    }

    public Fake con(String metodo, Object valor) {
        respuestas.put(metodo, a -> valor);
        return this;
    }

    public Fake conF(String metodo, Function<Object[], Object> f) {
        respuestas.put(metodo, f);
        return this;
    }

    public long contar(String metodo) {
        return llamadas.stream().filter(l -> l.startsWith(metodo + "(")).count();
    }

    @Override
    public Object invoke(Object p, Method m, Object[] args) {
        String n = m.getName();
        if (m.getDeclaringClass() == Object.class) {
            return switch (n) {
                case "equals" -> p == args[0];
                case "hashCode" -> System.identityHashCode(p);
                default -> nombre;
            };
        }
        if (n.equals("toString") && m.getParameterCount() == 0) return nombre;
        Object[] a = args == null ? new Object[0] : args;
        StringBuilder sb = new StringBuilder(n).append('(');
        for (int i = 0; i < a.length; i++) sb.append(i == 0 ? "" : ", ").append(seguro(a[i]));
        llamadas.add(sb.append(')').toString());
        Function<Object[], Object> r = respuestas.get(n);
        if (r != null) return r.apply(a);
        return cero(m.getReturnType());
    }

    static String seguro(Object o) {
        try {
            return String.valueOf(o);
        } catch (RuntimeException e) {
            return o.getClass().getSimpleName();
        }
    }

    static Object cero(Class<?> t) {
        if (!t.isPrimitive() || t == void.class) return null;
        if (t == boolean.class) return false;
        if (t == byte.class) return (byte) 0;
        if (t == short.class) return (short) 0;
        if (t == char.class) return (char) 0;
        if (t == int.class) return 0;
        if (t == long.class) return 0L;
        if (t == float.class) return 0f;
        return 0d;
    }
}
