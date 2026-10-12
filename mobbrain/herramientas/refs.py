#!/usr/bin/env python3
"""Lista cada referencia a la API (Paper/Bukkit/Adventure) que hay en el bytecode
compilado del plugin, para verificarlas contra las fuentes reales."""
import re
import subprocess
import sys
from pathlib import Path

API = ("org/bukkit/", "io/papermc/", "com/destroystokyo/", "net/kyori/")
classes_dir = Path(sys.argv[1])
out = Path(sys.argv[2])

ins = re.compile(r"\b(invokevirtual|invokeinterface|invokestatic|invokespecial|getstatic|putstatic|getfield|putfield|new|instanceof|checkcast|anewarray|ldc|ldc_w)\b.*//\s*(InterfaceMethod|Method|Field|class)\s+(\S+)")
header = re.compile(r"^(?:public |final |abstract |private |protected |static )*(?:class|interface) (\S+)(?: extends (\S+))?(?: implements (.+?))?\s*\{?$")

# miembros declarados en las clases del plugin (para detectar llamadas heredadas de JavaPlugin)
declared = set()
decl_re = re.compile(r"^\s*(?:[\w$<>\[\],. ]+\s)?([\w$<>]+)\(.*\)")
for cf in sorted(classes_dir.rglob("*.class")):
    txt = subprocess.run(["javap", "-p", "-s", str(cf)], capture_output=True, text=True).stdout
    cls = None
    last = None
    for line in txt.splitlines():
        h = re.match(r"^(?:.* )?(?:class|interface) (\S+)", line)
        if h and line.rstrip().endswith("{"):
            cls = h.group(1).replace(".", "/")
            continue
        m = re.match(r"^\s+(?:.*\s)?([\w$]+)\(", line)
        if m:
            last = m.group(1)
            continue
        d = re.match(r"^\s+descriptor: (\S+)", line)
        if d and last and cls:
            declared.add((cls, last, d.group(1)))
            last = None

refs = set()
for cf in sorted(classes_dir.rglob("*.class")):
    txt = subprocess.run(["javap", "-c", "-p", str(cf)], capture_output=True, text=True).stdout
    cur = None
    for line in txt.splitlines():
        h = header.match(line.strip())
        if h:
            cur = re.sub(r"<.*", "", h.group(1)).replace(".", "/")
            for t in [h.group(2)] + ((h.group(3) or "").split(",")):
                if t:
                    t = re.sub(r"<.*", "", t.strip()).replace(".", "/")
                    if t.startswith(API):
                        refs.add(f"super Class {t}")
            continue
        m = ins.search(line)
        if not m:
            continue
        op, kind, target = m.groups()
        if kind == "class":
            t = target.strip('"')
            if t.startswith("["):
                continue
            if t.startswith(API):
                refs.add(f"{op} Class {t}")
            continue
        owner_member, _, desc = target.partition(":")
        owner, _, name = owner_member.rpartition(".")
        name = name.strip('"')
        if owner == "":
            owner = cur  # javap omite la clase cuando es la actual
        if owner.startswith(API):
            refs.add(f"{op} {kind} {owner} {name} {desc}")
        elif owner == "com/luciano/mobbrain/MobBrain" and kind == "Method" and (owner, name, desc) not in declared:
            # heredado de JavaPlugin (getConfig, getServer, getLogger...)
            refs.add(f"{op} Method org/bukkit/plugin/java/JavaPlugin {name} {desc}")

# Métodos del plugin que tienen que sobrescribir a los de JavaPlugin
refs.add("invokevirtual Method org/bukkit/plugin/java/JavaPlugin onEnable ()V")
refs.add("invokevirtual Method org/bukkit/plugin/java/JavaPlugin onDisable ()V")
refs.add("invokevirtual Method org/bukkit/plugin/java/JavaPlugin onCommand (Lorg/bukkit/command/CommandSender;Lorg/bukkit/command/Command;Ljava/lang/String;[Ljava/lang/String;)Z")
refs.add("invokevirtual Method org/bukkit/plugin/java/JavaPlugin onTabComplete (Lorg/bukkit/command/CommandSender;Lorg/bukkit/command/Command;Ljava/lang/String;[Ljava/lang/String;)Ljava/util/List;")
out.write_text("\n".join(sorted(refs)) + "\n")
print(f"{len(refs)} referencias")
