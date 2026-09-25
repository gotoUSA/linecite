"""coderef command line.

    check [FILES]          drifted numbers, broken anchors, un-anchored citations; exit 1 if any
    sync  [FILES]          rewrite drifted numbers from the source, then report what still fails
    affected REV [FILES]   doc lines whose citations point into code changed since REV — re-read these
    list  [FILES]          every citation and the line it resolves to
    where SPEC             what a spec resolves to
    locate PATH LINE       enclosing symbol and a proposed spec for a path:line citation (--at SHA for history)
    audit [FILES]          number-only citations traced through git history: ok, stale, gone, unknown
    adopt [FILES] [--write]  convert number-only citations into links (markdown) or anchors (other)

Documents default to `docs` in the config; the cited code is `code_root` (or --root).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .adopt import Proposal, plan, title_attr, write
from .affected import affected
from .audit import GONE, STALE, STATUSES, UNVERIFIABLE, Traced, audit
from .config import Config, load
from .errors import ConfigError, RefError
from .repo import Repo
from .scan import Syntax, display, extract, line_of, scan_files
from .spec import Naming, describe, render, resolve


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
            if c.kind in ("bare-link", "legacy"):
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
    named = describe(repo, path, args.line, args.line, sha)
    spec = Naming(args.path, named.symbol, named.quotes).spec()  # the path as typed
    print(f"{path}:{args.line}{' @' + sha if sha else ''}  in {named.symbol}")
    print(f"    {lines[args.line - 1]}")
    print(f"spec  {spec}")
    print(
        f"link  [{path.rsplit('/', 1)[-1]}:{args.line}]({path}#L{args.line} "
        f"{title_attr(named.title())})"
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


def _traced_row(t: Traced) -> str:
    note = [t.note] if t.note else []
    if t.status != UNVERIFIABLE:
        note.append(
            f"written against {t.origin[:10]}"
            if t.origin
            else "not committed: read against the working tree"
        )
    now = f" -> {t.now}" if t.status == STALE else ""
    return f"{display(t.doc)}:{t.line}\t{t.status}\t{t.written}{now}\t{'; '.join(note)}"


def _tally(traced: list[Traced]) -> str:
    counts = " · ".join(f"{s} {sum(t.status == s for t in traced)}" for s in STATUSES)
    return f"number-only citations {len(traced)} · {counts}"


def cmd_audit(args, cfg: Config, repo: Repo) -> int:
    traced = audit(_files(args, cfg), repo, cfg)
    for t in traced:
        print(_traced_row(t))
    print(f"\n{_tally(traced)}")
    print(
        "estimates: each doc line is dated by git blame; a later edit to the line moves its date"
    )
    return 1 if any(t.status in (STALE, GONE) for t in traced) else 0


def cmd_adopt(args, cfg: Config, repo: Repo) -> int:
    plans = plan(_files(args, cfg), repo, cfg)
    converted: list[Traced] = []
    left = 0
    for p in plans:
        rows = [(x.traced.line, x) for x in p.proposals] + [
            (t.line, (t, why)) for t, why in p.left
        ]
        for _, row in sorted(rows, key=lambda r: r[0]):
            if isinstance(row, Proposal):
                t = row.traced
                converted.append(t)
                print(f"{display(p.path)}:{t.line}\t{t.status}\t{t.written}")
                print(f"\t=> {row.new}")
            else:
                t, why = row
                left += 1
                print(
                    f"{display(p.path)}:{t.line}\t{t.status}\t{t.written}\tleft as is: {why}"
                )
        if args.write and p.proposals:
            write(p)
    stale = sum(t.status == STALE for t in converted)
    print(
        f"\nadopt: {len(converted)} to convert (ok {len(converted) - stale} · stale {stale})"
        f" · {left} left for a human"
    )
    if args.write:
        print(f"written: {sum(bool(p.proposals) for p in plans)} file(s)")
    elif converted:
        print(
            "nothing written. Stale numbers are estimates from git blame: review them, then --write"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    # SUPPRESS: a subparser default would otherwise overwrite an option given before the subcommand
    common = argparse.ArgumentParser(add_help=False, argument_default=argparse.SUPPRESS)
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
        ("audit", "trace number-only citations through git history"),
    ):
        p = sub.add_parser(name, parents=[common], help=text)
        p.add_argument("files", nargs="*", type=Path)
    p = sub.add_parser(
        "adopt",
        parents=[common],
        help="convert number-only citations into links or anchors (dry run without --write)",
    )
    p.add_argument("files", nargs="*", type=Path)
    p.add_argument("--write", action="store_true", help="apply the proposals")
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


def run(args, cfg: Config, repo: Repo) -> int:
    if args.cmd in ("check", "sync"):
        return cmd_check(args, cfg, repo, write=args.cmd == "sync")
    commands = {
        "list": lambda: cmd_list(args, cfg, repo),
        "audit": lambda: cmd_audit(args, cfg, repo),
        "adopt": lambda: cmd_adopt(args, cfg, repo),
        "affected": lambda: cmd_affected(args, cfg, repo),
        "where": lambda: cmd_where(args, repo),
        "locate": lambda: cmd_locate(args, repo),
    }
    return commands[args.cmd]()


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    args = build_parser().parse_args(argv)
    try:
        cfg = load(
            getattr(args, "config", None), getattr(args, "root", None), Path.cwd()
        )
        repo = Repo(cfg.code_root)
        try:
            return run(args, cfg, repo)
        finally:
            repo.close()
    except (RefError, ConfigError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
