"""Regular lat-lon grids, the analytic land mask, the analytic inventories, their NetCDF files and totals.

Every field is a closed-form function of longitude, latitude, level index and record time whose constants
all live in the manifest: background plus Gaussian sources, times a diurnal factor (hourly data), a seasonal
factor (monthly data) and an exponential vertical profile (3-D data), times the land or ocean fraction. There
are no random numbers, so a run is reproducible from the manifest alone. Files are CF: 1-D `lon`/`lat`
centres with `bounds`, `area` in m², `time` with `time_bnds`, dimension order (time, [lev,] lat, lon),
float32 with deflate. Totals (sum of field times area per record and level) are logged and written to
`totals.yaml` for the conservation checks of later tasks.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import xarray as xr
from numpy.typing import NDArray
from pydantic import AwareDatetime, Field

from tools import manifest as mf
from tools.log import get_logger
from tools.settings import get_settings

log = get_logger("inventories")

Float2D = NDArray[np.float64]


class VariableEncoding(mf.Strict):
    """netCDF4 encoding of a data variable, from the settings' deflate options."""

    zlib: bool = Field(description="Whether the variable is deflated.")
    complevel: int = Field(description="zlib level, 0-9.", ge=0, le=9)
    shuffle: bool = Field(description="HDF5 shuffle filter before deflate.")

    @classmethod
    def from_settings(cls) -> VariableEncoding:
        settings = get_settings()
        return cls(
            zlib=settings.deflate_level > 0,
            complevel=settings.deflate_level,
            shuffle=settings.deflate_shuffle,
        )

    def as_encoding(self) -> dict[str, object]:
        return self.model_dump()


def coordinate_encoding() -> dict[str, object]:
    """Coordinates, bounds and areas are written without a _FillValue attribute (xarray would add NaN)."""
    return {"_FillValue": None}


class FileAttributes(mf.Strict):
    """Global attributes of a generated NetCDF file; `None` fields are left out."""

    Conventions: str = Field(
        description="CF conventions version (settings `cf_conventions`)."
    )
    title: str = Field(description="What the file holds.")
    source: str = Field(
        default="tools.make_mock_data", description="The generator that wrote the file."
    )
    category: str | None = Field(
        default=None, description="CATChem category name (inventory files)."
    )
    cadence: mf.Cadence | None = Field(
        default=None, description="hourly, monthly or static (inventory files)."
    )
    regrid_method: mf.RegridMethod | None = Field(
        default=None,
        description="CATChem-style regrid method the prototype assigns (inventory files).",
    )
    cece_mapalgo: mf.CeceMapalgo | None = Field(
        default=None, description="CECE stream mapalgo (inventory files)."
    )
    masked_by: mf.MaskedBy | None = Field(
        default=None, description="land, ocean or none (inventory files)."
    )
    resolution: mf.ResolutionName | None = Field(
        default=None,
        description="Cubed-sphere resolution of the run (inventory files).",
    )

    def as_attrs(self) -> dict[str, str]:
        return {k: str(v) for k, v in self.model_dump(exclude_none=True).items()}


class TimeAxis(mf.Strict):
    cadence: mf.Cadence = Field(description="Cadence the axis was built for.")
    values: list[float] = Field(
        description="Record times in `units` (the centre of each record's interval)."
    )
    bounds: list[tuple[float, float]] = Field(
        description="Record intervals in `units`."
    )
    units: str = Field(
        description="CF time units string, e.g. 'hours since 2021-03-22 06:00:00'."
    )
    calendar: str = Field(default="standard", description="CF calendar attribute.")
    stamps: list[AwareDatetime] = Field(
        description="The record times as datetimes, for evaluating the cycles."
    )


class VariableTotals(mf.Strict):
    units: str = Field(
        description="Units of the field; the totals are field times area, i.e. these units times m²."
    )
    records: list[list[float]] = Field(
        description="Sum of field times cell area, indexed [record][level] (one level for 2-D fields)."
    )


class CategoryTotals(mf.Strict):
    file: Path = Field(description="The category file the totals were computed from.")
    times: list[AwareDatetime] | None = Field(
        default=None,
        description="Time stamp of each record, in record order (UTC); None for totals written before task 4.",
    )
    variables: dict[str, VariableTotals] = Field(
        description="Totals per variable name."
    )


class Totals(mf.Strict):
    categories: dict[str, CategoryTotals] = Field(
        description="Totals per category name."
    )


# --- grid --------------------------------------------------------------------------------------------------


def lon_centres(grid: mf.LatLonGrid) -> Float2D:
    return grid.lon_first + grid.dlon * np.arange(grid.nlon, dtype=np.float64)


def lat_centres(grid: mf.LatLonGrid) -> Float2D:
    return grid.lat_first + grid.dlat * np.arange(grid.nlat, dtype=np.float64)


def lon_bounds(grid: mf.LatLonGrid) -> Float2D:
    c = lon_centres(grid)
    return np.stack([c - grid.dlon / 2, c + grid.dlon / 2], axis=1)


def lat_bounds(grid: mf.LatLonGrid) -> Float2D:
    c = lat_centres(grid)
    return np.stack([c - grid.dlat / 2, c + grid.dlat / 2], axis=1)


def cell_area(grid: mf.LatLonGrid, radius_m: float) -> Float2D:
    """Exact spherical cell areas, m²: R² Δλ (sin(lat_north) - sin(lat_south)), broadcast to (nlat, nlon)."""
    b = np.radians(lat_bounds(grid))
    band = radius_m**2 * np.radians(grid.dlon) * (np.sin(b[:, 1]) - np.sin(b[:, 0]))
    return np.broadcast_to(band[:, None], (grid.nlat, grid.nlon)).copy()


def wrap_degrees(delta: Float2D) -> Float2D:
    """Longitude differences folded into [-180, 180)."""
    return (delta + 180.0) % 360.0 - 180.0


def nearest_cell(grid: mf.LatLonGrid, lon: float, lat: float) -> tuple[int, int]:
    """(row, column) of the cell whose centre is nearest to (lon, lat)."""
    j = (
        round(
            float(wrap_degrees(np.array([lon - grid.lon_first]))[0] % 360.0) / grid.dlon
        )
        % grid.nlon
    )
    i = min(max(round((lat - grid.lat_first) / grid.dlat), 0), grid.nlat - 1)
    return i, j


def _mesh(grid: mf.LatLonGrid) -> tuple[Float2D, Float2D]:
    lon2d, lat2d = np.meshgrid(lon_centres(grid), lat_centres(grid))
    return lon2d, lat2d


# --- land mask ---------------------------------------------------------------------------------------------


def land_fraction(grid: mf.LatLonGrid, mask: mf.LandMask) -> Float2D:
    """Clipped sum of super-Gaussian continents, in [0, 1], on the grid's cell centres."""
    lon2d, lat2d = _mesh(grid)
    total = np.zeros_like(lon2d)
    for c in mask.continents:
        dx = wrap_degrees(lon2d - c.lon)
        dy = lat2d - c.lat
        theta = np.radians(c.rotation_deg)
        u = dx * np.cos(theta) + dy * np.sin(theta)
        v = -dx * np.sin(theta) + dy * np.cos(theta)
        r2 = (u / c.semi_axis_lon) ** 2 + (v / c.semi_axis_lat) ** 2
        total += np.exp(-(r2 ** (c.exponent / 2.0)))
    return np.clip(total, 0.0, 1.0)


def _grid_dataset(grid: mf.LatLonGrid, radius_m: float) -> xr.Dataset:
    ds = xr.Dataset(
        data_vars={
            "lon_bnds": (("lon", "nv"), lon_bounds(grid)),
            "lat_bnds": (("lat", "nv"), lat_bounds(grid)),
            "area": (
                ("lat", "lon"),
                cell_area(grid, radius_m),
                {"units": "m2", "long_name": "cell area", "standard_name": "cell_area"},
            ),
        },
        coords={
            "lon": (
                "lon",
                lon_centres(grid),
                {
                    "units": "degrees_east",
                    "standard_name": "longitude",
                    "axis": "X",
                    "bounds": "lon_bnds",
                },
            ),
            "lat": (
                "lat",
                lat_centres(grid),
                {
                    "units": "degrees_north",
                    "standard_name": "latitude",
                    "axis": "Y",
                    "bounds": "lat_bnds",
                },
            ),
        },
    )
    return ds


def write_land_mask(manifest: mf.Manifest) -> Path:
    grid = manifest.grids[manifest.land_mask.grid]
    fraction = land_fraction(grid, manifest.land_mask)
    ds = _grid_dataset(grid, manifest.earth_radius_m)
    ds["land_fraction"] = (
        ("lat", "lon"),
        fraction.astype(np.float32),
        {"units": "1", "long_name": "analytic land fraction"},
    )
    ds["land_mask"] = (
        ("lat", "lon"),
        (fraction >= manifest.land_mask.threshold).astype(np.int8),
        {
            "units": "1",
            "long_name": f"land_fraction >= {manifest.land_mask.threshold}",
            "flag_values": np.array([0, 1], dtype=np.int8),
            "flag_meanings": "ocean land",
        },
    )
    ds.attrs = FileAttributes(
        Conventions=get_settings().cf_conventions,
        title="analytic land mask for the coupling prototypes",
    ).as_attrs()
    path = manifest.land_mask.file
    path.parent.mkdir(parents=True, exist_ok=True)
    deflate = VariableEncoding.from_settings().as_encoding()
    encoding: dict[str, dict[str, object]] = {
        name: coordinate_encoding()
        for name in ("lon", "lat", "lon_bnds", "lat_bnds", "area")
    }
    encoding["land_fraction"] = dict(deflate)
    encoding["land_mask"] = dict(deflate) | coordinate_encoding()
    ds.to_netcdf(path, format="NETCDF4", encoding=encoding)
    log.info(
        "wrote %s (%d x %d, land fraction mean %.3f)",
        path,
        grid.nlat,
        grid.nlon,
        float(fraction.mean()),
    )
    return path


# --- time axes ---------------------------------------------------------------------------------------------


def _cf_date(when: datetime) -> str:
    return when.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")


def time_axis(cadence: mf.Cadence, clock: mf.Clock) -> TimeAxis:
    """hourly: 25 centred records from start to start+duration; monthly: 12 mid-month records of the start year; static: one record at start."""
    if cadence == "hourly":
        hours = list(range(clock.duration_hours + 1))
        return TimeAxis(
            cadence=cadence,
            values=[float(h) for h in hours],
            bounds=[(h - 0.5, h + 0.5) for h in hours],
            units=f"hours since {_cf_date(clock.start)}",
            stamps=[clock.start + timedelta(hours=h) for h in hours],
        )
    if cadence == "monthly":
        year = clock.start.astimezone(UTC).year
        jan1 = datetime(year, 1, 1, tzinfo=UTC)
        values: list[float] = []
        bounds: list[tuple[float, float]] = []
        stamps: list[datetime] = []
        for month in range(1, 13):
            first = datetime(year, month, 1, tzinfo=UTC)
            following = (
                datetime(year + 1, 1, 1, tzinfo=UTC)
                if month == 12
                else datetime(year, month + 1, 1, tzinfo=UTC)
            )
            d0, d1 = float((first - jan1).days), float((following - jan1).days)
            values.append((d0 + d1) / 2)
            bounds.append((d0, d1))
            stamps.append(jan1 + timedelta(days=(d0 + d1) / 2))
        return TimeAxis(
            cadence=cadence,
            values=values,
            bounds=bounds,
            units=f"days since {year}-01-01 00:00:00",
            stamps=stamps,
        )
    return TimeAxis(
        cadence=cadence,
        values=[0.0],
        bounds=[(0.0, float(clock.duration_hours))],
        units=f"hours since {_cf_date(clock.start)}",
        stamps=[clock.start],
    )


# --- fields ------------------------------------------------------------------------------------------------


def _gaussian_sum(variable: mf.Variable, lon2d: Float2D, lat2d: Float2D) -> Float2D:
    total = np.full_like(lon2d, variable.background)
    for s in variable.sources:
        d2 = wrap_degrees(lon2d - s.lon) ** 2 + (lat2d - s.lat) ** 2
        total += s.amplitude * np.exp(-d2 / (2.0 * s.sigma_deg**2))
    return total


def _time_factor(
    variable: mf.Variable, axis: TimeAxis, record: int, lon2d: Float2D
) -> Float2D | float:
    stamp = axis.stamps[record].astimezone(UTC)
    if axis.cadence == "hourly" and variable.diurnal is not None:
        utc_hour = stamp.hour + stamp.minute / 60.0 + stamp.second / 3600.0
        local = (utc_hour + lon2d / 15.0) % 24.0
        d = variable.diurnal
        return 1.0 + d.relative_amplitude * np.cos(
            2.0 * np.pi * (local - d.peak_local_hour) / 24.0
        )
    if axis.cadence == "monthly" and variable.seasonal is not None:
        s = variable.seasonal
        return 1.0 + s.relative_amplitude * float(
            np.cos(2.0 * np.pi * (stamp.month - s.peak_month) / 12.0)
        )
    return 1.0


def _vertical_factor(
    variable: mf.Variable, n_levels: int, level_order: mf.LevelOrder
) -> Float2D:
    k = np.arange(n_levels, dtype=np.float64)
    profile = (
        np.exp(-k / variable.vertical.scale_height_levels)
        if variable.vertical is not None
        else np.ones(n_levels)
    )
    return profile[::-1].copy() if level_order == "top_down" else profile


def _mask_factor(category: mf.Category, fraction: Float2D | None) -> Float2D | float:
    if category.masked_by == "none":
        return 1.0
    if fraction is None:
        raise ValueError(
            f"category masked_by={category.masked_by!r} needs the land fraction on its grid"
        )
    return fraction if category.masked_by == "land" else 1.0 - fraction


def field_values(
    variable: mf.Variable,
    category: mf.Category,
    grid: mf.LatLonGrid,
    clock: mf.Clock,
    fraction: Float2D | None,
    axis: TimeAxis,
) -> NDArray[np.float32]:
    """The variable on the grid for every record of the axis: (nt, nlat, nlon) or (nt, nlev, nlat, nlon), float32."""
    lon2d, lat2d = _mesh(grid)
    base = _gaussian_sum(variable, lon2d, lat2d) * _mask_factor(category, fraction)
    vertical = (
        _vertical_factor(variable, clock.n_levels, category.level_order)
        if category.is_3d
        else None
    )
    records = []
    for r in range(len(axis.values)):
        f = base * _time_factor(variable, axis, r, lon2d)
        records.append(
            f[None, :, :] * vertical[:, None, None] if vertical is not None else f
        )
    return np.stack(records).astype(np.float32)


# --- files -------------------------------------------------------------------------------------------------


def write_category(
    manifest: mf.Manifest, name: str, fraction: Float2D | None
) -> CategoryTotals:
    """Write one category file and return its totals. `fraction` is the land fraction on the category's grid (or None)."""
    category = manifest.categories[name]
    grid = manifest.grids[category.grid]
    axis = time_axis(category.cadence, manifest.clock)
    ds = _grid_dataset(grid, manifest.earth_radius_m)
    ds = ds.assign_coords(
        time=(
            "time",
            np.array(axis.values, dtype=np.float64),
            {
                "units": axis.units,
                "calendar": axis.calendar,
                "standard_name": "time",
                "axis": "T",
                "bounds": "time_bnds",
            },
        )
    )
    ds["time_bnds"] = (("time", "nv"), np.array(axis.bounds, dtype=np.float64))
    dims: tuple[str, ...] = ("time", "lat", "lon")
    if category.is_3d:
        ds = ds.assign_coords(
            lev=(
                "lev",
                np.arange(manifest.clock.n_levels, dtype=np.int32),
                {
                    "long_name": "model level index",
                    "positive": "up" if category.level_order == "bottom_up" else "down",
                    "axis": "Z",
                    "level_order": category.level_order,
                },
            )
        )
        dims = ("time", "lev", "lat", "lon")
    area = cell_area(grid, manifest.earth_radius_m)
    totals: dict[str, VariableTotals] = {}
    deflate = VariableEncoding.from_settings().as_encoding()
    encoding: dict[str, dict[str, object]] = {
        n: coordinate_encoding()
        for n in ("lon", "lat", "lon_bnds", "lat_bnds", "area", "time", "time_bnds")
    }
    for var_name, variable in category.variables.items():
        values = field_values(variable, category, grid, manifest.clock, fraction, axis)
        ds[var_name] = (
            dims,
            values,
            {"units": variable.units, "long_name": variable.long_name},
        )
        encoding[var_name] = dict(deflate)
        per_level = values.astype(np.float64) * area
        summed = per_level.sum(axis=(-2, -1))
        records = summed if category.is_3d else summed[:, None]
        totals[var_name] = VariableTotals(
            units=variable.units, records=records.tolist()
        )
        log.info(
            "%s/%s: %d records, total x area first record %.6e (%s m2)",
            name,
            var_name,
            values.shape[0],
            records[0].sum(),
            variable.units,
        )
    ds.attrs = FileAttributes(
        Conventions=get_settings().cf_conventions,
        title=f"analytic {name} inputs for the coupling prototypes",
        category=name,
        cadence=category.cadence,
        regrid_method=category.regrid_method,
        cece_mapalgo=category.cece_mapalgo,
        masked_by=category.masked_by,
        resolution=manifest.resolution,
    ).as_attrs()
    category.file.parent.mkdir(parents=True, exist_ok=True)
    ds.to_netcdf(category.file, format="NETCDF4", encoding=encoding)
    log.info("wrote %s", category.file)
    return CategoryTotals(file=category.file, times=axis.stamps, variables=totals)


def generate_inventories(manifest: mf.Manifest) -> Totals:
    """Land mask, every category file and totals.yaml under the manifest's inventories directory."""
    write_land_mask(manifest)
    fractions: dict[str, Float2D] = {}
    categories: dict[str, CategoryTotals] = {}
    for name, category in manifest.categories.items():
        fraction = None
        if category.masked_by != "none":
            if category.grid not in fractions:
                fractions[category.grid] = land_fraction(
                    manifest.grids[category.grid], manifest.land_mask
                )
            fraction = fractions[category.grid]
        categories[name] = write_category(manifest, name, fraction)
    totals = Totals(categories=categories)
    totals.to_yaml(manifest.inventories_dir / get_settings().totals_filename)
    return totals
