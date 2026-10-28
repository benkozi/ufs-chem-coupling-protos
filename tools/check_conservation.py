"""Compare MET's hosted-import totals with the inventory totals they were regridded from.

    uv run python -m tools.check_conservation --expected data/C48/inventories/totals.yaml \
        --run-dir build/protos/cece_met/run_C48_np6 --category anthro --variable NO --rtol 1e-4

Both files are `tools.inventories.Totals` (the task-2 generator writes the first, MET the second, as
`met_import_totals.yaml` in its run directory: --run-dir names the directory, --actual the file itself).
Records are matched by time stamp: MET records every coupling step, stamped with the provider's export time
(CECE evaluates its streams at the step start), and the inventory records sit on the hourly axis, so every
fifth step of a 720 s run coincides with a record and CECE's linear interpolation equals it there. For each
common stamp and level the relative error |actual - expected| / |expected| is logged (an expected total of
zero is compared absolutely against --atol); the worst record is logged, and the exit status is 0 when every
record is within its tolerance, 1 when one exceeds it (the message names the stamp and the tolerance), 2 for
a usage error (unknown category or variable, no `times` in a file, fewer common stamps than --min-matches, a
level-count mismatch, a --level beyond either file).

--level K compares level K alone (1-based), so a one-level inventory can be checked against an nz-deep
surface export whose flux sits in level 1 (task 6: CECE's exports are all nz deep); --zero-above then also
requires every actual level above K to be exactly zero (exit status 1 otherwise, naming the stamp and level).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import get_args

import pydantic

from tools import log
from tools.inventories import CategoryTotals, Totals
from tools.settings import LogLevel, get_settings

logger = log.get_logger("check_conservation")


class UsageError(Exception):
    """A problem with the inputs rather than with conservation (exit status 2)."""


class Comparison(pydantic.BaseModel):
    """One matched record: the stamp, both totals per level and the error per level."""

    model_config = pydantic.ConfigDict(extra="forbid")

    time: datetime = pydantic.Field(
        description="Record time stamp common to both files (UTC)."
    )
    expected: list[float] = pydantic.Field(
        description="Inventory total per level, field times area."
    )
    actual: list[float] = pydantic.Field(
        description="MET total per level, field times area."
    )
    errors: list[float] = pydantic.Field(
        description="Per level: relative error when the expected total is non-zero, else the absolute difference."
    )
    zero_expected: list[bool] = pydantic.Field(
        description="Per level: whether the expected total is zero (the error is then absolute, judged by atol)."
    )
    first_level: int = pydantic.Field(
        default=1,
        description="1-based level number of the first compared level (the --level value, or 1 for all levels).",
    )
    nonzero_above: list[int] = pydantic.Field(
        default_factory=list,
        description="1-based actual levels above the compared one whose total is not exactly zero (--level only).",
    )


def _category(totals: Totals, name: str, path: Path) -> CategoryTotals:
    try:
        return totals.categories[name]
    except KeyError as exc:
        raise UsageError(
            f"{path}: no category {name!r} (has {sorted(totals.categories)})"
        ) from exc


def _records(
    category: CategoryTotals, variable: str, path: Path
) -> dict[datetime, list[float]]:
    if category.times is None:
        raise UsageError(
            f"{path}: the category carries no `times`; regenerate it with the task-4 tooling"
        )
    try:
        records = category.variables[variable].records
    except KeyError as exc:
        raise UsageError(
            f"{path}: no variable {variable!r} (has {sorted(category.variables)})"
        ) from exc
    if len(records) != len(category.times):
        raise UsageError(
            f"{path}: {len(records)} records but {len(category.times)} time stamps for {variable!r}"
        )
    return dict(zip(category.times, records, strict=True))


def compare(
    expected_path: Path,
    actual_path: Path,
    category: str,
    variable: str,
    min_matches: int,
    level: int | None = None,
) -> list[Comparison]:
    """The matched records in time order; raises UsageError when the files cannot be compared."""
    expected = _records(
        _category(Totals.from_yaml(expected_path), category, expected_path),
        variable,
        expected_path,
    )
    actual = _records(
        _category(Totals.from_yaml(actual_path), category, actual_path),
        variable,
        actual_path,
    )
    common = sorted(set(expected) & set(actual))
    if len(common) < min_matches:
        raise UsageError(
            f"only {len(common)} common time stamps between {expected_path} ({len(expected)} records) and "
            f"{actual_path} ({len(actual)} records); at least {min_matches} required"
        )
    comparisons: list[Comparison] = []
    for when in common:
        e, a = expected[when], actual[when]
        nonzero_above: list[int] = []
        if level is not None:
            if level > len(e) or level > len(a):
                raise UsageError(
                    f"{when.isoformat()}: level {level} requested but {len(e)} expected and {len(a)} actual levels"
                )
            nonzero_above = [k + 1 for k in range(level, len(a)) if a[k] != 0.0]
            e, a = [e[level - 1]], [a[level - 1]]
        if len(e) != len(a):
            raise UsageError(
                f"{when.isoformat()}: {len(e)} expected levels but {len(a)} actual levels"
            )
        errors = [
            abs(av - ev) / abs(ev) if ev != 0.0 else abs(av - ev)
            for ev, av in zip(e, a, strict=True)
        ]
        comparisons.append(
            Comparison(
                time=when,
                expected=e,
                actual=a,
                errors=errors,
                zero_expected=[ev == 0.0 for ev in e],
                first_level=level if level is not None else 1,
                nonzero_above=nonzero_above,
            )
        )
    return comparisons


def worst(
    comparisons: Sequence[Comparison], rtol: float, atol: float
) -> tuple[float, datetime, int, bool]:
    """The record and level that exceeds its tolerance by the largest factor.

    Returns the error, its stamp, its level index and whether that tolerance was exceeded (a relative
    error is judged by rtol, the absolute error of a zero-expected record by atol).
    """
    best = (-1.0, comparisons[0].time, 0, False)
    best_ratio = -1.0
    for c in comparisons:
        for k, (err, zero) in enumerate(zip(c.errors, c.zero_expected, strict=True)):
            ratio = err / (atol if zero else rtol)
            if ratio > best_ratio:
                best_ratio = ratio
                best = (err, c.time, k, ratio > 1.0)
    return best


def _positive(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be a positive integer, got {value}")
    return value


def build_parser() -> argparse.ArgumentParser:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        prog="check_conservation", description=__doc__.split("\n\n")[0]
    )
    parser.add_argument(
        "--expected", required=True, type=Path, help="the generator's totals.yaml"
    )
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("--actual", type=Path, help="MET's totals file")
    which.add_argument(
        "--run-dir",
        type=Path,
        help=f"the run directory holding MET's {settings.met_totals_filename}",
    )
    parser.add_argument(
        "--category", required=True, help="category name in both files, e.g. anthro"
    )
    parser.add_argument(
        "--variable", required=True, help="variable name in both files, e.g. NO"
    )
    parser.add_argument(
        "--rtol",
        type=float,
        default=settings.conservation_rtol,
        help=f"largest relative error allowed (default {settings.conservation_rtol:g})",
    )
    parser.add_argument(
        "--atol",
        type=float,
        default=settings.conservation_atol,
        help=f"largest absolute error allowed where the expected total is zero (default {settings.conservation_atol:g})",
    )
    parser.add_argument(
        "--min-matches",
        type=int,
        default=settings.conservation_min_matches,
        help=f"smallest number of common time stamps accepted (default {settings.conservation_min_matches})",
    )
    parser.add_argument(
        "--level",
        type=_positive,
        default=None,
        help="compare this level alone (1-based), e.g. 1 for a surface flux in an nz-deep export",
    )
    parser.add_argument(
        "--zero-above",
        action="store_true",
        help="with --level: every actual level above it must be exactly zero",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        choices=get_args(LogLevel),
        help=f"log level (default: $PROTOS_LOG_LEVEL or {settings.log_level})",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    if args.zero_above and args.level is None:
        parser.error("--zero-above needs --level")
    log.configure(args.log_level)
    actual = (
        args.actual
        if args.actual is not None
        else args.run_dir / get_settings().met_totals_filename
    )
    try:
        comparisons = compare(
            args.expected,
            actual,
            args.category,
            args.variable,
            args.min_matches,
            args.level,
        )
    except (UsageError, OSError, pydantic.ValidationError) as exc:
        logger.error("%s", exc)
        return 2
    for c in comparisons:
        logger.info(
            "%s %s/%s expected %s actual %s relative error %s",
            c.time.isoformat(),
            args.category,
            args.variable,
            " ".join(f"{v:.9e}" for v in c.expected),
            " ".join(f"{v:.9e}" for v in c.actual),
            " ".join(f"{v:.3e}" for v in c.errors),
        )
    if args.zero_above:
        for c in comparisons:
            if c.nonzero_above:
                logger.error(
                    "check_conservation: %s level %d is not zero above level %d (%s/%s)",
                    c.time.isoformat(),
                    c.nonzero_above[0],
                    c.first_level,
                    args.category,
                    args.variable,
                )
                return 1
    error, when, level, exceeded = worst(comparisons, args.rtol, args.atol)
    worst_record = next(c for c in comparisons if c.time == when)
    zero = worst_record.zero_expected[level]
    kind = "absolute error (expected total is zero)" if zero else "relative error"
    tolerance = f"atol {args.atol:.1e}" if zero else f"rtol {args.rtol:.1e}"
    summary = (
        f"check_conservation: {len(comparisons)} matched records, max {kind} {error:.3e} "
        f"at {when.isoformat()} level {worst_record.first_level + level}"
    )
    if exceeded:
        logger.error("%s exceeds %s", summary, tolerance)
        return 1
    logger.info("%s within %s", summary, tolerance)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
