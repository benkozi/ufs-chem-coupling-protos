"""Stage a run directory for a checked-in weather-model configuration set, the way a regression test would.

    uv run python -m tools.stage_wm_run --set configs/wm/catchem_cece_control --out <run directory> \\
        --manifest data/C48_realistic/manifest.yaml --extdata data/C48_realistic/ExtData \\
        --streams build/gen/C48_realistic/streams

The set's YAML files are copied unmodified, CATChem's species file is copied from the submodule, `ExtData` and
`streams` are linked, the field dictionary is the prototype's base dictionary with the set's fragment appended
(as the weather model appends it to fd_ufs.yaml), and the driver configuration is the set's template with its
`@[...]` placeholders filled, followed by a delimited block of the keys only the prototype driver reads. The
path keys of the generated configurations are left out: the driver defaults to the run-directory names.
`--verify` compares a staged (and possibly run) directory with the set again and names every difference. Exit
status 0 on success, 1 for a missing input or a difference (logged), 2 for a usage error.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import get_args

import pydantic
import yaml
from pydantic import Field

from tools import driver_config as dc
from tools import log
from tools import manifest as mf
from tools.manifest import Strict
from tools.settings import LogLevel, get_settings

logger = log.get_logger("stage_wm_run")

DELIMITER = "# ---- prototype-only keys (tools/stage_wm_run.py); everything above is the filled weather-model template ----\n"
PLACEHOLDER = re.compile(r"@\[([A-Za-z0-9_]+)\]")
DICTIONARY_NAME = "fd.yaml"
LINKS = ("ExtData", "streams")


class StageError(Exception):
    """An input the staging refuses, named."""


class StageRequest(Strict):
    config_set: Path = Field(
        description="The checked-in set (configs/wm/<set>): the YAML files and the driver template."
    )
    out: Path = Field(description="The run directory to stage.")
    manifest: Path = Field(
        description="The mock-data manifest the prototype driver reads (clock, fix files, analytic meteorology)."
    )
    extdata: Path = Field(description="The ExtData directory the run directory links.")
    streams: Path = Field(
        description="The directory of the per-run window files (gen_configs writes them) the run directory links."
    )
    pets: int = Field(description="PET count; every component runs on all PETs.", gt=0)
    prototype: str = Field(
        description="The run's name (summary line, pass expression).", min_length=1
    )
    data_root: Path = Field(
        description="Directory the manifest's relative paths are relative to."
    )

    @property
    def set_files(self) -> list[str]:
        """The set's YAML files, by the settings' names."""
        settings = get_settings()
        return [
            settings.cece_config_filename,
            settings.catchem_config_filename,
            settings.catchem_emission_filename,
            settings.catchem_mapping_filename,
            settings.field_dictionary_fragment_filename,
        ]

    @property
    def links(self) -> dict[str, Path]:
        return dict(zip(LINKS, (self.extdata, self.streams), strict=True))


def placeholder_values(pets: int, step_seconds: int) -> dict[str, str]:
    """What the weather model's regression-test scripts would put into the template's placeholders, for this prototype."""
    values = {
        "esmf_logkind": "ESMF_LOGKIND_MULTI",
        "ESMF_THREADING": "false",
        "coupling_interval_sec": str(step_seconds),
    }
    for label, model in dc.COMPONENT_MODELS.items():
        stem = label.lower()
        values[f"{stem}_model"] = model
        values[f"{stem}_petlist_bounds"] = f"0 {pets - 1}"
        values[f"{stem}_omp_num_threads"] = "1"
    return values


def fill_template(template: str, values: dict[str, str]) -> str:
    unknown = sorted(set(PLACEHOLDER.findall(template)) - set(values))
    if unknown:
        raise StageError(
            "unknown placeholder(s) "
            + ", ".join(f"@[{name}]" for name in unknown)
            + " in the driver template"
        )
    return PLACEHOLDER.sub(lambda match: values[match.group(1)], template)


def filled_template(request: StageRequest) -> str:
    template = request.config_set / get_settings().wm_driver_filename
    step = mf.Manifest.from_yaml(request.manifest).clock.step_seconds
    return fill_template(template.read_text(), placeholder_values(request.pets, step))


def prototype_block(request: StageRequest) -> str:
    return DELIMITER + "".join(
        f"{key}: {value}\n"
        for key, value in (
            ("prototype", request.prototype),
            ("manifest", request.manifest.resolve()),
            ("data_root", request.data_root.resolve()),
            ("field_dictionary", DICTIONARY_NAME),
            ("grid_source", "mosaic"),
        )
    )


def merged_dictionary(base: Path, fragment: Path) -> dc.FieldDictionaryFile:
    """The base dictionary with the fragment's entries appended (a base entry of the same name gives way)."""
    dictionary = dc.load_field_dictionary(base)
    added = dc.FieldDictionaryFragment.model_validate(
        yaml.safe_load(fragment.read_text())["field_dictionary"]
    ).entries
    names = {entry.standard_name for entry in added}
    kept = [
        entry
        for entry in dictionary.field_dictionary.entries
        if entry.standard_name not in names
    ]
    return dc.FieldDictionaryFile(
        field_dictionary=dictionary.field_dictionary.model_copy(
            update={"entries": kept + added}
        )
    )


def _check_inputs(request: StageRequest) -> None:
    for name in [*request.set_files, get_settings().wm_driver_filename]:
        if not (request.config_set / name).is_file():
            raise StageError(f"{name} is not in the set {request.config_set}")
    for name, target in request.links.items():
        if not target.is_dir():
            raise StageError(f"{name} directory {target} does not exist")


def _clear(out: Path) -> None:
    """An empty run directory; only an empty directory or one this tool staged is replaced."""
    marker = out / get_settings().wm_run_marker_filename
    if out.exists():
        if any(out.iterdir()) and not marker.exists():
            raise StageError(
                f"{out} is not empty and was not staged by this tool (no {marker.name}); remove it yourself"
            )
        shutil.rmtree(out)
    out.mkdir(parents=True)
    marker.write_text("staged by tools/stage_wm_run.py\n")


def stage(request: StageRequest) -> list[Path]:
    """Write the run directory; returns what it holds."""
    settings = get_settings()
    _check_inputs(request)
    configure = filled_template(request) + prototype_block(request)
    out = request.out
    _clear(out)
    written: list[Path] = []
    for name in request.set_files:
        written.append(Path(shutil.copyfile(request.config_set / name, out / name)))
    species = settings.catchem_species_file
    written.append(Path(shutil.copyfile(species, out / species.name)))
    for name, target in request.links.items():
        (out / name).symlink_to(target.resolve(), target_is_directory=True)
        written.append(out / name)
    dictionary = out / DICTIONARY_NAME
    merged_dictionary(
        settings.base_field_dictionary,
        request.config_set / settings.field_dictionary_fragment_filename,
    ).to_yaml(dictionary)
    written.append(dictionary)
    driver = out / settings.wm_run_configure_filename
    driver.write_text(configure)
    written.append(driver)
    return written


def differences(request: StageRequest) -> list[str]:
    """Every way the run directory differs from the set it was staged from (empty: it is the set, unmodified)."""
    settings = get_settings()
    out = request.out
    found: list[str] = []
    species = settings.catchem_species_file
    sources = {name: request.config_set / name for name in request.set_files}
    sources[species.name] = species
    for name, source in sources.items():
        if not (out / name).is_file():
            found.append(f"{name} is missing from the run directory")
        elif (out / name).read_bytes() != source.read_bytes():
            found.append(f"{name} differs from the set's copy")
    driver = out / settings.wm_run_configure_filename
    if not driver.is_file():
        found.append(f"{driver.name} is missing from the run directory")
    elif not driver.read_text().startswith(filled_template(request) + DELIMITER):
        found.append(
            f"{driver.name} does not start with the filled {settings.wm_driver_filename}"
        )
    for name, target in request.links.items():
        link = out / name
        if not link.is_symlink() or link.resolve() != target.resolve():
            found.append(f"{name} is not a link to {target}")
    return found


def build_parser() -> argparse.ArgumentParser:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        prog="stage_wm_run", description=__doc__.split("\n\n")[0]
    )
    parser.add_argument(
        "--set",
        dest="config_set",
        required=True,
        type=Path,
        help="the checked-in set, e.g. configs/wm/catchem_cece_control",
    )
    parser.add_argument(
        "--out", required=True, type=Path, help="the run directory to stage"
    )
    parser.add_argument(
        "--manifest",
        required=True,
        type=Path,
        help="the mock-data manifest, e.g. data/C48_realistic/manifest.yaml",
    )
    parser.add_argument(
        "--extdata",
        required=True,
        type=Path,
        help="the ExtData directory to link, e.g. data/C48_realistic/ExtData",
    )
    parser.add_argument(
        "--streams",
        required=True,
        type=Path,
        help="the window-file directory to link, e.g. build/gen/C48_realistic/streams",
    )
    parser.add_argument(
        "--pets",
        type=int,
        default=settings.n_tiles,
        help=f"PET count (default {settings.n_tiles}, one per tile)",
    )
    parser.add_argument(
        "--prototype",
        default=None,
        help="the run's name (default: wm_<set directory name>)",
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=None,
        help="directory the manifest's relative paths are relative to (default: the working directory)",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="compare the run directory with the set instead of staging it",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        choices=get_args(LogLevel),
        help=f"log level (default: $PROTOS_LOG_LEVEL or {settings.log_level})",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    log.configure(args.log_level)
    try:
        request = StageRequest(
            config_set=args.config_set,
            out=args.out,
            manifest=args.manifest,
            extdata=args.extdata,
            streams=args.streams,
            pets=args.pets,
            prototype=args.prototype or f"wm_{args.config_set.resolve().name}",
            data_root=(args.data_root or Path.cwd()).resolve(),
        )
    except pydantic.ValidationError as exc:
        logger.error("%s", exc)
        return 2
    try:
        if args.verify:
            found = differences(request)
            for difference in found:
                logger.error("%s: %s", request.out, difference)
            if not found:
                logger.info("%s is the set, unmodified", request.out)
            return 1 if found else 0
        written = stage(request)
    except (StageError, OSError) as exc:
        logger.error("%s", exc)
        logger.debug("traceback", exc_info=True)
        return 1
    logger.info("staged %d entries under %s", len(written), request.out)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
