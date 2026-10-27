"""The project logger: one root handler, configured once, level from the caller or the environment."""

import logging
from collections.abc import Iterator

import pytest

from tools import log


@pytest.fixture(autouse=True)
def _reset_protos_logger() -> Iterator[None]:
    """Each test starts from an unconfigured `protos` logger."""
    log.reset()
    yield
    log.reset()


def test_get_logger_is_a_child_of_the_project_logger() -> None:
    logger = log.get_logger("fixfiles")
    assert logger.name == "protos.fixfiles"
    assert logger.parent is logging.getLogger(log.ROOT_NAME)


def test_configure_installs_one_handler_even_when_called_twice() -> None:
    log.configure("INFO")
    log.configure("DEBUG")
    root = logging.getLogger(log.ROOT_NAME)
    # pytest's caplog may attach its own capture handlers to a non-propagating logger; count only ours.
    assert sum(1 for h in root.handlers if getattr(h, "_protos_handler", False)) == 1
    assert root.level == logging.DEBUG


def test_level_defaults_to_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(log.LEVEL_ENV, "WARNING")
    log.configure()
    assert logging.getLogger(log.ROOT_NAME).level == logging.WARNING


def test_level_defaults_to_info_without_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(log.LEVEL_ENV, raising=False)
    log.configure()
    assert logging.getLogger(log.ROOT_NAME).level == logging.INFO


def test_unknown_level_is_rejected() -> None:
    with pytest.raises(ValueError, match="log level"):
        log.configure("LOUD")


def test_messages_reach_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    log.configure("INFO")
    log.get_logger("t").info("hello %s", "there")
    assert "INFO protos.t: hello there" in capsys.readouterr().err
