"""`affected --format markdown`: the re-read list a pull request comment carries, and shallow-clone errors."""

import warnings

from conftest import ORDERS
from linecite.cli import main

DOC = """
# Design

Locking: [orders.py:10](../app/orders.py#L10 "create_order: row.lock()") keeps rows in order.
Cancel: `orders.py::OrderService.cancel`.
Timeout: 18<!--@ orders.py::TIMEOUT -->.
"""


def changed_since_base(sb) -> str:
    sb.write("app/orders.py", ORDERS)
    sb.write("docs/a.md", DOC)
    base = sb.commit()
    sb.write("app/orders.py", ORDERS.replace("row.lock()", "row.lock(nowait=True)"))
    return base


def test_markdown_lists_the_lines_to_re_read(sb, capsys):
    base = changed_since_base(sb)
    code, out = sb.run(
        "affected", base, "docs/a.md", "--format", "markdown", capsys=capsys
    )
    assert code == 0, out
    assert out.startswith(
        f"**1 doc citation(s) point into code changed since `{base[:12]}`.**"
    )
    assert "\n`docs/a.md`\n" in out
    # links reduced to their text; the cited symbol and the changed line
    assert (
        "- line 3: `Locking: orders.py:10 keeps rows in order.`"
        " — cites `app/orders.py:7-11`, changed 10\n"
    ) in out
    assert "Cancel" not in out and "Timeout" not in out


def test_markdown_links_doc_lines_under_the_link_base(sb, capsys):
    base = changed_since_base(sb)
    code, out = sb.run(
        "affected", base, "docs/a.md", "--format", "markdown",
        "--link-base", "https://github.com/o/r/blob/abc123/",
        capsys=capsys,
    )  # fmt: skip
    assert (
        "- [line 3](https://github.com/o/r/blob/abc123/docs/a.md?plain=1#L3): " in out
    )


def test_markdown_when_nothing_is_affected(sb, capsys):
    sb.write("app/orders.py", ORDERS)
    sb.write("docs/a.md", DOC)
    base = sb.commit()
    code, out = sb.run(
        "affected", base, "docs/a.md", "--format", "markdown", capsys=capsys
    )
    assert (
        code == 0
        and out == f"No doc citation points into code changed since `{base[:12]}`.\n"
    )


def test_markdown_lists_citations_that_no_longer_resolve(sb, capsys):
    sb.write("app/orders.py", ORDERS)
    sb.write("docs/a.md", "Gone: 3<!--@ orders.py::nothing_here -->.\n")
    base = sb.commit()
    code, out = sb.run(
        "affected", base, "docs/a.md", "--format", "markdown", capsys=capsys
    )
    assert "**0 doc citation(s)" in out
    assert "Citations that no longer resolve" in out
    assert "- `docs/a.md:1`: `symbol 'nothing_here' not in app/orders.py`" in out


def test_unknown_rev_in_a_shallow_clone_says_how_to_fetch_it(
    sb, capsys, tmp_path_factory, monkeypatch
):
    base = changed_since_base(sb)
    sb.commit()
    clone = tmp_path_factory.mktemp("shallow") / "c"
    sb.git("clone", "-q", "--depth", "1", sb.root.as_uri(), str(clone))
    monkeypatch.chdir(clone)
    code, out = sb.run("affected", base, "docs/a.md", capsys=capsys)
    assert code == 2
    assert (
        "this is a shallow clone" in out and f"git fetch --depth=1 origin {base}" in out
    )
    assert "fetch-depth: 0" in out


def test_unknown_rev_in_a_full_clone_is_just_unknown(sb, capsys):
    changed_since_base(sb)
    code, out = sb.run("affected", "no-such-branch", "docs/a.md", capsys=capsys)
    assert code == 2 and "'no-such-branch' is not a commit" in out
    assert "shallow" not in out


def test_non_ascii_code_paths_are_diffed(sb, capsys):
    sb.write("app/café.py", ORDERS)
    sb.write(
        "docs/a.md",
        'Lock: [café.py:10](../app/café.py#L10 "create_order: row.lock()").\n',
    )
    base = sb.commit()
    sb.write("app/café.py", ORDERS.replace("row.lock()", "row.lock(nowait=True)"))
    code, out = sb.run("affected", base, "docs/a.md", capsys=capsys)
    assert "1 doc citation(s)" in out, out


def test_a_revision_range_is_accepted(sb, capsys):
    base = changed_since_base(sb)
    sb.commit()
    code, out = sb.run("affected", f"{base[:10]}...HEAD", "docs/a.md", capsys=capsys)
    assert code == 0 and "1 doc citation(s)" in out, out


def test_shallow_hint_fetches_a_branch_by_its_name(
    sb, capsys, tmp_path_factory, monkeypatch
):
    changed_since_base(sb)
    sb.commit()
    clone = tmp_path_factory.mktemp("shallow") / "c"
    sb.git("clone", "-q", "--depth", "1", sb.root.as_uri(), str(clone))
    monkeypatch.chdir(clone)
    code, out = sb.run("affected", "origin/develop", "docs/a.md", capsys=capsys)
    assert code == 2 and "git fetch --depth=1 origin develop" in out, out


def test_sparse_changed_lines_are_listed_as_runs(sb, capsys):
    sb.write("app/orders.py", ORDERS)
    sb.write("docs/a.md", DOC)
    base = sb.commit()
    sb.write(
        "app/orders.py",
        ORDERS.replace(
            "def create_order(self, items):", "def create_order(self, items, *, x=0):"
        ).replace("        return rows", "        return list(rows)"),
    )
    code, out = sb.run(
        "affected", base, "docs/a.md", "--format", "markdown", capsys=capsys
    )
    assert "changed 7, 11\n" in out, out


def test_cited_code_with_syntax_warnings_raises_none(sb, capsys):
    # the cited file itself holds the invalid escape "\d" (this test's source does not)
    sb.write(
        "app/rx.py", 'import re\n\n\ndef digits():\n    return re.compile("\\d+")\n'
    )
    sb.write("docs/a.md", "See 5<!--@ rx.py::digits `re.compile` -->.\n")
    sb.commit()
    # recorded here: pytest would otherwise capture the warning before it reached stderr
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        code = main(["check", "docs/a.md"])
    assert code == 0, capsys.readouterr().out
    assert [str(w.message) for w in caught] == []
