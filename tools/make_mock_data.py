"""Generate everything a prototype run needs except meteorology and configuration files.

    uv run python -m tools.make_mock_data --resolution C48 --out data/C48

Fetches and validates the FV3 fix files for the resolution, writes the analytic land mask, the six category
files and totals.yaml under <out>/inventories, and <out>/manifest.yaml describing all of it. The parameter
set comes from the defaults file (`tools/defaults/manifest.yaml`, or --defaults / PROTOS_DEFAULTS) with the
clock and grid options overriding it. Exit status 0 on success, 1 when the fetch or the generation fails
(logged), 2 for a usage error.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import get_args

import pydantic

from tools import fixfiles, inventories, log
from tools import manifest as mf
from tools.settings import LogLevel, get_settings

logger = log.get_logger("make_mock_data")


def aware_datetime(text: str) -> datetime:
    """ISO 8601 with a timezone (`2021-03-22T06:00:00Z`); naive stamps are a usage error."""
    try:
        when = datetime.fromisoformat(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not an ISO 8601 datetime: {text!r}") from exc
    if when.tzinfo is None:
        raise argparse.ArgumentTypeError(
            f"{text!r} has no timezone; write e.g. 2021-03-22T06:00:00Z"
        )
    return when


def build_parser() -> argparse.ArgumentParser:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        prog="make_mock_data", description=__doc__.split("\n\n")[0]
    )
    parser.add_argument(
        "--resolution",
        required=True,
        choices=("C48", "C96"),
        help="cubed-sphere resolution (C48 for CI, C96 for the prototype)",
    )
    parser.add_argument(
        "--out",
        required=True,
        type=Path,
        help="output directory, e.g. data/C48 (git-ignored)",
    )
    parser.add_argument(
        "--defaults",
        type=Path,
        default=None,
        help=f"defaults file (default: {settings.defaults_file}, or $PROTOS_DEFAULTS)",
    )
    parser.add_argument(
        "--start",
        type=aware_datetime,
        default=None,
        help="run start, ISO 8601 with timezone (default: the defaults file's clock.start)",
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=None,
        help="run length in hours (default: the defaults file's clock.duration_hours)",
    )
    parser.add_argument(
        "--step-seconds",
        type=int,
        default=None,
        help="coupling step in seconds (default: the defaults file's clock.step_seconds)",
    )
    parser.add_argument(
        "--levels",
        type=int,
        default=None,
        help="number of model levels (default: the defaults file's clock.n_levels)",
    )
    parser.add_argument(
        "--inventory-resolution",
        type=float,
        default=None,
        metavar="DEG",
        help="lat-lon spacing of the 2-D inventory grid in degrees (default: the defaults file's grids.inventory)",
    )
    parser.add_argument(
        "--fix-url",
        default=None,
        help=f"fix-file directory URL template with {{resolution}} (default: {settings.fix_url}, or $PROTOS_FIX_URL)",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        choices=get_args(LogLevel),
        help=f"log level (default: $PROTOS_LOG_LEVEL or {settings.log_level})",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(args_list)
    log.configure(args.log_level)
    try:
        manifest = mf.default_manifest(
            resolution=args.resolution,
            out_dir=args.out,
            command=" ".join(["make_mock_data", *args_list]),
            defaults_file=args.defaults,
            start=args.start,
            duration_hours=args.hours,
            step_seconds=args.step_seconds,
            n_levels=args.levels,
            inventory_resolution_deg=args.inventory_resolution,
            source_url=args.fix_url,
        )
    except (pydantic.ValidationError, OSError) as exc:
        logger.error("invalid run parameters or defaults file: %s", exc)
        return 2
    logger.info(
        "mock data for %s -> %s (%s, %d h, %d s step, %d levels)",
        manifest.resolution,
        args.out,
        manifest.clock.start.isoformat(),
        manifest.clock.duration_hours,
        manifest.clock.step_seconds,
        manifest.clock.n_levels,
    )
    manifest_path = args.out / get_settings().manifest_filename
    try:
        sizes = fixfiles.fetch(
            manifest.fix, manifest.resolution, manifest.n, manifest.earth_radius_m
        )
        manifest = manifest.model_copy(
            update={"fix": manifest.fix.model_copy(update={"sizes": sizes})}
        )
        inventories.generate_inventories(manifest)
        manifest.to_yaml(manifest_path)
    except (fixfiles.FixFileError, OSError, ValueError) as exc:
        logger.error("%s", exc)
        logger.debug("traceback", exc_info=True)
        return 1
    logger.info("wrote %s", manifest_path)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
