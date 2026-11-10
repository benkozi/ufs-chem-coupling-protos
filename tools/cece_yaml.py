"""The CECE configuration the generator emits: the task-4 multi-tile form, every export nz deep.

Stream timing (issue #18) follows each file's own time coordinate: a daily series is read from the window
file the generator writes (CECE's cap path reads one file per stream, `cece_driver_facade.cpp` BuildStreamConfig)
with `taxmode: limit`; a monthly file whose explicit axis does not bracket the run (the 2010 HTAP, 2011 DMS and
2022 FENGSHA climatologies) wraps by year with `taxmode: cycle`, a bracketing one (GMI, Dec 2020 to Jan 2022)
keeps `limit`; a static file is one record (`series`/`extend`); a point table has no stream.

One CECE component has one `driver.grid.nz` (its cap realizes every export with that depth and keeps its config
path in module globals, so there is one instance per executable): `nz` is the manifest's level count, a surface
category's stream variable declares `levels: 1` (CECE gives a variable without `levels` the grid's nz,
`cece_io.cpp`, and then refuses a 2-D record; task 6) and its species layer keeps the default vertical
distribution (`single`, layer 0), so the flux sits in export level 1; the 3-D category declares `levels: nz`
and is exported level for level without a flip. Stream paths are absolute (CECE resolves them against the process directory).
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field

from tools import gen_defaults as gd
from tools import manifest as mf
from tools.catchem_yaml import rooted, species_key, standard_name
from tools.manifest import Strict

StreamCadence = Literal["series", "daily", "monthly"]
TaxMode = Literal["limit", "extend", "cycle"]


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
    amio_worker_threads: int = Field(
        description="AMIO background I/O threads per PET (cece_amio).", gt=0
    )
    amio_staging_buffer_count: int = Field(
        description="AMIO staging buffers per data file per PET (cece_amio).", gt=0
    )
    amio_staging_buffer_capacity_bytes: int = Field(
        description="Nominal bytes per AMIO staging buffer (cece_amio).", gt=0
    )
    amio_prefetch_depth: int = Field(
        description="AMIO records read ahead per data file (cece_amio).", gt=0
    )


class StreamVariable(Strict):
    file: str = Field(description="Variable name in the inventory file.")
    model: str = Field(description="CECE field name, <category>_<variable>.")
    levels: int = Field(
        description="Declared level count: 1 for a 2-D variable, nz for a 3-D one (CECE refuses any other value above 1, and a missing one means nz).",
        gt=0,
    )


class Stream(Strict):
    name: str = Field(description="Stream name: the category.")
    file: str = Field(description="Absolute inventory path.")
    cadence: StreamCadence = Field(
        description="series (hourly and static files), daily (one file per day) or monthly."
    )
    tintalgo: Literal["linear", "nearest"] = Field(
        description="linear blends the two bracketing records; nearest holds one (CATChem's time_interpolation none: with CECE's time_label auto a midnight-stamped daily record is held for its day)."
    )
    taxmode: TaxMode = Field(
        description="limit (fail outside the axis), extend (a single static record) or cycle (a climatology read by year wrap)."
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


def axis_brackets(axis: mf.ExplicitTimeAxis, clock: mf.Clock) -> bool:
    """Whether the file's own records enclose the run (first record at or before the start, last at or after the end)."""
    stamps = axis.stamps()
    return stamps[0] <= clock.start and stamps[-1] >= clock.end


def stream_timing(
    category: mf.Category, clock: mf.Clock
) -> tuple[StreamCadence, TaxMode]:
    """CATChem cadence and the file's time axis -> CECE stream cadence and axis mode (the table in the module docstring)."""
    cadence = category.cadence
    if cadence == "hourly":
        return "series", "limit"
    if cadence == "daily":
        return "daily", "limit"
    if cadence == "monthly":
        axis = category.layout.time_axis if category.layout is not None else None
        if axis is not None and not axis_brackets(axis, clock):
            return "monthly", "cycle"
        return "monthly", "limit"
    if cadence == "static":
        return "series", "extend"
    raise ValueError(f"unknown cadence {cadence!r}")


def stream_file(
    name: str,
    category: mf.Category,
    data_root: Path | None,
    windows: Mapping[Path, Path],
) -> Path:
    """The one file CECE reads for the category: the generator's window file for a daily series, else the inventory.

    Without a data root the manifest's path is written as it is (the run-directory layout, where the paths are
    already relative to the directory CECE runs in).
    """
    if category.cadence != "daily":
        return category.file if data_root is None else rooted(category.file, data_root)
    window = windows.get(category.file)
    if window is None:
        raise ValueError(
            f"daily category {name!r} has no window file for {category.file} (CECE reads one file per stream)"
        )
    return window


def cece_config(
    manifest: mf.Manifest,
    data_root: Path | None,
    amio: gd.CeceAmio,
    omit: frozenset[tuple[str, str]] = frozenset(),
    windows: Mapping[Path, Path] | None = None,
    time_interpolation: Mapping[str, gd.TimeInterpolation] | None = None,
) -> CeceConfig:
    """The CECE YAML for the manifest; `omit` (category, variable) pairs are neither read nor exported.

    `omit` exists for the unconnected-import failure case (gen_configs --cece-omit): CATChem still advertises
    the field, CECE does not provide it. A category with every variable omitted has no stream, nor has a point
    table. `windows` maps a daily category's token template to the window file the generator wrote;
    `time_interpolation` maps a category to CATChem's linear or none (default linear).
    """
    interpolation = time_interpolation or {}
    nz = manifest.clock.n_levels
    streams: list[Stream] = []
    species: dict[str, list[SpeciesLayer]] = {}
    exports: dict[str, ExportField] = {}
    for name, category in manifest.categories.items():
        if category.is_point_table:
            continue
        stream_cadence, taxmode = stream_timing(category, manifest.clock)
        variables = [
            StreamVariable(
                file=variable,
                model=species_key(name, variable),
                levels=nz if category.is_3d else 1,
            )
            for variable in category.variables
            if (name, variable) not in omit
        ]
        if not variables:
            continue
        streams.append(
            Stream(
                name=name,
                file=str(stream_file(name, category, data_root, windows or {})),
                cadence=stream_cadence,
                tintalgo="nearest" if interpolation.get(name) == "none" else "linear",
                taxmode=taxmode,
                mapalgo=category.cece_mapalgo or "consd",
                variables=variables,
            )
        )
        for variable, spec in category.variables.items():
            if (name, variable) in omit:
                continue
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
            amio_worker_threads=amio.worker_threads,
            amio_staging_buffer_count=amio.staging_buffer_count,
            amio_staging_buffer_capacity_bytes=amio.staging_buffer_capacity_bytes,
            amio_prefetch_depth=amio.prefetch_depth,
        ),
        species=species,
        cece_data=CeceData(streams=streams),
        nuopc=Nuopc(export_fields=exports),
        output=Output(enabled=False),
    )
