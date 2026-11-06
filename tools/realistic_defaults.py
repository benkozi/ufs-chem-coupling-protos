"""Build the realistic profile's parameter set from the ExtData scrape and CATChem's production configuration (issue #17).

    uv run python -m tools.realistic_defaults --headers tools/defaults/extdata/extdata_headers.json \\
        --catalog tools/defaults/extdata/file_catalog.json \\
        --catchem-config extern/CATChem/tests/Configs/Default/CATChem_new_config.yml \\
        --emission-map extern/CATChem/tests/Configs/Default/CATChem_emission.yml \\
        --out tools/defaults/manifest_realistic.yaml

What becomes real: the category names, the file paths (relative, under `ExtData/`, with the daily date
tokens), the cadences, the grids (count, spacing, origin, pole-centred or not), the time coordinates of the
monthly and static files, the level count and order, the variable names, units, types and fill values, and the
magnitudes (background = the scraped mean, source amplitude = the scraped maximum above it). What stays
analytic: the fields themselves (background plus Gaussian sources on the mask's continents, a seasonal cycle on
monthly data, an exponential profile on 3-D data), the land mask, the clock and the meteorology, all taken
from the analytic defaults file. The output is checked in and digest-pinned; this script is run once per
scrape. Exit status 0, 1 for a write failure, 2 for an input the builder refuses (named in the log).
"""

from __future__ import annotations

import argparse
import math
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import cast, get_args

import pydantic
import yaml
from pydantic import Field

from tools import extdata as xd
from tools import log
from tools import manifest as mf
from tools.settings import LogLevel, get_settings

logger = log.get_logger("realistic_defaults")

# Which fraction multiplies a category's field: sea-surface concentrations are ocean, aviation and 3-D
# oxidant fields cover the globe, everything else is land-bound (the analytic profile's rule).
OCEAN_CATEGORIES = ("dms", "ship")
UNMASKED_CATEGORIES = ("gmi",)
UNMASKED_SUBSTRINGS = ("aviation",)
SEASONAL_RELATIVE_AMPLITUDE = 0.3
SEASONAL_PEAK_MONTH = 7
VERTICAL_SCALE_HEIGHT_LEVELS = 25.0
FALLBACK_AMPLITUDE = 1.0e-10
OCEAN_SOURCES = ((-30.0, 50.0), (160.0, -45.0))
GLOBAL_SOURCE = (0.0, 0.0)
SOURCE_SIGMA_DEG = 15.0
# Scraped statistics are rounded to this many significant digits: a 17-digit float does not survive the YAML
# formatter (yamlfix re-emits floats through ruyaml and moves the last digit), and the data do not carry it.
SIGNIFICANT_DIGITS = 6


def rounded(value: float) -> float:
    return float(f"{value:.{SIGNIFICANT_DIGITS}g}")


class ExtemisCategory(mf.Strict):
    """The keys of a `processes.extemis.<category>` block the builder reads (the rest is ignored)."""

    model_config = pydantic.ConfigDict(extra="ignore")

    activate: bool = Field(default=True, description="Category switch.")
    source_file: str = Field(
        description="File template, relative to the run directory."
    )
    format: str = Field(
        default="netcdf", description="netcdf, or volcano/point for a point table."
    )
    gridded: bool = Field(default=True, description="False for point sources.")
    frequency: mf.Cadence = Field(
        description="CATChem frequency: the category's cadence."
    )
    regrid_method: mf.RegridMethod = Field(
        default="conserve", description="CATChem regrid method of the category."
    )
    is_2d: bool = Field(
        default=True, description="Surface category, or 3-D on the file's levels."
    )
    reverse_vertical: bool = Field(
        default=False,
        description="True when CATChem flips the file's levels (a top-down file).",
    )


class EmissionMapEntry(mf.Strict):
    """One file variable of CATChem's emission map (the keys the builder reads)."""

    model_config = pydantic.ConfigDict(extra="ignore")

    long_name: str = Field(description="Descriptive name of the file variable.")
    units: str = Field(
        description="CATChem's unit spelling, used when the file carries none."
    )


class Inputs(mf.Strict):
    """Everything the builder reads, validated."""

    headers: xd.ExtDataHeaders = Field(description="The describe output.")
    catalog: xd.FileCatalog = Field(description="The scan-log output.")
    extemis: dict[str, ExtemisCategory] = Field(
        description="CATChem's processes.extemis categories by name."
    )
    emission_map: dict[str, dict[str, EmissionMapEntry]] = Field(
        description="CATChem's emission map: category -> file variable -> entry."
    )
    base: mf.ManifestDefaults = Field(
        description="The analytic defaults: clock, land mask, meteorology and Earth radius are taken from it."
    )


class BuildError(ValueError):
    """An input the builder refuses, named."""


def load_inputs(
    headers: Path, catalog: Path, catchem_config: Path, emission_map: Path, base: Path
) -> Inputs:
    config = yaml.safe_load(catchem_config.read_text())
    extemis = {
        name: block
        for name, block in config["processes"]["extemis"].items()
        if isinstance(block, dict)
    }
    return Inputs.model_validate(
        {
            "headers": xd.ExtDataHeaders.model_validate_json(headers.read_text()),
            "catalog": xd.FileCatalog.model_validate_json(catalog.read_text()),
            "extemis": extemis,
            "emission_map": yaml.safe_load(emission_map.read_text()),
            "base": mf.ManifestDefaults.from_yaml(base),
        }
    )


def grid_from_axes(lon: xd.AxisSummary, lat: xd.AxisSummary) -> mf.LatLonGrid:
    """A regular grid with the file's counts and extent; a non-uniform axis is reproduced as uniform with the same count and extent."""
    if lon.spacing is None or lat.spacing is None:
        raise BuildError(
            f"axes {lon.name}/{lat.name} with a single value cannot define a grid"
        )
    dlon = 360.0 / lon.count
    lat_first = min(lat.first, lat.last)
    lat_last = max(lat.first, lat.last)
    dlat = (
        (lat_last - lat_first) / (lat.count - 1) if lat.count > 1 else abs(lat.spacing)
    )
    pole_centred = abs(abs(lat_first) - 90.0) < 1e-6
    return mf.LatLonGrid(
        dlon=dlon,
        dlat=round(dlat, 9),
        lon_first=round(min(lon.first, lon.last), 9),
        lat_first=round(lat_first, 9),
        nlon=lon.count,
        nlat=lat.count,
        pole_centred=pole_centred,
    )


def _grid_name(grid: mf.LatLonGrid) -> str:
    return f"g{grid.nlon}x{grid.nlat}{'p' if grid.pole_centred else ''}"


def _masked_by(name: str) -> mf.MaskedBy:
    if name in OCEAN_CATEGORIES:
        return "ocean"
    if name in UNMASKED_CATEGORIES or any(s in name for s in UNMASKED_SUBSTRINGS):
        return "none"
    return "land"


def _sources(
    masked_by: mf.MaskedBy, base: mf.ManifestDefaults, amplitude: float
) -> list[mf.GaussianSource]:
    if masked_by == "land":
        centres = [(c.lon, c.lat) for c in base.land_mask.continents]
    elif masked_by == "ocean":
        centres = list(OCEAN_SOURCES)
    else:
        centres = [GLOBAL_SOURCE]
    return [
        mf.GaussianSource(
            lon=lon, lat=lat, sigma_deg=SOURCE_SIGMA_DEG, amplitude=amplitude
        )
        for lon, lat in centres
    ]


def _variable(
    name: str,
    header: xd.VariableHeader,
    entry: EmissionMapEntry,
    category: ExtemisCategory,
    masked_by: mf.MaskedBy,
    base: mf.ManifestDefaults,
) -> mf.Variable:
    units = header.attributes.get("units") or entry.units
    long_name = header.attributes.get("long_name") or entry.long_name
    if header.stats is not None:
        background = rounded(max(header.stats.mean, 0.0))
        amplitude = rounded(max(header.stats.max - background, 0.0))
        logger.info(
            "%s: background %.4e, amplitude %.4e from the scraped statistics",
            name,
            background,
            amplitude,
        )
    else:
        background, amplitude = 0.0, FALLBACK_AMPLITUDE
        logger.warning(
            "%s: no statistics in the headers, analytic magnitudes used", name
        )
    return mf.Variable(
        units=str(units),
        long_name=str(long_name),
        background=background,
        sources=_sources(masked_by, base, amplitude),
        seasonal=(
            mf.Seasonal(
                relative_amplitude=SEASONAL_RELATIVE_AMPLITUDE,
                peak_month=SEASONAL_PEAK_MONTH,
            )
            if category.frequency == "monthly"
            else None
        ),
        vertical=(
            mf.Vertical(scale_height_levels=VERTICAL_SCALE_HEIGHT_LEVELS)
            if not category.is_2d
            else None
        ),
    )


def _layout(
    file: xd.FileHeader,
    first_variable: xd.VariableHeader,
    category_variable: xd.VariableHeader,
    category: ExtemisCategory,
) -> mf.FileLayout:
    """The file's layout: model, coordinate names and dimension order from the file (`first_variable` is its first data variable), dtype, fill and the time-dimension flag from the category's own variable (CEDS and MEGAN start with a float64 `AREA` map; a static soil map may share a file with a time-bearing field)."""
    axes = file.axes
    dtype: mf.DataType = (
        "float64" if category_variable.dtype == "float64" else "float32"
    )
    fill = category_variable.attributes.get("_FillValue")
    time_axis = None
    record_hour = 12.0
    if "time" in axes:
        time = axes["time"]
        if category.frequency == "daily":
            if time.stamps:
                stamp = time.stamps[0]
                hour, minute = int(stamp[11:13]), int(stamp[14:16])
                record_hour = hour + minute / 60.0
        elif time.units is not None:
            time_axis = mf.ExplicitTimeAxis(
                units=time.units,
                calendar=time.calendar or "standard",
                values=time.values,
            )
    dims = first_variable.dimensions
    lev_before_time = False
    if "lev" in axes and "time" in axes:
        lev_name, time_name = axes["lev"].name, axes["time"].name
        if lev_name in dims and time_name in dims:
            lev_before_time = dims.index(lev_name) < dims.index(time_name)
    time_dimension = (
        "time" not in axes or axes["time"].name in category_variable.dimensions
    )
    if not time_dimension:
        time_axis = None
    if not time_dimension and category.frequency != "static":
        raise BuildError(
            f"{file.path}: variables without a time dimension in a {category.frequency} category"
        )
    lev_values = None
    lev_units = None
    if not category.is_2d and "lev" in axes:
        lev_values = list(axes["lev"].values)
        lev_units = axes["lev"].units
    model = file.model or "NETCDF4"
    if model not in get_args(mf.NetcdfModel):
        raise BuildError(f"{file.path}: unsupported NetCDF model {model!r}")
    return mf.FileLayout(
        model=cast(mf.NetcdfModel, model),
        lon_name=axes["lon"].name,
        lat_name=axes["lat"].name,
        lev_name=axes["lev"].name if "lev" in axes else "lev",
        time_name=axes["time"].name if "time" in axes else "time",
        dtype=dtype,
        # NaN cannot round-trip through YAML equality; xarray's default float fill is NaN anyway
        fill_value=(
            float(fill)
            if isinstance(fill, int | float) and not math.isnan(float(fill))
            else None
        ),
        lev_before_time=lev_before_time,
        write_bounds_and_area=False,
        daily_record_hour=record_hour,
        time_axis=time_axis,
        time_dimension=time_dimension,
        lev_values=lev_values,
        lev_units=lev_units,
    )


def _relative_template(source_file: str) -> Path:
    """The CATChem template as the manifest path: relative to the output directory, under the ExtData directory."""
    text = source_file.removeprefix("./")
    return Path(text)


def build(inputs: Inputs) -> mf.ManifestDefaults:
    """The realistic `ManifestDefaults`: the base's clock, land mask and meteorology, the catalog's categories."""
    base = inputs.base
    headers_by_path = inputs.headers.by_path()
    grids: dict[str, mf.LatLonGrid] = {
        base.land_mask.grid: base.grids[base.land_mask.grid]
    }
    categories: dict[str, mf.Category] = {}
    catalogued = [
        f for f in inputs.catalog.files if f.role in ("inventory", "point_table")
    ]
    seen: set[str] = set()
    for read in catalogued:
        for name in read.categories:
            if name in seen:
                continue
            seen.add(name)
            if name not in inputs.extemis:
                raise BuildError(
                    f"category {name!r} read {read.path} but has no processes.extemis block"
                )
            category = inputs.extemis[name]
            template = _relative_template(category.source_file)
            if not category.gridded or category.format in ("volcano", "point"):
                categories[name] = mf.Category(
                    file=template,
                    cadence=category.frequency,
                    regrid_method="none",
                    grid=base.land_mask.grid,
                    is_3d=False,
                    masked_by="none",
                    variables={},
                    layout=mf.FileLayout(format="point_table"),
                )
                continue
            header = headers_by_path.get(read.path)
            if header is None or not header.exists or header.error:
                raise BuildError(
                    f"category {name!r}: {read.path} is missing or in error in the headers"
                )
            if "lon" not in header.axes or "lat" not in header.axes:
                raise BuildError(
                    f"category {name!r}: {read.path} has no longitude/latitude axes"
                )
            grid = grid_from_axes(header.axes["lon"], header.axes["lat"])
            grid_name = _grid_name(grid)
            grids.setdefault(grid_name, grid)
            is_3d = not category.is_2d
            if is_3d:
                lev = header.axes.get("lev")
                if lev is None or lev.count != base.clock.n_levels:
                    raise BuildError(
                        f"category {name!r}: {read.path} has {lev.count if lev else 'no'} levels, the clock has {base.clock.n_levels}"
                    )
            map_entries = inputs.emission_map.get(name, {})
            if not map_entries:
                raise BuildError(f"category {name!r} has no emission map entries")
            masked_by = _masked_by(name)
            variables: dict[str, mf.Variable] = {}
            for var_name, entry in map_entries.items():
                var_header = header.variables.get(var_name)
                if var_header is None:
                    raise BuildError(
                        f"category {name!r}: variable {var_name!r} of the emission map is not in {read.path}"
                    )
                variables[var_name] = _variable(
                    f"{name}/{var_name}", var_header, entry, category, masked_by, base
                )
            # the layout comes from the file's first data variable, so categories sharing a file agree
            first_header = next(
                v for v in header.variables.values() if len(v.dimensions) >= 2
            )
            categories[name] = mf.Category(
                file=template,
                cadence=category.frequency,
                regrid_method=category.regrid_method,
                grid=grid_name,
                is_3d=is_3d,
                level_order="top_down" if category.reverse_vertical else "bottom_up",
                masked_by=masked_by,
                variables=variables,
                layout=_layout(
                    header,
                    first_header,
                    header.variables[next(iter(map_entries))],
                    category,
                ),
            )
    skipped = sorted(set(inputs.extemis) - seen)
    if skipped:
        logger.warning(
            "categories in the CATChem config but not in the catalog, left out: %s",
            ", ".join(skipped),
        )
    return mf.ManifestDefaults(
        earth_radius_m=base.earth_radius_m,
        clock=base.clock,
        grids=grids,
        land_mask=base.land_mask,
        categories=categories,
        met=base.met,
    )


def build_parser() -> argparse.ArgumentParser:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        prog="realistic_defaults", description=__doc__.split("\n\n")[0]
    )
    extdata = settings.extdata_defaults_dir
    parser.add_argument(
        "--headers",
        type=Path,
        default=extdata / settings.extdata_headers_filename,
        help="the describe JSON",
    )
    parser.add_argument(
        "--catalog",
        type=Path,
        default=extdata / settings.file_catalog_filename,
        help="the scan-log JSON",
    )
    parser.add_argument(
        "--catchem-config",
        type=Path,
        required=True,
        help="CATChem_new_config.yml with the production extemis block",
    )
    parser.add_argument(
        "--emission-map", type=Path, required=True, help="CATChem_emission.yml"
    )
    parser.add_argument(
        "--base",
        type=Path,
        default=settings.defaults_file,
        help="the analytic defaults (clock, land mask, met)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=settings.realistic_defaults_file,
        help="the realistic defaults YAML to write",
    )
    parser.add_argument(
        "--log-level", default=None, choices=get_args(LogLevel), help="log level"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    log.configure(args.log_level)
    try:
        inputs = load_inputs(
            args.headers,
            args.catalog,
            args.catchem_config,
            args.emission_map,
            args.base,
        )
        defaults = build(inputs)
    except (
        pydantic.ValidationError,
        OSError,
        KeyError,
        BuildError,
        yaml.YAMLError,
    ) as exc:
        logger.error("cannot build the realistic defaults: %s", exc)
        return 2
    try:
        defaults.to_yaml(args.out)
    except OSError as exc:
        logger.error("cannot write %s: %s", args.out, exc)
        return 1
    logger.info(
        "wrote %s (%d categories, %d grids)",
        args.out,
        len(defaults.categories),
        len(defaults.grids),
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
