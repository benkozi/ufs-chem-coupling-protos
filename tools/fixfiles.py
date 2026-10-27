"""Fetch and validate the FV3 fix files: the GRIDSPEC mosaic, six supergrid tiles, six grid_spec tiles.

Source: https://ftp.emc.ncep.noaa.gov/static_files/public/UFS/GFS/fix/fix_fv3/<res>/ (unchanged since 2016).
Downloads are streamed to `<name>.part` and renamed only when the byte count matches `Content-Length`, so a
truncated transfer never leaves a partial file in place. Validation checks what the consumers actually read:
ESMF_GridCreateMosaic finds tile variables by `standard_name` and requires (2N+1)² supergrids; CECE's
gridspec reader and the inventory generator use `grid_lont`/`grid_latt`/`area`; the mosaic must name the six
tile files that were downloaded.
"""

from __future__ import annotations

import math
import os
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Protocol, Self

import netCDF4
import numpy as np
from pydantic import Field

from tools import manifest as mf
from tools.log import get_logger
from tools.settings import get_settings

log = get_logger("fixfiles")


class FixFileError(Exception):
    """A fix file could not be downloaded or does not look like the file the consumers expect."""


class _Response(Protocol):
    @property
    def headers(self) -> object: ...

    def read(self, n: int = -1, /) -> bytes: ...

    def __enter__(self) -> Self: ...

    def __exit__(self, *exc: object) -> object: ...


Opener = Callable[..., _Response]


class GridInfo(mf.Strict):
    shape: tuple[int, int] = Field(
        description="Shape of the supergrid coordinate arrays, (2N+1, 2N+1)."
    )


class GridSpecInfo(mf.Strict):
    shape: tuple[int, int] = Field(
        description="Shape of the cell-centre arrays, (N, N)."
    )
    area_total: float = Field(description="Sum of the tile's cell areas, m².")


def expected_names(resolution: str) -> list[str]:
    """Mosaic first, then the six grid tiles, then the six grid_spec tiles."""
    return [
        f"{resolution}_mosaic.nc",
        *(f"{resolution}_grid.tile{t}.nc" for t in range(1, 7)),
        *(f"{resolution}_grid_spec.tile{t}.nc" for t in range(1, 7)),
    ]


def fix_files_for(
    resolution: str, fix_dir: Path, source_url: str | None = None
) -> mf.FixFiles:
    names = expected_names(resolution)
    url = (source_url or get_settings().fix_url).format(resolution=resolution)
    return mf.FixFiles(
        source_url=url,
        directory=fix_dir,
        mosaic=fix_dir / names[0],
        grid_tiles=[fix_dir / n for n in names[1:7]],
        grid_spec_tiles=[fix_dir / n for n in names[7:]],
    )


def url_for(fix: mf.FixFiles, name: str) -> str:
    return fix.source_url + name


def _paths(fix: mf.FixFiles) -> list[Path]:
    return [fix.mosaic, *fix.grid_tiles, *fix.grid_spec_tiles]


# --- download ----------------------------------------------------------------------------------------------


def download(url: str, dest: Path, opener: Opener) -> int:
    """Stream `url` to `dest` through a `.part` file; return the byte count. Raises FixFileError on any failure."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    count = 0
    try:
        settings = get_settings()
        with (
            opener(url, timeout=settings.download_timeout_s) as response,
            part.open("wb") as out,
        ):
            expected = _content_length(response)
            while chunk := response.read(settings.download_chunk_bytes):
                out.write(chunk)
                count += len(chunk)
    except (urllib.error.URLError, OSError) as exc:
        part.unlink(missing_ok=True)
        raise FixFileError(f"download of {dest.name} from {url} failed: {exc}") from exc
    if expected is not None and count != expected:
        part.unlink(missing_ok=True)
        raise FixFileError(
            f"download of {dest.name} is incomplete: got {count} bytes, expected {expected} (Content-Length)"
        )
    os.replace(part, dest)
    log.debug("downloaded %s (%d bytes)", dest.name, count)
    return count


def _content_length(response: _Response) -> int | None:
    headers = response.headers
    value = headers.get("Content-Length") if hasattr(headers, "get") else None
    return int(value) if value else None


def fetch(
    fix: mf.FixFiles,
    resolution: str,
    n: int,
    radius_m: float,
    opener: Opener | None = None,
) -> dict[str, int]:
    """Download every file not already present and valid, then validate the whole set; return byte sizes by name.

    `n` is the tile size (48 for C48); it is separate from `resolution` so tests can run tiny synthetic tiles.
    """
    open_url: Opener = opener or urllib.request.urlopen
    skipped = 0
    for path in _paths(fix):
        if path.exists():
            try:
                _validate_one(path, fix, n, resolution, radius_m)
            except FixFileError as exc:
                log.warning(
                    "%s is present but invalid (%s); downloading again", path.name, exc
                )
            else:
                skipped += 1
                log.debug("%s present and valid, skipped", path.name)
                continue
        url = url_for(fix, path.name)
        log.info("downloading %s", url)
        download(url, path, open_url)
    sizes = validate_set(fix, n, resolution, radius_m)
    log.info(
        "fix files for %s complete in %s (%d downloaded, %d already present)",
        resolution,
        fix.directory,
        len(sizes) - skipped,
        skipped,
    )
    return sizes


# --- validation --------------------------------------------------------------------------------------------


def _open(path: Path) -> netCDF4.Dataset:
    try:
        return netCDF4.Dataset(path, "r")
    except OSError as exc:
        raise FixFileError(f"cannot read {path.name}: {exc}") from exc


def _var_by_standard_name(
    ds: netCDF4.Dataset, standard_name: str, path: Path
) -> netCDF4.Variable[object]:
    for var in ds.variables.values():
        if getattr(var, "standard_name", None) == standard_name:
            return var
    raise FixFileError(f"{path.name}: no variable with standard_name {standard_name!r}")


def validate_grid(path: Path, n: int) -> GridInfo:
    """A supergrid tile as ESMF reads it: grid_tile_spec/cube_gnomonic, (2N+1)² geographic_longitude/latitude."""
    expected = (2 * n + 1, 2 * n + 1)
    with _open(path) as ds:
        tile = _var_by_standard_name(ds, "grid_tile_spec", path)
        projection = getattr(tile, "projection", None)
        if projection != "cube_gnomonic":
            raise FixFileError(
                f"{path.name}: tile projection is {projection!r}, expected 'cube_gnomonic'"
            )
        lon = _var_by_standard_name(ds, "geographic_longitude", path)
        lat = _var_by_standard_name(ds, "geographic_latitude", path)
        for name, var in (("longitude", lon), ("latitude", lat)):
            if tuple(var.shape) != expected:
                raise FixFileError(
                    f"{path.name}: {name} supergrid shape is {tuple(var.shape)}, expected {expected}"
                )
        x = np.ma.filled(lon[:].astype(float), np.nan)
        y = np.ma.filled(lat[:].astype(float), np.nan)
    if not (np.isfinite(x).all() and (np.abs(x) <= 360.0).all()):
        raise FixFileError(
            f"{path.name}: longitude values are not finite or outside [-360, 360]"
        )
    if not (np.isfinite(y).all() and (np.abs(y) <= 90.0).all()):
        raise FixFileError(
            f"{path.name}: latitude values are not finite or outside [-90, 90]"
        )
    return GridInfo(shape=expected)


def validate_grid_spec(path: Path, n: int, radius_m: float) -> GridSpecInfo:
    """A grid_spec tile: (N, N) grid_lont/grid_latt/area, (N+1, N+1) grid_lon/grid_lat, areas summing to a sixth of the sphere."""
    centre = (n, n)
    corner = (n + 1, n + 1)
    with _open(path) as ds:
        for name, shape in (
            ("grid_lont", centre),
            ("grid_latt", centre),
            ("area", centre),
            ("grid_lon", corner),
            ("grid_lat", corner),
        ):
            if name not in ds.variables:
                raise FixFileError(f"{path.name}: variable {name!r} is missing")
            if tuple(ds[name].shape) != shape:
                raise FixFileError(
                    f"{path.name}: {name} shape is {tuple(ds[name].shape)}, expected {shape}"
                )
        area = np.ma.filled(ds["area"][:].astype(float), np.nan)
    if not (np.isfinite(area).all() and (area > 0).all()):
        raise FixFileError(f"{path.name}: area has non-finite or non-positive cells")
    total = float(area.sum())
    sixth = 4.0 * math.pi * radius_m**2 / 6.0
    tolerance = get_settings().area_tolerance
    if abs(total / sixth - 1.0) > tolerance:
        raise FixFileError(
            f"{path.name}: area total {total:.6e} m² differs from a sixth of the sphere {sixth:.6e} m² by more than {tolerance:.0%}"
        )
    return GridSpecInfo(shape=centre, area_total=total)


def validate_mosaic(path: Path, resolution: str) -> list[str]:
    """The mosaic names exactly the six tile files of this resolution; returns the names in tile order."""
    expected = expected_names(resolution)[1:7]
    with _open(path) as ds:
        ntiles = len(ds.dimensions["ntiles"]) if "ntiles" in ds.dimensions else 0
        if ntiles != 6:
            raise FixFileError(f"{path.name}: ntiles is {ntiles}, expected 6")
        if "gridfiles" not in ds.variables:
            raise FixFileError(f"{path.name}: variable 'gridfiles' is missing")
        names = [
            str(s).strip("\x00 ") for s in netCDF4.chartostring(ds["gridfiles"][:])
        ]
        ncontact = len(ds.dimensions["ncontact"]) if "ncontact" in ds.dimensions else 0
    if names != expected:
        raise FixFileError(f"{path.name}: gridfiles are {names}, expected {expected}")
    if ncontact != 12:
        raise FixFileError(
            f"{path.name}: ncontact is {ncontact}, expected 12 (a six-tile cube has twelve contacts)"
        )
    return names


def _validate_one(
    path: Path, fix: mf.FixFiles, n: int, resolution: str, radius_m: float
) -> None:
    if path == fix.mosaic:
        validate_mosaic(path, resolution)
    elif path in fix.grid_tiles:
        validate_grid(path, n)
    else:
        validate_grid_spec(path, n, radius_m)


def validate_set(
    fix: mf.FixFiles, n: int, resolution: str, radius_m: float
) -> dict[str, int]:
    """Validate all 13 files; return their byte sizes by name. Raises FixFileError on the first problem."""
    sizes: dict[str, int] = {}
    for path in _paths(fix):
        if not path.exists():
            raise FixFileError(f"missing fix file {path.name} in {fix.directory}")
        _validate_one(path, fix, n, resolution, radius_m)
        sizes[path.name] = path.stat().st_size
    return sizes
