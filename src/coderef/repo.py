"""Read access to the cited code: tracked files, their lines, and symbol spans — at HEAD or a pinned commit."""

from __future__ import annotations

import ast
import os
import re
import subprocess
import threading
from pathlib import Path

from .errors import RefError

Span = tuple[int, int]
# coderef only reads: no opportunistic index refresh (git diff would otherwise rewrite .git/index),
# and no fetching of missing blobs in a partial clone (which would write packs and go to the network)
GIT_ENV = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_NO_LAZY_FETCH": "1"}


def split_lines(text: str) -> tuple[str, ...]:
    """Lines as git and editors number them: split on \\n only (str.splitlines also splits on \\f, \\u2028, …)."""
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    return tuple(line.removesuffix("\r") for line in lines)


class _Blobs:
    """One long-lived `git cat-file --batch` for reading old file versions.

    A process per `git show` costs tens of milliseconds on some systems (Windows), and an audit
    reads thousands of versions.
    """

    def __init__(self, root: Path):
        self.root = root
        self.proc: subprocess.Popen | None = None
        self.lock = threading.Lock()

    def read(self, rev_path: str) -> bytes | None:
        with self.lock:
            if self.proc is None:
                self.proc = subprocess.Popen(
                    ["git", "cat-file", "--batch"],
                    cwd=self.root,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    env=GIT_ENV,
                )
            self.proc.stdin.write(rev_path.encode("utf-8") + b"\n")
            self.proc.stdin.flush()
            header = self.proc.stdout.readline().split()
            if len(header) != 3:  # "<name> missing" / "<name> ambiguous"
                return None
            data = self.proc.stdout.read(int(header[2]))
            self.proc.stdout.read(1)  # the newline after the content
            return data if header[1] == b"blob" else None

    def close(self) -> None:
        with self.lock:
            if self.proc is not None:
                self.proc.stdin.close()
                self.proc.wait()
                self.proc = None


class Repo:
    """The git repository whose code the docs cite. `sha=None` means the working tree."""

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self._tracked: dict[str | None, tuple[str, ...]] = {}
        self._tracked_set: dict[str | None, frozenset[str]] = {}
        self._by_name: dict[str | None, dict[str, list[str]]] = {}  # basename -> paths
        self._resolved: dict[tuple[str, str | None], str | RefError] = {}
        self._lines: dict[tuple[str, str | None], tuple[str, ...]] = {}
        self._symbols: dict[tuple[str, str | None], dict[str, Span]] = {}
        self._blobs = _Blobs(self.root)
        self._mainline: list[tuple[int, str]] | None = None
        self._rename_log: tuple[dict[str, int], list[tuple[int, str, str]]] | None = (
            None
        )

    def close(self) -> None:
        self._blobs.close()

    def git(self, *args: str) -> str:
        out = subprocess.run(
            ["git", *args], cwd=self.root, capture_output=True, env=GIT_ENV
        )
        if out.returncode:
            err = out.stderr.decode("utf-8", "replace").strip()
            raise RefError(f"git {' '.join(args)}: {err}")
        return out.stdout.decode(
            "utf-8", "replace"
        )  # history holds files in other encodings

    def toplevel(self) -> Path:
        return Path(self.git("rev-parse", "--show-toplevel").strip()).resolve()

    def tracked(self, sha: str | None) -> tuple[str, ...]:
        if sha not in self._tracked:
            if sha is None:
                # untracked-but-not-ignored files count: docs are often written before the commit;
                # files deleted from the working tree do not
                out = self.git(
                    "ls-files", "-z", "--cached", "--others", "--exclude-standard"
                )
                paths = {p for p in out.split("\0") if p and (self.root / p).is_file()}
            else:
                out = self.git("ls-tree", "-r", "-z", "--name-only", sha)
                paths = {p for p in out.split("\0") if p}
            self._tracked[sha] = tuple(sorted(paths))
            self._tracked_set[sha] = frozenset(paths)
            by_name: dict[str, list[str]] = {}
            for p in self._tracked[sha]:
                by_name.setdefault(p.rsplit("/", 1)[-1], []).append(p)
            self._by_name[sha] = by_name
        return self._tracked[sha]

    def has(self, path: str, sha: str | None) -> bool:
        self.tracked(sha)
        return path in self._tracked_set[sha]

    def resolve_path(self, suffix: str, sha: str | None) -> str:
        suffix = suffix.strip().removeprefix("./")
        key = (suffix, sha)
        if key not in self._resolved:
            self.tracked(sha)
            same_name = self._by_name[sha].get(suffix.rsplit("/", 1)[-1], [])
            hits = [p for p in same_name if p == suffix or p.endswith("/" + suffix)]
            if not hits:
                self._resolved[key] = RefError(
                    f"no tracked file ends with {suffix!r}"
                    + (f" at {sha[:10]}" if sha else "")
                )
            elif len(hits) > 1:
                self._resolved[key] = RefError(
                    f"{suffix!r} is ambiguous: {', '.join(hits[:5])}"
                )
            else:
                self._resolved[key] = hits[0]
        hit = self._resolved[key]
        if isinstance(hit, RefError):
            raise hit
        return hit

    def source_lines(self, path: str, sha: str | None) -> tuple[str, ...]:
        key = (path, sha)
        if key not in self._lines:
            if sha is None:
                data = (self.root / path).read_bytes()
            else:
                data = self._blobs.read(
                    f"{sha}:./{path}"
                )  # ./ : code_root may be a subdirectory
                if data is None:
                    raise RefError(f"{path} cannot be read at {sha[:10]}")
            # history holds files in other encodings
            self._lines[key] = split_lines(data.decode("utf-8", "replace"))
        return self._lines[key]

    def follow(self, path: str, sha: str) -> str | None:
        """Where PATH as of SHA is in the working tree, following the renames committed since; None if gone.

        One `git log` over the whole history finds every rename: per-commit diffs are small, while a
        diff from each old commit to today would redo rename detection over the whole tree every time.
        """
        if self.has(path, None):
            return path
        if self._rename_log is None:
            order = {
                s: k for k, s in enumerate(self.git("rev-list", "HEAD").split())
            }  # 0 = newest
            events: list[tuple[int, str, str]] = []
            at = None
            log = self.git(
                "-c", "core.quotepath=false", "log", "-M", "--relative",
                "--diff-filter=R", "--name-status", "--format=@%H", "HEAD",
            )  # fmt: skip
            for row in log.split("\n"):
                if row.startswith("@"):
                    at = order.get(row[1:])
                elif row.startswith("R") and at is not None:
                    _, old, new = row.split("\t")
                    events.append((at, old, new))
            events.sort(key=lambda e: -e[0])  # oldest first
            self._rename_log = (order, events)
        order, events = self._rename_log
        start = order.get(sha)
        if start is None:
            return None
        for at, old, new in events:
            if at < start and old == path:  # renamed after SHA
                path = new
        return path if self.has(path, None) else None

    def commit_before(self, when: int) -> str | None:
        """The commit HEAD's first-parent line was at on the unix time WHEN; None if there was none yet.

        First parent: a feature-branch commit merged later was not yet what the mainline looked like.
        Committer dates, as `git rev-list --before` compares them.
        """
        if self._mainline is None:
            out = self.git("log", "--first-parent", "--format=%ct %H", "HEAD")
            self._mainline = [
                (int(t), s) for t, s in (row.split() for row in out.split("\n") if row)
            ]
        return next((s for t, s in self._mainline if t <= when), None)

    def enclosing_symbol(
        self, path: str, sha: str | None, line: int, end: int | None = None
    ) -> str | None:
        """The innermost Python symbol whose span holds LINE (through END); None outside Python or any symbol."""
        if not path.endswith((".py", ".pyi")):
            return None
        try:
            syms = self.python_symbols(path, sha)
        except RefError:
            return None
        end = line if end is None else end
        best: tuple[str, Span] | None = None
        for name, (a, b) in syms.items():
            if (
                a <= line
                and end <= b
                and (best is None or b - a < best[1][1] - best[1][0])
            ):
                best = (name, (a, b))
        return best[0] if best else None

    def python_symbols(self, path: str, sha: str | None) -> dict[str, Span]:
        key = (path, sha)
        if key not in self._symbols:
            try:
                tree = ast.parse("\n".join(self.source_lines(path, sha)))
            except SyntaxError as e:
                raise RefError(
                    f"{path}: cannot parse ({e.msg}, line {e.lineno})"
                ) from e
            out: dict[str, Span] = {}

            def visit(body, prefix):
                for node in body:
                    if isinstance(
                        node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                    ):
                        start = min(
                            [node.lineno] + [d.lineno for d in node.decorator_list]
                        )
                        name = prefix + node.name
                        out[name] = (start, node.end_lineno)
                        visit(node.body, name + ".")
                    elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                        targets = (
                            node.targets
                            if isinstance(node, ast.Assign)
                            else [node.target]
                        )
                        for t in targets:
                            if isinstance(t, ast.Name):
                                out[prefix + t.id] = (node.lineno, node.end_lineno)

            visit(tree.body, "")
            self._symbols[key] = out
        return self._symbols[key]

    def symbol_span(self, path: str, sha: str | None, symbol: str | None) -> Span:
        lines = self.source_lines(path, sha)
        if not symbol:
            return 1, len(lines)
        if path.endswith((".py", ".pyi")):
            syms = self.python_symbols(path, sha)
            if symbol in syms:
                return syms[symbol]
            hits = [k for k in syms if k.endswith("." + symbol)]
            if len(hits) == 1:
                return syms[hits[0]]
            if not hits:
                raise RefError(f"symbol {symbol!r} not in {path}")
            raise RefError(
                f"symbol {symbol!r} is ambiguous in {path}: {', '.join(hits)}"
            )
        if path.endswith((".yml", ".yaml")):
            return key_span(lines, symbol)
        raise RefError(
            f"symbols are supported in .py and .yaml files; cite {path} by quote only"
        )


def key_span(lines: tuple[str, ...], dotted: str) -> Span:
    """Span of a dotted key path in an indentation-structured file (YAML)."""
    lo, hi, indent = 1, len(lines), -1
    for key in dotted.split("."):
        pat = re.compile(r"^(\s*)(?:-\s+)?[\"']?" + re.escape(key) + r"[\"']?\s*:")
        for n in range(lo, hi + 1):
            m = pat.match(lines[n - 1])
            if m and len(m.group(1)) > indent:
                indent = len(m.group(1))
                end = n
                for k in range(n + 1, hi + 1):
                    s = lines[k - 1]
                    if s.strip() and len(s) - len(s.lstrip()) <= indent:
                        break
                    if s.strip():
                        end = k
                lo, hi = n, end
                break
        else:
            raise RefError(f"key {key!r} not found")
    return lo, hi
