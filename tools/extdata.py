"""The two JSON documents of `tools/extdata_catalog.py` as pydantic models (issue #17).

`FileCatalog` is what `scan-log` writes from a CATChem PET log: the unique files a run opened, with the
format, the readers, the categories and the model times of the reads. `ExtDataHeaders` is what `describe`
writes on the RT platform from the `ExtData` tree: per file the NetCDF model, dimensions, variables with typed
attributes, decoded axes, value statistics and, for a volcanic `.rc` table, the parsed point table. The
script writes plain JSON (it runs on Ursa through `uv run --script` without this repository); these models
validate it afterwards and are what `tools/realistic_defaults.py` and issue #16's generator mode read.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from tools.manifest import Strict

FileFormat = Literal["netcdf", "yaml", "point_ascii", "binary"]
FileRole = Literal["inventory", "point_table", "optics", "config"]
AttrValue = str | int | float | list[int] | list[float]
Direction = Literal["increasing", "decreasing", "constant"]


class FileRead(Strict):
    path: str = Field(
        description="Path as the run opened it, relative to the run directory, without a leading './'."
    )
    format: FileFormat = Field(description="The `format=` value of the log line.")
    role: FileRole = Field(
        description="What the file is to the prototype: inventory (gridded emission input), point_table (volcanic .rc), optics (Mie table), config (YAML)."
    )
    sources: dict[str, int] = Field(
        description="Reader routine (`source=`) -> number of log lines from it."
    )
    categories: dict[str, list[str]] = Field(
        description="CATChem category -> model times ('init' or ISO stamps) at which it read the file."
    )
    pets: list[int] = Field(description="PET numbers whose logs showed the read.")
    read_count: int = Field(description="Total number of log lines for the file.", ge=1)
    first_seen: str = Field(
        description="Wall-clock stamp of the first log line (YYYYMMDD hhmmss.uuu)."
    )
    point_count: int | None = Field(
        default=None,
        description="Point sources CATChem reported reading from a point table, or null.",
    )


class FileCatalog(Strict):
    generated_utc: str = Field(description="When scan-log ran, ISO 8601 UTC.")
    logs: list[str] = Field(description="The PET log files scanned.", min_length=1)
    line_shapes: list[str] = Field(
        description="Which log line shapes were seen: 'catchem_file_read' (the tag) and/or 'emission_open' (the companion branch's DEBUG line)."
    )
    files: list[FileRead] = Field(
        description="Unique files in order of first appearance."
    )

    def by_role(self, role: FileRole) -> list[FileRead]:
        return [f for f in self.files if f.role == role]


class AxisSummary(Strict):
    name: str = Field(description="Coordinate variable name in the file.")
    count: int = Field(description="Number of values.", ge=1)
    first: float = Field(description="First value.")
    last: float = Field(description="Last value.")
    spacing: float | None = Field(
        default=None,
        description="Mean spacing between consecutive values, or null for one value.",
    )
    uniform: bool = Field(
        description="True when every spacing equals the mean within the script's tolerance."
    )
    direction: Direction = Field(description="Monotonic direction of the values.")
    units: str | None = Field(
        default=None, description="The variable's units attribute."
    )
    calendar: str | None = Field(
        default=None, description="The time variable's calendar attribute, or null."
    )
    values: list[float] = Field(description="Every value, in file order.")
    stamps: list[str] | None = Field(
        default=None,
        description="For a time axis: every value decoded to an ISO 8601 stamp, or null when the units could not be decoded.",
    )


class ValueStats(Strict):
    min: float = Field(
        description="Minimum over every record and level, fill values excluded."
    )
    max: float = Field(description="Maximum, fill values excluded.")
    mean: float = Field(description="Mean, fill values excluded.")
    std: float = Field(description="Standard deviation, fill values excluded.")
    nonzero_fraction: float = Field(
        description="Fraction of valid cells that are not zero.", ge=0, le=1
    )
    fill_fraction: float = Field(
        description="Fraction of cells that are fill.", ge=0, le=1
    )
    record_sums: list[float] = Field(
        description="Plain sum of the valid cells per record (every level together); one entry for a variable without a time dimension."
    )
    area_weighted_sums: list[float] | None = Field(
        default=None,
        description="Sum of value times cell area (m2, from the uniform lat-lon spacing and the script's Earth radius) per record, or null when the grid is not uniform.",
    )


class VariableHeader(Strict):
    dtype: str = Field(description="NetCDF data type as stored (numpy dtype name).")
    dimensions: list[str] = Field(description="Dimension names in file order.")
    shape: list[int] = Field(description="Shape in file order.")
    attributes: dict[str, AttrValue] = Field(
        description="Variable attributes with their types kept (numbers stay numbers)."
    )
    stats: ValueStats | None = Field(
        default=None,
        description="Value statistics, or null for a coordinate variable or when skipped.",
    )
    stats_skipped_reason: str | None = Field(
        default=None, description="Why stats are null, when they were requested."
    )


class PointTable(Strict):
    label: str = Field(description="The block label read (`volcano`).")
    columns: list[str] = Field(
        description="Column names in the order CATChem reads them.",
        min_length=5,
        max_length=5,
    )
    units: list[str] = Field(
        description="Units of each column.", min_length=5, max_length=5
    )
    row_count: int = Field(description="Data rows inside the block.", ge=0)
    malformed_rows: int = Field(
        description="Rows inside the block CATChem's reader would skip.", ge=0
    )
    ranges: dict[str, list[float]] = Field(
        description="Column -> [min, max] over the rows."
    )
    total_emission: float = Field(
        description="Sum of the emission column, in the column's units (kg S s-1)."
    )
    rows: list[list[float]] = Field(description="Every row as five numbers.")

    @model_validator(mode="after")
    def _rows_are_consistent(self) -> PointTable:
        if len(self.rows) != self.row_count:
            raise ValueError("row_count does not match rows")
        if any(len(r) != 5 for r in self.rows):
            raise ValueError("every row must have five numbers")
        return self


class FileHeader(Strict):
    path: str = Field(description="The catalog path (relative to the run directory).")
    role: FileRole = Field(description="The catalog role.")
    categories: list[str] = Field(description="The catalog categories that read it.")
    exists: bool = Field(
        description="Whether the file was found under the ExtData root."
    )
    size: int | None = Field(
        default=None, description="Byte size, or null when missing."
    )
    mtime: str | None = Field(
        default=None, description="Modification time, ISO 8601 UTC, or null."
    )
    model: str | None = Field(
        default=None,
        description="netCDF4 data_model (NETCDF4, NETCDF4_CLASSIC, NETCDF3_CLASSIC, NETCDF3_64BIT_OFFSET), or null for a text file.",
    )
    reader: Literal["netCDF4", "text"] | None = Field(
        default=None, description="Which reader described the file."
    )
    dimensions: dict[str, dict[str, int | bool]] = Field(
        default_factory=dict, description="Dimension -> {size, unlimited}."
    )
    variables: dict[str, VariableHeader] = Field(
        default_factory=dict, description="Every variable, coordinates included."
    )
    global_attributes: dict[str, AttrValue] = Field(
        default_factory=dict, description="Global attributes with their types kept."
    )
    axes: dict[str, AxisSummary] = Field(
        default_factory=dict,
        description="Detected coordinate axes by role: lon, lat, lev, time (whichever exist).",
    )
    point_table: PointTable | None = Field(
        default=None, description="The parsed table for a point_table file."
    )
    error: str | None = Field(
        default=None, description="Why the file could not be described, or null."
    )


class ExtDataHeaders(Strict):
    generated_utc: str = Field(description="When describe ran, ISO 8601 UTC.")
    python: str = Field(description="Python version that ran describe.")
    extdata_root: str = Field(description="The ExtData directory described.")
    catalog_digest: str = Field(description="sha256 of the catalog file describe read.")
    files: list[FileHeader] = Field(
        description="One record per catalogued ExtData file."
    )

    def by_path(self) -> dict[str, FileHeader]:
        return {f.path: f for f in self.files}
