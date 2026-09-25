"""The GitHub Action: its report script against a fake GitHub API, and the composite action's shape."""

from __future__ import annotations

import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("comment", ROOT / "action" / "comment.py")
comment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(comment)

AFFECTED = (
    "**1 doc citation(s) point into code changed since `abc`.** Re-read the sentences around them.\n\n"
    "`docs/a.md`\n\n- line 3: `Locking` — cites `app/orders.py:7-11`, changed 10\n"
)
NOTHING = "No doc citation points into code changed since `abc`.\n"


class FakeGitHub:
    """Issue comments of pull request 7 in o/r; `status` makes every call fail with that HTTP code."""

    def __init__(self):
        self.comments: list[dict] = []
        self.calls: list[str] = []
        self.status = 200
        self.truncate = False
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, code, payload):
                data = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                # truncate: promise more bytes than are sent, as a dropped connection does
                self.send_header("Content-Length", str(len(data) + 50 * fake.truncate))
                self.end_headers()
                self.wfile.write(data)

            def _body(self):
                return json.loads(self.rfile.read(int(self.headers["Content-Length"])))

            def handle_one(self, method):
                url = urlparse(self.path)
                fake.calls.append(f"{method} {url.path}")
                assert self.headers["Authorization"] == "Bearer tkn"
                if fake.status != 200:
                    return self._send(
                        fake.status, {"message": "Resource not accessible"}
                    )
                if method == "GET" and url.path == "/repos/o/r/issues/7/comments":
                    q = parse_qs(url.query)
                    per, page = int(q["per_page"][0]), int(q["page"][0])
                    return self._send(200, fake.comments[(page - 1) * per : page * per])
                if method == "POST" and url.path == "/repos/o/r/issues/7/comments":
                    row = {
                        "id": 1000 + len(fake.comments),
                        "body": self._body()["body"],
                    }
                    fake.comments.append(row)
                    return self._send(201, row)
                if method == "PATCH" and url.path.startswith(
                    "/repos/o/r/issues/comments/"
                ):
                    cid = int(url.path.rsplit("/", 1)[1])
                    row = next(c for c in fake.comments if c["id"] == cid)
                    row["body"] = self._body()["body"]
                    return self._send(200, row)
                return self._send(404, {"message": "Not Found"})

            def do_GET(self):
                self.handle_one("GET")

            def do_POST(self):
                self.handle_one("POST")

            def do_PATCH(self):
                self.handle_one("PATCH")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def ours(self) -> list[dict]:
        return [c for c in self.comments if c["body"].startswith("<!-- linecite:report")]


@pytest.fixture
def gh():
    fake = FakeGitHub()
    yield fake
    fake.server.shutdown()


@pytest.fixture
def run(tmp_path, monkeypatch, gh):
    """Run the report script as the action's last step would, with the given step results."""

    def go(
        affected="", err="", check_code="", check_out="", event=None, comment_on="true",
        affected_code=None, key=".",
    ):  # fmt: skip
        files = {
            "LINECITE_AFFECTED_MD": affected,
            "LINECITE_AFFECTED_ERR": err,
            "LINECITE_CHECK_OUT": check_out,
        }
        for var, text in files.items():
            p = tmp_path / var
            p.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))
            monkeypatch.setenv(var, str(p))
        if affected_code is None:  # the affected step ran unless the test says otherwise
            affected_code = "0" if affected else ""
        ev = tmp_path / "event.json"
        ev.write_text(
            json.dumps(event if event is not None else {"pull_request": {"number": 7}})
        )
        for name in ("summary", "output"):
            (tmp_path / name).write_text("")
        env = {
            "LINECITE_CHECK_CODE": check_code,
            "LINECITE_AFFECTED_CODE": affected_code,
            "LINECITE_KEY": key,
            "LINECITE_COMMENT": comment_on,
            "GITHUB_TOKEN": "tkn",
            "GITHUB_API_URL": gh.url,
            "GITHUB_REPOSITORY": "o/r",
            "GITHUB_EVENT_PATH": str(ev),
            "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
            "GITHUB_OUTPUT": str(tmp_path / "output"),
        }
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        assert comment.main() == 0
        return (tmp_path / "summary").read_text("utf-8"), (tmp_path / "output").read_text("utf-8")

    return go


def test_affected_paragraphs_become_one_comment(run, gh):
    summary, output = run(
        affected=AFFECTED, check_code="0", check_out="x\nanchors 3 · broken 0"
    )
    assert output == "affected=1\n"
    assert "- line 3: `Locking`" in summary and "linecite:report" not in summary
    [c] = gh.ours()
    assert c["body"].startswith("<!-- linecite:report . -->\n")
    assert "- line 3: `Locking`" in c["body"]
    assert "`linecite check` passed: anchors 3 · broken 0" in c["body"]


def test_a_later_push_updates_the_comment_instead_of_adding_one(run, gh):
    gh.comments.append({"id": 1, "body": "someone else's review"})
    run(affected=AFFECTED)
    run(affected=NOTHING)
    [c] = gh.ours()
    assert NOTHING.strip() in c["body"]  # the stale list is gone
    assert len(gh.comments) == 2
    assert gh.calls.count("POST /repos/o/r/issues/7/comments") == 1


def test_nothing_to_say_posts_nothing(run, gh):
    summary, output = run(affected=NOTHING, check_code="0", check_out="anchors 3")
    assert gh.comments == [] and output == "affected=0\n"
    assert NOTHING.strip() in summary  # the summary still says so


def test_failed_check_is_worth_a_comment(run, gh):
    out = "drift   docs/a.md:3: L10 -> L12  [orders.py `row.lock()`]\n\nanchors 1 · broken 0 · drift 1"
    run(affected=NOTHING, check_code="1", check_out=out)
    [c] = gh.ours()
    assert (
        "<code>linecite check</code> failed: anchors 1 · broken 0 · drift 1</summary>"
        in c["body"]
    )
    assert "L10 -> L12" in c["body"] and "linecite sync" in c["body"]


def test_affected_error_is_reported(run, gh):
    run(
        err="error: 'abc' is not a commit; this is a shallow clone: fetch it\n",
        affected_code="2",
    )
    [c] = gh.ours()
    assert (
        "`linecite affected` could not run" in c["body"]
        and "shallow clone" in c["body"]
    )


def test_existing_comment_is_found_on_a_later_page(run, gh):
    gh.comments += [{"id": n, "body": f"comment {n}"} for n in range(150)]
    gh.comments.insert(120, {"id": 999, "body": "<!-- linecite:report . -->\nold"})
    run(affected=AFFECTED)
    assert "PATCH /repos/o/r/issues/comments/999" in gh.calls
    assert "POST /repos/o/r/issues/7/comments" not in gh.calls


def test_read_only_token_warns_and_does_not_fail(run, gh, capsys):
    gh.status = 403
    run(affected=AFFECTED)
    out = capsys.readouterr().out
    assert "::warning::linecite could not comment" in out and "job summary" in out


def test_push_event_and_disabled_comment_make_no_api_calls(run, gh):
    run(affected=AFFECTED, event={"ref": "refs/heads/main"})
    run(affected=AFFECTED, comment_on="false")
    assert gh.calls == []


def test_action_steps_take_inputs_through_env_not_script_text():
    yaml = pytest.importorskip("yaml")
    action = yaml.safe_load((ROOT / "action.yml").read_text(encoding="utf-8"))
    assert action["runs"]["using"] == "composite"
    steps = action["runs"]["steps"]
    for step in steps:
        # ${{ }} inside a script is spliced in before bash parses it: a branch name could run code
        assert "${{" not in step["run"], step["name"]
        assert step["shell"] == "bash"
    assert (ROOT / "action" / "comment.py").is_file()
    assert set(action["inputs"]) == {
        "check", "comment", "base", "working-directory", "config", "github-token"
    }  # fmt: skip


def _step(name: str) -> str:
    yaml = pytest.importorskip("yaml")
    action = yaml.safe_load((ROOT / "action.yml").read_text(encoding="utf-8"))
    return next(s["run"] for s in action["runs"]["steps"] if s["name"] == name)


def _runner_bash(script: str, tmp_path: Path, env: dict, cwd: Path):
    """Run a step script the way the runner runs `shell: bash`: with -e and pipefail."""
    import os
    import shutil
    import subprocess

    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("no bash")
    (tmp_path / "step.sh").write_bytes(script.encode())
    out = tmp_path / "github_output"
    out.write_text("")
    proc = subprocess.run(
        [bash, "--noprofile", "--norc", "-eo", "pipefail", str(tmp_path / "step.sh")],
        cwd=cwd,
        capture_output=True,
        env={**os.environ, "RUNNER_TEMP": str(tmp_path), "GITHUB_OUTPUT": str(out), **env},
    )
    return proc, out.read_text()


def _stub(tmp_path: Path, code: int, stdout: str, stderr: str = "") -> str:
    stub = tmp_path / "linecite-stub"
    stub.write_bytes(
        f"#!/usr/bin/env bash\nprintf '%s' '{stdout}'\nprintf '%s' '{stderr}' >&2\nexit {code}\n".encode()
    )
    stub.chmod(0o755)
    return stub.as_posix()


def test_check_step_keeps_a_failing_exit_code_under_bash_e(tmp_path):
    lc = _stub(tmp_path, 1, "drift   docs/a.md:3: L10 -> L12\n")
    proc, output = _runner_bash(
        _step("linecite check"), tmp_path, {"LINECITE": lc, "CONFIG": ""}, tmp_path
    )
    assert proc.returncode == 0, proc.stderr
    assert "code=1" in output and b"L10 -> L12" in proc.stdout


def test_affected_step_reports_an_error_instead_of_failing(sb, tmp_path):
    sb.write("a.txt", "x\n")
    base = sb.commit()
    lc = _stub(tmp_path, 2, "", "error: something broke")
    env = {
        "LINECITE": lc, "CONFIG": "", "BASE": base,
        "GITHUB_SERVER_URL": "https://github.com", "GITHUB_REPOSITORY": "o/r",
    }  # fmt: skip
    proc, _ = _runner_bash(_step("linecite affected"), tmp_path, env, sb.root)
    assert proc.returncode == 0, proc.stderr
    assert b"::warning::linecite affected: error: something broke" in proc.stdout


def test_warnings_on_stderr_do_not_hide_the_list(run, gh):
    run(affected=AFFECTED, err="<unknown>:5: SyntaxWarning: invalid escape sequence")
    [c] = gh.ours()
    assert "- line 3: `Locking`" in c["body"] and "could not run" not in c["body"]


def test_a_comment_quoting_the_marker_is_not_ours(run, gh):
    gh.comments.append({"id": 1, "body": "LGTM <!-- linecite:report . -->"})
    run(affected=AFFECTED)
    assert gh.comments[0]["body"] == "LGTM <!-- linecite:report . -->"
    assert "POST /repos/o/r/issues/7/comments" in gh.calls


def test_each_use_of_the_action_keeps_its_own_comment(run, gh):
    run(affected=AFFECTED, key="docs ")
    run(affected=AFFECTED, key="api .linecite.toml")
    run(affected=NOTHING, key="docs ")
    assert [c["body"].split("\n", 1)[0] for c in gh.ours()] == [
        "<!-- linecite:report docs -->",
        "<!-- linecite:report api .linecite.toml -->",
    ]
    assert NOTHING.strip() in gh.ours()[0]["body"]


def test_a_long_list_is_cut_to_fit_a_comment(run, gh):
    rows = "".join(
        f"- line {n}: `{'x' * 80}` \u2014 cites `a.py:{n}`, changed {n}\n" for n in range(3000)
    )
    head = "**3000 doc citation(s) point into code changed since `abc`.**\n\n"
    summary, _ = run(affected=head + rows)
    [c] = gh.ours()
    assert len(c["body"]) <= 65536 and "more line(s) in the job summary" in c["body"]
    assert "- line 2999:" in summary


def test_a_broken_api_response_does_not_fail_the_job(run, gh, capsys):
    gh.truncate = True
    run(affected=AFFECTED)
    assert "::warning::linecite could not comment" in capsys.readouterr().out


def test_undecodable_stderr_is_still_reported(run, gh):
    run(err=b"error: not a file: docs\\caf\xe9.md", affected_code="2")
    [c] = gh.ours()
    assert "could not run (exit 2)" in c["body"] and "caf\ufffd.md" in c["body"]


def test_a_skipped_affected_step_reports_no_leftover_list(run, gh):
    summary, output = run(affected=AFFECTED, affected_code="")
    assert output == "affected=0\n" and "Locking" not in summary and gh.comments == []


def test_reset_step_removes_leftover_report_files(tmp_path):
    for name in ("linecite-check.txt", "linecite-affected.md", "linecite-affected.err"):
        (tmp_path / name).write_text("old")
    proc, _ = _runner_bash(_step("Reset report files"), tmp_path, {}, tmp_path)
    assert proc.returncode == 0 and not list(tmp_path.glob("linecite-*"))
