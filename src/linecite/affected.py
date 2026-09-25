"""After a code change, list the doc lines whose citations point into the changed code.

`sync` keeps the numbers right; whether the prose around them is still true needs a human.
This narrows the re-read to the citations whose code actually changed.
"""

from __future__ import annotations

import codecs
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .errors import RefError
from .repo import Repo
from .scan import LINK_RE, Syntax, display, extract, line_of
from .spec import format_quote, parse_target, resolve

HUNK_RE = re.compile(r"\+(\d+)(?:,(\d+))?")
# what a reader sees of a doc line: links reduced to their text, anchors dropped
ANCHOR_RE = re.compile(r"<!--@.*?-->")
EXCERPT_CHARS = 120


def require_commit(repo: Repo, rev: str) -> None:
    """Fail with the fix when REV (or an end of A..B / A...B) is missing — in CI, usually a shallow clone."""
    for end in filter(None, re.split(r"\.\.\.?", rev, maxsplit=1)):
        try:
            repo.git(
                "rev-parse",
                "--verify",
                "--quiet",
                "--end-of-options",
                end + "^{commit}",
            )
        except RefError:
            hint = ""
            if repo.git("rev-parse", "--is-shallow-repository").strip() == "true":
                name = end.removeprefix(
                    "origin/"
                )  # the refspec names the remote's branch
                hint = (
                    f"; this is a shallow clone: fetch it (git fetch --depth=1 origin {name})"
                    " or check out full history (actions/checkout with fetch-depth: 0)"
                )
            raise RefError(f"{end!r} is not a commit in {repo.root}{hint}") from None


def _diff_path(line: str) -> str | None:
    """The new-side path of a `+++ ` line; git C-quotes paths with unusual characters."""
    path = line[4:]
    if path.startswith('"') and path.endswith('"'):
        path = codecs.escape_decode(path[1:-1])[0].decode("utf-8", "replace")
    return path[2:] if path.startswith("b/") else None


def changed_lines(repo: Repo, rev: str) -> dict[str, set[int]]:
    """Working-tree line numbers changed since REV, per file (committed, staged and unstaged edits).

    REV may be a range (A..B, A...B): then git compares commits, not the working tree.
    """
    require_commit(repo, rev)
    out: dict[str, set[int]] = {}
    current = None
    for line in repo.git(
        "-c",
        "core.quotepath=false",
        "diff",
        "-U0",
        "--no-color",
        "--relative",
        rev,
        "--",
    ).splitlines():
        if line.startswith("+++ "):
            current = _diff_path(line)
        elif line.startswith("@@") and current:
            m = HUNK_RE.search(line)
            start, count = int(m.group(1)), int(m.group(2) or 1)
            if count == 0:  # pure deletion between line `start` and `start + 1`
                out.setdefault(current, set()).update((start, start + 1))
            else:
                out.setdefault(current, set()).update(range(start, start + count))
    return out


@dataclass
class Hit:
    doc: str
    line: int
    path: str
    span: tuple[int, int]
    touched: list[int]
    error: str | None = None
    source: Path | None = None  # the document
    excerpt: str = ""  # its citing line, as a reader sees it


def affected(files: list[Path], repo: Repo, cfg: Config, rev: str) -> list[Hit]:
    changed = changed_lines(repo, rev)
    syntax = Syntax(cfg)
    hits: list[Hit] = []
    for f in files:
        text = f.read_bytes().decode("utf-8")
        cites, _ = extract(text, f, syntax)
        seen = set()
        for c in cites:
            spec = c.spec
            if (
                c.kind not in ("anchor", "link", "symref")
                or c.error
                or spec.startswith("@")
            ):
                continue  # not code, malformed (check reports it), or pinned history that cannot change
            ln = line_of(text, c.start)
            where = {"source": f, "excerpt": excerpt(text, c.start)}
            try:
                target, rest = parse_target(repo, spec)
                # the whole enclosing symbol counts: a change next to the quoted line can change its meaning;
                # without a symbol only the quoted lines count — a whole-file span would flag every change
                a, b = target.span
                if "::" not in spec and rest:
                    _, a, b = resolve(repo, spec)
            except RefError as e:
                hits.append(Hit(display(f), ln, "", (0, 0), [], str(e), **where))
                continue
            touched = sorted(n for n in changed.get(target.path, ()) if a <= n <= b)
            key = (ln, target.path, a, b)
            if touched and key not in seen:
                seen.add(key)
                hits.append(Hit(display(f), ln, target.path, (a, b), touched, **where))
    return hits


def excerpt(text: str, pos: int) -> str:
    """The document line holding POS, as a reader sees it, cut to EXCERPT_CHARS."""
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    line = text[start : end if end >= 0 else len(text)]
    line = " ".join(ANCHOR_RE.sub("", LINK_RE.sub(r"\g<text>", line)).split())
    return line if len(line) <= EXCERPT_CHARS else line[: EXCERPT_CHARS - 1] + "…"


def _lines(ns: list[int] | tuple[int, int]) -> str:
    a, b = ns[0], ns[-1]
    return str(a) if a == b else f"{a}-{b}"


def _runs(ns: list[int], most: int = 4) -> str:
    """Sorted line numbers as runs: `7, 11`, `10-12, 20`; more than MOST runs end in a count."""
    runs: list[list[int]] = []
    for n in ns:
        if runs and n == runs[-1][-1] + 1:
            runs[-1].append(n)
        else:
            runs.append([n])
    shown = ", ".join(_lines(r) for r in runs[:most])
    return shown if len(runs) <= most else f"{shown}, … ({len(ns)} lines)"


def markdown(
    hits: list[Hit], rev: str, link: Callable[[Hit], str | None] = lambda h: None
) -> str:
    """The re-read list as markdown, for a pull request comment or a CI summary.

    LINK gives a URL for a hit's doc line, when there is one to give.
    """
    if re.fullmatch(r"[0-9a-f]{40}", rev):
        rev = rev[:12]
    if not hits:
        return f"No doc citation points into code changed since `{rev}`.\n"
    found = [h for h in hits if not h.error]
    out = [
        f"**{len(found)} doc citation(s) point into code changed since `{rev}`.** "
        "Re-read the sentences around them: `linecite sync` keeps line numbers right, "
        "not what the prose says about the code."
    ]
    by_doc: dict[str, list[Hit]] = {}
    for h in found:
        by_doc.setdefault(h.doc, []).append(h)
    for doc, rows in by_doc.items():
        out.append(f"\n`{doc}`\n")
        for h in rows:
            url = link(h)
            where = f"[line {h.line}]({url})" if url else f"line {h.line}"
            out.append(
                f"- {where}: {format_quote(h.excerpt)}"
                f" — cites `{h.path}:{_lines(h.span)}`, changed {_runs(h.touched)}"
            )
    broken = [h for h in hits if h.error]
    if broken:
        out.append(
            "\nCitations that no longer resolve (`linecite check` reports them):\n"
        )
        out += [f"- `{h.doc}:{h.line}`: {format_quote(h.error)}" for h in broken]
    return "\n".join(out) + "\n"
