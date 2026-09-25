# How linecite works

This page is for contributors. Its citations are checked by linecite itself on every commit, so the
line numbers below are right for the version you are reading.

## One reader

Every command reads documents through one function, [`scan.py:311`](../src/linecite/scan.py#L311 "extract: def extract("). It finds anchors first —
everywhere, including code blocks, since an anchor in a code excerpt is a real citation — then links
and symbol references outside code, then number-only citations outside code. Regions between ignore
markers are blanked before any of that ([`scan.py:152`](../src/linecite/scan.py#L152 "ignore_regions: def ignore_regions(")); an unclosed marker ignores nothing and is
reported.

## Resolving a citation

[`spec.py:170`](../src/linecite/spec.py#L170 "resolve: def resolve(") splits a spec into a file suffix, a symbol and quotes. The suffix must match exactly one
tracked file; the symbol's span comes from Python's `ast` ([`repo.py:234`](../src/linecite/repo.py#L234 "Repo.python_symbols: def python_symbols(")) or from YAML indentation;
the quote must hit exactly one line of that span ([`spec.py:48`](../src/linecite/spec.py#L48 "Target.find: def find(")) unless `#n` picks a hit. `sync` then
rewrites the numbers, writing bytes so the file keeps its own line endings ([`scan.py:429`](../src/linecite/scan.py#L429 "scan_file: path.write_bytes(")).

## What changed since a revision

`affected` reads `git diff -U0` ([`affected.py:59`](../src/linecite/affected.py#L59 "changed_lines: def changed_lines(")) and flags a citation when a changed line falls
anywhere inside the cited symbol, not just on the quoted line ([`affected.py:124`](../src/linecite/affected.py#L124 "affected: a, b = target.span")): a change next to a
line can change what the prose says about it.

## Auditing number-only citations

A number like `orders.py:310`<!--@--> records nothing about its code, so `audit` asks git when the
doc line was written ([`history.py:57`](../src/linecite/history.py#L57 "DocHistory._blame: def _blame(")) and which code it described ([`audit.py:114`](../src/linecite/audit.py#L114 "Tracer.origin: def origin(")): the same commit
when docs and code share a repository, or the code's first-parent commit at that time when they do
not ([`repo.py:207`](../src/linecite/repo.py#L207 "Repo.commit_before: out = self.git(\"log\", \"--first-parent\", \"--format=%ct %H\", \"HEAD\")")). A diff from that code to today's ([`history.py:89`](../src/linecite/history.py#L89 "LineMap: class LineMap:")) says where the line went. A
rewritten line counts as moved only when the diff dropped exactly one copy of its text and added
exactly one ([`audit.py:302`](../src/linecite/audit.py#L302 "Tracer.trace_line: moved = hits[0] if len(hits) == 1 and len(lost) == 1 else None")).

`adopt` offers a conversion only after reading the document back with the replacement in place, as
`check` will ([`adopt.py:101`](../src/linecite/adopt.py#L101 "_check: def _check(")).

## Read-only git

Every git process runs with [`repo.py:18`](../src/linecite/repo.py#L18 "GIT_ENV: GIT_ENV = {**os.environ, \"GIT_OPTIONAL_LOCKS\": \"0\", \"GIT_NO_LAZY_FETCH\": \"1\"}"): no opportunistic index refresh, and no lazy fetches in a
partial clone.
