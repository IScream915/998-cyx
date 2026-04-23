from __future__ import annotations

import logging
from typing import Any


DEFAULT_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configure_logging(
    config: dict[str, Any] | None = None,
    *,
    level: str | None = None,
) -> None:
    log_cfg = dict((config or {}).get("logging", {}))
    resolved_level = str(level or log_cfg.get("level", "WARNING")).upper()
    numeric_level = getattr(logging, resolved_level, logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format=str(log_cfg.get("format", DEFAULT_LOG_FORMAT)),
        force=True,
    )
