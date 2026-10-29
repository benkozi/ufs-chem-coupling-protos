"""The conservation checker: matching by time stamp, the error metric, exit codes and usage errors."""

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tools import check_conservation as cc
from tools import log
from tools.inventories import CategoryTotals, Totals, VariableTotals

START = datetime(2021, 3, 22, 6, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _configured_logger() -> None:
    """pytest attaches caplog to the non-propagating project logger only if it is configured before at_level."""
    log.configure("INFO")


def stamps(count: int, step: timedelta) -> list[datetime]:
    return [START + k * step for k in range(count)]


def write_totals(
    path: Path,
    times: list[datetime] | None,
    records: list[list[float]],
    category: str = "anthro",
    variable: str = "NO",
) -> Path:
    Totals(
        categories={
            category: CategoryTotals(
                file=Path("data/C48/inventories/anthro.nc"),
                times=times,
                variables={
                    variable: VariableTotals(units="kg m-2 s-1", records=records)
                },
            )
        }
    ).to_yaml(path)
    return path


@pytest.fixture
def files(tmp_path: Path) -> tuple[Path, Path]:
    """Hourly inventory totals (25 records) and MET totals every 720 s (120 records), identical at the hours."""
    hourly = stamps(25, timedelta(hours=1))
    expected = write_totals(
        tmp_path / "totals.yaml", hourly, [[1000.0 + 10.0 * k] for k in range(25)]
    )
    steps = stamps(120, timedelta(seconds=720))
    # 2 per step = 10 per hour: exact in floating point, so the hourly stamps agree bit for bit
    actual = write_totals(
        tmp_path / "met_import_totals.yaml",
        steps,
        [[1000.0 + 2.0 * k] for k in range(120)],
    )
    return expected, actual


def args(expected: Path, actual: Path, *extra: str) -> list[str]:
    return [
        "--expected",
        str(expected),
        "--actual",
        str(actual),
        "--category",
        "anthro",
        "--variable",
        "NO",
        *extra,
    ]


def test_identical_records_pass_with_zero_error(
    files: tuple[Path, Path], caplog: pytest.LogCaptureFixture
) -> None:
    expected, actual = files
    with caplog.at_level(logging.INFO, logger="protos"):
        assert cc.main(args(expected, actual, "--rtol", "1e-12")) == 0
    assert "24 matched records, max relative error 0.000e+00" in caplog.text
    assert "within rtol 1.0e-12" in caplog.text


def test_a_perturbation_above_the_tolerance_fails_and_names_the_stamp(
    files: tuple[Path, Path], caplog: pytest.LogCaptureFixture
) -> None:
    expected, actual = files
    comparisons = cc.compare(expected, actual, "anthro", "NO", min_matches=24)
    assert len(comparisons) == 24
    # perturb the record at 14:00 (step 40) by 1e-3 relative
    perturbed = Totals.from_yaml(actual)
    records = perturbed.categories["anthro"].variables["NO"].records
    records[40] = [records[40][0] * (1 + 1e-3)]
    perturbed.to_yaml(actual)
    with caplog.at_level(logging.INFO, logger="protos"):
        assert cc.main(args(expected, actual, "--rtol", "1e-6")) == 1
    assert "exceeds rtol 1.0e-06" in caplog.text
    assert "at 2021-03-22T14:00:00+00:00 level 1" in caplog.text
    # the same perturbation is within a looser tolerance
    assert cc.main(args(expected, actual, "--rtol", "1e-2")) == 0


def test_run_dir_names_the_met_file(files: tuple[Path, Path], tmp_path: Path) -> None:
    expected, actual = files
    argv = [
        "--expected",
        str(expected),
        "--run-dir",
        str(tmp_path),
        "--category",
        "anthro",
        "--variable",
        "NO",
    ]
    assert cc.main(argv) == 0
    assert actual.name == "met_import_totals.yaml"


def test_missing_times_is_a_usage_error(
    tmp_path: Path, files: tuple[Path, Path]
) -> None:
    expected, actual = files
    legacy = write_totals(tmp_path / "legacy.yaml", None, [[1.0]] * 25)
    assert cc.main(args(legacy, actual)) == 2
    assert cc.main(args(expected, legacy)) == 2


def test_too_few_common_stamps_is_a_usage_error(
    tmp_path: Path, files: tuple[Path, Path]
) -> None:
    expected, _ = files
    shifted = write_totals(
        tmp_path / "shifted.yaml", stamps(10, timedelta(minutes=7)), [[1000.0]] * 10
    )
    assert cc.main(args(expected, shifted)) == 2
    assert (
        cc.main(args(expected, shifted, "--min-matches", "1")) == 0
    )  # the start stamp is common


def test_unknown_category_or_variable_is_a_usage_error(
    files: tuple[Path, Path],
) -> None:
    expected, actual = files
    base = ["--expected", str(expected), "--actual", str(actual)]
    assert cc.main([*base, "--category", "fire", "--variable", "NO"]) == 2
    assert cc.main([*base, "--category", "anthro", "--variable", "SO2"]) == 2


def test_level_count_mismatch_is_a_usage_error(
    tmp_path: Path, files: tuple[Path, Path]
) -> None:
    expected, _ = files
    two_levels = write_totals(
        tmp_path / "two.yaml", stamps(25, timedelta(hours=1)), [[1.0, 2.0]] * 25
    )
    assert cc.main(args(expected, two_levels)) == 2


def test_record_and_stamp_counts_must_agree(
    tmp_path: Path, files: tuple[Path, Path]
) -> None:
    expected, _ = files
    short = write_totals(
        tmp_path / "short.yaml", stamps(25, timedelta(hours=1)), [[1.0]] * 24
    )
    assert cc.main(args(expected, short)) == 2


def test_unreadable_file_is_a_usage_error(
    tmp_path: Path, files: tuple[Path, Path]
) -> None:
    expected, _ = files
    assert cc.main(args(expected, tmp_path / "missing.yaml")) == 2
    bad = tmp_path / "bad.yaml"
    bad.write_text("categories: 3\n")
    assert cc.main(args(expected, bad)) == 2


def test_zero_expected_uses_the_absolute_tolerance(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    hourly = stamps(25, timedelta(hours=1))
    expected = write_totals(tmp_path / "zero.yaml", hourly, [[0.0]] * 25)
    actual = write_totals(tmp_path / "near_zero.yaml", hourly, [[1e-9]] * 25)
    with caplog.at_level(logging.INFO, logger="protos"):
        assert cc.main(args(expected, actual, "--rtol", "1e-6", "--atol", "1e-6")) == 0
    assert "absolute error (expected total is zero) 1.000e-09" in caplog.text
    assert "within atol 1.0e-06" in caplog.text
    with caplog.at_level(logging.INFO, logger="protos"):
        assert cc.main(args(expected, actual, "--rtol", "1e-6", "--atol", "1e-12")) == 1
    assert "exceeds atol 1.0e-12" in caplog.text


def test_worst_picks_the_record_furthest_over_its_tolerance() -> None:
    one_hour = timedelta(hours=1)
    comparisons = [
        cc.Comparison(
            time=START,
            expected=[1.0, 1.0],
            actual=[1.0, 1.1],
            errors=[0.0, 0.1],
            zero_expected=[False, False],
        ),
        cc.Comparison(
            time=START + one_hour,
            expected=[1.0],
            actual=[1.5],
            errors=[0.5],
            zero_expected=[False],
        ),
        cc.Comparison(
            time=START + 2 * one_hour,
            expected=[0.0],
            actual=[1e-3],
            errors=[1e-3],
            zero_expected=[True],
        ),
    ]
    # a loose atol: the 50 % relative error leads
    assert cc.worst(comparisons, rtol=1e-6, atol=1.0) == (
        0.5,
        START + one_hour,
        0,
        True,
    )
    # a tight atol: the zero-expected record exceeds its tolerance by the larger factor
    assert cc.worst(comparisons, rtol=1.0, atol=1e-9) == (
        1e-3,
        START + 2 * one_hour,
        0,
        True,
    )
    # nothing exceeded: the largest ratio still names the worst record
    assert cc.worst(comparisons[:1], rtol=1.0, atol=1.0) == (0.1, START, 1, False)


@pytest.fixture
def deep_files(tmp_path: Path) -> tuple[Path, Path]:
    """A one-level inventory against an nz-deep surface export: the flux in level 1, zero above."""
    hourly = stamps(25, timedelta(hours=1))
    expected = write_totals(
        tmp_path / "totals.yaml", hourly, [[1000.0 + 10.0 * k] for k in range(25)]
    )
    steps = stamps(120, timedelta(seconds=720))
    actual = write_totals(
        tmp_path / "met_import_totals.yaml",
        steps,
        [[1000.0 + 2.0 * k, 0.0, 0.0] for k in range(120)],
    )
    return expected, actual


def test_level_selects_one_level_of_a_deeper_export(
    deep_files: tuple[Path, Path], caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="protos"):
        assert cc.main(args(*deep_files, "--level", "1", "--rtol", "1e-15")) == 0
    assert "24 matched records" in caplog.text
    assert "level 1 within" in caplog.text


def test_without_level_a_deeper_export_is_still_a_usage_error(
    deep_files: tuple[Path, Path],
) -> None:
    assert cc.main(args(*deep_files)) == 2


def test_zero_above_passes_when_the_levels_above_are_zero(
    deep_files: tuple[Path, Path],
) -> None:
    assert cc.main(args(*deep_files, "--level", "1", "--zero-above")) == 0


def test_zero_above_fails_on_a_non_zero_level_and_names_it(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    hourly = stamps(25, timedelta(hours=1))
    expected = write_totals(tmp_path / "totals.yaml", hourly, [[1.0]] * 25)
    actual = write_totals(
        tmp_path / "met.yaml", hourly, [[1.0, 0.0, 0.0]] * 24 + [[1.0, 0.0, 5.0e-30]]
    )
    with caplog.at_level(logging.INFO, logger="protos"):
        assert cc.main(args(expected, actual, "--level", "1", "--zero-above")) == 1
    assert "level 3" in caplog.text
    assert "is not zero" in caplog.text


def test_level_beyond_either_file_is_a_usage_error(
    deep_files: tuple[Path, Path],
) -> None:
    assert cc.main(args(*deep_files, "--level", "2")) == 2
    assert cc.main(args(*deep_files, "--level", "4")) == 2


def test_zero_above_needs_level(deep_files: tuple[Path, Path]) -> None:
    with pytest.raises(SystemExit) as exc:
        cc.main(args(*deep_files, "--zero-above"))
    assert exc.value.code == 2


def test_level_must_be_positive(deep_files: tuple[Path, Path]) -> None:
    with pytest.raises(SystemExit) as exc:
        cc.main(args(*deep_files, "--level", "0"))
    assert exc.value.code == 2
