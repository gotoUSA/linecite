from conftest import ORDERS


def test_legacy_citations_are_reported(sb, capsys):
    sb.write("app/orders.py", ORDERS)
    sb.write(
        "docs/a.md",
        "The bug is in orders.py:12 and app/orders.py:7-11. "
        "Not citations: localhost:8000, example.com:443, v1.2.3.\n",
    )
    sb.commit()
    code, out = sb.run("check", "docs/a.md", capsys=capsys)
    assert code == 1
    assert sum(line.startswith("legacy ") for line in out.splitlines()) == 2
    assert "orders.py:12" in out and "app/orders.py:7-11" in out


def test_legacy_modes(sb, capsys):
    sb.write("app/orders.py", ORDERS)
    sb.write("docs/a.md", "see orders.py:12\n")
    sb.commit()
    sb.write(".coderef.toml", 'legacy = "warn"\n')
    code, out = sb.run("check", "docs/a.md", capsys=capsys)
    assert code == 0 and "(not failing)" in out
    sb.write(".coderef.toml", 'legacy = "off"\n')
    code, out = sb.run("check", "docs/a.md", capsys=capsys)
    assert code == 0 and "legacy 0" in out


def test_pinned_pathref_is_not_legacy(sb, capsys):
    sb.write("app/orders.py", ORDERS)
    sha = sb.commit()
    sb.write("docs/a.md", f"old code: @{sha[:8]}:app/orders.py:12\n")
    code, out = sb.run("check", "docs/a.md", capsys=capsys)
    assert code == 0, out


def test_affected_lists_citations_into_changed_symbols(sb, capsys):
    sb.write("app/orders.py", ORDERS)
    sb.write(
        "docs/a.md",
        """
        Locking: line 10<!--@ orders.py::create_order `row.lock()` -->.
        Cancel: `orders.py::OrderService.cancel`.
        Timeout: 19<!--@ orders.py::TIMEOUT -->.
        """,
    )
    base = sb.commit()
    sb.write(
        "app/orders.py",
        ORDERS.replace('order.status = "cancelled"', 'order.status = "canceled"'),
    )
    code, out = sb.run("affected", base, "docs/a.md", capsys=capsys)
    assert code == 0
    assert "docs\\a.md:2" in out or "docs/a.md:2" in out
    assert ":1\t" not in out and ":3\t" not in out
    assert "1 doc citation(s)" in out


def test_affected_quote_only_citation_counts_only_quoted_lines(sb, capsys):
    sb.write("app/orders.py", ORDERS)
    sb.write("docs/a.md", "timeout 19<!--@ orders.py `TIMEOUT = 30` -->\n")
    base = sb.commit()
    sb.write(
        "app/orders.py", ORDERS.replace("import logging", "import logging  # noqa")
    )
    code, out = sb.run("affected", base, "docs/a.md", capsys=capsys)
    assert "0 doc citation(s)" in out


def test_config_from_pyproject_with_separate_code_root(sb, capsys, tmp_path_factory):
    code_dir = tmp_path_factory.mktemp("code")
    from conftest import Sandbox

    code = Sandbox(code_dir)
    code.write("svc/orders.py", ORDERS)
    code.commit()
    sb.write(
        "pyproject.toml",
        f'[tool.coderef]\ncode_root = "{code_dir.as_posix()}"\ndocs = ["notes/**/*.md"]\n',
    )
    sb.write("notes/deep/a.md", "line 1<!--@ orders.py::TIMEOUT -->\n")
    rc, out = sb.run("sync", capsys=capsys)
    assert rc == 0, out
    assert sb.read("notes/deep/a.md").startswith("line 18<!--@")


def test_unknown_config_key_is_an_error(sb, capsys):
    sb.write(".coderef.toml", 'doc = ["x.md"]\n')
    sb.write("x.md", "hi\n")
    rc, out = sb.run("check", "x.md", capsys=capsys)
    assert rc == 2 and "unknown key(s) doc" in out


def test_no_documents_is_an_error(sb, capsys):
    rc, out = sb.run("check", capsys=capsys)
    assert rc == 2 and "no documents" in out


def test_locate_proposes_a_spec(sb, capsys):
    sb.write("app/orders.py", ORDERS)
    sb.commit()
    rc, out = sb.run("locate", "orders.py", "10", capsys=capsys)
    assert "spec  orders.py::OrderService.create_order `row.lock()`" in out
    assert "HEAD  10" in out
