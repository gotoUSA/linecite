"""linecite checks its own README and docs: the numbers in them are re-derived from this source tree."""

from pathlib import Path

from linecite.cli import main

ROOT = Path(__file__).resolve().parent.parent


def test_own_docs_pass_check(monkeypatch, capsys):
    monkeypatch.chdir(ROOT)
    code = main(["check"])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "anchors 0 " not in out  # the docs really cite this code
