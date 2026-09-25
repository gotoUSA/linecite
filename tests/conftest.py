from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

import pytest
from coderef.cli import main


class Sandbox:
    """A throwaway git repo holding both the cited code and the docs that cite it."""

    def __init__(self, root: Path):
        self.root = root
        self.git("init", "-q")

    def git(self, *args: str, env: dict | None = None) -> str:
        out = subprocess.run(
            [
                "git",
                "-c",
                "user.name=t",
                "-c",
                "user.email=t@t",
                "-c",
                "commit.gpgsign=false",
                "-c",
                "core.autocrlf=false",
                *args,
            ],
            cwd=self.root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
            env={**os.environ, **(env or {})},
        )
        return out.stdout

    def write(self, rel: str, text: str) -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(textwrap.dedent(text).lstrip("\n").encode("utf-8"))
        return p

    def read(self, rel: str) -> str:
        return (self.root / rel).read_bytes().decode("utf-8")

    def commit(self, msg: str = "c", date: str | None = None) -> str:
        """Commit everything; DATE (e.g. "2026-01-02T00:00:00+0000") sets author and committer time."""
        self.git("add", "-A")
        env = {"GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date} if date else None
        self.git("commit", "-q", "-m", msg, env=env)
        return self.git("rev-parse", "HEAD").strip()

    def run(self, *argv: str, capsys) -> tuple[int, str]:
        code = main(list(argv))
        out = capsys.readouterr()
        return code, out.out + out.err


@pytest.fixture
def sb(tmp_path, monkeypatch) -> Sandbox:
    monkeypatch.chdir(tmp_path)
    return Sandbox(tmp_path)


ORDERS = """
import logging

log = logging.getLogger(__name__)


class OrderService:
    def create_order(self, items):
        rows = sorted(items, key=lambda i: i.product_id)
        for row in rows:
            row.lock()
        return rows

    def cancel(self, order):
        order.status = "cancelled"
        return order


TIMEOUT = 30
"""
