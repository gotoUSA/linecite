"""Convert number-only citations into citations that name their code, using the history `audit` reads.

Markdown gets the link form; every other document a hidden anchor right after the number:

    orders.py:10  ->  [orders.py:12](../app/orders.py#L12 "OrderService.create_order: row.lock()")
    orders.py:10  ->  orders.py:12<!--@ app/orders.py::OrderService.create_order `row.lock()` -->

The number written into the new citation is where the cited line is now by audit's trace — never
line N of today's code, which would freeze a number that is already stale. Only ok and stale
citations are converted; gone, unknown and unverifiable ones are left for a human. Every proposal
is read back through `extract` and resolved before it is offered, so an applied proposal passes `check`.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .audit import OK, STALE, Traced, Tracer, number_only, renumber
from .config import Config
from .errors import RefError
from .repo import Repo
from .scan import MARKDOWN_SUFFIXES, Syntax, code_spans, extract, line_of
from .spec import describe, resolve

# [bracketed text] on one line, one level of nesting: link texts (inline or reference-style) and the like
BRACKETS_RE = re.compile(r"\[(?:[^\[\]\n]|\[[^\[\]\n]*\])*\]")


@dataclass
class Proposal:
    traced: Traced
    start: int
    end: int
    new: str


@dataclass
class DocPlan:
    """What adopt would do to one document."""

    path: Path
    text: str
    proposals: list[Proposal] = field(default_factory=list)
    left: list[tuple[Traced, str]] = field(default_factory=list)  # (citation, why)

    def converted(self) -> str:
        out, last = [], 0
        for p in sorted(self.proposals, key=lambda p: p.start):
            out += [self.text[last : p.start], p.new]
            last = p.end
        out.append(self.text[last:])
        return "".join(out)


def link_url(doc: Path, repo: Repo, path: str) -> str:
    """How a link from DOC names PATH: relative when the doc lives in the code repo, else from the code root."""
    doc = doc.resolve()
    if not doc.is_relative_to(repo.root):
        return path
    rel = Path(os.path.relpath(repo.root / path, doc.parent)).as_posix()
    kept = "/".join(p for p in rel.split("/") if p not in ("", ".", ".."))
    return (
        rel if kept == path else "/" + path
    )  # links resolve by suffix; keep the whole path


def title_attr(title: str, in_table: bool = False) -> str:
    title = title.replace("\\", "\\\\").replace('"', '\\"')
    if in_table:  # an unescaped | ends a table cell, even inside a link title
        title = title.replace("|", "\\|")
    return f'"{title}"'


def _line_at(text: str, pos: int) -> str:
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    return text[start : end if end >= 0 else len(text)]


@dataclass
class _Doc:
    path: Path
    text: str
    brackets: list[tuple[int, int]]  # [bracketed] spans: text of other links
    code: list[tuple[int, int]]  # markdown inline `code` spans

    @classmethod
    def of(cls, path: Path, text: str) -> _Doc:
        markdown = path.suffix.lower() in MARKDOWN_SUFFIXES
        return cls(
            path,
            text,
            [m.span() for m in BRACKETS_RE.finditer(text)] if markdown else [],
            code_spans(text) if markdown else [],
        )


def _check(
    d: _Doc,
    start: int,
    end: int,
    new: str,
    syntax: Syntax,
    repo: Repo,
    want: tuple[int, int],
) -> None:
    """Read the document back with the replacement in place, as `check` will: the replaced region must
    hold exactly one link or anchor, resolving to WANT with nothing to sync. In place matters —
    `!` before a link makes it an image, a surrounding code span makes it literal text."""
    after = d.text[:start] + new + d.text[end:]
    lo, hi = start, start + len(new)
    cites = [
        c for c in extract(after, d.path, syntax)[0] if c.start < hi and lo < c.end
    ]
    if len(cites) != 1 or cites[0].kind not in ("link", "anchor") or cites[0].error:
        raise RefError("the converted citation would not read back as a citation here")
    _, a, b = resolve(repo, cites[0].spec)
    if (a, b) != want or any(s.render(a, b) != s.old for s in cites[0].slots):
        raise RefError(
            f"the converted citation would resolve to {a}-{b}, not {want[0]}-{want[1]}"
        )


def _within(pos: int, spans: list[tuple[int, int]]) -> tuple[int, int] | None:
    return next(((s, e) for s, e in spans if s <= pos < e), None)


def propose(d: _Doc, t: Traced, repo: Repo, syntax: Syntax) -> Proposal:
    """The replacement for one traced citation; RefError says why there is none."""
    if t.status not in (OK, STALE):
        raise RefError(t.note or t.status)
    if len(t.spans) != 1:
        raise RefError("a list of lines: give each line its own citation first")
    (a, b), c, text = t.spans[0], t.citation, d.text
    naming = describe(repo, t.path, a, b)
    frag = f"#L{a}" if a == b else f"#L{a}-L{b}"
    title = title_attr(naming.title(), _line_at(text, c.start).lstrip().startswith("|"))
    start, end = c.start, c.end

    if c.kind == "bare-link":
        # the author's own text and url, renumbered; the url is replaced only if the file moved
        body, _, url = renumber(text, c, t.spans)[1:].partition("](")
        url = url.partition("#")[0]
        try:
            same_file = repo.resolve_path(c.cited.path, None) == t.path
        except RefError:
            same_file = False
        if not same_file:
            url = link_url(d.path, repo, t.path)
        new = f"[{body}]({url}{frag} {title})"
    elif d.path.suffix.lower() in MARKDOWN_SUFFIXES:
        if _within(c.start, d.brackets):
            raise RefError("inside [brackets], such as another link's text")
        # a link inside inline code renders as literal text: the whole code span becomes the link text
        start, end = _within(c.start, d.code) or (start, end)
        label = text[start : c.start] + t.now + text[c.end : end]
        new = f"[{label}]({link_url(d.path, repo, t.path)}{frag} {title})"
    else:
        spec = naming.spec()
        if "-->" in spec:
            raise RefError("the quoted code contains `-->`, which would end the anchor")
        new = f"{t.now}<!--@ {spec} -->"
    _check(d, start, end, new, syntax, repo, (a, b))
    return Proposal(t, start, end, new)


def plan(files: list[Path], repo: Repo, cfg: Config) -> list[DocPlan]:
    tracer = Tracer(repo)
    syntax = Syntax(cfg)
    docs = number_only(files, syntax)
    tracer.prefetch([(h, [line_of(text, c.start) for c in cs]) for h, text, cs in docs])
    plans = []
    for history, text, cites in docs:
        d = _Doc.of(history.path, text)
        p = DocPlan(history.path, text)
        for c in cites:
            t = tracer.trace(history, text, c)
            try:
                prop = propose(d, t, repo, syntax)
                if any(q.start < prop.end and prop.start < q.end for q in p.proposals):
                    raise RefError("shares its inline code with another citation")
                p.proposals.append(prop)
            except RefError as e:
                p.left.append((t, str(e)))
        plans.append(p)
    return plans


def write(p: DocPlan) -> None:
    p.path.write_bytes(
        p.converted().encode("utf-8")
    )  # bytes: keep the file's own line endings
