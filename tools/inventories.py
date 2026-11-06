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

import itertools
import shutil
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Literal, cast

import netCDF4
import numpy as np
import xarray as xr
from numpy.typing import NDArray
from pydantic import AwareDatetime, Field

from tools import manifest as mf
from tools.log import get_logger
from tools.settings import get_settings

log = get_logger("inventories")

Float2D = NDArray[np.float64]
XarrayFormat = Literal["NETCDF4", "NETCDF4_CLASSIC", "NETCDF3_64BIT", "NETCDF3_CLASSIC"]
# netCDF4's data-model names (the manifest's `layout.model`) -> xarray's `to_netcdf(format=)` spelling.
XARRAY_FORMATS: dict[mf.NetcdfModel, XarrayFormat] = {
    "NETCDF4": "NETCDF4",
    "NETCDF4_CLASSIC": "NETCDF4_CLASSIC",
    "NETCDF3_CLASSIC": "NETCDF3_CLASSIC",
    "NETCDF3_64BIT_OFFSET": "NETCDF3_64BIT",
}


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
    file: Path = Field(
        description="The category file the totals were computed from (the token template for a daily category)."
    )
    files: list[Path] | None = Field(
        default=None,
        description="Every file written for the category in record order (daily categories have one per day); None for one file.",
    )
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
    """Cell bounds; a pole_centred grid's polar half cells are clipped to ±90."""
    c = lat_centres(grid)
    return np.clip(
        np.stack([c - grid.dlat / 2, c + grid.dlat / 2], axis=1), -90.0, 90.0
    )


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


def _grid_dataset(
    grid: mf.LatLonGrid, radius_m: float, layout: mf.FileLayout | None = None
) -> xr.Dataset:
    """Coordinates named by the layout (lon/lat by default), with bounds and the area variable unless the layout says not to."""
    lon_name = layout.lon_name if layout else "lon"
    lat_name = layout.lat_name if layout else "lat"
    with_extras = layout is None or layout.write_bounds_and_area
    lon_attrs = {"units": "degrees_east", "standard_name": "longitude", "axis": "X"}
    lat_attrs = {"units": "degrees_north", "standard_name": "latitude", "axis": "Y"}
    data_vars: dict[str, tuple[tuple[str, ...], Float2D, dict[str, str]]] = {}
    if with_extras:
        lon_attrs["bounds"] = "lon_bnds"
        lat_attrs["bounds"] = "lat_bnds"
        data_vars = {
            "lon_bnds": ((lon_name, "nv"), lon_bounds(grid), {}),
            "lat_bnds": ((lat_name, "nv"), lat_bounds(grid), {}),
            "area": (
                (lat_name, lon_name),
                cell_area(grid, radius_m),
                {"units": "m2", "long_name": "cell area", "standard_name": "cell_area"},
            ),
        }
    return xr.Dataset(
        data_vars=data_vars,
        coords={
            lon_name: (lon_name, lon_centres(grid), lon_attrs),
            lat_name: (lat_name, lat_centres(grid), lat_attrs),
        },
    )


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


def _days(clock: mf.Clock) -> list[date]:
    """The calendar days (UTC) a run touches, in order."""
    first = clock.start.astimezone(UTC).date()
    last = (clock.end - timedelta(microseconds=1)).astimezone(UTC).date()
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


def _decode(values: list[float], units: str, calendar: str) -> list[datetime]:
    decoded = netCDF4.num2date(
        np.asarray(values, dtype=np.float64),
        units=units,
        calendar=calendar,
        only_use_cftime_datetimes=False,
        only_use_python_datetimes=True,
    )
    dates = decoded if isinstance(decoded, np.ndarray) else np.array([decoded])
    return [d.replace(tzinfo=UTC) for d in cast(list[datetime], dates.ravel().tolist())]


def explicit_time_axis(cadence: mf.Cadence, axis: mf.ExplicitTimeAxis) -> TimeAxis:
    """A file's own time coordinate written verbatim; bounds are the midpoints between records (a single record gets ±0.5)."""
    values = axis.values
    if len(values) > 1:
        mids = [(a + b) / 2 for a, b in itertools.pairwise(values)]
        edges = [
            values[0] - (mids[0] - values[0]),
            *mids,
            values[-1] + (values[-1] - mids[-1]),
        ]
    else:
        edges = [values[0] - 0.5, values[0] + 0.5]
    return TimeAxis(
        cadence=cadence,
        values=list(values),
        bounds=list(itertools.pairwise(edges)),
        units=axis.units,
        calendar=axis.calendar,
        stamps=_decode(values, axis.units, axis.calendar),
    )


def daily_time_axis(day: date, record_hour: float) -> TimeAxis:
    """One record at `record_hour` of the day, in hours since the day's 00:00 UTC (the CEDS, MEGAN and QFED daily files)."""
    midnight = datetime(day.year, day.month, day.day, tzinfo=UTC)
    return TimeAxis(
        cadence="daily",
        values=[record_hour],
        bounds=[(0.0, 24.0)],
        units=f"hours since {_cf_date(midnight)}",
        stamps=[midnight + timedelta(hours=record_hour)],
    )


def time_axis(
    cadence: mf.Cadence,
    clock: mf.Clock,
    layout: mf.FileLayout | None = None,
    day: date | None = None,
) -> TimeAxis:
    """hourly: 25 centred records from start to start+duration; daily: one record of `day`; monthly: 12 mid-month records of the start year (or the layout's explicit axis); static: one record at start (or the explicit axis)."""
    if layout is not None and layout.time_axis is not None:
        return explicit_time_axis(cadence, layout.time_axis)
    if cadence == "daily":
        if day is None:
            raise ValueError("a daily time axis needs the day")
        return daily_time_axis(day, layout.daily_record_hour if layout else 12.0)
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


def _dims(layout: mf.FileLayout | None, is_3d: bool) -> tuple[str, ...]:
    lon = layout.lon_name if layout else "lon"
    lat = layout.lat_name if layout else "lat"
    time = layout.time_name if layout else "time"
    lev = layout.lev_name if layout else "lev"
    if not is_3d:
        return (time, lat, lon)
    return (
        (lev, time, lat, lon)
        if layout and layout.lev_before_time
        else (time, lev, lat, lon)
    )


def write_file(
    manifest: mf.Manifest,
    path: Path,
    names: list[str],
    fraction: Float2D | None,
    day: date | None = None,
) -> dict[str, CategoryTotals]:
    """Write one inventory file holding the variables of the categories `names` (which share grid, cadence and layout) and return their totals.

    `fraction` is the land fraction on the file's grid (or None); `day` is the calendar day of a daily file.
    """
    first = manifest.categories[names[0]]
    layout = first.layout
    grid = manifest.grids[first.grid]
    axis = time_axis(first.cadence, manifest.clock, layout, day)
    time_name = layout.time_name if layout else "time"
    with_extras = layout is None or layout.write_bounds_and_area
    ds = _grid_dataset(grid, manifest.earth_radius_m, layout)
    time_attrs = {
        "units": axis.units,
        "calendar": axis.calendar,
        "standard_name": "time",
        "axis": "T",
    }
    if with_extras:
        time_attrs["bounds"] = "time_bnds"
    ds = ds.assign_coords(
        {time_name: (time_name, np.array(axis.values, dtype=np.float64), time_attrs)}
    )
    if with_extras:
        ds["time_bnds"] = ((time_name, "nv"), np.array(axis.bounds, dtype=np.float64))
    dims = _dims(layout, first.is_3d)
    if first.is_3d:
        lev_name = layout.lev_name if layout else "lev"
        ds = ds.assign_coords(
            {
                lev_name: (
                    lev_name,
                    np.arange(manifest.clock.n_levels, dtype=np.int32),
                    {
                        "long_name": "model level index",
                        "positive": "up"
                        if first.level_order == "bottom_up"
                        else "down",
                        "axis": "Z",
                        "level_order": first.level_order,
                    },
                )
            }
        )
    area = cell_area(grid, manifest.earth_radius_m)
    dtype = np.dtype(layout.dtype if layout else "float32")
    classic = layout is not None and layout.model != "NETCDF4"
    deflate = {} if classic else VariableEncoding.from_settings().as_encoding()
    encoding: dict[str, dict[str, object]] = {
        str(n): coordinate_encoding() for n in [*ds.coords, *ds.data_vars]
    }
    totals: dict[str, CategoryTotals] = {}
    for name in names:
        category = manifest.categories[name]
        per_category: dict[str, VariableTotals] = {}
        for var_name, variable in category.variables.items():
            values = field_values(
                variable, category, grid, manifest.clock, fraction, axis
            )
            per_level = values.astype(np.float64) * area
            summed = per_level.sum(axis=(-2, -1))
            records = summed if category.is_3d else summed[:, None]
            if category.is_3d and layout is not None and layout.lev_before_time:
                values = np.swapaxes(values, 0, 1)
            ds[var_name] = (
                dims,
                values.astype(dtype),
                {"units": variable.units, "long_name": variable.long_name},
            )
            encoding[var_name] = dict(deflate)
            if layout is not None and layout.fill_value is not None:
                encoding[var_name]["_FillValue"] = np.asarray(
                    layout.fill_value, dtype=dtype
                )
            per_category[var_name] = VariableTotals(
                units=variable.units, records=records.tolist()
            )
            log.info(
                "%s/%s: %d records, total x area first record %.6e (%s m2)",
                name,
                var_name,
                len(axis.values),
                records[0].sum(),
                variable.units,
            )
        totals[name] = CategoryTotals(
            file=category.file, files=[path], times=axis.stamps, variables=per_category
        )
    ds.attrs = FileAttributes(
        Conventions=get_settings().cf_conventions,
        title=f"analytic {', '.join(names)} inputs for the coupling prototypes",
        category=",".join(names),
        cadence=first.cadence,
        regrid_method=first.regrid_method,
        cece_mapalgo=first.cece_mapalgo,
        masked_by=first.masked_by,
        resolution=manifest.resolution,
    ).as_attrs()
    path.parent.mkdir(parents=True, exist_ok=True)
    ds.to_netcdf(
        path,
        format=XARRAY_FORMATS[layout.model if layout else "NETCDF4"],
        encoding=encoding,
    )
    log.info("wrote %s", path)
    return totals


def write_category(
    manifest: mf.Manifest, name: str, fraction: Float2D | None
) -> CategoryTotals:
    """Write the (single, non-daily) file of one category and return its totals; the analytic profile's entry point."""
    return write_file(manifest, manifest.categories[name].file, [name], fraction)[name]


def _concatenate(parts: list[CategoryTotals]) -> CategoryTotals:
    """The totals of a daily category's files, in day order."""
    first = parts[0]
    variables = {
        v: VariableTotals(
            units=first.variables[v].units,
            records=[r for part in parts for r in part.variables[v].records],
        )
        for v in first.variables
    }
    return CategoryTotals(
        file=first.file,
        files=[f for part in parts for f in part.files or []],
        times=[s for part in parts for s in part.times or []],
        variables=variables,
    )


def copy_point_table(manifest: mf.Manifest, name: str) -> list[Path]:
    """Copy the real volcanic tables (settings `extdata_defaults_dir`/`volcanic_dirname`) to the category's files for the clock."""
    settings = get_settings()
    source_dir = settings.extdata_defaults_dir / settings.volcanic_dirname
    written: list[Path] = []
    for target in manifest.categories[name].files_for(manifest.clock):
        source = source_dir / target.name
        if not source.exists():
            raise ValueError(
                f"point table {source} for category {name!r} is not checked in"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        log.info("copied %s -> %s", source, target)
        written.append(target)
    return written


def generate_inventories(manifest: mf.Manifest) -> Totals:
    """Land mask, every inventory file (one per file path and day, shared by the categories that name it), the point tables, and totals.yaml."""
    write_land_mask(manifest)
    fractions: dict[str, Float2D] = {}
    categories: dict[str, CategoryTotals] = {}
    for template, names in manifest.categories_by_file().items():
        first = manifest.categories[names[0]]
        if first.is_point_table:
            copy_point_table(manifest, names[0])
            continue
        fraction = None
        if any(manifest.categories[n].masked_by != "none" for n in names):
            if first.grid not in fractions:
                fractions[first.grid] = land_fraction(
                    manifest.grids[first.grid], manifest.land_mask
                )
            fraction = fractions[first.grid]
        parts: dict[str, list[CategoryTotals]] = {n: [] for n in names}
        if first.cadence == "daily":
            days = _days(manifest.clock)
            for day, path in zip(days, first.files_for(manifest.clock), strict=True):
                for n, part in write_file(manifest, path, names, fraction, day).items():
                    parts[n].append(part)
        else:
            for n, part in write_file(manifest, template, names, fraction).items():
                parts[n].append(part)
        for n in names:
            categories[n] = _concatenate(parts[n])
    totals = Totals(categories=categories)
    totals.to_yaml(manifest.inventories_dir / get_settings().totals_filename)
    return totals
