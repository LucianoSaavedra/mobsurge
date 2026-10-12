import javax.annotation.processing.AbstractProcessor;
import javax.annotation.processing.ProcessingEnvironment;
import javax.annotation.processing.RoundEnvironment;
import javax.annotation.processing.SupportedAnnotationTypes;
import javax.annotation.processing.SupportedOptions;
import javax.lang.model.SourceVersion;
import javax.lang.model.element.AnnotationMirror;
import javax.lang.model.element.AnnotationValue;
import javax.lang.model.element.Element;
import javax.lang.model.element.ElementKind;
import javax.lang.model.element.ExecutableElement;
import javax.lang.model.element.Modifier;
import javax.lang.model.element.TypeElement;
import javax.lang.model.element.TypeParameterElement;
import javax.lang.model.element.VariableElement;
import javax.lang.model.type.ArrayType;
import javax.lang.model.type.DeclaredType;
import javax.lang.model.type.TypeKind;
import javax.lang.model.type.TypeMirror;
import javax.lang.model.type.TypeVariable;
import javax.lang.model.type.WildcardType;
import javax.lang.model.util.Elements;
import javax.lang.model.util.Types;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.stream.Collectors;

/**
 * Compile-time helper. Runs with -proc:only over the real Paper API sources, so
 * only declarations are resolved (method bodies are never attributed).
 *
 *  gen    : writes exact signature stubs for a list of API types.
 *  verify : checks every member reference found in compiled plugin bytecode
 *           against the real API declarations (JVM resolution rules).
 *  dump   : prints members of types with their erased descriptors.
 */
@SupportedAnnotationTypes("*")
@SupportedOptions({"stubgen.mode", "stubgen.types", "stubgen.out", "stubgen.refs"})
public class StubGen extends AbstractProcessor {
    private boolean ran;
    private Elements el;
    private Types ty;
    private final Set<String> allowed = new LinkedHashSet<>();

    @Override
    public SourceVersion getSupportedSourceVersion() {
        return SourceVersion.latestSupported();
    }

    @Override
    public synchronized void init(ProcessingEnvironment env) {
        super.init(env);
        el = env.getElementUtils();
        ty = env.getTypeUtils();
    }

    @Override
    public boolean process(Set<? extends TypeElement> annotations, RoundEnvironment round) {
        if (ran) return false;
        ran = true;
        Map<String, String> o = processingEnv.getOptions();
        try {
            switch (o.getOrDefault("stubgen.mode", "")) {
                case "gen" -> gen(Path.of(o.get("stubgen.types")), Path.of(o.get("stubgen.out")));
                case "verify" -> verify(Path.of(o.get("stubgen.refs")), Path.of(o.get("stubgen.out")));
                case "dump" -> dump(Path.of(o.get("stubgen.types")), Path.of(o.get("stubgen.out")));
                default -> System.err.println("STUBGEN: unknown mode");
            }
        } catch (Throwable t) {
            t.printStackTrace();
        }
        return false;
    }

    private static List<String> readList(Path p) throws IOException {
        return Files.readAllLines(p).stream().map(String::trim)
                .filter(s -> !s.isEmpty() && !s.startsWith("#")).collect(Collectors.toList());
    }

    // ------------------------------------------------------------------ gen

    private void gen(Path typesFile, Path out) throws IOException {
        List<String> names = readList(typesFile);
        List<String> report = new ArrayList<>();
        Map<String, TypeElement> tops = new LinkedHashMap<>();
        for (String n : names) {
            TypeElement te = el.getTypeElement(n);
            if (te == null) {
                report.add("MISSING " + n);
                continue;
            }
            for (Element e = te; e instanceof TypeElement t; e = e.getEnclosingElement()) {
                allowed.add(t.getQualifiedName().toString());
            }
            TypeElement top = te;
            while (top.getEnclosingElement() instanceof TypeElement p) top = p;
            tops.put(top.getQualifiedName().toString(), top);
        }
        for (TypeElement top : tops.values()) {
            String pkg = el.getPackageOf(top).getQualifiedName().toString();
            StringBuilder sb = new StringBuilder();
            sb.append("package ").append(pkg).append(";\n\n");
            emitType(sb, top, "", report);
            Path f = out.resolve(pkg.replace('.', '/')).resolve(top.getSimpleName() + ".java");
            Files.createDirectories(f.getParent());
            Files.writeString(f, sb);
        }
        Path m = out.resolve("stubgen/M.java");
        Files.createDirectories(m.getParent());
        Files.writeString(m, "package stubgen;\npublic final class M { private M() {} }\n");
        Files.write(out.resolve("gen-report.txt"), report);
    }

    private boolean isJdk(TypeElement te) {
        String pkg = el.getPackageOf(te).getQualifiedName().toString();
        return pkg.startsWith("java.") || pkg.startsWith("javax.");
    }

    private boolean isStub(TypeElement te) {
        return !isJdk(te) && allowed.contains(te.getQualifiedName().toString());
    }

    private boolean okElem(TypeElement te) {
        return isJdk(te) || allowed.contains(te.getQualifiedName().toString());
    }

    /** Can this type be written in a stub (ignoring type arguments)? */
    private boolean ok(TypeMirror t) {
        return switch (t.getKind()) {
            case BOOLEAN, BYTE, SHORT, INT, LONG, CHAR, FLOAT, DOUBLE, VOID -> true;
            case ARRAY -> ok(((ArrayType) t).getComponentType());
            case TYPEVAR -> true;
            case WILDCARD -> {
                WildcardType w = (WildcardType) t;
                yield (w.getExtendsBound() == null || ok(w.getExtendsBound()))
                        && (w.getSuperBound() == null || ok(w.getSuperBound()));
            }
            case DECLARED -> okElem((TypeElement) ((DeclaredType) t).asElement());
            default -> false;
        };
    }

    /** Would the erasure of this type be the same in the stub as in the real API? */
    private boolean erasureOk(TypeMirror t) {
        if (t.getKind() == TypeKind.ARRAY) return erasureOk(((ArrayType) t).getComponentType());
        if (t.getKind() == TypeKind.TYPEVAR) {
            TypeParameterElement tp = (TypeParameterElement) ((TypeVariable) t).asElement();
            List<? extends TypeMirror> b = tp.getBounds();
            if (b.isEmpty()) return true;
            TypeMirror first = b.get(0);
            if (first.getKind() == TypeKind.TYPEVAR) return erasureOk(first);
            return ok(first);
        }
        return ok(t);
    }

    private String p(TypeMirror t) {
        switch (t.getKind()) {
            case ARRAY:
                return p(((ArrayType) t).getComponentType()) + "[]";
            case TYPEVAR:
                return ((TypeVariable) t).asElement().getSimpleName().toString();
            case WILDCARD: {
                WildcardType w = (WildcardType) t;
                if (w.getExtendsBound() != null) return ok(w.getExtendsBound()) ? "? extends " + p(w.getExtendsBound()) : "?";
                if (w.getSuperBound() != null) return ok(w.getSuperBound()) ? "? super " + p(w.getSuperBound()) : "?";
                return "?";
            }
            case DECLARED: {
                DeclaredType d = (DeclaredType) t;
                TypeElement te = (TypeElement) d.asElement();
                String base = te.getQualifiedName().toString();
                List<? extends TypeMirror> args = d.getTypeArguments();
                if (args.isEmpty()) return base;
                return base + "<" + args.stream().map(x -> ok(x) ? p(x) : "?").collect(Collectors.joining(", ")) + ">";
            }
            case BOOLEAN: case BYTE: case SHORT: case INT: case LONG: case CHAR: case FLOAT: case DOUBLE: case VOID:
                return t.getKind().name().toLowerCase(java.util.Locale.ROOT);
            default:
                return t.toString();
        }
    }

    private String av(AnnotationValue v) {
        Object o = v.getValue();
        if (o instanceof VariableElement ve) {
            return ((TypeElement) ve.getEnclosingElement()).getQualifiedName() + "." + ve.getSimpleName();
        }
        if (o instanceof TypeMirror tm) return p(ty.erasure(tm)) + ".class";
        if (o instanceof AnnotationMirror m) return am(m);
        if (o instanceof List<?> l) {
            return l.stream().map(x -> av((AnnotationValue) x)).collect(Collectors.joining(", ", "{", "}"));
        }
        return el.getConstantExpression(o);
    }

    private String am(AnnotationMirror m) {
        String n = ((TypeElement) m.getAnnotationType().asElement()).getQualifiedName().toString();
        List<String> parts = new ArrayList<>();
        for (var en : m.getElementValues().entrySet()) {
            parts.add(en.getKey().getSimpleName() + " = " + av(en.getValue()));
        }
        return "@" + n + (parts.isEmpty() ? "" : "(" + String.join(", ", parts) + ")");
    }

    private String superStr(TypeMirror t) {
        DeclaredType d = (DeclaredType) t;
        TypeElement te = (TypeElement) d.asElement();
        boolean all = d.getTypeArguments().stream().allMatch(a -> a.getKind() != TypeKind.WILDCARD && ok(a));
        return all ? p(t) : te.getQualifiedName().toString();
    }

    private String typeParams(List<? extends TypeParameterElement> tps) {
        if (tps.isEmpty()) return "";
        List<String> parts = new ArrayList<>();
        for (TypeParameterElement tp : tps) {
            List<? extends TypeMirror> b = tp.getBounds();
            String s = tp.getSimpleName().toString();
            if (!b.isEmpty() && ok(b.get(0)) && !isObject(b.get(0))) {
                List<String> bs = new ArrayList<>();
                for (TypeMirror x : b) if (ok(x)) bs.add(p(x));
                s += " extends " + String.join(" & ", bs);
            }
            parts.add(s);
        }
        return "<" + String.join(", ", parts) + ">";
    }

    private boolean isObject(TypeMirror t) {
        return t.getKind() == TypeKind.DECLARED
                && ((TypeElement) ((DeclaredType) t).asElement()).getQualifiedName().contentEquals("java.lang.Object");
    }

    private boolean sigOk(ExecutableElement m) {
        for (TypeParameterElement tp : m.getTypeParameters()) {
            for (TypeMirror b : tp.getBounds()) if (!ok(b)) return false;
        }
        if (!ok(m.getReturnType()) || !erasureOk(m.getReturnType())) return false;
        for (VariableElement v : m.getParameters()) {
            if (!ok(v.asType()) || !erasureOk(v.asType())) return false;
        }
        return true;
    }

    private String params(ExecutableElement m) {
        List<String> ps = new ArrayList<>();
        List<? extends VariableElement> vs = m.getParameters();
        for (int i = 0; i < vs.size(); i++) {
            TypeMirror t = vs.get(i).asType();
            String ts;
            if (m.isVarArgs() && i == vs.size() - 1 && t.getKind() == TypeKind.ARRAY) {
                ts = p(((ArrayType) t).getComponentType()) + "...";
            } else {
                ts = p(t);
            }
            ps.add(ts + " arg" + i);
        }
        return String.join(", ", ps);
    }

    private String throwsStr(ExecutableElement m) {
        List<String> ts = new ArrayList<>();
        for (TypeMirror t : m.getThrownTypes()) if (ok(t)) ts.add(p(t));
        return ts.isEmpty() ? "" : " throws " + String.join(", ", ts);
    }

    private String methodTypeParams(ExecutableElement m) {
        String s = typeParams(m.getTypeParameters());
        return s.isEmpty() ? "" : s + " ";
    }

    private String defaultValue(TypeMirror t) {
        return switch (t.getKind()) {
            case BOOLEAN -> "false";
            case CHAR -> "'\\0'";
            case LONG -> "0L";
            case FLOAT -> "0.0f";
            case DOUBLE -> "0.0d";
            case BYTE -> "(byte) 0";
            case SHORT -> "(short) 0";
            case INT -> "0";
            default -> "null";
        };
    }

    private boolean isImplicitEnumMethod(ExecutableElement m) {
        String n = m.getSimpleName().toString();
        if (n.equals("values") && m.getParameters().isEmpty()) return true;
        return n.equals("valueOf") && m.getParameters().size() == 1
                && ty.erasure(m.getParameters().get(0).asType()).toString().equals("java.lang.String");
    }

    private boolean overridesObject(ExecutableElement m) {
        String n = m.getSimpleName().toString();
        int pc = m.getParameters().size();
        if (n.equals("hashCode") && pc == 0) return true;
        if (n.equals("toString") && pc == 0) return true;
        return n.equals("equals") && pc == 1;
    }

    private String sig(ExecutableElement m) {
        return m.getSimpleName() + m.getParameters().stream()
                .map(v -> ty.erasure(v.asType()).toString()).collect(Collectors.joining(",", "(", ")"));
    }

    /** Methods that the stub of {@code te} will contain. */
    private List<ExecutableElement> emitted(TypeElement te) {
        List<ExecutableElement> res = new ArrayList<>();
        boolean isIface = te.getKind().isInterface();
        for (Element e : te.getEnclosedElements()) {
            if (e.getKind() != ElementKind.METHOD) continue;
            ExecutableElement m = (ExecutableElement) e;
            Set<Modifier> mm = m.getModifiers();
            boolean pub = mm.contains(Modifier.PUBLIC) || (isIface && !mm.contains(Modifier.PRIVATE));
            if (!pub && !mm.contains(Modifier.PROTECTED)) continue;
            if (te.getKind() == ElementKind.ENUM && isImplicitEnumMethod(m)) continue;
            if (isIface && te.getKind() != ElementKind.ANNOTATION_TYPE && overridesObject(m)) continue;
            if (!sigOk(m)) continue;
            res.add(m);
        }
        return res;
    }

    private List<TypeElement> stubSupers(TypeElement te) {
        List<TypeElement> r = new ArrayList<>();
        if (te.getKind() == ElementKind.CLASS) {
            TypeMirror sc = te.getSuperclass();
            if (sc.getKind() == TypeKind.DECLARED) {
                TypeElement se = (TypeElement) ((DeclaredType) sc).asElement();
                if (isStub(se)) r.add(se);
            }
        }
        for (TypeMirror i : te.getInterfaces()) {
            if (i.getKind() == TypeKind.DECLARED) {
                TypeElement ie = (TypeElement) ((DeclaredType) i).asElement();
                if (isStub(ie)) r.add(ie);
            }
        }
        return r;
    }

    private boolean stubSubtype(TypeElement a, TypeElement b) {
        Deque<TypeElement> q = new ArrayDeque<>(List.of(a));
        Set<TypeElement> seen = new HashSet<>();
        while (!q.isEmpty()) {
            TypeElement x = q.poll();
            if (x.equals(b)) return true;
            if (seen.add(x)) q.addAll(stubSupers(x));
        }
        return false;
    }

    /** Stub-level superinterfaces whose defaults would clash in {@code te}; returns methods to re-declare. */
    private List<ExecutableElement> clashes(TypeElement te, Set<String> declared) {
        Map<String, List<TypeElement>> providers = new LinkedHashMap<>();
        Map<String, ExecutableElement> sample = new LinkedHashMap<>();
        Set<TypeElement> seen = new HashSet<>();
        Deque<TypeElement> q = new ArrayDeque<>();
        for (TypeElement s : stubSupers(te)) if (s.getKind().isInterface()) q.add(s);
        // methods found on the stub superclass chain win over interface defaults
        Set<String> classProvided = new HashSet<>();
        for (TypeElement c = te; c != null; ) {
            TypeElement next = null;
            for (TypeElement s : stubSupers(c)) {
                if (s.getKind() == ElementKind.CLASS) {
                    next = s;
                    for (ExecutableElement m : emitted(s)) classProvided.add(sig(m));
                    for (TypeElement si : stubSupers(s)) if (si.getKind().isInterface()) q.add(si);
                }
            }
            c = next;
        }
        while (!q.isEmpty()) {
            TypeElement x = q.poll();
            if (!seen.add(x)) continue;
            for (ExecutableElement m : emitted(x)) {
                if (m.getModifiers().contains(Modifier.STATIC)) continue;
                String s = sig(m);
                providers.computeIfAbsent(s, k -> new ArrayList<>()).add(x);
                sample.putIfAbsent(s, m);
            }
            for (TypeElement s : stubSupers(x)) if (s.getKind().isInterface()) q.add(s);
        }
        List<ExecutableElement> res = new ArrayList<>();
        for (var en : providers.entrySet()) {
            if (declared.contains(en.getKey()) || classProvided.contains(en.getKey())) continue;
            List<TypeElement> ps = en.getValue();
            List<TypeElement> maximal = new ArrayList<>();
            for (TypeElement a : ps) {
                boolean dominated = false;
                for (TypeElement b : ps) if (a != b && stubSubtype(b, a)) dominated = true;
                if (!dominated) maximal.add(a);
            }
            if (maximal.size() > 1) res.add(sample.get(en.getKey()));
        }
        return res;
    }

    private boolean hasNoArgCtor(TypeElement te) {
        for (Element e : te.getEnclosedElements()) {
            if (e.getKind() == ElementKind.CONSTRUCTOR && ((ExecutableElement) e).getParameters().isEmpty()) {
                Set<Modifier> m = e.getModifiers();
                if (m.contains(Modifier.PUBLIC) || m.contains(Modifier.PROTECTED)) return true;
            }
        }
        return false;
    }

    private void emitType(StringBuilder sb, TypeElement te, String ind, List<String> report) {
        ElementKind k = te.getKind();
        Set<Modifier> mods = te.getModifiers();
        boolean nested = te.getEnclosingElement() instanceof TypeElement;
        boolean encIface = nested && te.getEnclosingElement().getKind().isInterface();
        boolean isIface = k.isInterface();
        StringBuilder h = new StringBuilder();
        if (k == ElementKind.ANNOTATION_TYPE) {
            for (AnnotationMirror am : te.getAnnotationMirrors()) {
                String an = ((TypeElement) am.getAnnotationType().asElement()).getQualifiedName().toString();
                if (an.equals("java.lang.annotation.Retention") || an.equals("java.lang.annotation.Target")) {
                    h.append(ind).append(am(am)).append("\n");
                }
            }
        }
        h.append(ind);
        if (mods.contains(Modifier.PUBLIC)) h.append("public ");
        else if (mods.contains(Modifier.PROTECTED)) h.append("protected ");
        if (nested && !encIface && mods.contains(Modifier.STATIC) && (k == ElementKind.CLASS || k == ElementKind.RECORD)) {
            h.append("static ");
        }
        if (k == ElementKind.CLASS && mods.contains(Modifier.ABSTRACT)) h.append("abstract ");
        if ((k == ElementKind.CLASS && mods.contains(Modifier.FINAL)) || k == ElementKind.RECORD) h.append("final ");
        h.append(switch (k) {
            case INTERFACE -> "interface ";
            case ANNOTATION_TYPE -> "@interface ";
            case ENUM -> "enum ";
            default -> "class ";
        });
        h.append(te.getSimpleName()).append(typeParams(te.getTypeParameters()));
        TypeElement superEmitted = null;
        if (k == ElementKind.CLASS) {
            TypeMirror sc = te.getSuperclass();
            if (sc.getKind() == TypeKind.DECLARED) {
                TypeElement se = (TypeElement) ((DeclaredType) sc).asElement();
                boolean obj = se.getQualifiedName().contentEquals("java.lang.Object");
                if (!obj && okElem(se) && (isStub(se) || hasNoArgCtor(se))) {
                    h.append(" extends ").append(superStr(sc));
                    superEmitted = se;
                } else if (!obj) {
                    report.add("DROP superclass " + te.getQualifiedName() + " -> " + se.getQualifiedName());
                }
            }
        }
        List<String> ifs = new ArrayList<>();
        for (TypeMirror i : te.getInterfaces()) {
            if (i.getKind() != TypeKind.DECLARED) continue;
            TypeElement ie = (TypeElement) ((DeclaredType) i).asElement();
            if (okElem(ie)) ifs.add(superStr(i));
            else report.add("DROP interface " + te.getQualifiedName() + " -> " + ie.getQualifiedName());
        }
        if (k != ElementKind.ANNOTATION_TYPE && !ifs.isEmpty()) {
            h.append(isIface ? " extends " : " implements ").append(String.join(", ", ifs));
        }
        sb.append(h).append(" {\n");
        String in = ind + "    ";

        if (k == ElementKind.ENUM) {
            List<String> cs = te.getEnclosedElements().stream().filter(e -> e.getKind() == ElementKind.ENUM_CONSTANT)
                    .map(e -> e.getSimpleName().toString()).collect(Collectors.toList());
            sb.append(in).append(String.join(", ", cs)).append(";\n");
        }

        for (Element e : te.getEnclosedElements()) {
            if (e.getKind() != ElementKind.FIELD) continue;
            VariableElement v = (VariableElement) e;
            Set<Modifier> fm = v.getModifiers();
            boolean pub = fm.contains(Modifier.PUBLIC) || isIface;
            if (!pub && !fm.contains(Modifier.PROTECTED)) continue;
            if (!ok(v.asType()) || !erasureOk(v.asType())) {
                report.add("SKIP field " + te.getQualifiedName() + "." + v.getSimpleName() + " : " + v.asType());
                continue;
            }
            StringBuilder f = new StringBuilder(in);
            if (!isIface) {
                f.append(pub ? "public " : "protected ");
                if (fm.contains(Modifier.STATIC)) f.append("static ");
                if (fm.contains(Modifier.FINAL)) f.append("final ");
            }
            f.append(p(v.asType())).append(' ').append(v.getSimpleName());
            Object cv = v.getConstantValue();
            if (cv != null) f.append(" = ").append(el.getConstantExpression(cv));
            else if (isIface || fm.contains(Modifier.FINAL)) f.append(" = ").append(defaultValue(v.asType()));
            sb.append(f).append(";\n");
        }

        if (k == ElementKind.CLASS || k == ElementKind.RECORD) {
            String sname = te.getSimpleName().toString();
            String superCall = (superEmitted != null && isStub(superEmitted)) ? "super((stubgen.M) null);" : "super();";
            sb.append(in).append("protected ").append(sname).append("(stubgen.M stubgen$m) { ").append(superCall).append(" }\n");
            for (Element e : te.getEnclosedElements()) {
                if (e.getKind() != ElementKind.CONSTRUCTOR) continue;
                ExecutableElement c = (ExecutableElement) e;
                Set<Modifier> cm = c.getModifiers();
                if (!cm.contains(Modifier.PUBLIC) && !cm.contains(Modifier.PROTECTED)) continue;
                if (!sigOk(c)) {
                    report.add("SKIP ctor " + te.getQualifiedName() + " " + c);
                    continue;
                }
                sb.append(in).append(cm.contains(Modifier.PUBLIC) ? "public " : "protected ")
                        .append(methodTypeParams(c)).append(sname).append('(').append(params(c)).append(')')
                        .append(throwsStr(c)).append(" { this((stubgen.M) null); }\n");
            }
        }

        Set<String> declared = new HashSet<>();
        List<ExecutableElement> ms = emitted(te);
        for (Element e : te.getEnclosedElements()) {
            if (e.getKind() == ElementKind.METHOD && !ms.contains(e)) {
                Set<Modifier> mm = e.getModifiers();
                boolean vis = mm.contains(Modifier.PUBLIC) || mm.contains(Modifier.PROTECTED) || (isIface && !mm.contains(Modifier.PRIVATE));
                if (vis) report.add("SKIP method " + te.getQualifiedName() + "." + e);
            }
        }
        for (ExecutableElement m : ms) {
            declared.add(sig(m));
            sb.append(methodStr(te, m, in));
        }
        if (k != ElementKind.ANNOTATION_TYPE) {
            for (ExecutableElement m : clashes(te, declared)) {
                report.add("CLASH-FIX " + te.getQualifiedName() + "." + m);
                sb.append(methodStr(te, m, in));
            }
        }

        for (Element e : te.getEnclosedElements()) {
            if (e instanceof TypeElement nt && allowed.contains(nt.getQualifiedName().toString())) {
                emitType(sb, nt, in, report);
            }
        }
        sb.append(ind).append("}\n");
    }

    private String methodStr(TypeElement owner, ExecutableElement m, String in) {
        boolean isIface = owner.getKind().isInterface();
        Set<Modifier> mm = m.getModifiers();
        String name = m.getSimpleName().toString();
        if (owner.getKind() == ElementKind.ANNOTATION_TYPE) {
            StringBuilder a = new StringBuilder(in).append(p(m.getReturnType())).append(' ').append(name).append("()");
            AnnotationValue dv = m.getDefaultValue();
            if (dv != null) a.append(" default ").append(av(dv));
            return a.append(";\n").toString();
        }
        boolean st = mm.contains(Modifier.STATIC) && m.getEnclosingElement().equals(owner);
        boolean pub = mm.contains(Modifier.PUBLIC) || m.getEnclosingElement().getKind().isInterface();
        StringBuilder s = new StringBuilder(in);
        if (isIface) {
            s.append(st ? "static " : "default ");
        } else {
            s.append(pub ? "public " : "protected ");
            if (st) s.append("static ");
            if (mm.contains(Modifier.FINAL) && m.getEnclosingElement().equals(owner)) s.append("final ");
        }
        s.append(methodTypeParams(m)).append(p(m.getReturnType())).append(' ').append(name)
                .append('(').append(params(m)).append(')').append(throwsStr(m))
                .append(" { throw new UnsupportedOperationException(); }\n");
        return s.toString();
    }

    // --------------------------------------------------------------- verify

    private String desc(TypeMirror t) {
        TypeMirror e = ty.erasure(t);
        return switch (e.getKind()) {
            case BOOLEAN -> "Z";
            case BYTE -> "B";
            case SHORT -> "S";
            case INT -> "I";
            case LONG -> "J";
            case CHAR -> "C";
            case FLOAT -> "F";
            case DOUBLE -> "D";
            case VOID -> "V";
            case ARRAY -> "[" + desc(((ArrayType) e).getComponentType());
            case DECLARED -> "L" + el.getBinaryName((TypeElement) ((DeclaredType) e).asElement()).toString().replace('.', '/') + ";";
            default -> "?" + e.getKind() + "(" + e + ")";
        };
    }

    private String mdesc(ExecutableElement m) {
        StringBuilder sb = new StringBuilder("(");
        for (VariableElement v : m.getParameters()) sb.append(desc(v.asType()));
        return sb.append(')').append(desc(m.getReturnType())).toString();
    }

    private TypeElement superclassOf(TypeElement te) {
        TypeMirror sc = te.getSuperclass();
        return sc.getKind() == TypeKind.DECLARED ? (TypeElement) ((DeclaredType) sc).asElement() : null;
    }

    private List<TypeElement> interfacesOf(TypeElement te) {
        List<TypeElement> r = new ArrayList<>();
        for (TypeMirror i : te.getInterfaces()) {
            if (i.getKind() == TypeKind.DECLARED) r.add((TypeElement) ((DeclaredType) i).asElement());
        }
        return r;
    }

    private ExecutableElement declaredMethod(TypeElement te, String name, String d) {
        for (Element e : te.getEnclosedElements()) {
            if ((e.getKind() == ElementKind.METHOD || e.getKind() == ElementKind.CONSTRUCTOR)
                    && e.getSimpleName().contentEquals(name) && mdesc((ExecutableElement) e).equals(d)) {
                return (ExecutableElement) e;
            }
        }
        return null;
    }

    private ExecutableElement findInSuperIfaces(TypeElement te, String name, String d, Set<TypeElement> seen) {
        for (TypeElement i : interfacesOf(te)) {
            if (!seen.add(i)) continue;
            ExecutableElement m = declaredMethod(i, name, d);
            if (m != null && !m.getModifiers().contains(Modifier.STATIC) && !m.getModifiers().contains(Modifier.PRIVATE)) return m;
            ExecutableElement r = findInSuperIfaces(i, name, d, seen);
            if (r != null) return r;
        }
        return null;
    }

    private VariableElement findField(TypeElement te, String name, String d, Set<TypeElement> seen) {
        if (te == null || !seen.add(te)) return null;
        for (Element e : te.getEnclosedElements()) {
            if ((e.getKind() == ElementKind.FIELD || e.getKind() == ElementKind.ENUM_CONSTANT)
                    && e.getSimpleName().contentEquals(name) && desc(e.asType()).equals(d)) {
                return (VariableElement) e;
            }
        }
        for (TypeElement i : interfacesOf(te)) {
            VariableElement v = findField(i, name, d, seen);
            if (v != null) return v;
        }
        return findField(superclassOf(te), name, d, seen);
    }

    private void verify(Path refsFile, Path out) throws IOException {
        List<String> rep = new ArrayList<>();
        int fails = 0;
        for (String line : readList(refsFile)) {
            String[] f = line.split(" ");
            String op = f[0], kind = f[1], owner = f[2];
            String name = f.length > 3 ? f[3] : "";
            String d = f.length > 4 ? f[4] : "";
            TypeElement te = el.getTypeElement(owner.replace('/', '.').replace('$', '.'));
            if (te == null) {
                rep.add("FAIL class-missing " + line);
                fails++;
                continue;
            }
            if (kind.equals("Class")) {
                boolean bad = (op.equals("new") && (te.getKind().isInterface() || te.getModifiers().contains(Modifier.ABSTRACT)))
                        || (op.equals("super") && te.getModifiers().contains(Modifier.FINAL));
                rep.add((bad ? "FAIL abstract-new " : "OK   ") + line);
                if (bad) fails++;
                continue;
            }
            if (kind.equals("Field")) {
                VariableElement v = findField(te, name, d, new HashSet<>());
                boolean wantStatic = op.equals("getstatic") || op.equals("putstatic");
                if (v == null) {
                    rep.add("FAIL field-missing " + line);
                    fails++;
                } else if (v.getModifiers().contains(Modifier.STATIC) != wantStatic && !te.getKind().isInterface()) {
                    rep.add("FAIL field-static " + line);
                    fails++;
                } else {
                    rep.add("OK   " + line + "   [" + v.getEnclosingElement() + "]" + dep(v));
                }
                continue;
            }
            boolean ifaceRef = kind.equals("InterfaceMethod");
            if (ifaceRef != te.getKind().isInterface()) {
                rep.add("FAIL kind (ref " + kind + ", real " + te.getKind() + ") " + line);
                fails++;
                continue;
            }
            ExecutableElement found = null;
            boolean enumImplicit = false;
            if (name.equals("<init>")) {
                found = declaredMethod(te, name.equals("<init>") ? "<init>" : name, d);
            } else if (!ifaceRef) {
                for (TypeElement c = te; c != null && found == null; c = superclassOf(c)) {
                    found = declaredMethod(c, name, d);
                    if (found == null && c.getKind() == ElementKind.ENUM && isEnumImplicit(c, name, d)) {
                        enumImplicit = true;
                        break;
                    }
                }
                if (found == null && !enumImplicit) {
                    Set<TypeElement> seen = new HashSet<>();
                    for (TypeElement c = te; c != null && found == null; c = superclassOf(c)) {
                        found = findInSuperIfaces(c, name, d, seen);
                    }
                }
            } else {
                found = declaredMethod(te, name, d);
                if (found == null) found = findInSuperIfaces(te, name, d, new HashSet<>());
                if (found == null) found = declaredMethod(el.getTypeElement("java.lang.Object"), name, d);
            }
            if (enumImplicit) {
                rep.add("OK   " + line + "   [implicit enum method]");
                continue;
            }
            if (found == null) {
                rep.add("FAIL member-missing " + line + candidates(te, name));
                fails++;
                continue;
            }
            boolean wantStatic = op.equals("invokestatic");
            if (found.getModifiers().contains(Modifier.STATIC) != wantStatic && !name.equals("<init>")) {
                rep.add("FAIL static-mismatch " + line);
                fails++;
                continue;
            }
            rep.add("OK   " + line + "   [" + found.getEnclosingElement() + "]" + dep(found));
        }
        rep.add(0, "FAILS=" + fails);
        Files.write(out, rep);
    }

    private String dep(Element e) {
        for (AnnotationMirror m : e.getAnnotationMirrors()) {
            String n = ((TypeElement) m.getAnnotationType().asElement()).getQualifiedName().toString();
            if (n.equals("java.lang.Deprecated")) {
                boolean fr = m.getElementValues().entrySet().stream().anyMatch(en ->
                        en.getKey().getSimpleName().contentEquals("forRemoval") && Boolean.TRUE.equals(en.getValue().getValue()));
                return fr ? "   !!DEPRECATED-FOR-REMOVAL" : "   !DEPRECATED";
            }
            if (n.endsWith("ApiStatus.Experimental") || n.endsWith("ApiStatus.Internal")) return "   !" + n;
        }
        return "";
    }

    private boolean isEnumImplicit(TypeElement te, String name, String d) {
        String bn = "L" + el.getBinaryName(te).toString().replace('.', '/') + ";";
        return (name.equals("values") && d.equals("()[" + bn)) || (name.equals("valueOf") && d.equals("(Ljava/lang/String;)" + bn));
    }

    private String candidates(TypeElement te, String name) {
        List<String> c = new ArrayList<>();
        Deque<TypeElement> q = new ArrayDeque<>(List.of(te));
        Set<TypeElement> seen = new HashSet<>();
        while (!q.isEmpty()) {
            TypeElement x = q.poll();
            if (x == null || !seen.add(x)) continue;
            for (Element e : x.getEnclosedElements()) {
                if (e.getSimpleName().contentEquals(name) && e instanceof ExecutableElement ex) {
                    c.add(x.getSimpleName() + "." + name + mdesc(ex));
                }
            }
            q.add(superclassOf(x));
            q.addAll(interfacesOf(x));
        }
        return "   candidates=" + c;
    }

    // ----------------------------------------------------------------- dump

    private void dump(Path typesFile, Path out) throws IOException {
        List<String> rep = new ArrayList<>();
        for (String n : readList(typesFile)) {
            TypeElement te = el.getTypeElement(n);
            if (te == null) {
                rep.add("MISSING " + n);
                continue;
            }
            rep.add("== " + te.getKind() + " " + n + " super=" + te.getSuperclass() + " ifaces=" + te.getInterfaces());
            for (Element e : te.getEnclosedElements()) {
                if (e instanceof ExecutableElement ex) {
                    rep.add("   " + ex.getModifiers() + " " + ex + " " + mdesc(ex) + " -> " + ex.getReturnType());
                } else if (e.getKind() == ElementKind.FIELD) {
                    rep.add("   field " + e.getModifiers() + " " + e.getSimpleName() + " " + desc(e.asType()));
                }
            }
        }
        Files.write(out, rep);
    }
}
