"""Citation specs: what a doc anchor names, and the line numbers that name resolves to.

Grammar:  [@<sha>:]<path>[::<symbol>] [<quote>[#n] [.. <quote>[#n]]]

    path    suffix of a tracked file; must match exactly one file
    symbol  .py: qualified name (Class.method) or a unique suffix of one; module-level NAME = assignment
            .yaml: dotted key path by indentation (services.worker)
    quote   `text` (use ``text`` when the code itself contains a backtick) or 「text」.
            Whitespace-insensitive substring of one line inside the symbol's span;
            it must hit exactly one line unless #n picks the n-th hit.
    none    no quote -> the whole symbol span, rendered A-B
    @sha    resolve against that commit — for code that no longer exists at HEAD.
            Only pinned specs may use plain integers instead of quotes.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

from .errors import RefError
from .repo import Repo, Span

SHA_RE = re.compile(r"@([0-9a-f]{7,40}):")
NTH_RE = re.compile(r"#(\d+)")


@dataclass(frozen=True)
class Ref:
    """One end of a line reference: a quote (optionally the n-th hit) or, for pinned specs, an integer."""

    quote: str | None = None
    nth: int | None = None
    line: int | None = None


@dataclass
class Target:
    repo: Repo
    sha: str | None
    path: str
    span: Span

    def lines(self) -> tuple[str, ...]:
        return self.repo.source_lines(self.path, self.sha)

    def find(self, quote: str, nth: int | None) -> int:
        q = _norm(html.unescape(quote))
        if not q:
            raise RefError("empty quote")
        a, b = self.span
        hits = [n for n in range(a, b + 1) if q in _norm(self.lines()[n - 1])]
        if not hits:
            raise RefError(f"quote {quote!r} not found in {self.path}:{a}-{b}")
        if nth is None:
            if len(hits) > 1:
                raise RefError(
                    f"quote {quote!r} hits {len(hits)} lines ({', '.join(map(str, hits))}); add #n"
                )
            return hits[0]
        if not 1 <= nth <= len(hits):
            raise RefError(f"quote {quote!r}#{nth}: only {len(hits)} hits")
        return hits[nth - 1]

    def ref(self, ref: Ref) -> int:
        if ref.quote is not None:
            return self.find(ref.quote, ref.nth)
        if self.sha is None:
            raise RefError(f"integer line {ref.line} on an unpinned spec — use a quote")
        return ref.line


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s)


def _backtick_run(s: str, i: int) -> int:
    return len(s[i:]) - len(s[i:].lstrip("`"))


def _read_quote(s: str, i: int) -> tuple[str, int]:
    """Read a quote starting at s[i]; return (text, index after the closing delimiter)."""
    if s[i] == "「":
        depth, j = 0, i
        while j < len(s):
            if s[j] == "「":
                depth += 1
            elif s[j] == "」":
                depth -= 1
                if depth == 0:
                    return s[i + 1 : j], j + 1
            j += 1
        raise RefError(f"unclosed 「 in {s!r}")
    # markdown rule: a quote opened by N backticks closes with exactly N
    run = _backtick_run(s, i)
    fence = "`" * run
    start = j = i + run
    while True:
        k = s.find(fence, j)
        if k < 0:
            raise RefError(f"unclosed {fence} in {s!r}")
        if _backtick_run(s, k) != run:  # a longer run is part of the text
            j = k + _backtick_run(s, k)
            continue
        after = k + run
        text = s[start:k]
        if run > 1 and text.startswith(" ") and text.endswith(" ") and text.strip():
            text = text[1:-1]
        return text, after


def parse_refs(rest: str) -> list[Ref]:
    """Parse the quote part of a spec: one ref, or two joined by '..'."""
    refs: list[Ref] = []
    i, s = 0, rest
    while True:
        while i < len(s) and s[i].isspace():
            i += 1
        if i >= len(s):
            raise RefError(f"missing line reference in {rest!r}")
        if s[i] in "`「":
            text, i = _read_quote(s, i)
            m = NTH_RE.match(s, i)
            nth = None
            if m:
                nth, i = int(m.group(1)), m.end()
            refs.append(Ref(quote=text, nth=nth))
        else:
            m = re.compile(r"\d+").match(s, i)
            if not m:
                raise RefError(f"bad line reference at {s[i:]!r}")
            refs.append(Ref(line=int(m.group(0))))
            i = m.end()
        while i < len(s) and s[i].isspace():
            i += 1
        if i >= len(s):
            break
        if not s.startswith("..", i):
            raise RefError(f"expected '..' or end at {s[i:]!r}")
        i += 2
    if len(refs) > 2:
        raise RefError(f"a range has two ends, got {len(refs)} in {rest!r}")
    return refs


def parse_target(repo: Repo, spec: str) -> tuple[Target, str]:
    """Split a spec into its Target and the remaining line-reference part (may be empty)."""
    spec = spec.strip()
    sha = None
    m = SHA_RE.match(spec)
    if m:
        sha, spec = m.group(1), spec[m.end() :]
    cut = min((k for k in (spec.find("`"), spec.find("「")) if k >= 0), default=-1)
    head, rest = (spec, "") if cut < 0 else (spec[:cut], spec[cut:])
    words = head.split(
        None, 1
    )  # paths and symbols hold no spaces; anything after is a line reference
    if len(words) > 1:
        head, rest = words[0], f"{words[1]} {rest}"
    path_part, _, symbol = head.strip().partition("::")
    if not path_part:
        raise RefError(f"no path in spec {spec!r}")
    path = repo.resolve_path(path_part, sha)
    return Target(
        repo, sha, path, repo.symbol_span(path, sha, symbol.strip() or None)
    ), rest.strip()


def resolve(repo: Repo, spec: str) -> tuple[Target, int, int]:
    target, rest = parse_target(repo, spec)
    if not rest:
        return (target, *target.span)
    refs = parse_refs(rest)
    a, b = target.ref(refs[0]), target.ref(refs[-1])
    if b < a:
        raise RefError(f"range end before start ({a}..{b})")
    return target, a, b


def render(a: int, b: int, sep: str = "-") -> str:
    return str(a) if a == b else f"{a}{sep}{b}"


def format_quote(text: str) -> str:
    """Quote a code fragment for a spec, widening the backtick fence when the code contains backticks."""
    longest = max((len(r) for r in re.findall(r"`+", text)), default=0)
    if not longest:
        return f"`{text}`"
    fence = "`" * (longest + 1)
    return f"{fence} {text} {fence}"
