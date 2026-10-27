"""The make_mock_data command line: outputs, exit codes, logging."""

import logging
import shutil
from pathlib import Path

import pytest
import yaml

from tools import fixfiles, log
from tools import make_mock_data as cli
from tools import manifest as mf
from tools.settings import get_settings
from tools.tests.test_fixfiles import write_fake_set

FAST = ["--inventory-resolution", "10", "--levels", "3"]


@pytest.fixture
def fake_fetch(monkeypatch: pytest.MonkeyPatch) -> list[mf.FixFiles]:
    """Replace the EMC download with a synthetic 4x4 tile set written in place."""
    calls: list[mf.FixFiles] = []

    def fetch(
        fix: mf.FixFiles,
        resolution: str,
        n: int,
        radius_m: float,
        opener: object = None,
    ) -> dict[str, int]:
        del n, radius_m, opener
        calls.append(fix)
        written = write_fake_set(fix.directory, resolution, 4)
        return {
            p.name: p.stat().st_size
            for p in [written.mosaic, *written.grid_tiles, *written.grid_spec_tiles]
        }

    monkeypatch.setattr(fixfiles, "fetch", fetch)
    return calls


def test_success_writes_fix_inventories_and_manifest(
    tmp_path: Path, fake_fetch: list[mf.FixFiles]
) -> None:
    out = tmp_path / "C48"
    assert cli.main(["--resolution", "C48", "--out", str(out), *FAST]) == 0
    assert len(fake_fetch) == 1
    assert (out / "fix" / "C48_mosaic.nc").exists()
    for name in mf.CATEGORY_NAMES:
        assert (out / "inventories" / f"{name}.nc").exists()
    assert (out / "inventories" / "landmask.nc").exists()
    assert (out / "inventories" / "totals.yaml").exists()
    manifest = mf.Manifest.from_yaml(out / "manifest.yaml")
    assert manifest.resolution == "C48"
    assert manifest.clock.n_levels == 3
    assert manifest.grids["inventory"].nlon == 36
    assert set(manifest.fix.sizes) == set(fixfiles.expected_names("C48"))
    assert manifest.generator.command.startswith("make_mock_data --resolution C48")


def test_clock_options_reach_the_manifest(
    tmp_path: Path, fake_fetch: list[mf.FixFiles]
) -> None:
    out = tmp_path / "C96"
    argv = [
        "--resolution",
        "C96",
        "--out",
        str(out),
        "--start",
        "2020-01-01T00:00:00Z",
        "--hours",
        "6",
        "--step-seconds",
        "1800",
        *FAST,
    ]
    assert cli.main(argv) == 0
    manifest = mf.Manifest.from_yaml(out / "manifest.yaml")
    assert manifest.clock.duration_hours == 6
    assert manifest.clock.step_seconds == 1800
    assert manifest.clock.n_steps == 12
    assert manifest.clock.start.isoformat() == "2020-01-01T00:00:00+00:00"
    assert fake_fetch[0].mosaic.name == "C96_mosaic.nc"


def test_fetch_failure_exits_1_and_writes_no_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def failing(*args: object, **kwargs: object) -> dict[str, int]:
        del args, kwargs
        raise fixfiles.FixFileError("server said no")

    monkeypatch.setattr(fixfiles, "fetch", failing)
    out = tmp_path / "C48"
    with caplog.at_level(logging.ERROR, logger=log.ROOT_NAME):
        assert cli.main(["--resolution", "C48", "--out", str(out), *FAST]) == 1
    assert "server said no" in caplog.text
    assert not (out / "manifest.yaml").exists()


def test_bad_resolution_is_a_usage_error(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--resolution", "C192", "--out", str(tmp_path)])
    assert exc.value.code == 2


def test_naive_start_is_a_usage_error(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(
            [
                "--resolution",
                "C48",
                "--out",
                str(tmp_path),
                "--start",
                "2021-03-22T06:00:00",
            ]
        )
    assert exc.value.code == 2


def test_invalid_clock_is_a_usage_error(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR, logger=log.ROOT_NAME):
        assert (
            cli.main(
                ["--resolution", "C48", "--out", str(tmp_path), "--step-seconds", "700"]
            )
            == 2
        )
    assert "divide" in caplog.text


@pytest.mark.usefixtures("fake_fetch")
def test_log_level_is_honoured(tmp_path: Path) -> None:
    assert (
        cli.main(
            [
                "--resolution",
                "C48",
                "--out",
                str(tmp_path / "x"),
                "--log-level",
                "DEBUG",
                *FAST,
            ]
        )
        == 0
    )
    assert logging.getLogger(log.ROOT_NAME).level == logging.DEBUG


@pytest.mark.usefixtures("fake_fetch")
def test_fix_url_override_reaches_the_manifest(tmp_path: Path) -> None:
    out = tmp_path / "C48"
    assert (
        cli.main(
            [
                "--resolution",
                "C48",
                "--out",
                str(out),
                "--fix-url",
                "http://mirror.example/{resolution}/",
                *FAST,
            ]
        )
        == 0
    )
    assert (
        mf.Manifest.from_yaml(out / "manifest.yaml").fix.source_url
        == "http://mirror.example/C48/"
    )


def test_unparseable_start_is_a_usage_error(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(
            ["--resolution", "C48", "--out", str(tmp_path), "--start", "yesterday"]
        )
    assert exc.value.code == 2


@pytest.mark.usefixtures("fake_fetch")
def test_defaults_option_and_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    custom = tmp_path / "custom.yaml"
    doc = yaml.safe_load(get_settings().defaults_file.read_text())
    doc["clock"]["duration_hours"] = 2
    custom.write_text(yaml.safe_dump(doc))
    out = tmp_path / "a"
    assert (
        cli.main(
            ["--resolution", "C48", "--out", str(out), "--defaults", str(custom), *FAST]
        )
        == 0
    )
    assert mf.Manifest.from_yaml(out / "manifest.yaml").clock.duration_hours == 2
    env_file = tmp_path / "env.yaml"
    shutil.copy(custom, env_file)
    monkeypatch.setenv("PROTOS_DEFAULTS", str(env_file))
    get_settings.cache_clear()
    out2 = tmp_path / "b"
    assert cli.main(["--resolution", "C48", "--out", str(out2), *FAST]) == 0
    assert mf.Manifest.from_yaml(out2 / "manifest.yaml").clock.duration_hours == 2


def test_missing_defaults_file_is_a_usage_error(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR, logger=log.ROOT_NAME):
        assert (
            cli.main(
                [
                    "--resolution",
                    "C48",
                    "--out",
                    str(tmp_path),
                    "--defaults",
                    str(tmp_path / "nope.yaml"),
                ]
            )
            == 2
        )
    assert "nope.yaml" in caplog.text
