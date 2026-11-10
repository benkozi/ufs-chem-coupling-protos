"""Synthetic GOCART optics (Mie) tables for the realistic profile.

CATChem's production science configuration names six tables under `ExtData/monochromatic/` (its `mie:` section)
and its settling scheme (`gocart/simple_scheme`) refuses to initialize without them. The real files are 74 to
317 MB each, nearly all of it the phase-function moments (`pmom`, `pback`) the settling scheme never reads. A
synthetic table keeps the real file's data model, dimensions (the moments' dimension `nMom` shrunk to one
entry), variables, types and attributes as the ExtData scrape recorded them (`extdata_headers.json`), leaves
the two moment variables out, and fills the rest from closed forms and the scraped statistics: the relative
humidity and wavelength axes (550 nm among the wavelengths, which the settling scheme looks up by exact match), dry radii spanning the scraped effective-radius range, a growth factor rising
with humidity, a wet density falling with it, and the scraped mean for every other variable. No random numbers:
the tables are reproducible from the scrape alone. They are inputs that let the production configuration run;
their optical values are not physical results.
"""

from __future__ import annotations

from pathlib import Path
from typing import get_args

import netCDF4
import numpy as np
from numpy.typing import NDArray

from tools import extdata as xd
from tools import manifest as mf
from tools.log import get_logger
from tools.settings import get_settings

log = get_logger("optics")

FloatArray = NDArray[np.float64]

# Dimensions and variables of the phase-function moments: the dimension is kept at one entry, the variables are
# not written (GOCART2G_MieMod reads `pmom` only when asked for moments and treats `pback` as optional).
MOMENT_DIMENSION = "nMom"
MOMENT_VARIABLES = ("pmom", "pback")
# Axis ranges of the real tables' kind: relative humidity as a fraction below saturation, wavelength in metres.
RH_MAX = 0.99
LAMBDA_RANGE_M = (2.5e-7, 4.0e-5)
# GOCART looks a wavelength up by exact match (GOCART2G_MieMod getChannel, tolerance 1e-9 m) and the settling
# scheme asks for 550 nm (GOCART2G_Process.F90:1560), so the axis must hold it: the nearest entry is moved there.
REQUIRED_WAVELENGTH_M = 550.0e-9
# Bin edges around the dry effective radius, as factors.
BIN_EDGE_FACTORS = (0.8, 1.25)
# Largest wet-to-dry radius ratio when the table has no growth_factor variable to take it from.
DEFAULT_GROWTH_MAX = 2.0
WATER_DENSITY_KG_M3 = 1000.0
MODELS: dict[str, mf.NetcdfModel] = {name: name for name in get_args(mf.NetcdfModel)}
SOURCE_NOTE = (
    "synthetic table written by tools/optics.py from the ExtData scrape (dimensions, variables and statistics "
    "of the real file; phase-function moments left out); not a physical result"
)


def load_headers() -> xd.ExtDataHeaders:
    """The checked-in scrape (settings `extdata_defaults_dir`/`extdata_headers_filename`)."""
    settings = get_settings()
    path = settings.extdata_defaults_dir / settings.extdata_headers_filename
    return xd.ExtDataHeaders.model_validate_json(path.read_text())


def _stats(header: xd.FileHeader, name: str) -> xd.ValueStats:
    stats = header.variables[name].stats
    if stats is None:
        raise ValueError(f"{Path(header.path).name}: no statistics for {name}")
    return stats


def table_values(header: xd.FileHeader) -> dict[str, FloatArray]:
    """Every variable of the table except the moments, as float64 arrays in the file's dimension order."""
    sizes = {name: int(d["size"]) for name, d in header.dimensions.items()}
    rh = np.linspace(0.0, RH_MAX, sizes["rh"])
    reff = _stats(header, "rEff")
    # Dry radii from the smallest scraped effective radius to the geometric middle of the range; the growth
    # factor takes the largest bin from there to at most the scraped maximum.
    dry = np.geomspace(reff.min, np.sqrt(reff.min * reff.max), sizes["radius"])
    growth_max = (
        _stats(header, "growth_factor").max
        if "growth_factor" in header.variables
        else DEFAULT_GROWTH_MAX
    )
    growth_max = min(growth_max, float(np.sqrt(reff.max / reff.min)))
    growth = np.broadcast_to(
        1.0 + (growth_max - 1.0) * (rh / RH_MAX) ** 4, (sizes["radius"], sizes["rh"])
    )
    wavelengths = np.geomspace(*LAMBDA_RANGE_M, sizes["lambda"])
    wavelengths[np.abs(wavelengths - REQUIRED_WAVELENGTH_M).argmin()] = (
        REQUIRED_WAVELENGTH_M
    )
    values: dict[str, FloatArray] = {
        "rh": rh,
        "lambda": wavelengths,
        "radius": dry,
        "rLow": dry * BIN_EDGE_FACTORS[0],
        "rUp": dry * BIN_EDGE_FACTORS[1],
        "rEff": dry[:, None] * growth,
        "growth_factor": growth,
    }
    if "rhop" in header.variables:
        dry_density = _stats(header, "rhop").max
        values["rhop"] = (
            WATER_DENSITY_KG_M3 + (dry_density - WATER_DENSITY_KG_M3) / growth**3
        )
    for name, variable in header.variables.items():
        if name in MOMENT_VARIABLES or name in values:
            continue
        values[name] = np.full(variable.shape, _stats(header, name).mean)
    return {name: values[name] for name in header.variables if name in values}


def write_table(header: xd.FileHeader, path: Path) -> Path:
    """One synthetic table at `path`, in the real file's data model."""
    model = MODELS.get(header.model or "")
    if model is None:
        raise ValueError(f"{header.path}: the scrape has no NetCDF data model")
    values = table_values(header)
    path.parent.mkdir(parents=True, exist_ok=True)
    with netCDF4.Dataset(path, "w", format=model) as nc:
        for name, dimension in header.dimensions.items():
            nc.createDimension(
                name, 1 if name == MOMENT_DIMENSION else int(dimension["size"])
            )
        for name, array in values.items():
            described = header.variables[name]
            variable = nc.createVariable(
                name, np.dtype(described.dtype), tuple(described.dimensions)
            )
            variable.setncatts(described.attributes)
            variable[:] = array
        nc.setncattr("source", SOURCE_NOTE)
    log.info("wrote %s (%d bytes)", path, path.stat().st_size)
    return path


def write_optics_tables(headers: xd.ExtDataHeaders, out_dir: Path) -> list[Path]:
    """Every optics table the scrape names, at its catalog path under `out_dir` (`ExtData/monochromatic/...`)."""
    tables = [f for f in headers.files if f.role == "optics"]
    if not tables:
        raise ValueError("the ExtData scrape names no optics table")
    return [write_table(header, out_dir / header.path) for header in tables]
