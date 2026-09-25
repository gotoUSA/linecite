import pytest
from coderef.errors import RefError
from coderef.repo import Repo
from coderef.spec import format_quote, parse_refs, resolve
from conftest import ORDERS


def test_symbol_reference_in_prose_is_checked(sb, capsys):
    sb.write("app/orders.py", ORDERS)
    sb.write(
        "docs/a.md",
        "`orders.py::OrderService.cancel` exists; `orders.py::refund` does not.\n",
    )
    sb.commit()
    code, out = sb.run("check", "docs/a.md", capsys=capsys)
    assert code == 1
    assert "symbol 'refund' not in app/orders.py" in out
    assert "anchors 2 · broken 1" in out


def test_pinned_spec_resolves_old_code_and_allows_integers(sb, capsys):
    sb.write("app/orders.py", ORDERS)
    old = sb.commit()
    sb.write("app/orders.py", "X = 1\n")
    sb.commit()
    sb.write(
        "docs/a.md",
        f"it used to be 7-11<!--@ @{old[:10]}:orders.py::create_order -->, then 10<!--@ @{old[:10]}:orders.py 10 -->\n",
    )
    code, out = sb.run("check", "docs/a.md", capsys=capsys)
    assert code == 0, out


def test_integer_on_unpinned_spec_is_rejected(sb):
    sb.write("app/orders.py", ORDERS)
    sb.commit()
    with pytest.raises(RefError, match="use a quote"):
        resolve(Repo(sb.root), "orders.py 10")


def test_yaml_key_path(sb, capsys):
    sb.write(
        "deploy/compose.yml",
        """
        services:
          web:
            image: app
          worker:
            image: app
            command: celery -A app worker
        """,
    )
    sb.write(
        "docs/a.md",
        "worker is 1-1<!--@ compose.yml::services.worker -->, command at 1<!--@ compose.yml::services.worker `command` -->\n",
    )
    sb.commit()
    sb.run("sync", "docs/a.md", capsys=capsys)
    assert sb.read("docs/a.md").startswith(
        "worker is 4-6<!--@"
    ) and "command at 6<!--@" in sb.read("docs/a.md")


def test_ambiguous_path_suffix(sb):
    sb.write("a/util.py", "X = 1\n")
    sb.write("b/util.py", "X = 1\n")
    sb.commit()
    with pytest.raises(RefError, match="ambiguous"):
        resolve(Repo(sb.root), "util.py::X")
    assert resolve(Repo(sb.root), "a/util.py::X")[1:] == (1, 1)


def test_untracked_file_can_be_cited(sb):
    sb.write("app/x.py", "X = 1\n")
    sb.commit()
    sb.write("app/new.py", "Y = 2\n")
    assert resolve(Repo(sb.root), "new.py::Y")[1:] == (1, 1)


def test_quote_only_citation_works_for_any_language(sb):
    sb.write(
        "web/app.ts",
        "export function total(xs: number[]) {\n  return xs.reduce((a, b) => a + b, 0)\n}\n",
    )
    sb.commit()
    assert resolve(Repo(sb.root), "app.ts `xs.reduce`")[1:] == (2, 2)
    with pytest.raises(RefError, match="quote only"):
        resolve(Repo(sb.root), "app.ts::total")


def test_parse_refs_grammar():
    assert [r.quote for r in parse_refs("`a` .. `b`")] == ["a", "b"]
    assert parse_refs("`x`#3")[0].nth == 3
    assert parse_refs("「a..b」")[0].quote == "a..b"
    assert parse_refs("`` a ` b ``")[0].quote == "a ` b"
    with pytest.raises(RefError):
        parse_refs("`a` .. `b` .. `c`")
    with pytest.raises(RefError):
        parse_refs("`unclosed")


def test_format_quote_round_trips():
    for text in ['order_by("id")', "echo `date`", "a ``b`` c"]:
        assert parse_refs(format_quote(text))[0].quote == text
