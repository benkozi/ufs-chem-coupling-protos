"""Generate the configuration files of a prototype run from a task-2 manifest.

    uv run python -m tools.gen_configs --manifest data/C48/manifest.yaml --out build/gen/C48 --pets 6

Writes under --out: the CECE YAML, the NUOPC field dictionary, the CATChem science config, emission map and
field mapping, the driver config and a record of what was generated. Relative manifest paths resolve against
--data-root (default: the current directory, where make_mock_data was run); every emitted path is absolute.
Exit status 0 on success, 1 when a file cannot be written or CECE's validator rejects the emitted YAML, 2 for a
usage error (the PET rule, an invalid manifest, a category the defaults do not know, an unknown map target).
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import get_args

import pydantic
from pydantic import AwareDatetime, Field

from tools import catchem_yaml as cy
from tools import cece_yaml as cz
from tools import driver_config as dc
from tools import gen_defaults as gd
from tools import log
from tools import manifest as mf
from tools.manifest import Strict
from tools.settings import LogLevel, get_settings

logger = log.get_logger("gen_configs")
REPO_ROOT = Path(__file__).resolve().parents[1]


class UsageError(Exception):
    """A request the generator cannot honour (exit 2)."""


class GenerationError(Exception):
    """The emitted files are wrong (exit 1)."""


class GeneratedSet(Strict):
    manifest: Path = Field(
        description="Absolute path of the manifest the files were generated from."
    )
    manifest_digest: str = Field(description="sha256 of the manifest file.")
    defaults_file: Path = Field(description="The generator defaults file used.")
    data_root: Path = Field(
        description="Directory the manifest's relative paths were resolved against."
    )
    pets: int = Field(description="PET count of the driver config.")
    regdecomp: tuple[int, int] | None = Field(
        default=None, description="Per-tile decomposition, or null for the default."
    )
    outputs: dict[str, Path] = Field(description="Emitted files by name.")
    command: str = Field(description="The command line, for the record.")
    generated_at: AwareDatetime = Field(description="UTC time of generation.")


def repo_path(path: Path) -> Path:
    return path if path.is_absolute() else (REPO_ROOT / path).resolve()


def validate_cece_file(path: Path) -> list[str]:
    """CECE's own validator on an emitted file (empty when valid, or when the validator is absent)."""
    script = repo_path(get_settings().cece_validator)
    if not script.is_file():
        logger.warning(
            "CECE validator %s not found; the emitted CECE YAML was not validated",
            script,
        )
        return []
    spec = importlib.util.spec_from_file_location("validate_cece_config", script)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise GenerationError(f"cannot load {script}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    errors = module.validate_file(path, str(path), module.load_config_module(), set())
    return [f"{display}:{line}: {message}" for display, line, message in errors]


def hosted_rows(
    manifest: mf.Manifest, defaults: gd.GenDefaults, data_root: Path
) -> list[dc.HostedImportRow]:
    """The met_hosted_imports:: rows of the defaults' hosted imports, checked against the manifest."""
    rows: list[dc.HostedImportRow] = []
    if defaults.hosted_imports and "CECE -> MET" not in defaults.run_sequence:
        raise UsageError(
            "hosted imports need `CECE -> MET` in run_sequence (the driver adds only the connectors runSeq:: names)"
        )
    for h in defaults.hosted_imports:
        category = manifest.categories.get(h.category)
        if category is None or h.variable not in category.variables:
            raise UsageError(
                f"hosted import {h.category}/{h.variable} is not a variable of the manifest"
            )
        rows.append(
            dc.HostedImportRow(
                standard_name=cy.standard_name(h.category, h.variable),
                category=h.category,
                variable=h.variable,
                file=cy.rooted(category.file, data_root),
            )
        )
    return rows


def omitted_pairs(
    manifest: mf.Manifest,
    requests: Sequence[str],
    hosted: Sequence[dc.HostedImportRow],
) -> frozenset[tuple[str, str]]:
    """The --cece-omit <category>/<variable> pairs, each a manifest variable that MET does not host."""
    pairs: set[tuple[str, str]] = set()
    hosted_pairs = {(h.category, h.variable) for h in hosted}
    for request in requests:
        category, _, variable = request.partition("/")
        spec = manifest.categories.get(category)
        if spec is None or variable not in spec.variables:
            raise UsageError(
                f"--cece-omit {request}: not a <category>/<variable> of the manifest"
            )
        if (category, variable) in hosted_pairs:
            raise UsageError(
                f"--cece-omit {request}: MET hosts this import, it cannot be left unconnected"
            )
        pairs.add((category, variable))
    return frozenset(pairs)


def generate(
    *,
    manifest_path: Path,
    out: Path,
    pets: int,
    regdecomp: tuple[int, int] | None,
    data_root: Path,
    defaults_file: Path,
    source_files: bool,
    command: str,
    cece_omit: Sequence[str] = (),
) -> GeneratedSet:
    settings = get_settings()
    manifest_path = manifest_path.resolve()
    try:
        manifest = mf.Manifest.from_yaml(manifest_path)
    except (OSError, pydantic.ValidationError) as exc:
        raise UsageError(f"manifest {manifest_path}: {exc}") from exc
    defaults = gd.GenDefaults.from_yaml(defaults_file)
    if pets % settings.n_tiles:
        raise UsageError(
            f"PET count {pets} must be a multiple of {settings.n_tiles} (one DE per PET on {settings.n_tiles} tiles)"
        )
    if (
        regdecomp is not None
        and regdecomp[0] * regdecomp[1] != pets // settings.n_tiles
    ):
        raise UsageError(
            f"regdecomp {regdecomp[0]} x {regdecomp[1]} must equal the PET count per tile {pets // settings.n_tiles}"
        )
    hosted = hosted_rows(manifest, defaults, data_root)
    omit = omitted_pairs(manifest, cece_omit, hosted)
    species_file = repo_path(settings.catchem_species_file)
    emission_map = cy.emission_map(manifest, defaults)
    cy.check_emission_targets(emission_map, cy.species_names(species_file))
    base_mapping = cy.load_field_mapping(repo_path(settings.catchem_field_mapping))
    base_dictionary = dc.load_field_dictionary(
        repo_path(settings.base_field_dictionary)
    )

    out = out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    paths = {
        "cece": out / settings.cece_config_filename,
        "dictionary": out / settings.field_dictionary_filename,
        "science": out / settings.catchem_config_filename,
        "emission": out / settings.catchem_emission_filename,
        "mapping": out / settings.catchem_mapping_filename,
        "driver": out / settings.driver_config_filename,
    }
    cz.cece_config(manifest, data_root, defaults.cece_amio, omit).to_yaml(paths["cece"])
    dc.with_emission_entries(base_dictionary, manifest).to_yaml(paths["dictionary"])
    cy.science_config(
        manifest,
        defaults,
        species_file=species_file,
        emission_file=paths["emission"],
        source_files=source_files,
        data_root=data_root,
    ).to_yaml(paths["science"])
    emission_map.to_yaml(paths["emission"])
    # The file-path run advertises no emission import, so CATChem's readers feed every category.
    cy.merged_mapping(base_mapping, manifest, advertise=not source_files).to_yaml(
        paths["mapping"]
    )
    dc.DriverConfig(
        prototype=defaults.prototype,
        components=["CECE", "MET", "CATCHEM"],
        pets=pets,
        verbosity=defaults.verbosity,
        manifest=manifest_path,
        data_root=data_root,
        field_dictionary=paths["dictionary"],
        catchem_config_file=paths["science"],
        catchem_field_mapping_file=paths["mapping"],
        cece_config_file=paths["cece"],
        regdecomp=regdecomp,
        step_seconds=manifest.clock.step_seconds,
        run_sequence=list(defaults.run_sequence),
        hosted_imports=hosted,
    ).write(paths["driver"])
    errors = validate_cece_file(paths["cece"])
    if errors:
        raise GenerationError(
            "CECE rejects the emitted configuration: " + "; ".join(errors)
        )
    record = GeneratedSet(
        manifest=manifest_path,
        manifest_digest=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        defaults_file=defaults_file,
        data_root=data_root,
        pets=pets,
        regdecomp=regdecomp,
        outputs={p.name: p for p in paths.values()}
        | {settings.gen_record_filename: out / settings.gen_record_filename},
        command=command,
        generated_at=datetime.now(tz=UTC),
    )
    record.to_yaml(out / settings.gen_record_filename)
    for path in record.outputs.values():
        logger.info("wrote %s", path)
    return record


def build_parser() -> argparse.ArgumentParser:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        prog="gen_configs", description=__doc__.split("\n\n")[0]
    )
    parser.add_argument(
        "--manifest",
        required=True,
        type=Path,
        help="the task-2 manifest (data/<res>/manifest.yaml)",
    )
    parser.add_argument(
        "--out",
        required=True,
        type=Path,
        help="output directory (git-ignored, e.g. build/gen/C48)",
    )
    parser.add_argument(
        "--pets",
        required=True,
        type=int,
        help=f"PET count, a multiple of {settings.n_tiles}",
    )
    parser.add_argument(
        "--regdecomp",
        nargs=2,
        type=int,
        metavar=("NX", "NY"),
        default=None,
        help="per-tile decomposition, product = pets / tiles",
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=None,
        help="directory the manifest's relative paths are relative to (default: the current directory)",
    )
    parser.add_argument(
        "--defaults",
        type=Path,
        default=None,
        help=f"generator defaults file (default: {settings.gen_defaults_file}, or $PROTOS_GEN_DEFAULTS)",
    )
    parser.add_argument(
        "--catchem-source-files",
        action="store_true",
        help="the file-path comparison run: source: file, the manifest's inventory paths as source_file, "
        "and no emission import advertised",
    )
    parser.add_argument(
        "--cece-omit",
        action="append",
        default=[],
        metavar="CATEGORY/VARIABLE",
        help="leave this emission import unconnected: CECE neither reads nor exports it (repeatable; "
        "the unconnected-import failure case)",
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
    settings = get_settings()
    try:
        record = generate(
            manifest_path=args.manifest,
            out=args.out,
            pets=args.pets,
            regdecomp=None
            if args.regdecomp is None
            else (args.regdecomp[0], args.regdecomp[1]),
            data_root=(args.data_root or Path.cwd()).resolve(),
            defaults_file=repo_path(args.defaults or settings.gen_defaults_file),
            source_files=args.catchem_source_files,
            cece_omit=args.cece_omit,
            command=" ".join(["gen_configs", *args_list]),
        )
    except (UsageError, cy.ConfigError, pydantic.ValidationError) as exc:
        logger.error("%s", exc)
        return 2
    except (OSError, GenerationError) as exc:
        logger.error("%s", exc)
        logger.debug("traceback", exc_info=True)
        return 1
    logger.info("generated %d files under %s", len(record.outputs), args.out)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
