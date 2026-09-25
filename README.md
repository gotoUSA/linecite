# coderef

Docs that cite code by line number (`orders.py:310`) go stale on the next commit that touches the lines above.
coderef lets a citation name the code it means — a **symbol and a quoted fragment** — derives the line number
from the source, and tells you which paragraphs to re-read after the code changes.

```md
Locks are taken in product order at [orders.py:310](app/orders.py#L310 "create_order: order_by(\"product_id\")").
```

That is an ordinary markdown link: it renders as `orders.py:310`, clicks through to the line on GitHub, and its
title says what the line is. When code above it moves, `coderef sync` rewrites both the `#L310` fragment and the
`:310` in the link text; when the quoted code disappears, `coderef check` fails.

| command | what it does |
|---|---|
| `coderef check` | reports drifted numbers, citations that no longer resolve, and number-only citations; exit 1 if any (CI) |
| `coderef sync` | rewrites drifted numbers in place (pre-commit), then reports what still needs a human |
| `coderef affected <rev>` | lists doc lines whose citations point into code changed since `<rev>` — the prose to re-read |
| `coderef locate <path> <line>` | proposes a citation for an existing `path:line` |
| `coderef list` / `where <spec>` | inspect what citations resolve to |

## Citation forms

**Link** (markdown) — a link to a code file with a `#L<n>` or `#L<a>-L<b>` fragment and a title:

| title | meaning |
|---|---|
| `"create_order: row.lock()"` | the one line in `create_order` containing `row.lock()` (whitespace-insensitive) |
| `"OrderService.create_order"` | the whole symbol, as a range |
| `": xs.reduce"` | no symbol — for languages without symbol support, cite by fragment only |
| ``"OrderService `return`#2"`` | backtick grammar: the 2nd hit; also `` `a` .. `b` `` for a range |

A link with a `#L` fragment but no title is reported as legacy: nothing records which code it meant.
Links inside fenced code blocks are examples and are not checked.

**Anchor** (hidden comment) — for HTML, for numbers in running prose, and for comments in code excerpts:

```md
the lock is taken at line 310<!--@ app/orders.py::create_order `order_by("product_id")` -->
```

The anchor sits right after the number it owns. Its spec grammar:

```
[@<sha>:]<path>[::<symbol>] [<quote>[#n] [.. <quote>[#n]]]
```

- **path** — suffix of a tracked file; must match exactly one file.
- **symbol** — Python: qualified name or a unique suffix of one, or a module-level assignment. YAML: dotted key path.
- **quote** — `` `fragment` `` (widen to ``` `` ``` when the code holds a backtick) or `「fragment」`; `#n` picks the n-th hit.
- **`@sha:`** — pin to a commit for code that no longer exists; pinned specs may use plain integers.
- A bare `<!--@-->` marks a number that is not a code line.

**Symbol reference** — `` `orders.py::OrderService.cancel` `` in prose fails `check` once the method is gone.

## Configuration

`.coderef.toml` (top-level keys) or `[tool.coderef]` in `pyproject.toml`:

```toml
code_root = "."                 # git repo of the cited code, relative to this file
docs = ["docs/**/*.md", "README.md"]
number_suffixes = ["`"]         # text allowed between a number and its anchor: `orders.py:12`<!--@ … -->
legacy = "error"                # number-only citations: "error" | "warn" | "off"
ignore_patterns = []            # regexes of regions to skip (the legacy scan also skips fenced code)
```

Status: early development.
