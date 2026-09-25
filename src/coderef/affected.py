"""After a code change, list the doc lines whose citations point into the changed code.

`sync` keeps the numbers right; whether the prose around them is still true needs a human.
This narrows the re-read to the citations whose code actually changed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .errors import RefError
from .repo import Repo
from .scan import Syntax, display, extract, line_of
from .spec import parse_target, resolve

HUNK_RE = re.compile(r"\+(\d+)(?:,(\d+))?")


def changed_lines(repo: Repo, rev: str) -> dict[str, set[int]]:
    """Working-tree line numbers changed since REV, per file (committed, staged and unstaged edits)."""
    out: dict[str, set[int]] = {}
    current = None
    for line in repo.git("diff", "-U0", "--no-color", "--relative", rev, "--").splitlines():
        if line.startswith("+++ "):
            current = line[6:] if line.startswith("+++ b/") else None
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
            try:
                target, rest = parse_target(repo, spec)
                # the whole enclosing symbol counts: a change next to the quoted line can change its meaning;
                # without a symbol only the quoted lines count — a whole-file span would flag every change
                a, b = target.span
                if "::" not in spec and rest:
                    _, a, b = resolve(repo, spec)
            except RefError as e:
                hits.append(Hit(display(f), ln, "", (0, 0), [], str(e)))
                continue
            touched = sorted(n for n in changed.get(target.path, ()) if a <= n <= b)
            key = (ln, target.path, a, b)
            if touched and key not in seen:
                seen.add(key)
                hits.append(Hit(display(f), ln, target.path, (a, b), touched))
    return hits
