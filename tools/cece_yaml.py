"""The CECE configuration the generator emits: the task-4 multi-tile form, every export nz deep.

One CECE component has one `driver.grid.nz` (its cap realizes every export with that depth and keeps its config
path in module globals, so there is one instance per executable): `nz` is the manifest's level count, a surface
category's stream variable is 2-D in CECE and its species layer keeps the default vertical distribution
(`single`, layer 0), so the flux sits in export level 1; the 3-D category declares `levels` and is exported level
for level without a flip. Stream paths are absolute (CECE resolves them against the process directory).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field

from tools import manifest as mf
from tools.catchem_yaml import rooted, species_key, standard_name
from tools.manifest import Strict

StreamCadence = Literal["series", "monthly"]
TaxMode = Literal["limit", "extend"]


class DriverGrid(Strict):
    nz: int = Field(
        description="Levels of every export (the manifest's n_levels).", gt=0
    )


class Driver(Strict):
    start_time: str = Field(
        description="Run start, YYYY-MM-DDTHH:MM:SS (the manifest clock)."
    )
    end_time: str = Field(
        description="Run end; a shorter end freezes the export after CECE reports completion."
    )
    timestep_seconds: int = Field(description="The coupling step.", gt=0)
    gridspec_file: str | None = Field(
        default=None,
        description="Never set: the parent grid wins on the host-block path.",
    )
    grid: DriverGrid = Field(
        description="The only grid key CECE reads on the parent-grid path."
    )


class StreamVariable(Strict):
    file: str = Field(description="Variable name in the inventory file.")
    model: str = Field(description="CECE field name, <category>_<variable>.")
    levels: int | None = Field(
        default=None,
        description="Declared level count of a 3-D variable (CECE refuses one that differs from nz).",
    )


class Stream(Strict):
    name: str = Field(description="Stream name: the category.")
    file: str = Field(description="Absolute inventory path.")
    cadence: StreamCadence = Field(
        description="series (hourly and static files) or monthly."
    )
    tintalgo: Literal["linear"] = Field(
        description="Linear time interpolation between records."
    )
    taxmode: TaxMode = Field(
        description="limit (fail outside the axis) or extend (a single static record)."
    )
    mapalgo: str = Field(
        description="The manifest's cece_mapalgo (consd, bilinear, passthrough)."
    )
    variables: list[StreamVariable] = Field(
        description="The category's variables.", min_length=1
    )


class CeceData(Strict):
    streams: list[Stream] = Field(description="One stream per category.", min_length=1)


class SpeciesLayer(Strict):
    field: str = Field(description="The stream variable's model name.")
    operation: str = Field(default="add", description="Layer operation.")


class ExportField(Strict):
    standard_name: str = Field(
        description="NUOPC standard name, <prefix>_<category>_<variable>."
    )
    units: str = Field(description="Units string (the NUOPC Units attribute).")


class Nuopc(Strict):
    export_fields: dict[str, ExportField] = Field(description="Exports by species key.")


class Output(Strict):
    enabled: bool = Field(
        description="NetCDF output; refused on a host block, so false."
    )


class CeceConfig(Strict):
    driver: Driver = Field(description="Clock and nz.")
    species: dict[str, list[SpeciesLayer]] = Field(
        description="One species per stream variable, one add layer each."
    )
    cece_data: CeceData = Field(description="The streams.")
    nuopc: Nuopc = Field(description="The exports.")
    output: Output = Field(description="Output off.")

    def to_yaml(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.safe_dump(
                self.model_dump(mode="json", exclude_none=True), sort_keys=False
            )
        )


def stream_timing(cadence: mf.Cadence) -> tuple[StreamCadence, TaxMode]:
    """CATChem cadence -> CECE stream cadence and axis mode (task-2 audit of CECE's cadences)."""
    if cadence == "hourly":
        return "series", "limit"
    if cadence == "monthly":
        return "monthly", "limit"
    if cadence == "static":
        return "series", "extend"
    raise ValueError(f"unknown cadence {cadence!r}")


def cece_config(manifest: mf.Manifest, data_root: Path) -> CeceConfig:
    nz = manifest.clock.n_levels
    streams: list[Stream] = []
    species: dict[str, list[SpeciesLayer]] = {}
    exports: dict[str, ExportField] = {}
    for name, category in manifest.categories.items():
        stream_cadence, taxmode = stream_timing(category.cadence)
        variables = [
            StreamVariable(
                file=variable,
                model=species_key(name, variable),
                levels=nz if category.is_3d else None,
            )
            for variable in category.variables
        ]
        streams.append(
            Stream(
                name=name,
                file=str(rooted(category.file, data_root)),
                cadence=stream_cadence,
                tintalgo="linear",
                taxmode=taxmode,
                mapalgo=category.cece_mapalgo or "consd",
                variables=variables,
            )
        )
        for variable, spec in category.variables.items():
            key = species_key(name, variable)
            species[key] = [SpeciesLayer(field=key)]
            exports[key] = ExportField(
                standard_name=standard_name(name, variable), units=spec.units
            )
    return CeceConfig(
        driver=Driver(
            start_time=manifest.clock.start.strftime("%Y-%m-%dT%H:%M:%S"),
            end_time=manifest.clock.end.strftime("%Y-%m-%dT%H:%M:%S"),
            timestep_seconds=manifest.clock.step_seconds,
            grid=DriverGrid(nz=nz),
        ),
        species=species,
        cece_data=CeceData(streams=streams),
        nuopc=Nuopc(export_fields=exports),
        output=Output(enabled=False),
    )
