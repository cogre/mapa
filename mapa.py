# -*- coding: utf-8 -*-
"""
mapa - reads a folder of Python code and draws who depends on whom.
Standard library only. Nothing leaves your machine. The code it reads is parsed, never executed.

  python mapa.py FOLDER                  build the map (writes to mapa-out/<folder name>/)
  python mapa.py FOLDER explain NAME     what it is, who uses it, what it uses, which tables it touches
  python mapa.py FOLDER path A B         the shortest path between two pieces
  python mapa.py FOLDER table NAME       who creates, writes and reads a database table
  python mapa.py FOLDER --out DIR        write the map somewhere else

What it sees, by reading the syntax tree (the ast module):
  files, functions, classes and methods;
  imports    file -> file
  calls      function -> function     EXACT when the name resolves through an import;
                                      INFERRED when only one method with that name exists in the project
  inherits   class -> class
  reads / writes / creates   function -> database table, from the SQL written inside it

What it does NOT see: calls built at run time (getattr, a dict of functions filled elsewhere), HTTP routes
called by a browser, and SQL assembled from pieces. "Nobody calls it" means "nobody calls it by name".
"""
import ast, json, os, re, sys
from collections import defaultdict, deque

HERE = os.path.dirname(os.path.abspath(__file__))
SKIP = {"__pycache__", ".git", "node_modules", "venv", ".venv", "env", "build", "dist", "mapa-out", "site-packages"}
SQL_READS = re.compile(r"\b(?:FROM|JOIN)\s+([a-z_][a-z0-9_]*)", re.I)
SQL_WRITES = re.compile(r"\b(?:INSERT(?:\s+OR\s+\w+)?\s+INTO|REPLACE\s+INTO|UPDATE|DELETE\s+FROM)\s+([a-z_][a-z0-9_]*)", re.I)
SQL_CREATES = re.compile(r"\bCREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([a-z_][a-z0-9_]*)", re.I)
# Method names too common to guess an owner for.
COMMON = {"get", "set", "append", "items", "keys", "values", "join", "split", "strip", "format", "read", "write", "close", "open",
          "send", "call", "run", "main", "update", "execute", "commit", "fetchone", "fetchall", "encode", "decode", "replace",
          "lower", "upper", "startswith", "endswith", "sort", "pop", "add", "remove", "copy", "index", "count", "extend"}


def files(root):
    for folder, subs, names in os.walk(root):
        subs[:] = sorted(d for d in subs if d not in SKIP and not d.startswith("."))
        for n in sorted(names):
            if n.endswith(".py"):
                yield os.path.relpath(os.path.join(folder, n), root).replace("\\", "/")


def _strings(node):
    for n in ast.walk(node):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and len(n.value) > 12:
            yield n.value


def _first_line(node):
    return (ast.get_docstring(node) or "").strip().split("\n")[0][:160]


class Module:
    def __init__(self, rel, tree, lines):
        self.rel, self.tree, self.lines = rel, tree, lines
        self.doc = _first_line(tree)
        self.defs = {}             # name -> {type, line, end, doc, node, cls}
        self.imports = {}          # alias -> ("mod", dotted) | ("name", dotted, name)
        self.bases = {}            # class -> [base names]
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._def(node.name, "function", node)
            elif isinstance(node, ast.ClassDef):
                self._def(node.name, "class", node)
                self.bases[node.name] = [ast.unparse(b) for b in node.bases]
                for m in node.body:
                    if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        self._def(node.name + "." + m.name, "method", m, node.name)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    self.imports[a.asname or a.name.split(".")[0]] = ("mod", a.name)
            elif isinstance(node, ast.ImportFrom):
                base = "." * node.level + (node.module or "")
                for a in node.names:
                    self.imports[a.asname or a.name] = ("name", base, a.name)

    def _def(self, name, kind, node, cls=None):
        self.defs[name] = {"type": kind, "line": node.lineno, "end": getattr(node, "end_lineno", node.lineno),
                           "doc": _first_line(node), "node": node, "cls": cls}


def build(root):
    mods, errors = {}, []
    for rel in files(root):
        try:
            with open(os.path.join(root, rel), encoding="utf-8-sig") as f:
                source = f.read()
            mods[rel] = Module(rel, ast.parse(source), source.count("\n") + 1)
        except (SyntaxError, UnicodeDecodeError, ValueError) as e:
            errors.append("%s: %s" % (rel, type(e).__name__))
    by_name = defaultdict(list)               # "db" -> ["core/db.py"]
    for rel in mods:
        by_name[os.path.basename(rel)[:-3]].append(rel)

    def file_of(dotted, src):
        """The project file an import points to, or None when it is an outside library."""
        if dotted.startswith("."):
            level = len(dotted) - len(dotted.lstrip("."))
            base = os.path.dirname(src)
            for _ in range(level - 1):
                base = os.path.dirname(base)
            dotted = ((base.replace("/", ".") + ".") if base else "") + dotted.lstrip(".")
            dotted = dotted.strip(".")
        path = dotted.replace(".", "/")
        for c in (path + ".py", path + "/__init__.py", os.path.dirname(src) + "/" + path + ".py"):
            c = c.lstrip("/")
            if c in mods:
                return c
        last = dotted.split(".")[-1]
        if len(by_name.get(last, [])) == 1:   # projects that edit sys.path: if only one file has that name, that's the one
            return by_name[last][0]
        return None

    # ---- resolve each file's aliases
    for m in mods.values():
        m.target = {}                         # alias -> ("mod", rel) | ("def", rel, name)
        for alias, imp in m.imports.items():
            if imp[0] == "mod":
                rel = file_of(imp[1], m.rel)
                if rel:
                    m.target[alias] = ("mod", rel)
                continue
            package, name = imp[1], imp[2]
            rel = file_of((package + "." + name) if package.strip(".") else package + name, m.rel)   # from core import db
            if rel:
                m.target[alias] = ("mod", rel)
                continue
            rel = file_of(package, m.rel)                                                           # from core.db import connect
            if rel and name in mods[rel].defs:
                m.target[alias] = ("def", rel, name)
            elif rel:
                m.target[alias] = ("mod", rel)

    methods = defaultdict(list)               # bare method/function name -> [(rel, full name)]
    for m in mods.values():
        for name in m.defs:
            methods[name.split(".")[-1]].append((m.rel, name))
    tables = set()
    for m in mods.values():
        for s in _strings(m.tree):
            tables.update(t.lower() for t in SQL_CREATES.findall(s))

    nodes, edges = {}, defaultdict(lambda: {"n": 0})
    for m in mods.values():
        nodes[m.rel] = {"id": m.rel, "type": "file", "name": m.rel, "lines": m.lines, "doc": m.doc, "folder": os.path.dirname(m.rel) or "."}
        for name, d in m.defs.items():
            nodes[m.rel + "::" + name] = {"id": m.rel + "::" + name, "type": d["type"], "name": name, "file": m.rel, "line": d["line"],
                                          "lines": d["end"] - d["line"] + 1, "doc": d["doc"]}
    for t in sorted(tables):
        nodes["table:" + t] = {"id": "table:" + t, "type": "table", "name": t}

    def link(a, b, kind, certainty="EXACT"):
        if a == b or a not in nodes or b not in nodes:
            return
        e = edges[(a, b, kind)]
        e["n"] += 1
        e["certainty"] = "EXACT" if e.get("certainty") == "EXACT" else certainty

    for m in mods.values():
        for target in m.target.values():
            link(m.rel, target[1], "imports")
        for cls, bases in m.bases.items():
            for b in bases:
                head = b.split(".")[0]
                if b in m.defs:
                    link(m.rel + "::" + cls, m.rel + "::" + b, "inherits")
                elif head in m.target and m.target[head][0] == "def":
                    link(m.rel + "::" + cls, m.target[head][1] + "::" + m.target[head][2], "inherits")
                elif head in m.target and "." in b:
                    link(m.rel + "::" + cls, m.target[head][1] + "::" + b.split(".")[-1], "inherits")

        def scan(root_node, ident, cls):
            for node in ast.walk(root_node):
                if not isinstance(node, ast.Call):
                    continue
                f = node.func
                if isinstance(f, ast.Name):
                    if f.id in m.defs:
                        link(ident, m.rel + "::" + f.id, "calls")
                    elif f.id in m.target and m.target[f.id][0] == "def":
                        link(ident, m.target[f.id][1] + "::" + m.target[f.id][2], "calls")
                elif isinstance(f, ast.Attribute):
                    base = f.value
                    if isinstance(base, ast.Name) and base.id in m.target and m.target[base.id][0] == "mod":
                        link(ident, m.target[base.id][1] + "::" + f.attr, "calls")
                    elif isinstance(base, ast.Name) and base.id in ("self", "cls") and cls and (cls + "." + f.attr) in m.defs:
                        link(ident, m.rel + "::" + cls + "." + f.attr, "calls")
                    elif f.attr not in COMMON and len(f.attr) > 4 and len(methods.get(f.attr, [])) == 1:
                        rel, name = methods[f.attr][0]
                        link(ident, rel + "::" + name, "calls", "INFERRED")
            for s in _strings(root_node):
                if not re.search(r"\b(SELECT|INSERT|UPDATE|DELETE|CREATE|REPLACE)\b", s):
                    continue
                for t in SQL_CREATES.findall(s):
                    link(ident, "table:" + t.lower(), "creates")
                for t in SQL_WRITES.findall(s):
                    if t.lower() in tables:
                        link(ident, "table:" + t.lower(), "writes")
                for t in SQL_READS.findall(s):
                    if t.lower() in tables:
                        link(ident, "table:" + t.lower(), "reads")

        for name, d in m.defs.items():
            if d["type"] != "class":
                scan(d["node"], m.rel + "::" + name, d["cls"])
        top = ast.Module(body=[n for n in m.tree.body if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))], type_ignores=[])
        scan(top, m.rel, None)                           # what runs when the file is loaded
        for name in m.defs:
            edges[(m.rel, m.rel + "::" + name, "contains")]["n"] = 1

    out = [{"from": a, "to": b, "type": t, "n": e["n"], **({"certainty": e["certainty"]} if "certainty" in e else {})}
           for (a, b, t), e in sorted(edges.items())]
    # only the folder name is stored, so a shared map.html does not reveal where the project lives on your disk
    return {"root": os.path.basename(os.path.abspath(root)), "nodes": nodes, "edges": out, "errors": errors}


# ------------------------------------------------------------------ queries
def _indexes(g):
    out, inc = defaultdict(list), defaultdict(list)
    for e in g["edges"]:
        out[e["from"]].append(e)
        inc[e["to"]].append(e)
    return out, inc


def find(g, name):
    """One node by name. A name that matches several returns the list instead of guessing."""
    n = name.lower()
    exact = [i for i, x in g["nodes"].items() if i.lower() == n or x["name"].lower() == n or i.lower().endswith("::" + n) or i.lower() == "table:" + n
             or (x["type"] == "file" and os.path.basename(i).lower() in (n, n + ".py"))]
    if len(exact) == 1:
        return exact[0], []
    cands = exact or [i for i in g["nodes"] if n in i.lower()]
    return (cands[0], []) if len(cands) == 1 else (None, cands[:25])


def explain(g, name):
    ident, cands = find(g, name)
    if not ident:
        return "Nothing with that name." if not cands else "More than one match:\n  " + "\n  ".join(cands)
    out_e, in_e = _indexes(g)
    x = g["nodes"][ident]
    out = ["%s  [%s]" % (ident, x["type"])]
    if x.get("file"):
        out.append("  where: %s line %s (%s lines)" % (x["file"], x["line"], x["lines"]))
    elif x.get("lines"):
        out.append("  size: %s lines" % x["lines"])
    if x.get("doc"):
        out.append("  says: " + x["doc"])

    def block(title, es, side):
        es = [e for e in es if e["type"] != "contains"]
        if es:
            out.append("  %s (%d):" % (title, len(es)))
            for e in sorted(es, key=lambda e: (e["type"], e[side]))[:60]:
                out.append("    %-8s %s%s" % (e["type"], e[side], "  (inferred)" if e.get("certainty") == "INFERRED" else ""))
    block("used by", in_e[ident], "from")
    block("uses", out_e[ident], "to")
    if x["type"] == "file":
        inside = [e["to"].split("::")[1] for e in out_e[ident] if e["type"] == "contains"]
        out.append("  contains (%d): %s" % (len(inside), ", ".join(inside[:40]) + (" ..." if len(inside) > 40 else "")))
    return "\n".join(out)


def path(g, a, b):
    ia, ca = find(g, a)
    ib, cb = find(g, b)
    if not ia or not ib:
        return "Not found (or ambiguous): " + ", ".join((ca or cb)[:12])
    near = defaultdict(list)
    for e in g["edges"]:
        near[e["from"]].append((e["to"], "--%s-->" % e["type"]))
        near[e["to"]].append((e["from"], "<--%s--" % e["type"]))
    prev, queue = {ia: None}, deque([ia])
    while queue:
        x = queue.popleft()
        if x == ib:
            break
        for y, label in near[x]:
            if y not in prev:
                prev[y] = (x, label)
                queue.append(y)
    if ib not in prev:
        return "No connection between the two."
    steps, x = [], ib
    while prev[x]:
        steps.append((prev[x][1], x))
        x = prev[x][0]
    return "Path (%d steps):\n  %s\n  %s" % (len(steps), ia, "\n  ".join("%s %s" % p for p in reversed(steps)))


def table(g, name):
    ident = "table:" + name.lower()
    if ident not in g["nodes"]:
        similar = [x["name"] for x in g["nodes"].values() if x["type"] == "table" and name.lower() in x["name"]]
        return "No such table." + (" Similar: " + ", ".join(similar) if similar else "")
    _, in_e = _indexes(g)
    out = ["table " + name]
    for kind in ("creates", "writes", "reads"):
        es = sorted(e["from"] for e in in_e[ident] if e["type"] == kind)
        out.append("  %s (%d):%s" % (kind, len(es), "".join("\n    " + x for x in es)))
    return "\n".join(out)


# ------------------------------------------------------------------ report
def report(g):
    nodes, es = g["nodes"], g["edges"]
    out_e, in_e = _indexes(g)
    fs = [x for x in nodes.values() if x["type"] == "file"]
    defs = [x for x in nodes.values() if x["type"] in ("function", "method", "class")]
    tabs = [x for x in nodes.values() if x["type"] == "table"]
    calls = [e for e in es if e["type"] == "calls"]
    L = ["# Map of %s" % os.path.basename(g["root"]), "",
         "%d files, %d lines, %d functions and methods, %d classes, %d database tables." % (
             len(fs), sum(f["lines"] for f in fs), sum(1 for d in defs if d["type"] != "class"),
             sum(1 for d in defs if d["type"] == "class"), len(tabs)),
         "%d import links, %d call links (%d inferred), %d links to the database." % (
             sum(1 for e in es if e["type"] == "imports"), len(calls), sum(1 for e in calls if e.get("certainty") == "INFERRED"),
             sum(1 for e in es if e["type"] in ("reads", "writes", "creates"))), ""]

    def tab(title, head, rows):
        L.extend(["## " + title, "", "| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
                 + ["| " + " | ".join(str(c).replace("|", "\\|") for c in r) + " |" for r in rows] + [""])
    folders = defaultdict(lambda: [0, 0])
    for f in fs:
        folders[f["folder"]][0] += 1
        folders[f["folder"]][1] += f["lines"]
    tab("By folder", ["folder", "files", "lines"], [(p, v[0], v[1]) for p, v in sorted(folders.items(), key=lambda kv: -kv[1][1])])
    imp_in = {f["id"]: len({e["from"] for e in in_e[f["id"]] if e["type"] == "imports"}) for f in fs}
    imp_out = {f["id"]: len({e["to"] for e in out_e[f["id"]] if e["type"] == "imports"}) for f in fs}
    tab("Files the most others depend on", ["file", "imported by", "lines", "says"],
        [(f["id"], imp_in[f["id"]], f["lines"], f["doc"][:70]) for f in sorted(fs, key=lambda f: -imp_in[f["id"]])[:15]])
    tab("Files that depend on the most others", ["file", "imports", "lines"],
        [(f["id"], imp_out[f["id"]], f["lines"]) for f in sorted(fs, key=lambda f: -imp_out[f["id"]])[:12]])
    call_in = {d["id"]: len({e["from"] for e in in_e[d["id"]] if e["type"] == "calls"}) for d in defs}
    tab("Most called functions", ["function", "called from", "says"],
        [(d["id"], call_in[d["id"]], d["doc"][:70]) for d in sorted(defs, key=lambda d: -call_in[d["id"]])[:20]])
    tab("Largest functions", ["function", "lines"],
        [(d["id"], d["lines"]) for d in sorted((d for d in defs if d["type"] != "class"), key=lambda d: -d["lines"])[:15]])
    if tabs:
        def who(t, kind):
            return sorted({e["from"].split("::")[0] for e in in_e[t["id"]] if e["type"] == kind})
        rows = []
        for t in sorted(tabs, key=lambda t: -len(in_e[t["id"]])):
            w, r = who(t, "writes"), who(t, "reads")
            rows.append((t["name"], len(w), len(r), ", ".join(w[:4]) + (" ..." if len(w) > 4 else "")))
        tab("Database tables: who writes and who reads", ["table", "files writing", "files reading", "writers"], rows[:40])
        many = [r for r in rows if r[1] >= 3]
        if many:
            L.extend(["**Tables written by three or more files** (where a format change reaches the most code): "
                      + ", ".join("%s (%d)" % (r[0], r[1]) for r in many), ""])
        unread = [r[0] for r in rows if r[1] and not r[2]]
        if unread:
            L.extend(["**Tables somebody writes and nobody reads by name:** " + ", ".join(unread), ""])
    pairs = sorted({tuple(sorted((e["from"], e["to"]))) for e in es if e["type"] == "imports"
                    and any(x["to"] == e["from"] and x["type"] == "imports" for x in out_e[e["to"]])})
    if pairs:
        L.extend(["## Files that import each other", ""] + ["- %s and %s" % p for p in pairs] + [""])
    orphans = [d for d in defs if d["type"] == "function" and not call_in[d["id"]] and not d["name"].startswith("_") and d["name"] != "main"]
    L.extend(["## Functions nobody calls by name (%d)" % len(orphans), "",
              "Each one may be an entry point (command, route, script), a call built at run time, or leftover code. Look before deleting.", ""])
    per_file = defaultdict(list)
    for d in orphans:
        per_file[d["file"]].append(d["name"])
    L.extend(["- **%s** (%d): %s" % (f, len(ns), ", ".join(sorted(ns)[:14]) + (" ..." if len(ns) > 14 else ""))
              for f, ns in sorted(per_file.items(), key=lambda kv: -len(kv[1]))[:25]] + [""])
    if g["errors"]:
        L.extend(["## Files that could not be read", ""] + ["- " + e for e in g["errors"]] + [""])
    return "\n".join(L)


def save(g, dest):
    os.makedirs(dest, exist_ok=True)
    with open(os.path.join(dest, "graph.json"), "w", encoding="utf-8") as f:
        json.dump(g, f, ensure_ascii=False)
    with open(os.path.join(dest, "REPORT.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write(report(g))
    with open(os.path.join(HERE, "mapa.html"), encoding="utf-8") as f:
        page = f.read()
    with open(os.path.join(dest, "map.html"), "w", encoding="utf-8") as f:
        f.write(page.replace("/*__GRAPH__*/null", json.dumps(g, ensure_ascii=False).replace("</", "<\\/")))


def main(a):
    a = list(a)
    out_dir = None
    if "--out" in a:
        i = a.index("--out")
        if i + 1 >= len(a):
            print("--out needs a folder")
            return 1
        out_dir = a[i + 1]
        del a[i:i + 2]
    if not a or a[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    root = a[0]
    if not os.path.isdir(root):
        print("folder does not exist:", root)
        return 1
    g = build(root)
    if len(a) == 1:
        dest = out_dir or os.path.join(os.getcwd(), "mapa-out", os.path.basename(os.path.abspath(root)))
        save(g, dest)
        ns = g["nodes"].values()
        print("map of %s: %d files, %d functions/methods/classes, %d tables, %d links" % (
            root, sum(1 for x in ns if x["type"] == "file"), sum(1 for x in ns if x["type"] in ("function", "method", "class")),
            sum(1 for x in ns if x["type"] == "table"), len(g["edges"])))
        print("written to", dest, "(map.html, REPORT.md, graph.json)")
    elif a[1] == "explain" and len(a) > 2:
        print(explain(g, a[2]))
    elif a[1] == "path" and len(a) > 3:
        print(path(g, a[2], a[3]))
    elif a[1] == "table" and len(a) > 2:
        print(table(g, a[2]))
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    sys.exit(main(sys.argv[1:]))
