# linecite

Docs that cite code by line number (`orders.py:310`<!--@-->) go stale on the next commit that touches the lines above.
linecite lets a citation name the code it means — a **symbol and a quoted fragment** — derives the line number
from the source, and tells you which paragraphs to re-read after the code changes.

In the hand-written docs of 16 public repositories with 20k+ stars, two in three such numbers already point at
the wrong line ([measurement](#how-often-cited-line-numbers-are-wrong)).

```md
Locks are taken in product order at [orders.py:310](app/orders.py#L310 "create_order: order_by(\"product_id\")").
```

That is an ordinary markdown link: it renders as `orders.py:310`<!--@-->, clicks through to the line on GitHub, and its
title says what the line is. When code above it moves, `linecite sync` rewrites both the `#L310` fragment and the
`:310` in the link text; when the quoted code disappears, `linecite check` fails.

| command | what it does |
|---|---|
| `linecite check` | reports drifted numbers, citations that no longer resolve, and number-only citations; exit 1 if any (CI) |
| `linecite sync` | rewrites drifted numbers in place (pre-commit), then reports what still needs a human |
| `linecite affected <rev>` | lists doc lines whose citations point into code changed since `<rev>` — the prose to re-read |
| `linecite locate <path> <line>` | proposes a citation for an existing `path:line` |
| `linecite list` / `where <spec>` | inspect what citations resolve to |
| `linecite audit` | traces existing number-only citations through git history: which ones already point at the wrong line |
| `linecite adopt [--write]` | converts number-only citations into links (markdown) or anchors (other files) |

```sh
pip install linecite      # Python 3.11+, git
```

Run it where your configuration is (see [Configuration](#configuration)), locally, as a
[pre-commit hook](#pre-commit), or in [GitHub Actions](#github-action).

## How often cited line numbers are wrong

In September 2026, `linecite audit` was run read-only on 16 public repositories with 20k+ stars, found by
searching markdown for `file:line` citations: 9 created in 2025 or later, and 7 older ones (playwright,
mermaid, netdata, moby, deno, lazygit, vllm).

- 4,901 number-only citations; 4,266 could be judged. The rest cite a path that is not in the repository,
  a file name that several files share, or a blank line.
- **Hand-written docs: 67.6% wrong** — 2,114 of 3,126. 1,669 now point at a different line than the one
  the author meant; 445 point at code that no longer exists as written. Per repository (the 9 with 30+
  judged citations) the median is 65.7%, from 5.2% to 90.0%.
- Being recent does not help much: lines written in the last 90 days are 60.6% wrong; 90 days to a year,
  83.9%. Agent instruction files (`AGENTS.md`, `CLAUDE.md`, `.claude/` and the like): 22 of 47.
- **Generated docs: 6.0% wrong.** Checking on every change is what makes the difference: 2.3% in the
  repository that regenerates and verifies its catalogs in CI, 36.6% where generated docs are not rebuilt
  per change.
- **It is a new habit.** 73% of the citations were written in the last 90 days and 15 are older than a
  year; moby, lazygit and playwright have none. Line numbers are what AI-assisted development writes into
  plans, reports and agent notes.

Many of these docs are plans and reports — dated work notes as much as living docs. The number in them
was right on the day, but it does not say which commit it was written against, so a reader following it
today lands on other code. Verdicts are estimates, as in `audit`: a doc line edited later is dated by the
edit. 65 sampled verdicts were checked by hand; all were correct. To see your own docs, run `linecite
audit`.

## Existing docs: audit, then adopt

<!-- linecite-ignore-start -->
Docs you already have cite code as `orders.py:310` or `[orders.py:310](app/orders.py#L310)`. `linecite audit`
judges them without changing anything. For each citation it asks git when the doc line was written, reads line
310 of the code *as it was then*, and follows that line to today's code:
<!-- linecite-ignore-end -->

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

`linecite adopt` uses the same trace to propose conversions — a titled link in markdown, an anchor elsewhere —
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

<!-- linecite-ignore-start -->
```md
the lock is taken at line 310<!--@ app/orders.py::create_order `order_by("product_id")` -->
```
<!-- linecite-ignore-end -->

The anchor sits right after the number it owns. Its spec grammar:

```
[@<sha>:]<path>[::<symbol>] [<quote>[#n] [.. <quote>[#n]]]
```

- **path** — suffix of a tracked file; must match exactly one file.
- **symbol** — Python: qualified name or a unique suffix of one, or a module-level assignment. YAML: dotted key path.
- **quote** — `` `fragment` `` (widen to ``` `` ``` when the code holds a backtick) or `「fragment」`; `#n` picks the n-th hit.
- **`@sha:`** — pin to a commit for code that no longer exists; pinned specs may use plain integers.
- A bare `<!--@-->` marks a number that is not a code line.

**Examples** — docs that teach the syntax (a contributing guide, this README) wrap their examples in
ignore markers, each on a line of its own and outside code blocks (a marker shown in a code block is an
example itself); a marker that pairs with nothing fails `check`:

```md
<!-- linecite-ignore-start -->
Cite code as [orders.py:310](app/orders.py#L310 "create_order: row.lock()").
<!-- linecite-ignore-end -->
```

<!-- linecite-ignore-start -->
**Symbol reference** — `` `orders.py::OrderService.cancel` `` in prose fails `check` once the method is gone.
<!-- linecite-ignore-end -->

## Configuration

`.linecite.toml` (top-level keys) or `[tool.linecite]` in `pyproject.toml`:

<!-- linecite-ignore-start -->
```toml
code_root = "."                 # git repo of the cited code, relative to this file
docs = ["docs/**/*.md", "README.md"]
number_suffixes = ["`"]         # text allowed between a number and its anchor: `orders.py:12`<!--@ … -->
legacy = "error"                # number-only citations: "error" | "warn" | "off"
ignore_patterns = []            # regexes of regions to skip (the legacy scan also skips fenced code)
```
<!-- linecite-ignore-end -->

## pre-commit

```yaml
repos:
  - repo: https://github.com/gotoUSA/linecite
    rev: v0.1.1
    hooks:
      - id: linecite-sync     # or linecite-check, to report without rewriting
```

Both hooks read every configured document on every commit, whatever is staged: a commit that touches only
code can move the lines a doc cites. `linecite-sync` rewrites drifted numbers and pre-commit stops the
commit so you can stage the rewrite; citations whose code is gone still fail it.

## GitHub Action

```yaml
on: pull_request
permissions:
  contents: read
  pull-requests: write          # for the comment
jobs:
  linecite:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: gotoUSA/linecite@v0.1.1
```

The job fails when `linecite check` does. On a pull request, the action also comments with the doc lines
whose cited code the pull request changed — one comment per use of the action, rewritten on every push,
so a paragraph a later push made irrelevant drops off the list. The job summary always carries the full
report; a failure to comment (pull requests from forks get a read-only token) is a warning, not a failed
job.

| input | default | |
|---|---|---|
| `check` | `true` | run `linecite check` and fail on its findings |
| `comment` | `true` | comment on pull requests |
| `base` | the pull request's base commit | revision the changes are measured from |
| `working-directory` | `.` | where the configuration is |
| `config` | | configuration file, if not the default |
| `github-token` | `github.token` | needs `pull-requests: write` |

The output `affected` is the number of doc citations pointing into changed code.

**Shallow checkouts.** `actions/checkout` fetches a single commit by default. `affected` needs only the
base commit's files, which the action fetches itself; `audit` needs the history that dated every doc line,
so run it after `actions/checkout` with `fetch-depth: 0` — in a shallow clone it reports old lines as
`unverifiable` instead of guessing.

Other CI systems can post the same report: `linecite affected origin/main --format markdown --link-base
https://example.com/owner/repo/blob/<sha>` prints it as markdown with links to the doc lines.

## License

MIT
