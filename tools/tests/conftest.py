"""Shared test setup: the suite is offline by construction."""

import urllib.request
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any test that reaches the real `urlopen` fails: downloads are always mocked."""

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError(f"network access attempted: {args} {kwargs}")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)


@pytest.fixture
def c48_fixture_dir() -> Path:
    return FIXTURES / "C48"
