"""What git history knows about a citation: when its doc line was written, and where an old line went.

A number-only citation (`orders.py:10`) records nothing about the code it meant, but git usually knows
when the doc line was written — and the code as it was then is what the number described.
"""

from __future__ import annotations

import difflib
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .repo import GIT_ENV

UNCOMMITTED = "0" * 40
BLAME_HEADER_RE = re.compile(r"([0-9a-f]{40}) \d+ (\d+)")


@dataclass(frozen=True)
class Written:
    """The doc commit that last wrote a line (None: not committed yet) and its author time.

    `cut`: blame stopped at the edge of a shallow clone, so the line may be older than `sha`.
    """

    sha: str | None
    time: int | None
    cut: bool = False


def _git(cwd: Path, *args: str) -> str | None:
    out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, env=GIT_ENV)
    if out.returncode:
        return None
    return out.stdout.decode("utf-8", "replace")


class DocHistory:
    """git blame for one document. `root` is None when the document is not in a git repository."""

    def __init__(self, path: Path):
        self.path = Path(path).resolve()
        top = _git(self.path.parent, "rev-parse", "--show-toplevel")
        self.root = Path(top.strip()).resolve() if top else None
        self._written: dict[int, Written] | None = None

    def written(self, line: int) -> Written | None:
        """When LINE was written; None when there is no history to ask."""
        if self.root is None:
            return None
        if self._written is None:
            self._written = self._blame()
        return self._written.get(line, Written(None, None))

    def _blame(self) -> dict[int, Written]:
        cwd, name = self.path.parent, self.path.name
        if _git(cwd, "ls-files", "--error-unmatch", "--", name) is None:
            return {}  # untracked: every line is uncommitted
        # -w: whitespace-only edits keep the original date; -M: lines moved within the doc too
        out = _git(cwd, "blame", "--porcelain", "-w", "-M", "--", name) or ""
        shallow = (_git(cwd, "rev-parse", "--is-shallow-repository") or "").strip()
        times: dict[str, int] = {}
        shas: dict[int, str] = {}
        boundary: set[str] = (
            set()
        )  # root commits, and the cut-off commits of a shallow clone
        sha = None
        for row in out.split("\n"):
            if row.startswith("\t"):
                continue
            m = BLAME_HEADER_RE.match(row)
            if m:
                sha = m.group(1)
                shas[int(m.group(2))] = sha
            elif row.startswith("author-time ") and sha:
                times[sha] = int(row.split()[1])
            elif row == "boundary" and sha:
                boundary.add(sha)
        return {
            n: Written(None, None)
            if s == UNCOMMITTED
            else Written(s, times.get(s), shallow == "true" and s in boundary)
            for n, s in shas.items()
        }


class LineMap:
    """Where the lines of an old version of a file sit in a new one.

    Lines the diff leaves unchanged map exactly, however common their text; lines it changed map to
    nothing, and the new side's changed lines are kept to look for moved code among them.
    """

    def __init__(self, old: tuple[str, ...], new: tuple[str, ...]):
        self._equal: list[tuple[int, int, int]] = []
        self._replaced: list[tuple[int, int, int, int]] = []
        # 1-based lines of NEW that the diff inserted or rewrote
        self.changed_new: list[int] = []
        if old == new:
            self._equal.append((0, len(old), 0))
            return
        # the unchanged head and tail need no diffing; edits are usually local, and difflib is slow
        head = 0
        while head < min(len(old), len(new)) and old[head] == new[head]:
            head += 1
        tail = 0
        while (
            tail < min(len(old), len(new)) - head
            and old[len(old) - 1 - tail] == new[len(new) - 1 - tail]
        ):
            tail += 1
        if head:
            self._equal.append((0, head, 0))
        matcher = difflib.SequenceMatcher(
            None,
            old[head : len(old) - tail],
            new[head : len(new) - tail],
            autojunk=False,
        )
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            i1, i2, j1, j2 = i1 + head, i2 + head, j1 + head, j2 + head
            if tag == "equal":
                self._equal.append((i1, i2, j1))
            else:
                self.changed_new.extend(range(j1 + 1, j2 + 1))
                if tag == "replace":
                    self._replaced.append((i1, i2, j1, j2))
        if tail:
            self._equal.append((len(old) - tail, len(old), len(new) - tail))

    def get(self, n: int) -> int | None:
        for i1, i2, j1 in self._equal:
            if i1 < n <= i2:
                return j1 + n - i1
        return None

    def counterpart(self, n: int) -> int | None:
        """The new line in the same place as old line N when the diff rewrote the block holding it."""
        for i1, i2, j1, j2 in self._replaced:
            if i1 < n <= i2 and n - i1 <= j2 - j1:
                return j1 + n - i1
        return None
