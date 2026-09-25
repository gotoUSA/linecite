from coderef.repo import Repo
from coderef.spec import resolve
from conftest import ORDERS, Sandbox

MOVED = '"""Orders."""\n\n' + ORDERS.lstrip(
    "\n"
)  # two lines pushed in above everything
REORDERED = ORDERS.replace(
    """    def create_order(self, items):
        rows = sorted(items, key=lambda i: i.product_id)
        for row in rows:
            row.lock()
        return rows

    def cancel(self, order):
        order.status = "cancelled"
        return order
""",
    """    def cancel(self, order):
        order.status = "cancelled"
        return order

    def create_order(self, items):
        rows = sorted(items, key=lambda i: i.product_id)
        for row in rows:
            row.lock()
        return rows
""",
)


def audit_rows(sb, capsys, *argv) -> tuple[int, dict[int, list[str]], str]:
    """Run audit; return the exit code, {doc line: [status, citation, note]}, and the raw output."""
    code, out = sb.run("audit", *argv, capsys=capsys)
    rows = {}
    for line in out.replace("\\", "/").splitlines():
        cols = line.split("\t")
        if len(cols) == 4 and ":" in cols[0]:
            rows[int(cols[0].rsplit(":", 1)[1])] = cols[1:]
    return code, rows, out


def test_ok_when_the_code_has_not_moved(sb, capsys):
    sb.write("app/orders.py", ORDERS)
    sb.write("docs/a.md", "The lock is at orders.py:10.\n")
    sb.commit()
    code, rows, out = audit_rows(sb, capsys, "docs/a.md")
    assert code == 0, out
    assert rows[1][0] == "ok"
    assert "ok 1 · stale 0" in out


def test_stale_is_judged_by_the_line_as_written_not_the_line_there_now(sb, capsys):
    sb.write("app/orders.py", ORDERS)
    sb.write("docs/a.md", "The lock is at orders.py:10.\n")
    sb.commit()
    sb.write("app/orders.py", MOVED)
    sb.commit()
    # line 10 still exists and holds code — only history says it is no longer the cited line
    assert (
        Repo(sb.root)
        .source_lines("app/orders.py", None)[9]
        .strip()
        .startswith("rows = sorted(")
    )
    code, rows, out = audit_rows(sb, capsys, "docs/a.md")
    assert code == 1
    assert rows[1][:2] == ["stale", "orders.py:10 -> orders.py:12"]
    assert "written against" in rows[1][2]


def test_each_doc_line_is_dated_on_its_own(sb, capsys):
    sb.write("app/orders.py", ORDERS)
    sb.write("docs/a.md", "Old: orders.py:10.\n")
    sb.commit()
    sb.write("app/orders.py", MOVED)
    sb.commit()
    sb.write("docs/a.md", "Old: orders.py:10.\nNew: orders.py:12.\n")
    sb.commit()
    _, rows, _ = audit_rows(sb, capsys, "docs/a.md")
    assert rows[1][0] == "stale" and rows[2][0] == "ok"


def test_gone_when_the_cited_line_was_deleted(sb, capsys):
    sb.write("app/orders.py", ORDERS)
    sb.write("docs/a.md", "The lock is at orders.py:10.\n")
    sb.commit()
    sb.write("app/orders.py", ORDERS.replace("            row.lock()\n", ""))
    sb.commit()
    code, rows, _ = audit_rows(sb, capsys, "docs/a.md")
    assert code == 1
    assert rows[1][0] == "gone" and "`row.lock()` is gone" in rows[1][2]


def test_moved_code_is_found_among_the_changed_lines(sb, capsys):
    sb.write("app/orders.py", ORDERS)
    sb.write("docs/a.md", "Cancel sets orders.py:14.\n")
    sb.commit()
    sb.write("app/orders.py", REORDERED)
    sb.commit()
    _, rows, _ = audit_rows(sb, capsys, "docs/a.md")
    assert rows[1][:2] == ["stale", "orders.py:14 -> orders.py:8"]


def test_unknown_for_blank_and_near_empty_lines(sb, capsys):
    sb.write("app/call.py", "x = call(\n    1,\n)\n\ny = 2\n")
    sb.write("docs/a.md", "blank call.py:4\nparen call.py:3\n")
    sb.commit()
    code, rows, _ = audit_rows(sb, capsys, "docs/a.md")
    assert rows[1][0] == "unknown" and "blank" in rows[1][2]
    assert rows[2][0] == "unknown" and "too little to identify" in rows[2][2]
    assert code == 0


def test_repeated_line_left_untouched_by_the_diff_still_maps(sb, capsys):
    code_ = "def a():\n    return rows\n\n\ndef b():\n    return rows\n"
    sb.write("app/r.py", code_)
    sb.write("docs/a.md", "second return: r.py:6\n")
    sb.commit()
    sb.write("app/r.py", "import os\n" + code_)
    sb.commit()
    _, rows, _ = audit_rows(sb, capsys, "docs/a.md")
    assert rows[1][:2] == ["stale", "r.py:6 -> r.py:7"]


def test_unknown_when_a_rewritten_line_reappears_twice(sb, capsys):
    sb.write("app/orders.py", ORDERS)
    sb.write("docs/a.md", "The lock is at orders.py:10.\n")
    sb.commit()
    sb.write(
        "app/orders.py",
        ORDERS.replace("            row.lock()\n", "            pass\n")
        + "\n\ndef lock_one(row):\n    row.lock()\n\n\ndef lock_all(rows):\n    row.lock()\n",
    )
    sb.commit()
    _, rows, _ = audit_rows(sb, capsys, "docs/a.md")
    assert rows[1][0] == "unknown" and "now appears 2 times" in rows[1][2]


def test_uncommitted_doc_is_read_against_the_working_tree(sb, capsys):
    sb.write("app/orders.py", ORDERS)
    sb.commit()
    sb.write("app/orders.py", MOVED)
    sb.write("docs/a.md", "The lock is at orders.py:12.\n")
    _, rows, _ = audit_rows(sb, capsys, "docs/a.md")
    assert rows[1][0] == "ok" and "not committed" in rows[1][2]


def test_uncommitted_edit_to_a_committed_doc_line(sb, capsys):
    sb.write("app/orders.py", ORDERS)
    sb.write("docs/a.md", "a: orders.py:10\nb: orders.py:10\n")
    sb.commit()
    sb.write("app/orders.py", MOVED)
    sb.commit()
    sb.write("docs/a.md", "a: orders.py:10\nb: orders.py:12\n")  # line 2 re-cited today
    _, rows, _ = audit_rows(sb, capsys, "docs/a.md")
    assert rows[1][0] == "stale" and rows[2][0] == "ok"


def test_doc_outside_git_is_unverifiable(sb, capsys, tmp_path_factory, monkeypatch):
    sb.write("app/orders.py", ORDERS)
    sb.commit()
    loose = tmp_path_factory.mktemp("loose")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(loose.parent))
    doc = loose / "a.md"
    doc.write_text("see orders.py:10\n", encoding="utf-8")
    code, rows, _ = audit_rows(sb, capsys, str(doc))
    assert code == 0
    assert rows[1][0] == "unverifiable" and "not in a git repository" in rows[1][2]


def test_separate_code_repo_is_read_as_of_the_doc_line_date(
    sb, capsys, tmp_path_factory
):
    code = Sandbox(tmp_path_factory.mktemp("code"))
    code.write("app/orders.py", ORDERS)
    code.commit(date="2026-01-01T00:00:00+0000")
    code.write("app/orders.py", MOVED)
    code.commit(date="2026-03-01T00:00:00+0000")
    sb.write(".coderef.toml", f'code_root = "{code.root.as_posix()}"\n')
    sb.write("docs/a.md", "before any code: orders.py:1\n")
    sb.commit(date="2025-12-01T00:00:00+0000")
    sb.write("docs/a.md", "before any code: orders.py:1\nfebruary: orders.py:10\n")
    sb.commit(date="2026-02-01T00:00:00+0000")
    sb.write(
        "docs/a.md",
        "before any code: orders.py:1\nfebruary: orders.py:10\napril: orders.py:12\n",
    )
    sb.commit(date="2026-04-01T00:00:00+0000")
    _, rows, _ = audit_rows(sb, capsys, "docs/a.md")
    assert rows[1][0] == "unverifiable" and "no commit before" in rows[1][2]
    assert rows[2][:2] == ["stale", "orders.py:10 -> orders.py:12"]
    assert rows[3][0] == "ok"


def test_range_over_a_whole_function_follows_the_function(sb, capsys):
    sb.write("app/orders.py", ORDERS)
    sb.write("docs/a.md", "create_order is orders.py:7-11\n")
    sb.commit()
    sb.write("app/orders.py", MOVED)
    sb.commit()
    _, rows, _ = audit_rows(sb, capsys, "docs/a.md")
    assert rows[1][:2] == ["stale", "orders.py:7-11 -> orders.py:9-13"]


def test_link_without_title_is_traced_and_both_numbers_move(sb, capsys):
    sb.write("app/orders.py", ORDERS)
    sb.write("docs/a.md", "[orders.py:10](../app/orders.py#L10)\n")
    sb.commit()
    sb.write("app/orders.py", MOVED)
    sb.commit()
    _, rows, _ = audit_rows(sb, capsys, "docs/a.md")
    assert rows[1][0] == "stale"
    assert rows[1][1].endswith("-> [orders.py:12](../app/orders.py#L12)")


def test_renamed_file_is_followed(sb, capsys):
    sb.write("app/orders.py", ORDERS)
    sb.write("docs/a.md", "The lock is at orders.py:10.\n")
    sb.commit()
    sb.git("mv", "app/orders.py", "app/order_service.py")
    sb.commit()
    _, rows, _ = audit_rows(sb, capsys, "docs/a.md")
    assert rows[1][:2] == ["stale", "orders.py:10 -> app/order_service.py:10"]
    assert "file is now app/order_service.py" in rows[1][2]


def test_number_without_a_file_is_unknown(sb, capsys):
    sb.write(".coderef.toml", 'number_suffixes = ["`", "L"]\n')
    sb.write("app/orders.py", ORDERS)
    sb.write("docs/a.md", "The lock is at 10L.\n")
    sb.commit()
    _, rows, _ = audit_rows(sb, capsys, "docs/a.md")
    assert rows[1][0] == "unknown" and "names no file" in rows[1][2]


def test_line_numbers_split_on_newlines_only(sb):
    # str.splitlines() would also split at the form feed and push B to line 4
    sb.write("app/x.py", "A = 1\n\x0c\nB = 2\n")
    sb.commit()
    assert resolve(Repo(sb.root), "x.py `B = 2`")[1:] == (3, 3)


def test_legacy_lists_and_the_pinned_prefix(sb, capsys):
    sb.write("app/orders.py", ORDERS)
    sha = sb.commit()
    sb.write(
        "docs/a.md",
        f"pinned @{sha[:8]}:app/orders.py:12 then orders.py:7-11,14 nearby\n",
    )
    code, out = sb.run("check", "docs/a.md", capsys=capsys)
    assert code == 1
    legacy = [line for line in out.splitlines() if line.startswith("legacy ")]
    assert len(legacy) == 1 and "orders.py:7-11,14" in legacy[0]
    _, rows, _ = audit_rows(sb, capsys, "docs/a.md")
    assert rows[1][:2] == ["ok", "orders.py:7-11,14"]


def test_gone_says_when_the_line_was_rewritten_in_place(sb, capsys):
    sb.write("app/orders.py", ORDERS)
    sb.write("docs/a.md", "The lock is at orders.py:10.\n")
    sb.commit()
    sb.write("app/orders.py", ORDERS.replace("row.lock()", "row.lock(nowait=True)"))
    sb.commit()
    _, rows, _ = audit_rows(sb, capsys, "docs/a.md")
    assert rows[1][0] == "gone"
    assert "rewritten in place: line 10 is now `row.lock(nowait=True)`" in rows[1][2]


def test_a_dropped_line_is_not_matched_to_another_copy_the_diff_re_added(sb, capsys):
    # difflib keeps f's step_* block and re-adds g (moved above f) as new lines, so g's untouched
    # lock() lands among the changed lines while f's lock() is really deleted
    sb.write(
        "app/o.py",
        "def f():\n    lock()\n    step_one()\n    step_two()\n    step_three()\n"
        "def g():\n    lock()\n    other()\n",
    )
    sb.write("docs/a.md", "see o.py:2\n")
    sb.commit()
    sb.write(
        "app/o.py",
        "def g():\n    lock()\n    other()\n"
        "def f():\n    step_one()\n    step_two()\n    step_three()\n",
    )
    sb.commit()
    _, rows, _ = audit_rows(sb, capsys, "docs/a.md")
    assert rows[1][0] == "unknown" and "which one survived is unclear" in rows[1][2]


def test_shallow_clone_cannot_date_old_lines(sb, capsys, tmp_path_factory, monkeypatch):
    sb.write("app/orders.py", ORDERS)
    sb.write("docs/a.md", "The lock is at orders.py:10.\n")
    sb.commit()
    sb.write("app/orders.py", MOVED)
    sb.commit()
    clone = tmp_path_factory.mktemp("shallow") / "c"
    sb.git("clone", "-q", "--depth", "1", sb.root.as_uri(), str(clone))
    monkeypatch.chdir(clone)
    code, out = sb.run("audit", "docs/a.md", capsys=capsys)
    assert "\tunverifiable\t" in out and "shallow clone" in out, out
    assert code == 0


def test_separate_repo_date_follows_the_mainline(sb, capsys, tmp_path_factory):
    # a feature commit dated before the doc line but merged after it was not yet the mainline
    code = Sandbox(tmp_path_factory.mktemp("code"))
    code.write("app/orders.py", ORDERS)
    code.commit(date="2026-01-01T00:00:00+0000")
    main = code.git("rev-parse", "--abbrev-ref", "HEAD").strip()
    code.git("checkout", "-q", "-b", "feature")
    code.write("app/orders.py", MOVED)
    code.commit(date="2026-02-10T00:00:00+0000")
    code.git("checkout", "-q", main)
    code.git(
        "merge", "-q", "--no-ff", "-m", "merge", "feature",
        env={"GIT_AUTHOR_DATE": "2026-03-01T00:00:00+0000",
             "GIT_COMMITTER_DATE": "2026-03-01T00:00:00+0000"},
    )
    sb.write(".coderef.toml", f'code_root = "{code.root.as_posix()}"\n')
    sb.write("docs/a.md", "The lock is at orders.py:10.\n")
    sb.commit(date="2026-02-15T00:00:00+0000")
    _, rows, _ = audit_rows(sb, capsys, "docs/a.md")
    assert rows[1][:2] == ["stale", "orders.py:10 -> orders.py:12"]


def test_code_root_may_be_a_subdirectory(sb, capsys):
    sb.write("svc/app/orders.py", ORDERS)
    sb.write(".coderef.toml", 'code_root = "svc"\n')
    sb.write("docs/a.md", "The lock is at orders.py:10.\n")
    sb.commit()
    sb.write("svc/app/orders.py", MOVED)
    sb.commit()
    _, rows, out = audit_rows(sb, capsys, "docs/a.md")
    assert rows[1][:2] == ["stale", "orders.py:10 -> orders.py:12"], out
    code, out = sb.run("affected", "HEAD~1", "docs/a.md", capsys=capsys)
    assert code == 0, out


def test_a_name_that_became_ambiguous_is_stale(sb, capsys):
    sb.write("app/orders.py", ORDERS)
    sb.write("docs/a.md", "The lock is at orders.py:10.\n")
    sb.commit()
    sb.write("legacy/orders.py", "X = 1\n")
    sb.commit()
    _, rows, _ = audit_rows(sb, capsys, "docs/a.md")
    assert rows[1][:2] == ["stale", "orders.py:10 -> app/orders.py:10"]
    assert "`orders.py` now matches other files too" in rows[1][2]
