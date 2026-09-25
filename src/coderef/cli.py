"""coderef command line.

    check [FILES]          drifted numbers, broken anchors, un-anchored citations; exit 1 if any
    sync  [FILES]          rewrite drifted numbers from the source, then report what still fails
    affected REV [FILES]   doc lines whose citations point into code changed since REV — re-read these
    list  [FILES]          every citation and the line it resolves to
    where SPEC             what a spec resolves to
    locate PATH LINE       enclosing symbol and a proposed spec for a path:line citation (--at SHA for history)

Documents default to `docs` in the config; the cited code is `code_root` (or --root).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .affected import affected
from .config import Config, load
from .errors import ConfigError, RefError
from .repo import Repo
from .scan import Syntax, display, extract, line_of, scan_files
from .spec import format_quote, render, resolve


def _files(args, cfg: Config) -> list[Path]:
    files = list(args.files) if getattr(args, "files", None) else cfg.doc_files()
    if not files:
        raise ConfigError(
            "no documents: pass FILES or set `docs` in .coderef.toml / [tool.coderef]"
        )
    missing = [str(f) for f in files if not f.is_file()]
    if missing:
        raise ConfigError(f"not a file: {', '.join(missing)}")
    return files


def cmd_check(args, cfg: Config, repo: Repo, write: bool) -> int:
    found = scan_files(_files(args, cfg), repo, cfg, write)
    if write:
        for line in found.drift:
            print("synced ", line)
        found.drift = []
    for key in ("broken", "drift", "legacy"):
        for line in getattr(found, key):
            print(f"{key:7}", line)
    print(
        f"\nanchors {found.anchors} · broken {len(found.broken)} · drift {len(found.drift)}"
        f" · legacy {len(found.legacy)}"
        + (" (not failing)" if cfg.legacy == "warn" and found.legacy else "")
    )
    failing_legacy = found.legacy if cfg.legacy == "error" else []
    return 1 if (found.broken or found.drift or failing_legacy) else 0


def cmd_list(args, cfg: Config, repo: Repo) -> int:
    syntax = Syntax(cfg)
    for f in _files(args, cfg):
        text = f.read_bytes().decode("utf-8")
        cites, _ = extract(text, f, syntax)
        for c in cites:
            where = f"{display(f)}:{line_of(text, c.start)}"
            if c.kind == "marker":
                print(f"{where}\t(not code)")
                continue
            if c.kind == "bare-link":
                print(f"{where}\tLEGACY\t{text[c.start : c.end]}")
                continue
            if c.error:
                print(f"{where}\tBROKEN\t{c.error}")
                continue
            spec = c.spec
            try:
                t, a, b = resolve(repo, spec)
                print(
                    f"{where}\t{render(a, b)}\t{t.lines()[a - 1].strip()[:100]}\t[{spec[:80]}]"
                )
            except RefError as e:
                print(f"{where}\tBROKEN\t{e}")
    return 0


def cmd_where(args, repo: Repo) -> int:
    t, a, b = resolve(repo, args.spec)
    print(f"{t.path}{' @' + t.sha if t.sha else ''}: {render(a, b)}")
    for n in range(a, min(b, a + 40) + 1):
        print(f"{n:5}  {t.lines()[n - 1]}")
    return 0


def cmd_locate(args, repo: Repo) -> int:
    sha = args.at
    path = repo.resolve_path(args.path, sha)
    lines = repo.source_lines(path, sha)
    if not 1 <= args.line <= len(lines):
        raise RefError(f"{path} has {len(lines)} lines")
    text = lines[args.line - 1]
    enclosing = None
    if path.endswith((".py", ".pyi")):
        best = None
        for name, (a, b) in repo.python_symbols(path, sha).items():
            if a <= args.line <= b and (best is None or b - a < best[1] - best[0]):
                best, enclosing = (a, b), name
    spec = (
        args.path
        + (f"::{enclosing}" if enclosing else "")
        + " "
        + format_quote(text.strip())
    )
    print(f"{path}:{args.line}{' @' + sha if sha else ''}  in {enclosing}")
    print(f"    {text}")
    print(f"spec  {spec}")
    title = f"{enclosing or ''}: {text.strip()}".replace("\\", "\\\\").replace(
        '"', '\\"'
    )
    print(
        f'link  [{path.rsplit("/", 1)[-1]}:{args.line}]({path}#L{args.line} "{title}")'
    )
    try:
        _, a, b = resolve(repo, spec)
        print(
            f"HEAD  {render(a, b)}  {repo.source_lines(repo.resolve_path(args.path, None), None)[a - 1].strip()}"
        )
    except RefError as e:
        print(f"HEAD  BROKEN: {e}")
    return 0


def cmd_affected(args, cfg: Config, repo: Repo) -> int:
    hits = affected(_files(args, cfg), repo, cfg, args.rev)
    for h in hits:
        if h.error:
            print(f"{h.doc}:{h.line}\tBROKEN\t{h.error}")
        else:
            print(
                f"{h.doc}:{h.line}\t{h.path}:{h.span[0]}-{h.span[1]}"
                f"\tchanged {h.touched[0]}..{h.touched[-1]} ({len(h.touched)} lines)"
            )
    n = sum(1 for h in hits if not h.error)
    print(f"\n{n} doc citation(s) point into code changed since {args.rev}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--config",
        type=Path,
        help="config file (default: ./.coderef.toml or ./pyproject.toml)",
    )
    common.add_argument(
        "--root",
        type=Path,
        help="git repository of the cited code (overrides code_root)",
    )

    parser = argparse.ArgumentParser(
        prog="coderef", description=__doc__.split("\n\n")[0], parents=[common]
    )
    parser.add_argument("--version", action="version", version=f"coderef {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name, text in (
        ("check", "report drift, broken anchors and un-anchored citations"),
        ("sync", "rewrite drifted numbers, then report what still fails"),
        ("list", "every citation and what it resolves to"),
    ):
        p = sub.add_parser(name, parents=[common], help=text)
        p.add_argument("files", nargs="*", type=Path)
    p = sub.add_parser(
        "affected", parents=[common], help="doc lines citing code changed since REV"
    )
    p.add_argument("rev")
    p.add_argument("files", nargs="*", type=Path)
    p = sub.add_parser("where", parents=[common], help="what a spec resolves to")
    p.add_argument("spec")
    p = sub.add_parser(
        "locate", parents=[common], help="propose a spec for a path:line citation"
    )
    p.add_argument("path")
    p.add_argument("line", type=int)
    p.add_argument("--at", metavar="SHA", help="resolve the line at this commit")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    args = build_parser().parse_args(argv)
    try:
        cfg = load(args.config, args.root, Path.cwd())
        repo = Repo(cfg.code_root)
        if args.cmd in ("check", "sync"):
            return cmd_check(args, cfg, repo, write=args.cmd == "sync")
        if args.cmd == "list":
            return cmd_list(args, cfg, repo)
        if args.cmd == "affected":
            return cmd_affected(args, cfg, repo)
        if args.cmd == "where":
            return cmd_where(args, repo)
        if args.cmd == "locate":
            return cmd_locate(args, repo)
    except (RefError, ConfigError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    sys.exit(main())
