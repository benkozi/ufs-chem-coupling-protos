"""The driver configuration (ufs.configure shape, protos/met_catchem/esm.configure.in) and the field dictionary."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import Field, model_validator

from tools import manifest as mf
from tools.catchem_yaml import standard_name
from tools.manifest import Strict
from tools.settings import get_settings

KNOWN_COMPONENTS = ("CECE", "MET", "CATCHEM")


class FieldDictionaryEntry(Strict):
    standard_name: str = Field(description="NUOPC standard name.")
    canonical_units: str = Field(
        description="Canonical units (required on every entry, aliased ones included)."
    )
    alias: str | None = Field(
        default=None, description="An alias the UFS-pinned CATChem needs, or omitted."
    )


class FieldDictionary(Strict):
    version_number: float = Field(description="Dictionary version.")
    institution: str = Field(description="Dictionary institution.")
    description: str = Field(description="Dictionary description.")
    entries: list[FieldDictionaryEntry] = Field(
        description="The entries, in file order."
    )


class FieldDictionaryFile(Strict):
    field_dictionary: FieldDictionary = Field(
        description="The file's single top-level key."
    )

    def to_yaml(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.safe_dump(
                self.model_dump(mode="json", exclude_none=True), sort_keys=False
            )
        )


def load_field_dictionary(path: Path) -> FieldDictionaryFile:
    return FieldDictionaryFile.model_validate(yaml.safe_load(path.read_text()))


def with_emission_entries(
    base: FieldDictionaryFile, manifest: mf.Manifest
) -> FieldDictionaryFile:
    """The base entries without emission names, plus the manifest's (canonical units from the manifest); idempotent."""
    prefix = f"{get_settings().standard_name_prefix}_"
    kept = [
        e
        for e in base.field_dictionary.entries
        if not e.standard_name.startswith(prefix)
    ]
    emission = [
        FieldDictionaryEntry(
            standard_name=standard_name(category, variable), canonical_units=spec.units
        )
        for category, cat in manifest.categories.items()
        for variable, spec in cat.variables.items()
    ]
    dictionary = base.field_dictionary.model_copy(update={"entries": kept + emission})
    return FieldDictionaryFile(field_dictionary=dictionary)


class DriverConfig(Strict):
    prototype: str = Field(
        description="The run's name (summary line, driver component, pass expression)."
    )
    components: list[str] = Field(
        description="EARTH_component_list in order (CECE, MET, CATCHEM).", min_length=1
    )
    pets: int = Field(description="PET count; every component runs on all PETs.", gt=0)
    verbosity: str = Field(description="NUOPC Verbosity attribute of every component.")
    manifest: Path = Field(description="Absolute path of the manifest.")
    data_root: Path = Field(
        description="Directory the manifest's relative paths are relative to."
    )
    field_dictionary: Path = Field(description="Absolute path of the field dictionary.")
    catchem_config_file: Path = Field(
        description="Absolute path of the CATChem science config."
    )
    catchem_field_mapping_file: Path = Field(
        description="Absolute path of the CATChem field mapping."
    )
    cece_config_file: Path = Field(
        description="Absolute path of the CECE configuration."
    )
    grid_source: str = Field(
        default="mosaic", description="mosaic (the fix files) or analytic (debug)."
    )
    regdecomp: tuple[int, int] | None = Field(
        default=None,
        description="Per-tile decomposition nx ny, product = pets / tiles; omitted for 1 x pets/tiles.",
    )
    step_seconds: int = Field(
        description="The coupling step of the run sequence.", gt=0
    )
    run_sequence: list[str] = Field(
        description="Lines inside the @step loop.", min_length=1
    )

    @model_validator(mode="after")
    def _pet_rules(self) -> DriverConfig:
        tiles = get_settings().n_tiles
        if self.pets % tiles:
            raise ValueError(
                f"PET count {self.pets} must be a multiple of {tiles} (one DE per PET on {tiles} tiles)"
            )
        if (
            self.regdecomp is not None
            and self.regdecomp[0] * self.regdecomp[1] != self.pets // tiles
        ):
            raise ValueError(
                f"regdecomp {self.regdecomp[0]} x {self.regdecomp[1]} must equal the PET count per tile {self.pets // tiles}"
            )
        unknown = [c for c in self.components if c not in KNOWN_COMPONENTS]
        if unknown:
            raise ValueError(
                f"unknown components {unknown}; the driver knows {', '.join(KNOWN_COMPONENTS)}"
            )
        return self

    def render(self) -> str:
        lines = [
            "# Driver configuration (ufs.configure shape) written by tools/gen_configs.py; paths are absolute.",
            f"prototype: {self.prototype}",
            "",
            f"EARTH_component_list: {' '.join(self.components)}",
            "",
        ]
        for component in self.components:
            lines += [
                f"{component}_petlist_bounds: 0 {self.pets - 1}",
                f"{component}_attributes::",
                f"  Verbosity = {self.verbosity}",
                "::",
                "",
            ]
        lines += [
            f"manifest: {self.manifest}",
            f"data_root: {self.data_root}",
            f"field_dictionary: {self.field_dictionary}",
            f"catchem_config_file: {self.catchem_config_file}",
            f"catchem_field_mapping_file: {self.catchem_field_mapping_file}",
            f"cece_config_file: {self.cece_config_file}",
            f"grid_source: {self.grid_source}",
        ]
        if self.regdecomp is not None:
            lines.append(f"regdecomp_per_tile: {self.regdecomp[0]} {self.regdecomp[1]}")
        lines += [
            "",
            "runSeq::",
            f"  @{self.step_seconds}",
            *[f"    {line}" for line in self.run_sequence],
            "  @",
            "::",
        ]
        return "\n".join(lines) + "\n"

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.render())
