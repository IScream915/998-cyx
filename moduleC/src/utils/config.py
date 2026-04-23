from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 fallback
    import tomli as tomllib


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_config(path: str | Path) -> dict[str, Any]:
    config_path = Path(path)
    if not config_path.is_absolute():
        config_path = repo_root() / config_path
    with config_path.open("rb") as fh:
        return tomllib.load(fh)


def resolve_repo_path(path: str | Path) -> Path:
    path_obj = Path(path)
    if path_obj.is_absolute():
        return path_obj
    return repo_root() / path_obj


def ensure_dir(path: str | Path) -> Path:
    target = resolve_repo_path(path)
    target.mkdir(parents=True, exist_ok=True)
    return target
