"""The GitHub Action's report: job summary, and one pull request comment that later runs rewrite.

Reads what the action's earlier steps left in files (paths in the environment):

    LINECITE_CHECK_CODE / LINECITE_CHECK_OUT      exit code and output of `linecite check` (code empty: skipped)
    LINECITE_AFFECTED_CODE                        exit code of `linecite affected` (empty: skipped)
    LINECITE_AFFECTED_MD / LINECITE_AFFECTED_ERR  its markdown output and its stderr
    LINECITE_KEY                                  which use of the action this is (working directory, config)
    LINECITE_COMMENT                              "true" to comment on the pull request
    GITHUB_TOKEN, GITHUB_API_URL, GITHUB_REPOSITORY, GITHUB_EVENT_PATH, GITHUB_STEP_SUMMARY, GITHUB_OUTPUT

A comment is created only when there is something to say; the one this use of the action wrote before is
rewritten on every run, so a push that makes a listed paragraph irrelevant also takes it off the list.
Nothing here fails the job: pull requests from forks get a read-only token, and the job summary carries
the report either way.
"""

from __future__ import annotations

import html
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

COUNT_RE = re.compile(r"^\*\*(\d+) doc citation")
# GitHub rejects comment bodies over 65536 characters
MAX_BODY_CHARS = 60000
MAX_CHECK_CHARS = 20000


def marker() -> str:
    """The comment's first line: which use of the action wrote it (one PR can run several)."""
    key = " ".join(os.environ.get("LINECITE_KEY", "").split()) or "."
    return f"<!-- linecite:report {key.replace('--', '- -')} -->"


def _read(var: str) -> str:
    path = os.environ.get(var, "")
    try:
        # what a tool wrote to stderr need not be UTF-8
        return Path(path).read_bytes().decode("utf-8", "replace") if path else ""
    except FileNotFoundError:
        return ""


def affected_count(md: str) -> int:
    m = COUNT_RE.match(md)
    return int(m.group(1)) if m else 0


def _cut(md: str, room: int) -> str:
    """The first lines of MD that fit in ROOM characters, and a note of what was left out."""
    if len(md) <= room:
        return md
    kept, size = [], 0
    lines = md.splitlines()
    for line in lines:
        if size + len(line) + 1 > room - 200:
            break
        kept.append(line)
        size += len(line) + 1
    left = len(lines) - len(kept)
    return "\n".join(kept) + f"\n\n… {left} more line(s) in the job summary."


def report() -> tuple[str, str, bool, int]:
    """The comment body, the summary, whether there is anything to say, and the affected count."""
    affected_code = os.environ.get("LINECITE_AFFECTED_CODE", "")
    md, err = (
        _read("LINECITE_AFFECTED_MD").strip(),
        _read("LINECITE_AFFECTED_ERR").strip(),
    )
    check_code = os.environ.get("LINECITE_CHECK_CODE", "")
    check_out = _read("LINECITE_CHECK_OUT").strip()
    affected_failed = affected_code not in ("", "0")
    count = affected_count(md) if affected_code == "0" else 0
    head = ["### linecite"]
    if affected_failed:
        head.append(
            f"`linecite affected` could not run (exit {affected_code}):\n\n```\n{err}\n```"
        )
    tail = []
    tally = html.escape(check_out.splitlines()[-1]) if check_out else ""
    check_failed = check_code not in ("", "0")
    if check_code == "0":
        tail.append(f"`linecite check` passed: {tally}")
    elif check_failed:
        if len(check_out) > MAX_CHECK_CHARS:
            check_out = (
                check_out[:MAX_CHECK_CHARS] + "\n… (cut; the job log has all of it)"
            )
        tail.append(
            f"<details><summary><code>linecite check</code> failed: {tally or f'exit {check_code}'}"
            f"</summary>\n\n```\n{check_out}\n```\n\n"
            "Numbers that only drifted are fixed by `linecite sync`.\n</details>"
        )
    listing = [md] if md and affected_code == "0" else []

    def join(parts):
        return "\n\n".join(parts) + "\n"

    summary = join(head + listing + tail)
    room = MAX_BODY_CHARS - len(join([marker()] + head + tail))
    body = join([marker()] + head + [_cut(m, room) for m in listing] + tail)
    return body, summary, bool(count or affected_failed or check_failed), count


class GitHub:
    def __init__(self, api: str, repo: str, token: str):
        self.api, self.repo, self.token = api.rstrip("/"), repo, token

    def call(self, method: str, path: str, body: dict | None = None):
        req = urllib.request.Request(
            f"{self.api}{path}",
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read() or b"null")

    def find(self, number: int, first_line: str) -> int | None:
        """Our earlier comment: the marker is its first line, so a comment quoting it is not ours."""
        page = 1
        while True:
            rows = self.call(
                "GET",
                f"/repos/{self.repo}/issues/{number}/comments?per_page=100&page={page}",
            )
            for row in rows:
                if (row.get("body") or "").split("\n", 1)[0].strip() == first_line:
                    return row["id"]
            if len(rows) < 100:
                return None
            page += 1

    def upsert(self, number: int, body: str, worth_posting: bool) -> str:
        existing = self.find(number, body.split("\n", 1)[0])
        if existing is not None:
            self.call(
                "PATCH",
                f"/repos/{self.repo}/issues/comments/{existing}",
                {"body": body},
            )
            return "updated"
        if worth_posting:
            self.call(
                "POST", f"/repos/{self.repo}/issues/{number}/comments", {"body": body}
            )
            return "created"
        return "nothing to say"


def pull_number() -> int | None:
    try:
        event = json.loads(
            Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8")
        )
    except (KeyError, OSError, ValueError):
        return None
    return (event.get("pull_request") or {}).get("number")


def _append(var: str, text: str) -> None:
    path = os.environ.get(var)
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text)


def main() -> int:
    try:
        body, summary, worth_posting, count = report()
        _append("GITHUB_STEP_SUMMARY", summary)
        _append("GITHUB_OUTPUT", f"affected={count}\n")
        number = pull_number()
        if os.environ.get("LINECITE_COMMENT") != "true" or number is None:
            return 0
        gh = GitHub(
            os.environ.get("GITHUB_API_URL", "https://api.github.com"),
            os.environ.get("GITHUB_REPOSITORY", ""),
            os.environ.get("GITHUB_TOKEN", ""),
        )
        print(f"pull request comment: {gh.upsert(number, body, worth_posting)}")
    except Exception as e:  # the report is advice: no failure of it may fail the job
        hint = ""
        if isinstance(e, urllib.error.HTTPError) and e.code in (403, 404):
            hint = (
                " Pull requests from forks get a read-only token; otherwise grant"
                " `pull-requests: write`."
            )
        print(
            f"::warning::linecite could not comment on the pull request ({e!r}); the report"
            f" is in the job summary.{hint}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
