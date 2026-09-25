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
| `coderef audit` | traces existing number-only citations through git history: which ones already point at the wrong line |
| `coderef adopt [--write]` | converts number-only citations into links (markdown) or anchors (other files) |

## Existing docs: audit, then adopt

Docs you already have cite code as `orders.py:310` or `[orders.py:310](app/orders.py#L310)`. `coderef audit`
judges them without changing anything. For each citation it asks git when the doc line was written, reads line
310 of the code *as it was then*, and follows that line to today's code:

```
docs/design.md:14  ok       orders.py:310                  written against 9b2f41d0c3
docs/design.md:31  stale    orders.py:118 -> orders.py:131  written against 4e1a9c2b07
docs/design.md:40  gone     orders.py:77                   `row.lock()` is gone; written against 4e1a9c2b07
docs/design.md:52  unknown  orders.py:12                   line 12 was blank: too little to identify; written against 4e1a9c2b07

number-only citations 4 · ok 1 · stale 1 · gone 1 · unknown 1 · unverifiable 0
```

Today's line 310 is never the reference — it holds *some* code, so judging by it would pass numbers that are
already wrong. Where docs and code live in separate repositories, the code is read as of the doc line's
date (following the first-parent line of `HEAD`); lines not committed yet are read against the working tree.
The results are estimates: a doc line edited later (a typo fix) is dated by that edit. In a shallow clone (CI
checkouts often fetch one commit) lines older than the clone are reported `unverifiable` — fetch full history.

`coderef adopt` uses the same trace to propose conversions — a titled link in markdown, an anchor elsewhere —
with the number set to where the cited line is now. It writes nothing until `--write`; every proposal is
resolved before it is shown, so converted citations pass `check`. Citations that are gone, ambiguous or
undatable are listed and left alone.

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
