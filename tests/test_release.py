"""Release plumbing: the version is written once per release, and only the release workflow can publish."""

import tomllib
from pathlib import Path

import pytest

import linecite

ROOT = Path(__file__).resolve().parent.parent


def test_package_version_matches_pyproject():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert linecite.__version__ == project["version"]


def test_readme_examples_pin_the_current_release():
    version = linecite.__version__
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert f"rev: v{version}" in readme and f"gotoUSA/linecite@v{version}" in readme


def test_only_the_publish_job_can_mint_a_pypi_token():
    yaml = pytest.importorskip("yaml")
    wf = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8"))
    assert wf[True] == {"release": {"types": ["published"]}}  # yaml reads the key `on` as True
    assert wf["permissions"] == {"contents": "read"}
    jobs = wf["jobs"]
    assert jobs["publish"]["permissions"] == {"id-token": "write"}
    assert jobs["publish"]["environment"]["name"] == "pypi"
    assert "permissions" not in jobs["build"]
    assert any("does not match pyproject.toml version" in s.get("run", "") for s in jobs["build"]["steps"])
