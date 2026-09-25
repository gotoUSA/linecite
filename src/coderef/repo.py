"""Read access to the cited code: tracked files, their lines, and symbol spans — at HEAD or a pinned commit."""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

from .errors import RefError

Span = tuple[int, int]


class Repo:
    """The git repository whose code the docs cite. `sha=None` means the working tree."""

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self._tracked: dict[str | None, tuple[str, ...]] = {}
        self._lines: dict[tuple[str, str | None], tuple[str, ...]] = {}
        self._symbols: dict[tuple[str, str | None], dict[str, Span]] = {}

    def git(self, *args: str) -> str:
        out = subprocess.run(["git", *args], cwd=self.root, capture_output=True)
        if out.returncode:
            err = out.stderr.decode("utf-8", "replace").strip()
            raise RefError(f"git {' '.join(args)}: {err}")
        return out.stdout.decode("utf-8")

    def tracked(self, sha: str | None) -> tuple[str, ...]:
        if sha not in self._tracked:
            if (
                sha is None
            ):  # untracked-but-not-ignored files count: docs are often written before the commit
                out = self.git(
                    "ls-files", "-z", "--cached", "--others", "--exclude-standard"
                )
            else:
                out = self.git("ls-tree", "-r", "-z", "--name-only", sha)
            self._tracked[sha] = tuple(sorted({p for p in out.split("\0") if p}))
        return self._tracked[sha]

    def resolve_path(self, suffix: str, sha: str | None) -> str:
        suffix = suffix.strip().removeprefix("./")
        hits = [p for p in self.tracked(sha) if p == suffix or p.endswith("/" + suffix)]
        if not hits:
            raise RefError(
                f"no tracked file ends with {suffix!r}" + (f" at {sha}" if sha else "")
            )
        if len(hits) > 1:
            raise RefError(f"{suffix!r} is ambiguous: {', '.join(hits[:5])}")
        return hits[0]

    def source_lines(self, path: str, sha: str | None) -> tuple[str, ...]:
        key = (path, sha)
        if key not in self._lines:
            if sha is None:
                text = (self.root / path).read_text(encoding="utf-8")
            else:
                text = self.git("show", f"{sha}:{path}")
            self._lines[key] = tuple(text.splitlines())
        return self._lines[key]

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
