"""Find the code citations in a document, re-derive their line numbers, and report what drifted or broke.

Two citation forms carry what they point at, so their numbers can be re-derived:

    link    [orders.py:10](app/orders.py#L10 "create_order: row.lock()")
            a markdown link to a code line; the title names the symbol and a quoted fragment.
            `sync` rewrites the #L fragment and a trailing number in the link text.
    anchor  line 10<!--@ orders.py::create_order `row.lock()` -->
            a hidden comment right after the number it owns (HTML, prose numbers, code excerpts).

Symbol references in prose (`orders.py::OrderService.cancel`) are checked for existence.
Citations that carry only a number — `orders.py:10`, a #L link without a title — are reported as
legacy: nothing records which code they meant, so they cannot be re-derived.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config
from .errors import RefError
from .repo import Repo
from .spec import format_quote, render, resolve

FENCE_RE = re.compile(
    r"^[ \t]*(`{3,}|~{3,})[^\n]*\n.*?(?:^[ \t]*\1[ \t]*$|\Z)", re.M | re.S
)
MARKDOWN_SUFFIXES = (".md", ".markdown", ".mdx")
LINK_RE = re.compile(
    r"(?<!!)\[(?P<text>[^\]\n]*)\]\((?P<url>[^\s()]+)"
    r"(?:[ \t]+(?P<title>\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\\\n]|\\.)*'))?\)"
)
FRAGMENT_RE = re.compile(r"L(\d+)(?:-L(\d+))?")
TEXT_PATH_NUM_RE = re.compile(r":(\d+(?:[-~]\d+)?)$")
NUM_TOKEN_RE = re.compile(r"(?<![\d.])\d+(?:[-~]\d+)?(?![\d])")
TITLE_SIMPLE_RE = re.compile(r"(?P<sym>[\w.]*)(?:\s*:\s+(?P<quote>.+))?", re.S)
TITLE_QUOTED_RE = re.compile(r"(?P<sym>[\w.]*)\s*(?P<rest>[`「].*)", re.S)


class Syntax:
    """Regexes derived from the configuration."""

    def __init__(self, cfg: Config):
        alts = "|".join(
            re.escape(s) for s in sorted(cfg.number_suffixes, key=len, reverse=True)
        )
        self.anchor = re.compile(
            r"(?P<num>\d+(?:[-~]\d+)?)(?P<suf>(?:" + alts + r")?)<!--@(?P<spec>.*?)-->"
        )
        self.any_anchor = re.compile(r"<!--@(?P<spec>.*?)-->")
        self.symref = re.compile(
            r"`(?P<spec>(?:@[0-9a-f]{7,40}:)?[\w./-]+\.(?:py|pyi|ya?ml)::[\w.]+)`"
        )
        exts = "|".join(re.escape(e) for e in cfg.legacy_extensions)
        legacy = (
            r"(?P<pathref>(?<![\w/.-])[\w./-]+\.(?:" + exts + r"):\d+(?:[-~,]\d+)*)"
            r"(?!\d|[-~]\d|(?:" + alts + r")?<!--@)"
        )
        words = [s for s in cfg.number_suffixes if s[0].isalnum()]
        if words:  # a word suffix (e.g. "L") makes "310L" a citation in prose
            word_alts = "|".join(re.escape(w) for w in words)
            legacy += (
                r"|(?P<word>(?<![\d.])\d+(?:[-~·,]\d+)*(?:" + word_alts + r"))(?!<!--@)"
            )
        self.legacy = re.compile(legacy)
        self.pinned_pathref = re.compile(r"@[0-9a-f]{7,40}:[\w./-]+:\d")
        self.ignore = [re.compile(p, re.S) for p in cfg.ignore_patterns]


@dataclass(frozen=True)
class Slot:
    """A number in the document that is derived from the cited code."""

    start: int
    end: int
    old: str
    style: str  # "num" (10, 9-10, 9~10) or "fragment" (L10, L9-L10)

    def render(self, a: int, b: int) -> str:
        if self.style == "fragment":
            return f"L{a}" if a == b else f"L{a}-L{b}"
        return render(a, b, "~" if "~" in self.old else "-")


@dataclass
class Citation:
    kind: str  # "anchor" | "link" | "symref" | "marker" (a <!--@--> non-code number) | "bare-link" (no title)
    start: int
    end: int
    spec: str = ""
    slots: list[Slot] = field(default_factory=list)
    error: str | None = None  # the citation itself is malformed

    @property
    def span(self) -> tuple[int, int]:
        return self.start, self.end


@dataclass
class Findings:
    anchors: int = 0
    broken: list[str] = field(default_factory=list)
    drift: list[str] = field(default_factory=list)
    legacy: list[str] = field(default_factory=list)

    def extend(self, other: Findings) -> None:
        self.anchors += other.anchors
        self.broken += other.broken
        self.drift += other.drift
        self.legacy += other.legacy


def blank(text: str, spans: list[tuple[int, int]]) -> str:
    """Replace the spans with spaces, keeping newlines so positions and line numbers survive."""
    if not spans:
        return text
    chars = list(text)
    for a, b in spans:
        for k in range(a, b):
            if chars[k] != "\n":
                chars[k] = " "
    return "".join(chars)


def visible(text: str, path: Path, syntax: Syntax, skip_fences: bool = False) -> str:
    """The text with skipped regions blanked: configured ignore patterns, plus fenced code blocks if asked."""
    spans: list[tuple[int, int]] = []
    if skip_fences and path.suffix.lower() in MARKDOWN_SUFFIXES:
        spans += [m.span() for m in FENCE_RE.finditer(text)]
    for rx in syntax.ignore:
        spans += [m.span() for m in rx.finditer(text)]
    return blank(text, spans)


def line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def display(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def _link_path(url: str) -> str | None:
    """The code path a link points into, as a tracked-file suffix; None for links that are not code citations."""
    if re.match(
        r"[a-z][\w+.-]*:", url, re.I
    ):  # absolute URLs (https:, mailto:) are out of scope for now
        return None
    parts = [p for p in url.split("/") if p not in ("", ".", "..")]
    return "/".join(parts) or None


def title_spec(path: str, title: str) -> str:
    """Build a spec from a link title: `symbol: fragment`, `symbol`, `: fragment`, or the backtick grammar."""
    t = title.strip()
    m = TITLE_QUOTED_RE.fullmatch(t)
    if m:
        sym, rest = m.group("sym"), m.group("rest")
    else:
        m = TITLE_SIMPLE_RE.fullmatch(t)
        if not m or not (m.group("sym") or m.group("quote")):
            raise RefError(f"link title {title!r} is not `symbol: fragment`")
        sym, quote = m.group("sym"), m.group("quote")
        rest = format_quote(quote.strip()) if quote else ""
    return path + (f"::{sym}" if sym else "") + (f" {rest}" if rest else "")


def _link(m: re.Match) -> Citation | None:
    url = m.group("url")
    target, _, fragment = url.partition("#")
    frag = FRAGMENT_RE.fullmatch(fragment)
    path = _link_path(target) if frag else None
    if path is None:
        return None
    c = Citation("link", m.start(), m.end())
    title = m.group("title")
    if title is None:
        c.kind = "bare-link"
        return c
    try:
        c.spec = title_spec(path, re.sub(r"\\(.)", r"\1", title[1:-1]))
    except RefError as e:
        c.error = str(e)
        return c
    old_frag = fragment
    frag_start = m.start("url") + len(target) + 1
    c.slots.append(Slot(frag_start, frag_start + len(old_frag), old_frag, "fragment"))
    text, text_start = m.group("text"), m.start("text")
    old = render(int(frag.group(1)), int(frag.group(2) or frag.group(1)))
    tail = TEXT_PATH_NUM_RE.search(text)  # `orders.py:10` style text owns its number
    if tail:
        c.slots.append(
            Slot(
                text_start + tail.start(1),
                text_start + tail.end(1),
                tail.group(1),
                "num",
            )
        )
    else:  # otherwise a number token equal to the fragment ("line 10") moves with it
        hits = [
            t
            for t in NUM_TOKEN_RE.finditer(text)
            if t.group(0).replace("~", "-") == old
        ]
        if hits:
            t = hits[-1]
            c.slots.append(
                Slot(text_start + t.start(), text_start + t.end(), t.group(0), "num")
            )
    return c


def extract(
    text: str, path: Path, syntax: Syntax
) -> tuple[list[Citation], list[tuple[int, int]]]:
    """All citations in the document, in order, plus the spans of anchors with no number (orphans)."""
    view = visible(text, path, syntax)
    cites: list[Citation] = []
    for m in syntax.anchor.finditer(view):
        spec = m.group("spec").strip()
        slot = Slot(m.start("num"), m.end("num"), m.group("num"), "num")
        cites.append(
            Citation(
                "anchor" if spec else "marker",
                m.start(),
                m.end(),
                spec,
                [slot] if spec else [],
            )
        )
    owned = [c.span for c in cites]
    orphans = [
        m.span()
        for m in syntax.any_anchor.finditer(view)
        if not any(a <= m.start() < b for a, b in owned)
    ]

    # links are markup only outside code fences; there they are literal example text
    prose = visible(blank(text, owned + orphans), path, syntax, skip_fences=True)
    for m in LINK_RE.finditer(prose):
        c = _link(m)
        if c is not None:
            cites.append(c)
    masked = blank(view, [c.span for c in cites] + orphans)
    for m in syntax.symref.finditer(masked):
        cites.append(Citation("symref", m.start(), m.end(), m.group("spec")))
    cites.sort(key=lambda c: c.start)
    return cites, orphans


def scan_file(
    path: Path, repo: Repo, syntax: Syntax, cfg: Config, write: bool = False
) -> Findings:
    text = path.read_bytes().decode("utf-8")
    name = display(path)
    found = Findings()
    cites, orphans = extract(text, path, syntax)
    edits: list[tuple[int, int, str]] = []

    for c in cites:
        ln = line_of(text, c.start)
        if c.kind == "marker":
            continue
        if c.kind == "bare-link":
            if cfg.legacy != "off":
                found.legacy.append(
                    f"{name}:{ln}: {text[c.start : c.end]}   (link without a title naming the code)"
                )
            continue
        found.anchors += 1
        if c.error:
            found.broken.append(f"{name}:{ln}: {c.error}")
            continue
        try:
            _, a, b = resolve(repo, c.spec)
        except RefError as e:
            found.broken.append(f"{name}:{ln}: {e}  [{c.spec}]")
            continue
        changed = [(s, s.render(a, b)) for s in c.slots if s.render(a, b) != s.old]
        if changed:
            s0 = c.slots[0]
            found.drift.append(
                f"{name}:{ln}: {s0.old} -> {s0.render(a, b)}  [{c.spec}]"
            )
            edits += [(s.start, s.end, new) for s, new in changed]

    for a, b in orphans:
        spec = text[a:b][5:-3].strip()
        if spec:
            found.broken.append(
                f"{name}:{line_of(text, a)}: anchor has no number right before it  [{spec}]"
            )

    if cfg.legacy != "off":
        masked = blank(text, [c.span for c in cites] + orphans)
        prose = visible(masked, path, syntax, skip_fences=True)
        for m in syntax.legacy.finditer(prose):
            start = max(0, m.start() - 60)
            if m.group("pathref") and syntax.pinned_pathref.search(
                prose[start : m.end()]
            ):
                continue  # @sha:path:12 names history, it cannot drift
            ctx = " ".join(prose[start : m.end() + 20].split())
            found.legacy.append(
                f"{name}:{line_of(text, m.start())}: {m.group(0)}   …{ctx}…"
            )

    if write and edits:
        out, last = [], 0
        for s, e, new in sorted(edits):
            out += [text[last:s], new]
            last = e
        out.append(text[last:])
        path.write_bytes(
            "".join(out).encode("utf-8")
        )  # bytes: keep the file's own line endings
    return found


def scan_files(
    files: list[Path], repo: Repo, cfg: Config, write: bool = False
) -> Findings:
    syntax = Syntax(cfg)
    total = Findings()
    for f in files:
        total.extend(scan_file(f, repo, syntax, cfg, write))
    return total
