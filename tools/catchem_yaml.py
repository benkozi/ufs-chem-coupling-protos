"""The CATChem files the generator emits: the field mapping, the emission map and the science config.

Naming: an emission import is the mapping entry `standard_name: <prefix>_<category>_<variable>`,
`catchem_var: <prefix>/<category>/<variable>` (prefix `extemis`), rank 3 on the model levels (one CECE instance
has one nz; a surface category carries its flux in level 1), optional and advertised. The science config
sets `processes.extemis.source: nuopc_import` (every gridded manifest category from imports, no source_file, no
time interpolation: CECE did both; CATChem opens no emission file) and keeps point sources off. The models mirror the keys CATChem's Fortran and C++ readers take.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field

from tools import gen_defaults as gd
from tools import manifest as mf
from tools.manifest import Strict
from tools.settings import get_settings

Frequency = Literal["hourly", "monthly", "static"]
Source = Literal["file", "nuopc_import"]


class ConfigError(Exception):
    """The manifest and the generator defaults do not agree, or an emission-map target is unknown."""


# --- naming ------------------------------------------------------------------------------------------------


def standard_name(category: str, variable: str) -> str:
    """The NUOPC standard name of an emission input, `<prefix>_<category>_<variable>`."""
    return f"{get_settings().standard_name_prefix}_{category}_{variable}"


def catchem_var(category: str, variable: str) -> str:
    """The mapping path CATChem binds, `<prefix>/<category>/<variable>`."""
    return f"{get_settings().standard_name_prefix}/{category}/{variable}"


def species_key(category: str, variable: str) -> str:
    """The CECE species (and export item) name, unique across categories."""
    return f"{category}_{variable}"


def rooted(path: Path, data_root: Path) -> Path:
    """A manifest path made absolute against the data root (where make_mock_data was run)."""
    return path if path.is_absolute() else (data_root / path).resolve()


# --- field mapping -----------------------------------------------------------------------------------------


class FieldMappingEntry(Strict):
    standard_name: str = Field(description="NUOPC standard name of the field.")
    catchem_var: str = Field(
        description="CATChem variable path: a met-state name, or <prefix>/<category>/<variable> for an emission input."
    )
    dimensions: int = Field(description="Rank of the field (2, 3 or 4).", ge=2, le=4)
    units: str = Field(
        default="",
        description="Units string the cap records (nothing compares it at run time).",
    )
    vertical_axis: str = Field(
        default="level",
        description="Vertical semantic of a 3-D field: level (nz), interface (nz+1), level_to_interface, soil_layer.",
    )
    host_tracer_name: str = Field(
        default="",
        description="Host tracer exposed as a CATChem met field (rank-4 entry only).",
    )
    host_tracer_var: str = Field(
        default="",
        description="CATChem met name of host_tracer_name (rank-4 entry only).",
    )
    optional: bool = Field(
        default=False, description="Whether the cap tolerates the field's absence."
    )
    advertise: bool = Field(
        default=False, description="Whether an optional field is advertised anyway."
    )


class _IndentedSequenceDumper(yaml.SafeDumper):
    """Indents block sequences under their key (`key:\n  - a: 1\n    b: 2`): CATChem's mapping reader is
    line-based and only takes `- ` items indented deeper than the section header (PyYAML's default puts them
    at the header's column, which the reader skips without an error)."""

    def increase_indent(self, flow: bool = False, indentless: bool = False) -> None:
        del (
            indentless
        )  # PyYAML passes it by keyword; a block sequence is always indented here
        super().increase_indent(flow, False)


class FieldMapping(Strict):
    import_fields: list[FieldMappingEntry] = Field(
        description="The cap's imports, in file order."
    )
    export_fields: list[FieldMappingEntry] = Field(
        description="The cap's exports, in file order."
    )

    def to_yaml(self, path: Path) -> None:
        """Write the mapping with the keys each entry had (the submodule's entries keep their shape)."""
        doc = {
            "import_fields": [
                e.model_dump(mode="json", exclude_unset=True)
                for e in self.import_fields
            ],
            "export_fields": [
                e.model_dump(mode="json", exclude_unset=True)
                for e in self.export_fields
            ],
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.dump(doc, Dumper=_IndentedSequenceDumper, sort_keys=False))


def load_field_mapping(path: Path) -> FieldMapping:
    return FieldMapping.model_validate(yaml.safe_load(path.read_text()))


def is_emission_entry(entry: FieldMappingEntry) -> bool:
    return entry.catchem_var.startswith(f"{get_settings().standard_name_prefix}/")


def emission_entries(
    manifest: mf.Manifest, *, advertise: bool = True
) -> list[FieldMappingEntry]:
    """One optional rank-3 entry per gridded category and variable of the manifest, advertised by default.

    `advertise=False` is the file-path comparison run (gen_configs --catchem-source-files): CATChem then
    advertises no emission import, nothing connects, and its readers take every category from the files.
    """
    return [
        FieldMappingEntry(
            standard_name=standard_name(category, variable),
            catchem_var=catchem_var(category, variable),
            dimensions=3,
            vertical_axis="level",
            units=spec.units,
            optional=True,
            advertise=advertise,
        )
        for category, cat in manifest.categories.items()
        for variable, spec in cat.variables.items()
    ]


def merged_mapping(
    base: FieldMapping, manifest: mf.Manifest, *, advertise: bool = True
) -> FieldMapping:
    """The base mapping without its emission entries, plus the manifest's; idempotent."""
    kept = [e for e in base.import_fields if not is_emission_entry(e)]
    return FieldMapping(
        import_fields=kept + emission_entries(manifest, advertise=advertise),
        export_fields=list(base.export_fields),
    )


# --- emission map ------------------------------------------------------------------------------------------


class EmissionMapEntry(gd.EmissionMapVariable):
    units: str = Field(
        description="Units of the file variable in CATChem's spelling (the defaults' catchem_units of the manifest's string); catchem_emis_apply converts by it."
    )


class EmissionMap(Strict):
    categories: dict[str, dict[str, EmissionMapEntry]] = Field(
        description="Category -> file variable -> map entry, the layout of CATChem_emission.yml."
    )

    def to_mapping(self) -> dict[str, dict[str, dict[str, object]]]:
        return {
            category: {
                name: entry.model_dump(mode="json") for name, entry in variables.items()
            }
            for category, variables in self.categories.items()
        }

    @classmethod
    def from_mapping(cls, doc: dict[str, dict[str, dict[str, object]]]) -> EmissionMap:
        return cls(
            categories={
                category: {
                    name: EmissionMapEntry.model_validate(entry)
                    for name, entry in variables.items()
                }
                for category, variables in doc.items()
            }
        )

    def to_yaml(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(self.to_mapping(), sort_keys=False))


def species_names(path: Path) -> set[str]:
    """The species names of a CATChem species file (a list of entries with `name`)."""
    return {str(entry["name"]) for entry in yaml.safe_load(path.read_text())}


def _category_defaults(name: str, defaults: gd.GenDefaults) -> gd.CategoryDefaults:
    if name not in defaults.categories:
        raise ConfigError(f"category {name!r} has no entry in the generator defaults")
    return defaults.categories[name]


def emission_map(manifest: mf.Manifest, defaults: gd.GenDefaults) -> EmissionMap:
    categories: dict[str, dict[str, EmissionMapEntry]] = {}
    for name, category in manifest.categories.items():
        cat_defaults = _category_defaults(name, defaults)
        entries: dict[str, EmissionMapEntry] = {}
        for variable, spec in category.variables.items():
            if variable not in cat_defaults.variables:
                raise ConfigError(
                    f"variable {name}/{variable} has no emission-map entry in the generator defaults"
                )
            if spec.units not in defaults.catchem_units:
                raise ConfigError(
                    f"{name}/{variable}: units {spec.units!r} have no CATChem spelling in the defaults' catchem_units"
                )
            entries[variable] = EmissionMapEntry(
                units=defaults.catchem_units[spec.units],
                **cat_defaults.variables[variable].model_dump(),
            )
        categories[name] = entries
    return EmissionMap(categories=categories)


def check_emission_targets(emission_map: EmissionMap, species: set[str]) -> None:
    """Every map target is a species of the mechanism or a MET_* met-state input."""
    for category, variables in emission_map.categories.items():
        for name, entry in variables.items():
            for target in entry.map:
                if target not in species and not target.startswith("MET_"):
                    raise ConfigError(
                        f"{category}/{name} maps to {target!r}, which is not a species or a MET_* input"
                    )


# --- science config ----------------------------------------------------------------------------------------


class Verbose(Strict):
    activate: bool = Field(description="Per-PET run-phase markers in the ESMF log.")


class Simulation(Strict):
    name: str = Field(description="Simulation name.")
    start_date: str = Field(
        description="Start as YYYYMMDD HHMM (informational: the cap runs on the driver's clock)."
    )
    end_date: str = Field(description="End as YYYYMMDD HHMM (informational).")
    species_filename: str = Field(description="Absolute path of the species file.")
    emission_filename: str = Field(description="Absolute path of the emission map.")
    verbose: Verbose = Field(description="Verbose logging switch.")


class GridLevels(Strict):
    number_of_levels: int = Field(
        description="Model levels (overridden by the host's rank-4 tracer field).", gt=0
    )


class Timesteps(Strict):
    transport_timestep_in_s: int = Field(description="Transport step, s.", gt=0)
    chemistry_timestep_in_s: int = Field(description="Chemistry step, s.", gt=0)


class DiagnosticsOutput(Strict):
    enabled: bool = Field(description="Whether the cap writes NetCDF diagnostics.")
    directory: str = Field(
        description="Output directory, relative to the run directory."
    )
    prefix: str = Field(description="File name prefix.")
    frequency: int = Field(description="Write interval, s.", gt=0)
    format: str = Field(description="netcdf or latlon.")
    diag_list: list[str] = Field(description="Species written.")


class DiagnosticsCollection(Strict):
    enabled: bool = Field(description="The in-memory diagnostics collection switch.")


class Diagnostics(Strict):
    output: DiagnosticsOutput = Field(description="The NetCDF writer block.")
    collection: DiagnosticsCollection = Field(description="The collection block.")


class RunPhase(Strict):
    description: str = Field(description="What the phase runs.")
    processes: list[str] = Field(description="Processes of the phase, in order.")


class SeasaltProcess(Strict):
    activate: bool = Field(description="Process switch.")
    diagnostics: bool = Field(description="Process diagnostics switch.")
    scheme: str = Field(description="Sea-salt scheme (gong97, gong03, geos12).")


class ExtemisCategory(Strict):
    """One gridded category of processes.extemis, the keys catchem_emis_mod reads."""

    activate: bool = Field(default=True, description="Category switch.")
    scale_factor: float = Field(
        default=1.0, description="Global scale of the category."
    )
    source_file: str | None = Field(
        default=None,
        description="Inventory file of the file path; omitted on the import path, so an unconnected import fails loudly.",
    )
    format: str = Field(
        default="netcdf",
        description="File format (netcdf; volcano/point select the point reader).",
    )
    gridded: bool = Field(
        default=True, description="Gridded category (false for point sources)."
    )
    is_2d: bool = Field(
        description="Surface (2-D) category, or 3-D on the model levels."
    )
    frequency: Frequency = Field(description="CATChem frequency: the manifest cadence.")
    regrid_method: str = Field(
        description="CATChem regrid method of the file path (unused when imports are connected)."
    )
    time_interpolation: str = Field(
        default="none",
        description="none on the import path: CECE interpolates in time.",
    )
    lat_name: str = Field(
        default="lat", description="Latitude variable of the file path."
    )
    lon_name: str = Field(
        default="lon", description="Longitude variable of the file path."
    )
    vertical_dist: str = Field(
        default="none", description="Vertical distribution of a surface flux."
    )
    plume_rise: str | None = Field(
        default=None, description="Plume-rise scheme, omitted when none."
    )
    apply_method: gd.ApplyMethod = Field(
        description="add (flux) or replace (concentration or met input)."
    )
    reverse_vertical: bool = Field(
        default=False,
        description="Flip the levels of a 3-D input (CECE delivers bottom-up).",
    )
    diagnostics: bool = Field(default=False, description="Category diagnostics switch.")


class PointCategory(Strict):
    activate: bool = Field(
        default=False, description="Point sources stay off (no CECE path)."
    )
    gridded: bool = Field(default=False, description="Point sources are not gridded.")


class Extemis(Strict):
    activate: bool = Field(
        description="The external-emissions switch (the reader defaults it to true when absent)."
    )
    global_diagnostics: bool = Field(description="Diagnostics of every category.")
    source: Source = Field(
        default="file",
        description="Where every category's inputs come from: nuopc_import (connected imports only) or file "
        "(CATChem's readers); CATChem's default when the key is absent is file.",
    )
    categories: dict[str, ExtemisCategory] = Field(
        description="Gridded categories by name."
    )
    point: PointCategory = Field(description="The point category, always off.")

    def to_mapping(self) -> dict[str, object]:
        doc: dict[str, object] = {
            "activate": self.activate,
            "global_diagnostics": self.global_diagnostics,
            "source": self.source,
        }
        for name, category in self.categories.items():
            doc[name] = category.model_dump(mode="json", exclude_none=True)
        doc["point"] = self.point.model_dump(mode="json")
        return doc

    @classmethod
    def from_mapping(cls, doc: dict[str, object]) -> Extemis:
        fixed = {"activate", "global_diagnostics", "source", "point"}
        return cls(
            activate=bool(doc["activate"]),
            global_diagnostics=bool(doc.get("global_diagnostics", True)),
            source=doc.get("source", "file"),  # type: ignore[arg-type]
            categories={
                k: ExtemisCategory.model_validate(v)
                for k, v in doc.items()
                if k not in fixed
            },
            point=PointCategory.model_validate(doc.get("point", {})),
        )


class Processes(Strict):
    seasalt: SeasaltProcess = Field(description="The sea-salt process.")
    extemis: Extemis = Field(description="The external emissions.")


class ScienceConfig(Strict):
    simulation: Simulation = Field(description="Name, clock and files.")
    grid: GridLevels = Field(description="Levels.")
    timesteps: Timesteps = Field(description="Steps.")
    diagnostics: Diagnostics = Field(description="Diagnostics.")
    run_phases: dict[str, RunPhase] = Field(description="Run phases by name.")
    processes: Processes = Field(description="Process configuration.")

    def to_mapping(self) -> dict[str, object]:
        doc = self.model_dump(mode="json", exclude={"processes"})
        doc["processes"] = {
            "seasalt": self.processes.seasalt.model_dump(mode="json"),
            "extemis": self.processes.extemis.to_mapping(),
        }
        return doc

    @classmethod
    def from_mapping(cls, doc: dict[str, object]) -> ScienceConfig:
        processes = dict(doc.get("processes", {}))  # type: ignore[call-overload]
        extemis = Extemis.from_mapping(dict(processes.get("extemis", {})))
        rest = {k: v for k, v in doc.items() if k != "processes"}
        return cls.model_validate(
            rest
            | {"processes": {"seasalt": processes.get("seasalt"), "extemis": extemis}}
        )

    def to_yaml(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(self.to_mapping(), sort_keys=False))


def science_config(
    manifest: mf.Manifest,
    defaults: gd.GenDefaults,
    *,
    species_file: Path,
    emission_file: Path,
    source_files: bool = False,
    data_root: Path | None = None,
) -> ScienceConfig:
    """The science config for the manifest, point off.

    By default every gridded category is import-fed (`source: nuopc_import`, no source_file); with
    `source_files` it is the file-path comparison run (`source: file`, the manifest's inventory paths).
    """
    categories: dict[str, ExtemisCategory] = {}
    for name, category in manifest.categories.items():
        cat_defaults = _category_defaults(name, defaults)
        source = (
            str(rooted(category.file, data_root or Path.cwd()))
            if source_files
            else None
        )
        categories[name] = ExtemisCategory(
            source_file=source,
            is_2d=not category.is_3d,
            frequency=category.cadence,
            regrid_method=category.regrid_method,
            vertical_dist=cat_defaults.vertical_dist,
            plume_rise=cat_defaults.plume_rise,
            apply_method=cat_defaults.apply_method,
            diagnostics=cat_defaults.diagnostics,
        )
    return ScienceConfig(
        simulation=Simulation(
            name=defaults.prototype,
            start_date=manifest.clock.start.strftime("%Y%m%d %H%M"),
            end_date=manifest.clock.end.strftime("%Y%m%d %H%M"),
            species_filename=str(species_file),
            emission_filename=str(emission_file),
            verbose=Verbose(activate=False),
        ),
        grid=GridLevels(number_of_levels=manifest.clock.n_levels),
        timesteps=Timesteps(
            transport_timestep_in_s=manifest.clock.step_seconds,
            chemistry_timestep_in_s=manifest.clock.step_seconds,
        ),
        diagnostics=Diagnostics(
            output=DiagnosticsOutput(**defaults.diagnostics.model_dump()),
            collection=DiagnosticsCollection(enabled=False),
        ),
        run_phases={
            "proto": RunPhase(
                description="The prototype's processes; external emissions arrive as NUOPC imports from CECE.",
                processes=list(defaults.processes),
            )
        },
        processes=Processes(
            seasalt=SeasaltProcess(
                activate="seasalt" in defaults.processes,
                diagnostics=False,
                scheme=defaults.seasalt_scheme,
            ),
            extemis=Extemis(
                activate=True,
                global_diagnostics=False,
                source="file" if source_files else "nuopc_import",
                categories=categories,
                point=PointCategory(),
            ),
        ),
    )
