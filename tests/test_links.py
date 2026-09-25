from conftest import ORDERS

MOVED = '"""Orders."""\n\n' + ORDERS.lstrip(
    "\n"
)  # two lines pushed in above everything


def test_link_citation_passes_when_current(sb, capsys):
    sb.write("app/orders.py", ORDERS)
    sb.write(
        "docs/a.md",
        'Locks: [orders.py:10](../app/orders.py#L10 "create_order: row.lock()").\n',
    )
    sb.commit()
    code, out = sb.run("check", "docs/a.md", capsys=capsys)
    assert code == 0, out
    assert "anchors 1 · broken 0 · drift 0 · legacy 0" in out


def test_link_sync_rewrites_fragment_and_text(sb, capsys):
    sb.write("app/orders.py", ORDERS)
    sb.write(
        "docs/a.md",
        'Locks: [orders.py:10](../app/orders.py#L10 "create_order: row.lock()").\n',
    )
    sb.commit()
    sb.write("app/orders.py", MOVED)
    code, out = sb.run("check", "docs/a.md", capsys=capsys)
    assert code == 1 and "L10 -> L12" in out
    sb.run("sync", "docs/a.md", capsys=capsys)
    assert (
        sb.read("docs/a.md")
        == 'Locks: [orders.py:12](../app/orders.py#L12 "create_order: row.lock()").\n'
    )


def test_link_range_for_a_whole_symbol(sb, capsys):
    sb.write("app/orders.py", ORDERS)
    sb.write(
        "docs/a.md",
        '[orders.py:1-2](app/orders.py#L1-L2 "OrderService.create_order")\n',
    )
    sb.commit()
    sb.run("sync", "docs/a.md", capsys=capsys)
    assert (
        sb.read("docs/a.md")
        == '[orders.py:7-11](app/orders.py#L7-L11 "OrderService.create_order")\n'
    )


def test_link_text_without_number_keeps_its_text(sb, capsys):
    sb.write("app/orders.py", ORDERS)
    sb.write("docs/a.md", '[the lock](app/orders.py#L3 "create_order: row.lock()")\n')
    sb.commit()
    sb.run("sync", "docs/a.md", capsys=capsys)
    assert (
        sb.read("docs/a.md")
        == '[the lock](app/orders.py#L10 "create_order: row.lock()")\n'
    )


def test_link_text_line_word_moves_with_fragment(sb, capsys):
    sb.write("app/orders.py", ORDERS)
    sb.write("docs/a.md", '[line 10](app/orders.py#L10 "create_order: row.lock()")\n')
    sb.commit()
    sb.write("app/orders.py", MOVED)
    sb.run("sync", "docs/a.md", capsys=capsys)
    assert (
        sb.read("docs/a.md")
        == '[line 12](app/orders.py#L12 "create_order: row.lock()")\n'
    )


def test_link_title_with_backtick_grammar_and_nth(sb, capsys):
    sb.write("app/orders.py", ORDERS)
    sb.write("docs/a.md", "[orders.py:1](app/orders.py#L1 'OrderService `return`#2')\n")
    sb.commit()
    sb.run("sync", "docs/a.md", capsys=capsys)
    assert sb.read("docs/a.md").startswith("[orders.py:15](app/orders.py#L15 ")


def test_link_title_quote_only_for_other_languages(sb, capsys):
    sb.write(
        "web/app.ts",
        "export function total(xs: number[]) {\n  return xs.reduce((a, b) => a + b, 0)\n}\n",
    )
    sb.write("docs/a.md", '[app.ts:9](web/app.ts#L9 ": xs.reduce")\n')
    sb.commit()
    sb.run("sync", "docs/a.md", capsys=capsys)
    assert sb.read("docs/a.md") == '[app.ts:2](web/app.ts#L2 ": xs.reduce")\n'


def test_link_title_with_escaped_quotes(sb, capsys):
    sb.write("app/q.py", 'def f(qs):\n    return qs.order_by("product_id")\n')
    sb.write("docs/a.md", '[q.py:9](app/q.py#L9 "f: order_by(\\"product_id\\")")\n')
    sb.commit()
    code, out = sb.run("sync", "docs/a.md", capsys=capsys)
    assert code == 0, out
    assert sb.read("docs/a.md").startswith("[q.py:2](app/q.py#L2 ")


def test_link_without_title_is_legacy(sb, capsys):
    sb.write("app/orders.py", ORDERS)
    sb.write("docs/a.md", "[orders.py:10](app/orders.py#L10)\n")
    sb.commit()
    code, out = sb.run("check", "docs/a.md", capsys=capsys)
    assert code == 1
    assert (
        sum(line.startswith("legacy ") for line in out.splitlines()) == 1
    )  # not also flagged as orders.py:10
    assert "link without a title" in out


def test_broken_link_when_code_disappears(sb, capsys):
    sb.write("app/orders.py", ORDERS)
    sb.write(
        "docs/a.md", '[orders.py:10](app/orders.py#L10 "create_order: row.lock()")\n'
    )
    sb.commit()
    sb.write("app/orders.py", ORDERS.replace("row.lock()", "row.acquire()"))
    code, out = sb.run("check", "docs/a.md", capsys=capsys)
    assert code == 1 and "not found" in out


def test_bad_title_is_broken(sb, capsys):
    sb.write("app/orders.py", ORDERS)
    sb.write("docs/a.md", '[x](app/orders.py#L10 "just some tooltip text")\n')
    sb.commit()
    code, out = sb.run("check", "docs/a.md", capsys=capsys)
    assert code == 1 and "is not `symbol: fragment`" in out


def test_non_citation_links_are_ignored(sb, capsys):
    sb.write("app/orders.py", ORDERS)
    sb.write(
        "docs/a.md",
        "[docs](other.md) [site](https://example.com/x.py#L10) ![img](pic.png#L3) [sec](#usage)\n",
    )
    sb.commit()
    code, out = sb.run("check", "docs/a.md", capsys=capsys)
    assert code == 0, out
    assert "anchors 0" in out


def test_links_inside_code_fences_are_examples(sb, capsys):
    sb.write("app/orders.py", ORDERS)
    sb.write(
        "docs/a.md",
        """
        ```md
        [orders.py:999](app/orders.py#L999 "nothing_here: x")
        ```
        """,
    )
    sb.commit()
    code, out = sb.run("check", "docs/a.md", capsys=capsys)
    assert code == 0, out


def test_affected_includes_link_citations(sb, capsys):
    sb.write("app/orders.py", ORDERS)
    sb.write(
        "docs/a.md",
        'Cancel: [orders.py:14](app/orders.py#L14 "cancel: order.status")\n',
    )
    base = sb.commit()
    sb.write("app/orders.py", ORDERS.replace('"cancelled"', '"canceled"'))
    code, out = sb.run("affected", base, "docs/a.md", capsys=capsys)
    assert "1 doc citation(s)" in out


def test_locate_proposes_a_link(sb, capsys):
    sb.write("app/orders.py", ORDERS)
    sb.commit()
    code, out = sb.run("locate", "orders.py", "10", capsys=capsys)
    assert (
        'link  [orders.py:10](app/orders.py#L10 "OrderService.create_order: row.lock()")'
        in out
    )
