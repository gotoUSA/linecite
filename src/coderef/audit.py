"""Estimate whether number-only citations still point where they pointed when they were written.

For a `path:N` citation (or a #L link without a title), without changing the document:

  1. git blame dates the doc line. The code it described is that commit when docs and code share a
     repository, the code commit current at that time when they do not, and the working tree when the
     line is not committed yet.
  2. Line N of that code is the cited line. The diff from there to the working tree says where it is
     now; if the diff changed it, the same text among the changed lines is the moved line.
  3. ok: still at N · stale: now at M · gone: its text no longer exists · unknown: blank, too short to
     identify, or ambiguous · unverifiable: no history to date the doc line against.

The current line N is never the reference: judging by it would bless numbers that are already stale.
Results are estimates — a doc line re-edited later (a typo fix) is dated by the edit, not the writing.
"""

from __future__ import annotations

import difflib
import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .config import Config
from .errors import RefError
from .history import DocHistory, LineMap
from .repo import Repo, Span
from .scan import Citation, Syntax, extract, line_of
from .spec import _norm, render, too_short

OK, STALE, GONE, UNKNOWN, UNVERIFIABLE = (
    "ok",
    "stale",
    "gone",
    "unknown",
    "unverifiable",
)
STATUSES = (OK, STALE, GONE, UNKNOWN, UNVERIFIABLE)
DEF_RE = re.compile(r"\s*(?:async\s+def|def|class)\b")
# an edited line at least this similar (difflib ratio) to the cited one is reported as rewritten in place
REWRITE_SIMILARITY = 0.6
PREFETCH_WORKERS = min(8, os.cpu_count() or 1)


class Unverifiable(Exception):
    """No history to date a doc line against."""


@dataclass
class Part:
    """One line or range of a citation, traced."""

    status: str
    span: Span | None = None  # where it is now (ok, stale)
    note: str = ""


@dataclass
class Traced:
    """A number-only citation and what history says about it."""

    doc: Path
    line: int
    citation: Citation
    written: str  # the citation as it stands in the doc
    status: str = UNKNOWN
    path: str | None = None  # the cited file now (ok, stale)
    spans: tuple[Span, ...] = ()  # the cited lines now (ok, stale)
    now: str = ""  # the citation as it would read today (ok, stale)
    origin: str | None = (
        None  # code commit the number was written against; None = working tree
    )
    note: str = ""


def renumber(text: str, c: Citation, spans: tuple[Span, ...]) -> str:
    """The citation's own text with its numbers replaced by SPANS."""
    out, last = [], c.start
    # a link lists its #L fragment first, but the text number comes first in the doc
    for k, slot in sorted(enumerate(c.slots), key=lambda ks: ks[1].start):
        a, b = spans[k] if c.kind == "legacy" else spans[0]
        out += [text[last : slot.start], slot.render(a, b)]
        last = slot.end
    out.append(text[last : c.end])
    return "".join(out)


def _date(when: int | None) -> str:
    if when is None:
        return "an unknown date"
    return datetime.fromtimestamp(when, UTC).strftime("%Y-%m-%d")


def _similar(a: str, b: str) -> bool:
    return (
        difflib.SequenceMatcher(None, _norm(a), _norm(b), autojunk=False).ratio()
        >= REWRITE_SIMILARITY
    )


def _short(s: str, n: int = 60) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


class Tracer:
    def __init__(self, repo: Repo):
        self.repo = repo
        self.code_top = repo.toplevel()
        self._maps: dict[tuple[str, str | None, str], LineMap] = {}

    def origin(self, doc: DocHistory, line: int) -> str | None:
        """The code commit a doc line was written against; None for the working tree."""
        w = doc.written(line)
        if w is None:
            raise Unverifiable("the document is not in a git repository")
        if w.sha is None:
            return None  # not committed yet: it describes the code as it is
        if w.cut:  # dating by the shallow edge would make today's code the reference
            raise Unverifiable(
                "shallow clone: history ends before this line was written (git fetch --unshallow)"
            )
        if doc.root == self.code_top:
            return w.sha
        sha = self.repo.commit_before(w.time) if w.time is not None else None
        if sha is None:
            raise Unverifiable(
                f"the code has no commit before the doc line was written ({_date(w.time)})"
            )
        return sha

    def line_map(self, old_path: str, origin: str | None, new_path: str) -> LineMap:
        key = (old_path, origin, new_path)
        if key not in self._maps:
            self._maps[key] = LineMap(
                self.repo.source_lines(old_path, origin),
                self.repo.source_lines(new_path, None),
            )
        return self._maps[key]

    def current_path(self, old_path: str, origin: str | None) -> str | None:
        if origin is None:
            return old_path
        return self.repo.follow(old_path, origin)

    def prefetch(self, cited: list[tuple[DocHistory, list[int]]]) -> None:
        """Run the per-document blames and the per-commit file listings for the cited doc lines in parallel.

        Each is its own git process; one after another they dominate an audit's running time.
        """
        with ThreadPoolExecutor(PREFETCH_WORKERS) as pool:
            list(pool.map(lambda dl: dl[0].written(1), cited))
            origins = set()
            for doc, lines in cited:
                for line in lines:
                    try:
                        origins.add(self.origin(doc, line))
                    except Unverifiable:
                        pass
            list(pool.map(self.repo.tracked, origins - {None}))

    def trace(self, doc: DocHistory, text: str, c: Citation) -> Traced:
        t = Traced(doc.path, line_of(text, c.start), c, text[c.start : c.end])
        if c.cited is None or c.cited.path is None:
            t.note = "names no file"
            return t
        try:
            t.origin = self.origin(doc, t.line)
        except Unverifiable as e:
            t.status, t.note = UNVERIFIABLE, str(e)
            return t
        try:
            old_path = self.repo.resolve_path(c.cited.path, t.origin)
        except RefError as e:
            t.note = str(e)
            return t
        try:
            new_path = self.current_path(old_path, t.origin)
            if new_path is None:
                t.status, t.note = GONE, f"{old_path} no longer exists"
                return t
            parts = [
                self.trace_span(old_path, t.origin, new_path, span)
                for span in c.cited.ranges
            ]
        except (
            RefError
        ) as e:  # git could not produce a version (e.g. a blob missing locally)
            t.note = str(e)
            return t
        notes = [p.note for p in parts if p.note]
        statuses = {p.status for p in parts}
        if GONE in statuses:
            t.status = GONE
        elif UNKNOWN in statuses:
            t.status = UNKNOWN
        else:
            t.path, t.spans = new_path, tuple(p.span for p in parts)
            t.now = renumber(text, c, t.spans)
            moved_file = self._suffix_misses(c.cited.path, new_path)
            if moved_file:
                notes.insert(
                    0,
                    f"`{c.cited.path}` now matches other files too"
                    if new_path == old_path
                    else f"file is now {new_path}",
                )
                t.now = f"{new_path}:{','.join(render(a, b) for a, b in t.spans)}"
            t.status = OK if t.spans == c.cited.ranges and not moved_file else STALE
        t.note = "; ".join(notes)
        return t

    def _suffix_misses(self, suffix: str, path: str) -> bool:
        try:
            return self.repo.resolve_path(suffix, None) != path
        except RefError:
            return True

    def trace_span(
        self, old_path: str, origin: str | None, new_path: str, span: Span
    ) -> Part:
        a, b = span
        if a == b:
            return self.trace_line(old_path, origin, new_path, a, strict=True)
        if b < a:
            return Part(UNKNOWN, note=f"range {a}-{b} runs backwards")
        whole = self.trace_symbol(old_path, origin, new_path, a, b)
        if whole is not None:
            return whole
        ends = [
            self.trace_line(old_path, origin, new_path, n, strict=False) for n in span
        ]
        for status in (GONE, UNKNOWN):
            bad = [p for p in ends if p.status == status]
            if bad:
                return Part(status, note="; ".join(p.note for p in bad))
        na, nb = ends[0].span[0], ends[1].span[0]
        if nb < na:
            return Part(UNKNOWN, note=f"the ends of {a}-{b} are now out of order")
        return Part(OK if (na, nb) == span else STALE, (na, nb))

    def _def_line(self, path: str, sha: str | None, start: int, end: int) -> int:
        lines = self.repo.source_lines(path, sha)
        return next(
            (n for n in range(start, end + 1) if DEF_RE.match(lines[n - 1])), start
        )

    def trace_symbol(
        self, old_path: str, origin: str | None, new_path: str, a: int, b: int
    ) -> Part | None:
        """A range that covered a whole symbol (from its decorators or its def line) follows the symbol."""
        if not old_path.endswith((".py", ".pyi")):
            return None
        try:
            old = self.repo.python_symbols(old_path, origin)
            new = self.repo.python_symbols(new_path, None)
        except RefError:
            return None
        for name, (s, e) in old.items():
            if e != b or a not in (s, self._def_line(old_path, origin, s, e)):
                continue
            if name not in new:
                return None  # renamed or removed: trace the ends instead
            ns, ne = new[name]
            na = ns if a == s else self._def_line(new_path, None, ns, ne)
            return Part(OK if (na, ne) == (a, b) else STALE, (na, ne))
        return None

    def trace_line(
        self, old_path: str, origin: str | None, new_path: str, n: int, strict: bool
    ) -> Part:
        old = self.repo.source_lines(old_path, origin)
        if not 1 <= n <= len(old):
            return Part(
                UNKNOWN,
                note=f"{old_path} had {len(old)} lines when the doc line was written",
            )
        text = old[n - 1]
        weak = too_short(text)
        shown = repr(text.strip()) if text.strip() else "blank"
        if strict and weak:
            return Part(UNKNOWN, note=f"line {n} was {shown}: too little to identify")
        lm = self.line_map(old_path, origin, new_path)
        m = lm.get(n)
        if m is not None:
            return Part(OK if m == n else STALE, (m, m))
        if weak:
            return Part(UNKNOWN, note=f"line {n} ({shown}) was changed")
        # the diff rewrote the line; moved code reappears among the rewritten lines. Only a one-to-one
        # pairing is evidence: the copies of the text the diff dropped must match the copies it added
        # (a diff can drop and re-add an untouched line), else which copy survived is a guess
        new = self.repo.source_lines(new_path, None)
        want = _norm(text)
        hits = [j for j in lm.changed_new if _norm(new[j - 1]) == want]
        lost = [
            i
            for i in range(1, len(old) + 1)
            if lm.get(i) is None and _norm(old[i - 1]) == want
        ]
        moved = hits[0] if len(hits) == 1 and len(lost) == 1 else None
        sym = self.repo.enclosing_symbol(old_path, origin, n)
        if (
            moved is None and hits and sym
        ):  # several copies: pair them by enclosing symbol
            same_new = [
                j for j in hits if self.repo.enclosing_symbol(new_path, None, j) == sym
            ]
            same_old = [
                i
                for i in lost
                if self.repo.enclosing_symbol(old_path, origin, i) == sym
            ]
            if len(same_new) == 1 and same_old == [n]:
                moved = same_new[0]
        if moved is not None:
            return Part(OK if moved == n else STALE, (moved, moved), "moved")
        quoted = f"`{_short(text.strip())}`"
        if len(hits) > 1:
            return Part(UNKNOWN, note=f"{quoted} now appears {len(hits)} times")
        if hits:
            return Part(
                UNKNOWN,
                note=f"{quoted} was on {len(lost)} changed lines and is on 1 now: which one survived is unclear",
            )
        # still gone, but say so when the line was edited where it stood rather than removed
        near = lm.counterpart(n)
        if near is not None and _similar(text, new[near - 1]):
            return Part(
                GONE,
                note=f"{quoted} was rewritten in place: line {near} is now `{_short(new[near - 1].strip())}`",
            )
        return Part(GONE, note=f"{quoted} is gone")


def audit(files: list[Path], repo: Repo, cfg: Config) -> list[Traced]:
    """Trace every number-only citation in FILES."""
    tracer = Tracer(repo)
    docs = number_only(files, Syntax(cfg))
    tracer.prefetch(
        [(doc, [line_of(text, c.start) for c in cs]) for doc, text, cs in docs]
    )
    return [tracer.trace(doc, text, c) for doc, text, cs in docs for c in cs]


def number_only(
    files: list[Path], syntax: Syntax
) -> list[tuple[DocHistory, str, list[Citation]]]:
    """The documents that hold number-only citations: history, text, and those citations."""
    out = []
    for f in files:
        text = f.read_bytes().decode("utf-8")
        cites = [
            c for c in extract(text, f, syntax)[0] if c.kind in ("legacy", "bare-link")
        ]
        if cites:
            out.append((DocHistory(f), text, cites))
    return out
