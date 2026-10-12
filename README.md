# mapa

Reads a folder of Python code and shows who depends on whom: which file imports which, which function calls
which, and which functions read or write each database table.

One file, standard library only. Nothing is installed, nothing leaves your machine, and the code it reads is
parsed, never executed.

![The map of Python's own `email` package](docs/screenshot.png)

## Why

When a project grows past a few dozen files, the question before every change is "what else touches this?".
A text search answers it for one name at a time. `mapa` builds the whole graph once and lets you ask.

The part I couldn't find in similar tools: it reads the SQL written inside your functions and links each
function to the tables it creates, writes and reads. So "who writes to `orders`?" has a direct answer.

## Use

Requires Python 3.9 or newer.

```
python mapa.py path/to/project
```

This writes three files to `mapa-out/<project>/`:

- `map.html`: the clickable map. A circle is a file (size = lines, color = folder), a blue square is a table.
  Open it in any browser; it is a single self-contained file.
- `REPORT.md`: the files most others depend on, the most called and the largest functions, tables and who
  writes to them, files that import each other, and functions nobody calls by name.
- `graph.json`: the whole graph, for your own scripts.

Ask questions from the terminal:

```
python mapa.py path/to/project explain connect     # what it is, who uses it, what it uses
python mapa.py path/to/project path report.py orders   # shortest path between two pieces
python mapa.py path/to/project table orders        # who creates, writes and reads a table
```

Try it on the sample project in this repository:

```
python mapa.py tests/sample table orders
```

```
table orders
  creates (1):
    core/db.py
  writes (1):
    core/db.py::Store.save_order
  reads (1):
    report.py::total
```

## How it decides

Everything comes from the syntax tree (`ast`).

| Link | From | How |
|---|---|---|
| imports | file to file | `import` and `from ... import`, resolved to files inside the project |
| calls | function to function | **exact** when the name resolves through an import, the same file, or `self` |
| calls (inferred) | function to function | `x.method()` where only one method with that name exists in the project |
| inherits | class to class | base classes that resolve to a class in the project |
| creates / writes / reads | function to table | `CREATE TABLE`, `INSERT`/`UPDATE`/`DELETE`, `FROM`/`JOIN` in string literals |

Inferred links are marked as such everywhere they appear.

## What it does not see

- Calls built at run time: `getattr`, a dict of functions filled elsewhere, decorators that register handlers.
- HTTP routes called by a browser, and anything called from another language.
- SQL assembled from pieces, and tables created outside the code (migrations, an ORM).
- Only tables with a `CREATE TABLE` somewhere in the project are tracked.

So "nobody calls it" means "nobody calls it by name". Look before you delete.

Python only. It has been used on projects of about 100 files and 20,000 lines; I have not measured it on
much larger ones.

## Tests

```
python -m unittest discover -s tests
```

## Credit

The idea of turning a codebase into a queryable graph came from reading about
[Graphify](https://github.com/Graphify-Labs/graphify). This is a small rewrite of that one idea for my own
case, not a port of it.

## License

MIT
