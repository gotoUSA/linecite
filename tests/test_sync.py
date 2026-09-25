from conftest import ORDERS


def test_check_passes_when_numbers_match(sb, capsys):
    sb.write("app/orders.py", ORDERS)
    sb.write(
        "docs/a.md",
        "Locks are taken in order at line 10<!--@ orders.py::create_order `row.lock()` -->.\n",
    )
    sb.commit()
    code, out = sb.run("check", "docs/a.md", capsys=capsys)
    assert code == 0, out
    assert "anchors 1 · broken 0 · drift 0 · legacy 0" in out


def test_sync_rewrites_number_after_code_moves(sb, capsys):
    sb.write("app/orders.py", ORDERS)
    sb.write(
        "docs/a.md",
        "See line 10<!--@ orders.py::create_order `row.lock()` --> for the lock.\n",
    )
    sb.commit()
    sb.write(
        "app/orders.py", '"""Orders."""\n\n' + ORDERS.lstrip("\n")
    )  # two lines pushed in above

    code, out = sb.run("check", "docs/a.md", capsys=capsys)
    assert code == 1
    assert "10 -> 12" in out

    code, out = sb.run("sync", "docs/a.md", capsys=capsys)
    assert code == 0, out
    assert (
        sb.read("docs/a.md")
        == "See line 12<!--@ orders.py::create_order `row.lock()` --> for the lock.\n"
    )


def test_symbol_only_anchor_renders_the_span(sb, capsys):
    sb.write("app/orders.py", ORDERS)
    sb.write(
        "docs/a.md",
        "create_order is 1-2<!--@ orders.py::OrderService.create_order -->\n",
    )
    sb.commit()
    sb.run("sync", "docs/a.md", capsys=capsys)
    assert sb.read("docs/a.md").startswith("create_order is 7-11<!--@")


def test_range_between_two_quotes_keeps_tilde_separator(sb, capsys):
    sb.write("app/orders.py", ORDERS)
    sb.write(
        "docs/a.md",
        "loop 1~1<!--@ orders.py::create_order `for row` .. `row.lock()` -->\n",
    )
    sb.commit()
    sb.run("sync", "docs/a.md", capsys=capsys)
    assert sb.read("docs/a.md").startswith("loop 9~10<!--@")


def test_broken_when_quoted_code_disappears(sb, capsys):
    sb.write("app/orders.py", ORDERS)
    sb.write("docs/a.md", "line 10<!--@ orders.py::create_order `row.lock()` -->\n")
    sb.commit()
    sb.write("app/orders.py", ORDERS.replace("row.lock()", "row.acquire()"))
    code, out = sb.run("sync", "docs/a.md", capsys=capsys)
    assert code == 1
    assert "broken" in out and "not found" in out
    assert (
        sb.read("docs/a.md")
        == "line 10<!--@ orders.py::create_order `row.lock()` -->\n"
    )


def test_ambiguous_quote_needs_nth(sb, capsys):
    sb.write("app/orders.py", ORDERS)
    sb.write("docs/a.md", "x 1<!--@ orders.py::OrderService `return` -->\n")
    sb.commit()
    code, out = sb.run("check", "docs/a.md", capsys=capsys)
    assert code == 1 and "hits 2 lines" in out

    sb.write("docs/a.md", "x 1<!--@ orders.py::OrderService `return`#2 -->\n")
    sb.run("sync", "docs/a.md", capsys=capsys)
    assert sb.read("docs/a.md").startswith("x 15<!--@")


def test_corner_bracket_quotes_and_word_suffix(sb, capsys):
    sb.write("app/orders.py", ORDERS)
    sb.write(".linecite.toml", 'number_suffixes = ["`", "L"]\n')
    sb.write(
        "docs/a.md",
        "The lock is taken at 3L<!--@ orders.py::create_order「row.lock()」-->.\n",
    )
    sb.commit()
    sb.run("sync", "docs/a.md", capsys=capsys)
    assert sb.read("docs/a.md").startswith("The lock is taken at 10L<!--@")


def test_number_inside_backticked_path(sb, capsys):
    sb.write("app/orders.py", ORDERS)
    sb.write("docs/a.md", "see `orders.py:3`<!--@ orders.py::TIMEOUT -->\n")
    sb.commit()
    code, out = sb.run("sync", "docs/a.md", capsys=capsys)
    assert code == 0, out
    assert sb.read("docs/a.md").startswith("see `orders.py:18`<!--@")


def test_double_backtick_quote_holds_a_backtick(sb, capsys):
    sb.write("app/sh.py", 'CMD = "echo `date`"\nOTHER = 1\n')
    sb.write("docs/a.md", "cmd at 9<!--@ sh.py `` echo `date` `` -->\n")
    sb.commit()
    sb.run("sync", "docs/a.md", capsys=capsys)
    assert sb.read("docs/a.md").startswith("cmd at 1<!--@")


def test_crlf_line_endings_survive_sync(sb, capsys):
    sb.write("app/orders.py", ORDERS)
    p = sb.root / "docs" / "a.md"
    p.parent.mkdir(parents=True)
    p.write_bytes(b"first\r\nline 1<!--@ orders.py::TIMEOUT -->\r\nlast\r\n")
    sb.commit()
    sb.run("sync", "docs/a.md", capsys=capsys)
    assert p.read_bytes() == b"first\r\nline 18<!--@ orders.py::TIMEOUT -->\r\nlast\r\n"


def test_anchors_inside_code_fences_are_synced(sb, capsys):
    # docs annotate code excerpts with anchors in trailing comments; skipping them would let them rot silently
    sb.write("app/orders.py", ORDERS)
    sb.write(
        "docs/a.md",
        """
        ```python
        row.lock()   # 3<!--@ orders.py::create_order `row.lock()` -->
        ```
        """,
    )
    sb.commit()
    sb.run("sync", "docs/a.md", capsys=capsys)
    assert "# 10<!--@" in sb.read("docs/a.md")


def test_legacy_scan_skips_code_fences(sb, capsys):
    sb.write("app/orders.py", ORDERS)
    sb.write(
        "docs/a.md",
        """
        ```
        Traceback (most recent call last):
          File "orders.py:12", in create_order
        ```
        """,
    )
    sb.commit()
    code, out = sb.run("check", "docs/a.md", capsys=capsys)
    assert code == 0, out


def test_ignore_patterns_hide_syntax_examples(sb, capsys):
    sb.write("app/orders.py", ORDERS)
    sb.write(
        ".linecite.toml", 'ignore_patterns = ["<!-- example -->.*?<!-- /example -->"]\n'
    )
    sb.write(
        "docs/a.md",
        "<!-- example -->line 999<!--@ orders.py::nothing_here `x` --><!-- /example -->\n",
    )
    sb.commit()
    code, out = sb.run("check", "docs/a.md", capsys=capsys)
    assert code == 0, out


SYNTAX_EXAMPLES = """
Cite code like this:

<!-- linecite-ignore-start -->
Docs that say `orders.py:310` go stale. A link [orders.py:310](app/orders.py#L310 "gone: nothing")
or an anchor 310<!--@ orders.py::nothing_here `x` --> or `orders.py::Gone.symbol`.
<!-- linecite-ignore-end -->
"""


def test_ignore_markers_hide_syntax_examples(sb, capsys):
    sb.write("app/orders.py", ORDERS)
    sb.write(
        "docs/a.md", SYNTAX_EXAMPLES + "Real: line 10<!--@ orders.py `row.lock()` -->\n"
    )
    sb.commit()
    code, out = sb.run("check", "docs/a.md", capsys=capsys)
    assert code == 0, out
    assert "anchors 1 " in out
    code, out = sb.run("audit", "docs/a.md", capsys=capsys)
    assert "number-only citations 0" in out
    code, out = sb.run("adopt", "docs/a.md", capsys=capsys)
    assert "adopt: 0 to convert" in out


def test_unclosed_ignore_start_is_broken_and_ignores_nothing(sb, capsys):
    sb.write("app/orders.py", ORDERS)
    sb.write("docs/a.md", SYNTAX_EXAMPLES.replace("<!-- linecite-ignore-end -->\n", ""))
    sb.commit()
    code, out = sb.run("check", "docs/a.md", capsys=capsys)
    assert code == 1
    assert "a.md:3: linecite-ignore-start has no linecite-ignore-end" in out
    assert "nothing_here" in out and "legacy 1" in out


def test_stray_or_nested_ignore_markers_are_broken(sb, capsys):
    sb.write(
        "docs/a.md",
        "<!-- linecite-ignore-end -->\n"
        "<!-- linecite-ignore-start -->\n"
        "  <!--linecite-ignore-start-->\n"
        "<!-- linecite-ignore-end -->\n",
    )
    sb.write("app/x.py", "X = 1\n")
    sb.commit()
    code, out = sb.run("check", "docs/a.md", capsys=capsys)
    assert code == 1
    assert "a.md:1: linecite-ignore-end has no linecite-ignore-start" in out
    assert "a.md:2: linecite-ignore-start is not closed before the next one" in out
    assert "broken 2" in out


def test_ignore_marker_mentioned_inline_is_text(sb, capsys):
    sb.write("app/orders.py", ORDERS)
    sb.write(
        "docs/a.md",
        "Wrap examples in `<!-- linecite-ignore-start -->` and `<!-- linecite-ignore-end -->`.\n"
        "see orders.py:12\n",
    )
    sb.commit()
    code, out = sb.run("check", "docs/a.md", capsys=capsys)
    assert code == 1 and "legacy 1" in out and "broken 0" in out


def test_anchor_without_number_is_broken(sb, capsys):
    sb.write("app/orders.py", ORDERS)
    sb.write("docs/a.md", "see <!--@ orders.py::TIMEOUT -->\n")
    sb.commit()
    code, out = sb.run("check", "docs/a.md", capsys=capsys)
    assert code == 1 and "no number right before it" in out


def test_non_code_marker_is_left_alone(sb, capsys):
    sb.write("docs/a.md", "the table has 3 rows<!--@-->\n")
    sb.write("app/x.py", "X = 1\n")
    sb.commit()
    code, out = sb.run("check", "docs/a.md", capsys=capsys)
    assert code == 0, out


CODE9 = "class OrderService:\n    def create_order(self, items):\n        rows = sorted(items)\n        for row in rows:\n            pass\n        for row in rows:\n            x = 1\n        for row in rows:\n            row.lock()\n        return rows\n"
DRIFTED = '[orders.py:3](../app/orders.py#L3 "create_order: row.lock()")'


def test_ignore_markers_shown_in_code_blocks_are_examples(sb, capsys):
    # a guide that shows each marker in its own fence must not hide the real citations between them
    sb.write("app/orders.py", CODE9)
    fence = "```"
    sb.write(
        "docs/a.md",
        f"{fence}md\n<!-- linecite-ignore-start -->\n{fence}\n\n"
        f"The checker: {DRIFTED}.\n\n"
        f"    <!-- linecite-ignore-end -->\n\n"
        f"{fence}md\n<!-- linecite-ignore-end -->\n{fence}\n",
    )
    sb.commit()
    code, out = sb.run("check", "docs/a.md", capsys=capsys)
    assert code == 1 and "L3 -> L9" in out and "broken 0" in out, out


def test_crlf_docs_close_fences_and_ignore_regions(sb, capsys):
    sb.write("app/orders.py", CODE9)
    doc = (
        "```\ncode\n```\n\nAfter the fence: " + DRIFTED + " and app/orders.py:9.\n\n"
        "<!-- linecite-ignore-start -->\nexample 1<!--@ orders.py::nothing `x` -->\n"
        "<!-- linecite-ignore-end -->\n\n"
        "Use the ` key.\n\nLock: " + DRIFTED + "\n\nPress ` again.\n"
    )
    sb.write("docs/a.md", doc.replace("\n", "\r\n"))
    sb.commit()
    code, out = sb.run("check", "docs/a.md", capsys=capsys)
    assert "anchors 2 · broken 0 · drift 2 · legacy 1" in out, out
    sb.write("docs/b.md", "<!-- linecite-ignore-start -->\r\nx\r\n")
    code, out = sb.run("check", "docs/b.md", capsys=capsys)
    assert code == 1 and "has no linecite-ignore-end" in out
