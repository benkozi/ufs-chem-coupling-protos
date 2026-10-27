"""The project logger.

One configuration entry point (`configure`), one handler on stderr, and named children under `protos`
(`get_logger("fixfiles")` -> `protos.fixfiles`). The tooling logs; it never prints (ruff T20).
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Iterator

ROOT_NAME = "protos"
LEVEL_ENV = "PROTOS_LOG_LEVEL"
DEFAULT_LEVEL = "INFO"
FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def get_logger(name: str) -> logging.Logger:
    """A child of the project logger; `name` is the module or component, without the `protos.` prefix."""
    return logging.getLogger(f"{ROOT_NAME}.{name}")


def configure(level: str | None = None) -> logging.Logger:
    """Install the stderr handler once and set the level (argument, else `PROTOS_LOG_LEVEL`, else INFO).

    Idempotent: a second call only changes the level, so tests and nested entry points may call it freely.
    """
    chosen = (level or os.environ.get(LEVEL_ENV) or DEFAULT_LEVEL).upper()
    if chosen not in LEVELS:
        raise ValueError(
            f"unknown log level {chosen!r}; expected one of {', '.join(LEVELS)}"
        )
    root = logging.getLogger(ROOT_NAME)
    if not any(getattr(h, "_protos_handler", False) for h in root.handlers):
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(FORMAT))
        handler._protos_handler = True  # type: ignore[attr-defined]  # marks our handler among any others
        root.addHandler(handler)
    root.setLevel(chosen)
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
