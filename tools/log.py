"""The project logger.

One configuration entry point (`configure`), one handler on stderr, and named children under `protos`
(`get_logger("fixfiles")` -> `protos.fixfiles`). The level and format come from the shared settings
(`PROTOS_LOG_LEVEL`); the tooling logs, it never prints (ruff T20).
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterator

from tools.settings import LogLevel, get_settings

ROOT_NAME = "protos"


def get_logger(name: str) -> logging.Logger:
    """A child of the project logger; `name` is the module or component, without the `protos.` prefix."""
    return logging.getLogger(f"{ROOT_NAME}.{name}")


def configure(level: LogLevel | None = None) -> logging.Logger:
    """Install the stderr handler once and set the level (`level`, else the settings' `log_level`).

    Idempotent: a second call only changes the level, so tests and nested entry points may call it freely.
    """
    settings = get_settings()
    root = logging.getLogger(ROOT_NAME)
    if not any(getattr(h, "_protos_handler", False) for h in root.handlers):
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(settings.log_format))
        handler._protos_handler = True  # type: ignore[attr-defined]  # marks our handler among any others
        root.addHandler(handler)
    root.setLevel(level or settings.log_level)
    root.propagate = False
    return root


def reset() -> None:
    """Remove the project handler and level (tests only; a fresh `configure` follows)."""
    root = logging.getLogger(ROOT_NAME)
    for handler in list(_project_handlers(root)):
        root.removeHandler(handler)
    root.setLevel(logging.NOTSET)


def _project_handlers(root: logging.Logger) -> Iterator[logging.Handler]:
    for handler in root.handlers:
        if getattr(handler, "_protos_handler", False):
            yield handler
