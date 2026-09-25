"""The pre-commit hook manifest: valid for pre-commit, and every hook runs a real subcommand on every commit."""

from pathlib import Path

import pytest

from linecite.cli import build_parser

ROOT = Path(__file__).resolve().parent.parent
clientlib = pytest.importorskip("pre_commit.clientlib")


def test_manifest_is_valid_and_hooks_read_all_docs():
    hooks = clientlib.load_manifest(str(ROOT / ".pre-commit-hooks.yaml"))
    assert {h["id"] for h in hooks} == {"linecite-check", "linecite-sync"}
    for h in hooks:
        prog, cmd = h["entry"].split()
        assert prog == "linecite"
        assert build_parser().parse_args([cmd]).cmd == cmd
        # a code-only commit can move cited lines: never limit the run to staged docs
        assert h["pass_filenames"] is False and h["always_run"] is True
