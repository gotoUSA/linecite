from conftest import ORDERS, Sandbox

MOVED = '"""Orders."""\n\n' + ORDERS.lstrip(
    "\n"
)  # two lines pushed in above everything


def stale_doc(sb, rel: str, body: str) -> None:
    """Commit ORDERS and a doc citing it, then move the code two lines down."""
    sb.write("app/orders.py", ORDERS)
    sb.write(rel, body)
    sb.commit()
    sb.write("app/orders.py", MOVED)
    sb.commit()


def adopt_and_check(sb, capsys, rel: str) -> str:
    """adopt --write, then require check to pass; return the converted doc."""
    code, out = sb.run("adopt", "--write", rel, capsys=capsys)
    assert code == 0, out
    code, out = sb.run("check", rel, capsys=capsys)
    assert code == 0, out
    return sb.read(rel)


def test_markdown_gets_a_link_with_the_traced_number(sb, capsys):
    stale_doc(sb, "docs/a.md", "The lock is at orders.py:10.\n")
    assert adopt_and_check(sb, capsys, "docs/a.md") == (
        'The lock is at [orders.py:12](../app/orders.py#L12 "OrderService.create_order: row.lock()").\n'
    )


def test_dry_run_lists_and_writes_nothing(sb, capsys):
    stale_doc(sb, "docs/a.md", "The lock is at orders.py:10.\n")
    code, out = sb.run("adopt", "docs/a.md", capsys=capsys)
    assert code == 0
    assert '=> [orders.py:12](../app/orders.py#L12 "' in out
    assert "1 to convert (ok 0 · stale 1)" in out and "nothing written" in out
    assert sb.read("docs/a.md") == "The lock is at orders.py:10.\n"


def test_inline_code_becomes_the_link_text(sb, capsys):
    stale_doc(sb, "docs/a.md", "At `orders.py:10` and at `see orders.py:10 here`.\n")
    assert adopt_and_check(sb, capsys, "docs/a.md") == (
        'At [`orders.py:12`](../app/orders.py#L12 "OrderService.create_order: row.lock()")'
        ' and at [`see orders.py:12 here`](../app/orders.py#L12 "OrderService.create_order: row.lock()").\n'
    )


def test_html_gets_an_anchor_after_the_number(sb, capsys):
    stale_doc(sb, "docs/a.html", "<p>The lock is at orders.py:10.</p>\n")
    assert adopt_and_check(sb, capsys, "docs/a.html") == (
        "<p>The lock is at orders.py:12"
        "<!--@ app/orders.py::OrderService.create_order `row.lock()` -->.</p>\n"
    )


def test_link_without_title_keeps_its_url_and_gains_a_title(sb, capsys):
    stale_doc(sb, "docs/a.md", "See [line 10](../app/orders.py#L10).\n")
    assert adopt_and_check(sb, capsys, "docs/a.md") == (
        'See [line 12](../app/orders.py#L12 "OrderService.create_order: row.lock()").\n'
    )


def test_whole_function_range_is_titled_by_the_symbol(sb, capsys):
    stale_doc(sb, "docs/a.md", "create_order is orders.py:7-11.\n")
    assert adopt_and_check(sb, capsys, "docs/a.md") == (
        'create_order is [orders.py:9-13](../app/orders.py#L9-L13 "OrderService.create_order").\n'
    )


def test_repeated_line_is_quoted_with_its_hit_number(sb, capsys):
    sb.write(
        "app/c.py", "def f(count):\n    count += 1\n    count += 1\n    return count\n"
    )
    sb.write("docs/a.md", "the second increment: c.py:3\n")
    sb.commit()
    assert adopt_and_check(sb, capsys, "docs/a.md") == (
        'the second increment: [c.py:3](../app/c.py#L3 "f `count += 1`#2")\n'
    )


def test_what_cannot_be_traced_is_left_alone(sb, capsys):
    sb.write("app/orders.py", ORDERS)
    body = (
        "gone: orders.py:10\n"
        "blank: orders.py:2\n"
        "list: orders.py:10,14\n"
        "in a link: [see orders.py:14](https://example.com/x)\n"
    )
    sb.write("docs/a.md", body)
    sb.commit()
    sb.write("app/orders.py", ORDERS.replace("            row.lock()\n", ""))
    sb.commit()
    code, out = sb.run("adopt", "--write", "docs/a.md", capsys=capsys)
    assert code == 0
    assert "0 to convert" in out and "4 left for a human" in out
    assert "left as is: `row.lock()` is gone" in out
    assert "left as is: line 2 was blank" in out
    assert "left as is: inside [brackets]" in out
    assert sb.read("docs/a.md") == body


def test_list_of_lines_is_left_for_a_human(sb, capsys):
    sb.write("app/orders.py", ORDERS)
    sb.write("docs/a.md", "both: orders.py:10,14\n")
    sb.commit()
    code, out = sb.run("adopt", "docs/a.md", capsys=capsys)
    assert "left as is: a list of lines" in out


def test_table_cell_escapes_the_pipe_in_the_title(sb, capsys):
    sb.write("app/u.py", "def union(a, b):\n    return a | b\n")
    sb.write("docs/a.md", "| what | where |\n|---|---|\n| union | u.py:2 |\n")
    sb.commit()
    assert adopt_and_check(sb, capsys, "docs/a.md").splitlines()[2] == (
        '| union | [u.py:2](../app/u.py#L2 "union: return a \\| b") |'
    )


def test_separate_code_repo_links_by_code_root_path(sb, capsys, tmp_path_factory):
    code = Sandbox(tmp_path_factory.mktemp("code"))
    code.write("svc/orders.py", ORDERS)
    code.commit()
    sb.write(".coderef.toml", f'code_root = "{code.root.as_posix()}"\n')
    sb.write("notes/a.md", "The lock is at orders.py:10.\n")
    assert adopt_and_check(sb, capsys, "notes/a.md") == (
        'The lock is at [orders.py:10](svc/orders.py#L10 "OrderService.create_order: row.lock()").\n'
    )


def test_crlf_doc_keeps_its_line_endings(sb, capsys):
    sb.write("app/orders.py", ORDERS)
    p = sb.root / "docs" / "a.md"
    p.parent.mkdir(parents=True)
    p.write_bytes(b"first\r\nThe lock is at orders.py:10.\r\nlast\r\n")
    sb.commit()
    adopt_and_check(sb, capsys, "docs/a.md")
    assert p.read_bytes().count(b"\r\n") == 3 and b"#L10 " in p.read_bytes()


def test_locate_refuses_a_blank_line(sb, capsys):
    sb.write("app/orders.py", ORDERS)
    sb.commit()
    code, out = sb.run("locate", "orders.py", "2", capsys=capsys)
    assert code == 2 and "line 2 is blank" in out


def test_link_to_a_renamed_file_gets_the_new_url(sb, capsys):
    sb.write("app/orders.py", ORDERS)
    sb.write("docs/a.md", "See [line 10](../app/orders.py#L10) and orders.py:14.\n")
    sb.commit()
    sb.git("mv", "app/orders.py", "app/order_service.py")
    sb.write("app/order_service.py", MOVED)
    sb.commit()
    assert adopt_and_check(sb, capsys, "docs/a.md") == (
        'See [line 12](../app/order_service.py#L12 "OrderService.create_order: row.lock()")'
        ' and [app/order_service.py:16](../app/order_service.py#L16 "OrderService.cancel: order.status = \\"cancelled\\"").\n'
    )


def test_reference_links_and_urls_with_parens_are_not_nested(sb, capsys):
    sb.write("app/orders.py", ORDERS)
    body = (
        "See [the lock in orders.py:10][lock].\n"
        "Or [lock at orders.py:10](https://en.wikipedia.org/wiki/Lock_(computer_science)).\n"
        "\n[lock]: https://example.com/lock\n"
    )
    sb.write("docs/a.md", body)
    sb.commit()
    code, out = sb.run("adopt", "--write", "docs/a.md", capsys=capsys)
    assert "0 to convert" in out and out.count("inside [brackets]") == 2
    assert sb.read("docs/a.md") == body


def test_escaped_backtick_does_not_hide_the_real_code_span(sb, capsys):
    stale_doc(sb, "docs/a.md", "Use \\` to quote; see `orders.py:10` here.\n")
    assert adopt_and_check(sb, capsys, "docs/a.md") == (
        "Use \\` to quote; see [`orders.py:12`](../app/orders.py#L12"
        ' "OrderService.create_order: row.lock()") here.\n'
    )


def test_a_link_would_become_an_image_after_a_bang(sb, capsys):
    sb.write("app/orders.py", ORDERS)
    sb.write("docs/a.md", "Careful!orders.py:10 takes the lock.\n")
    sb.commit()
    code, out = sb.run("adopt", "--write", "docs/a.md", capsys=capsys)
    assert "would not read back as a citation here" in out
    assert sb.read("docs/a.md") == "Careful!orders.py:10 takes the lock.\n"


def test_link_inside_inline_code_is_an_example(sb, capsys):
    sb.write("app/orders.py", ORDERS)
    sb.write("docs/a.md", "Write links like `[x](app/orders.py#L999)`.\n")
    sb.commit()
    code, out = sb.run("check", "docs/a.md", capsys=capsys)
    assert code == 0 and "legacy 0" in out, out
