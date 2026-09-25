"""Configuration: which code repository is cited and which documents cite it.

Looked up in this order: --config FILE, ./.coderef.toml (top-level keys), ./pyproject.toml ([tool.coderef]).

    code_root        = "."                 # git repo of the cited code, relative to the config file
    docs             = ["docs/**/*.md"]    # documents to scan (globs, relative to the config file)
    number_suffixes  = ["`"]               # text allowed between a number and its anchor, e.g. `app.py:12`<!--@ …-->
    legacy           = "error"             # un-anchored path:line citations: "error" | "warn" | "off"
    legacy_extensions = [...]              # file extensions that make `name.ext:12` a citation
    ignore_patterns  = []                  # regexes of regions to skip (the legacy scan also skips fenced code)
"""

from __future__ import annotations

import glob
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .errors import ConfigError

DEFAULT_SUFFIXES = ("`",)
DEFAULT_EXTENSIONS = (
    "py", "pyi", "js", "jsx", "mjs", "cjs", "ts", "tsx", "go", "rs", "java", "kt", "rb", "php",
    "c", "h", "cc", "cpp", "hpp", "cs", "swift", "scala", "sql", "sh",
    "yml", "yaml", "toml", "json", "cfg", "ini", "html", "txt",
)  # fmt: skip
LEGACY_MODES = ("error", "warn", "off")
KEYS = {
    "code_root",
    "docs",
    "number_suffixes",
    "legacy",
    "legacy_extensions",
    "ignore_patterns",
}


@dataclass(frozen=True)
class Config:
    code_root: Path
    base: Path
    docs: tuple[str, ...] = ()
    number_suffixes: tuple[str, ...] = DEFAULT_SUFFIXES
    legacy: str = "error"
    legacy_extensions: tuple[str, ...] = DEFAULT_EXTENSIONS
    ignore_patterns: tuple[str, ...] = ()

    def doc_files(self) -> list[Path]:
        seen: dict[Path, None] = {}
        for pattern in self.docs:
            for hit in sorted(glob.glob(str(self.base / pattern), recursive=True)):
                p = Path(hit)
                if p.is_file():
                    seen.setdefault(p, None)
        return list(seen)


def _section(path: Path) -> dict | None:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}") from e
    if path.name == "pyproject.toml":
        return data.get("tool", {}).get("coderef")
    return data.get("tool", {}).get("coderef", data)


def _strings(data: dict, key: str, where: Path) -> tuple[str, ...] | None:
    if key not in data:
        return None
    value = data[key]
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"{where}: {key} must be a list of strings")
    return tuple(value)


def load(config_path: Path | None, root_override: Path | None, cwd: Path) -> Config:
    data: dict = {}
    base = cwd
    source = None
    if config_path is not None:
        if not config_path.is_file():
            raise ConfigError(f"config file not found: {config_path}")
        data = _section(config_path) or {}
        base, source = config_path.resolve().parent, config_path
    else:
        for name in (".coderef.toml", "pyproject.toml"):
            p = cwd / name
            if p.is_file():
                section = _section(p)
                if section is not None:
                    data, base, source = section, cwd, p
                    break

    unknown = set(data) - KEYS
    if unknown:
        raise ConfigError(f"{source}: unknown key(s) {', '.join(sorted(unknown))}")

    legacy = data.get("legacy", "error")
    if legacy not in LEGACY_MODES:
        raise ConfigError(f"{source}: legacy must be one of {', '.join(LEGACY_MODES)}")
    ignore = _strings(data, "ignore_patterns", source) or ()
    for pattern in ignore:
        try:
            re.compile(pattern)
        except re.error as e:
            raise ConfigError(f"{source}: bad ignore pattern {pattern!r}: {e}") from e
    suffixes = _strings(data, "number_suffixes", source)
    if suffixes is not None and (not suffixes or not all(suffixes)):
        raise ConfigError(f"{source}: number_suffixes must be non-empty strings")

    code_root = (
        root_override
        if root_override is not None
        else base / data.get("code_root", ".")
    )
    return Config(
        code_root=Path(code_root).resolve(),
        base=base,
        docs=_strings(data, "docs", source) or (),
        number_suffixes=suffixes or DEFAULT_SUFFIXES,
        legacy=legacy,
        legacy_extensions=_strings(data, "legacy_extensions", source)
        or DEFAULT_EXTENSIONS,
        ignore_patterns=ignore,
    )
