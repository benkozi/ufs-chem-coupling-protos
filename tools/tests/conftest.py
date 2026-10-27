"""Shared test setup.

Settings are re-read for every test (so `monkeypatch.setenv("PROTOS_…")` takes effect), and the suite is
offline by construction: an autouse fixture makes `urllib.request.urlopen` fail, so every download under
test is mocked. The one exception is `c48_fixture_dir`, which fetches the real C48 mosaic and tile-1 files
once into the settings' `fixture_dir` (git-ignored; no NetCDF file is ever committed) through the real opener
captured at import, and reuses them afterwards.
"""

import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

from tools import fixfiles
from tools.settings import get_settings

REAL_URLOPEN = urllib.request.urlopen
REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_NAMES = ("C48_mosaic.nc", "C48_grid.tile1.nc", "C48_grid_spec.tile1.nc")


@pytest.fixture(autouse=True)
def _fresh_settings() -> Iterator[None]:
    """Every test reads the settings from its own environment."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any test that reaches the real `urlopen` fails: downloads are always mocked."""

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError(f"network access attempted: {args} {kwargs}")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)


@pytest.fixture(scope="session")
def c48_fixture_dir() -> Path:
    """The real C48 mosaic, grid tile 1 and grid_spec tile 1, downloaded on demand and cached under data/."""
    get_settings.cache_clear()
    configured = get_settings().fixture_dir
    directory = configured if configured.is_absolute() else REPO_ROOT / configured
    fix = fixfiles.fix_files_for("C48", directory)
    for name in FIXTURE_NAMES:
        path = directory / name
        if not path.exists():
            fixfiles.download(fixfiles.url_for(fix, name), path, REAL_URLOPEN)
    return directory
