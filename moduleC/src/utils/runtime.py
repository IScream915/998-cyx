from __future__ import annotations

from pathlib import Path


def get_device(config_device: str | None) -> str:
    try:
        import torch
    except ModuleNotFoundError:
        return "cpu"

    requested = (config_device or "auto").strip().lower()
    has_mps = hasattr(torch.backends, "mps") and torch.backends.mps.is_available()

    if requested in {"", "auto"}:
        if torch.cuda.is_available():
            return "cuda:0"
        if has_mps:
            return "mps"
        return "cpu"

    if requested == "cpu":
        return "cpu"

    if requested == "mps" and has_mps:
        return "mps"

    if requested in {"cuda", "gpu"} and torch.cuda.is_available():
        return "cuda:0"

    if requested.startswith("cuda:") and torch.cuda.is_available():
        try:
            index = int(requested.split(":", maxsplit=1)[1])
        except ValueError:
            return "cpu"
        if 0 <= index < torch.cuda.device_count():
            return f"cuda:{index}"
        return "cpu"

    if requested.isdigit() and torch.cuda.is_available():
        index = int(requested)
        if 0 <= index < torch.cuda.device_count():
            return f"cuda:{index}"

    return "cpu"


def ensure_parent_dir(path: str | Path) -> Path:
    path_obj = Path(path)
    path_obj.parent.mkdir(parents=True, exist_ok=True)
    return path_obj
