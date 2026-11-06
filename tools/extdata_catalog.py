# /// script
# requires-python = ">=3.12"
# dependencies = ["netCDF4>=1.7", "numpy>=2.0"]
# ///
"""Catalog the files a CATChem run opened and describe them on the platform that holds them (issue #17).

Two subcommands, run from the repository (``uv run python -m tools.extdata_catalog ...``) or as a
self-contained script anywhere uv exists (``uv run --script tools/extdata_catalog.py ...``; the header above
declares the dependencies):

    scan-log PET000.ESMF_LogFile [more logs] --out file_catalog.json
        Parses the ``CATCHEM_FILE_READ: format=<f> source=<s> path=<p>`` lines (CATChem
        ``feature/log-all-filepaths``) and the companion branch's ``CATChem: emission open category=<c>
        reader=<r> path=<p>`` lines into the unique files, each with its format, role, readers, the CATChem
        categories and model times of the reads, and the PETs that read it.

    describe --extdata-root DIR --catalog file_catalog.json --out extdata_headers.json [--copy-tables DIR]
        For every catalogued file under ``ExtData/``: NetCDF model, size, dimensions, variables with typed
        attributes, the lon/lat/lev/time axes decoded, value statistics (numpy over netCDF4 records), and for a
        volcanic ``.rc`` table the parsed rows. ``--copy-tables`` copies the tables verbatim.

The output is plain JSON; ``tools/extdata.py`` holds the pydantic models that validate it in the repository.
This file imports nothing from ``tools`` so that it stays self-contained on the RT platform.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, cast

import netCDF4
import numpy as np

if TYPE_CHECKING:
    NcVariable = netCDF4.Variable[np.generic]
else:
    NcVariable = netCDF4.Variable

PREFIX_RE = re.compile(r"^(\d{8} \d{6}\.\d+)\s+(\w+)\s+PET(\d+)\s+(.*)$")
TAG_RE = re.compile(r"CATCHEM_FILE_READ: format=(\S+) source=(\S+) path=(\S+)")
OPEN_RE = re.compile(r"CATChem: emission open category=(\S+) reader=(\S+) path=(\S+)")
UPDATE_RE = re.compile(r"catchem_emis_update: reading emission for (\S+) @ (\S+)")
POPULATE_RE = re.compile(
    r"catchem_emis_populate_category: Successfully populated category (\S+)"
)
POINTS_RE = re.compile(r"catchem_emis_read_points: read (\d+) point sources from (\S+)")

LON_NAMES = ("lon", "longitude", "x")
LAT_NAMES = ("lat", "latitude", "y")
LEV_NAMES = ("lev", "level", "levels", "z", "plev", "height")
TIME_NAMES = ("time",)
POINT_COLUMNS = ("lat", "lon", "emission", "bottom", "top")
POINT_UNITS = ("degrees_north", "degrees_east", "kg S s-1", "m", "m")
EARTH_RADIUS_M = 6371.0e3
UNIFORM_RTOL = 1e-4
JsonDict = dict[str, object]


# --- scan-log ----------------------------------------------------------------------------------------------


def normalise(path: str) -> str:
    """The path as the run directory sees it: no leading './' segments."""
    while path.startswith("./"):
        path = path[2:]
    return path


def role_of(fmt: str, source: str) -> str:
    if fmt == "yaml":
        return "config"
    if fmt == "point_ascii" or source == "point_rc":
        return "point_table"
    if source == "init_mie_data":
        return "optics"
    return "inventory"


class _Reads:
    """Accumulator of one file's reads while scanning (the JSON record is built at the end)."""

    def __init__(self, path: str, fmt: str, role: str, stamp: str) -> None:
        self.path = path
        self.format = fmt
        self.role = role
        self.first_seen = stamp
        self.sources: dict[str, int] = {}
        self.categories: dict[str, list[str]] = {}
        self.pets: list[int] = []
        self.read_count = 0
        self.point_count: int | None = None

    def add(self, source: str, pet: int, category: tuple[str, str] | None) -> None:
        self.sources[source] = self.sources.get(source, 0) + 1
        self.read_count += 1
        if pet not in self.pets:
            self.pets.append(pet)
        if self.role in ("inventory", "point_table") and category is not None:
            times = self.categories.setdefault(category[0], [])
            if category[1] not in times:
                times.append(category[1])

    def as_json(self) -> JsonDict:
        return {
            "path": self.path,
            "format": self.format,
            "role": self.role,
            "sources": self.sources,
            "categories": self.categories,
            "pets": self.pets,
            "read_count": self.read_count,
            "first_seen": self.first_seen,
            "point_count": self.point_count,
        }


def scan_logs(logs: Sequence[Path]) -> JsonDict:
    """The catalog document for one or more PET logs."""
    files: dict[str, _Reads] = {}
    shapes: set[str] = set()
    for log_path in logs:
        category: tuple[str, str] | None = None
        for line in log_path.read_text(errors="replace").splitlines():
            prefix = PREFIX_RE.match(line)
            if prefix is None:
                continue
            stamp, pet, message = prefix.group(1), int(prefix.group(3)), prefix.group(4)
            if m := UPDATE_RE.search(message):
                category = (m.group(1), m.group(2))
                continue
            if m := POPULATE_RE.search(message):
                category = (m.group(1), "init")
                continue
            if m := POINTS_RE.search(message):
                files[normalise(m.group(2))].point_count = int(m.group(1))
                continue
            if m := TAG_RE.search(message):
                fmt, source, path = m.group(1), m.group(2), normalise(m.group(3))
                shapes.add("catchem_file_read")
            elif m := OPEN_RE.search(message):
                source, path = m.group(2), normalise(m.group(3))
                fmt = "point_ascii" if source == "point_rc" else "netcdf"
                category = (m.group(1), category[1] if category else "init")
                shapes.add("emission_open")
            else:
                continue
            record = files.get(path)
            if record is None:
                record = files[path] = _Reads(path, fmt, role_of(fmt, source), stamp)
            record.add(source, pet, category)
    return {
        "generated_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "logs": [str(p) for p in logs],
        "line_shapes": sorted(shapes),
        "files": [r.as_json() for r in files.values()],
    }


# --- describe ----------------------------------------------------------------------------------------------


def json_safe(value: object) -> object:
    """Attribute values as JSON numbers, strings or lists; numpy scalars and arrays are converted."""
    if isinstance(value, np.ndarray):
        return [json_safe(v) for v in value.tolist()]
    if isinstance(value, np.generic):
        return json_safe(value.item())
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    if isinstance(value, list | tuple):
        return [json_safe(v) for v in value]
    if isinstance(value, str | int | float):
        return value
    return str(value)


def attributes_of(obj: netCDF4.Dataset | NcVariable) -> JsonDict:
    return {name: json_safe(obj.getncattr(name)) for name in obj.ncattrs()}


def axis_role(name: str, var: NcVariable) -> str | None:
    """lon, lat, lev or time for a 1-D variable that looks like a coordinate, else None."""
    if var.ndim != 1:
        return None
    lowered = name.lower()
    attrs = {k.lower(): str(v).lower() for k, v in attributes_of(var).items()}
    axis = attrs.get("axis", "")
    std = attrs.get("standard_name", "")
    units = attrs.get("units", "")
    if lowered in TIME_NAMES or axis == "t" or std == "time" or " since " in units:
        return "time"
    if (
        lowered in LON_NAMES
        or axis == "x"
        or std == "longitude"
        or units.startswith("degrees_e")
    ):
        return "lon"
    if (
        lowered in LAT_NAMES
        or axis == "y"
        or std == "latitude"
        or units.startswith("degrees_n")
    ):
        return "lat"
    if lowered in LEV_NAMES or axis == "z" or "positive" in attrs:
        return "lev"
    return None


def decode_time(
    values: np.ndarray, units: str | None, calendar: str | None
) -> list[str] | None:
    if not units:
        return None
    try:
        decoded = netCDF4.num2date(values, units=units, calendar=calendar or "standard")
    except ValueError:
        return None
    dates = decoded if isinstance(decoded, np.ndarray) else np.array([decoded])
    return [d.isoformat() for d in cast(list[dt.datetime], dates.ravel().tolist())]


class Axis:
    """A decoded coordinate axis."""

    def __init__(self, name: str, var: NcVariable) -> None:
        values = np.asarray(var[:], dtype=np.float64).ravel()
        attrs = attributes_of(var)
        units = attrs.get("units")
        calendar = attrs.get("calendar")
        self.name = name
        self.dimension = str(var.dimensions[0])
        self.values = values
        self.units = None if units is None else str(units)
        self.calendar = None if calendar is None else str(calendar)
        self.spacing: float | None = None
        self.uniform = True
        self.direction = "constant"
        if values.size > 1:
            diffs = np.diff(values)
            mean = float(diffs.mean())
            self.spacing = mean
            self.uniform = bool(
                np.allclose(
                    diffs, mean, rtol=UNIFORM_RTOL, atol=1e-9 * max(1.0, abs(mean))
                )
            )
            if (diffs > 0).all():
                self.direction = "increasing"
            elif (diffs < 0).all():
                self.direction = "decreasing"
        self.is_time = axis_role(name, var) == "time"

    def as_json(self) -> JsonDict:
        return {
            "name": self.name,
            "count": int(self.values.size),
            "first": float(self.values[0]),
            "last": float(self.values[-1]),
            "spacing": self.spacing,
            "uniform": self.uniform,
            "direction": self.direction,
            "units": self.units,
            "calendar": self.calendar,
            "values": [float(v) for v in self.values],
            "stamps": decode_time(self.values, self.units, self.calendar)
            if self.is_time
            else None,
        }


def value_stats(
    var: NcVariable, time_dim: str | None, area: np.ndarray | None
) -> JsonDict:
    """Statistics over every record of a data variable, fill excluded; records are the time dimension."""
    var.set_auto_maskandscale(True)
    per_record = bool(var.dimensions) and time_dim == var.dimensions[0]
    n_records = int(var.shape[0]) if per_record else 1
    total = 0
    valid = 0
    nonzero = 0
    s1 = 0.0
    s2 = 0.0
    vmin = np.inf
    vmax = -np.inf
    sums: list[float] = []
    weighted: list[float] = []
    for r in range(n_records):
        data = np.ma.asarray(var[r] if per_record else var[:], dtype=np.float64)
        total += int(data.size)
        filled = np.asarray(data.filled(np.nan), dtype=np.float64)
        ok = ~np.isnan(filled)
        values = filled[ok]
        valid += int(values.size)
        if values.size:
            nonzero += int(np.count_nonzero(values))
            s1 += float(values.sum())
            s2 += float((values * values).sum())
            vmin = min(vmin, float(values.min()))
            vmax = max(vmax, float(values.max()))
        sums.append(float(values.sum()))
        if area is not None:
            weighted.append(float((np.where(ok, filled, 0.0) * area).sum()))
    mean = s1 / valid if valid else 0.0
    variance = max(s2 / valid - mean * mean, 0.0) if valid else 0.0
    return {
        "min": float(vmin) if valid else 0.0,
        "max": float(vmax) if valid else 0.0,
        "mean": mean,
        "std": float(np.sqrt(variance)),
        "nonzero_fraction": nonzero / valid if valid else 0.0,
        "fill_fraction": (total - valid) / total if total else 0.0,
        "record_sums": sums,
        "area_weighted_sums": weighted if area is not None else None,
    }


def cell_areas(lon: Axis, lat: Axis) -> np.ndarray | None:
    """(nlat, nlon) cell areas in m2 from uniform axes, or None when either axis is not uniform."""
    if not (lon.uniform and lat.uniform and lon.spacing and lat.spacing):
        return None
    dlat, dlon = abs(lat.spacing), abs(lon.spacing)
    south = np.clip(np.radians(lat.values - dlat / 2), -np.pi / 2, np.pi / 2)
    north = np.clip(np.radians(lat.values + dlat / 2), -np.pi / 2, np.pi / 2)
    band = EARTH_RADIUS_M**2 * np.radians(dlon) * (np.sin(north) - np.sin(south))
    return np.broadcast_to(band[:, None], (lat.values.size, lon.values.size)).copy()


def describe_netcdf(path: Path, with_stats: bool, max_bytes: int) -> JsonDict:
    with netCDF4.Dataset(path) as ds:
        axes: dict[str, Axis] = {}
        for name, var in ds.variables.items():
            role = axis_role(name, var)
            if role is not None and role not in axes:
                axes[role] = Axis(name, var)
        time_dim = axes["time"].dimension if "time" in axes else None
        area = (
            cell_areas(axes["lon"], axes["lat"])
            if "lon" in axes and "lat" in axes
            else None
        )
        coordinate_names = {a.name for a in axes.values()}
        variables: dict[str, JsonDict] = {}
        for name, var in ds.variables.items():
            stats: JsonDict | None = None
            reason: str | None = None
            if name not in coordinate_names and var.ndim >= 2:
                nbytes = int(np.prod(var.shape)) * 8
                if not with_stats:
                    reason = "--no-stats"
                elif nbytes > max_bytes:
                    reason = (
                        f"{nbytes} unpacked bytes exceed --stats-max-bytes {max_bytes}"
                    )
                else:
                    fits = area is not None and tuple(var.shape[-2:]) == area.shape
                    stats = value_stats(var, time_dim, area if fits else None)
            variables[name] = {
                "dtype": str(var.dtype),
                "dimensions": list(var.dimensions),
                "shape": [int(s) for s in var.shape],
                "attributes": attributes_of(var),
                "stats": stats,
                "stats_skipped_reason": reason,
            }
        return {
            "model": ds.data_model,
            "reader": "netCDF4",
            "dimensions": {
                n: {"size": len(d), "unlimited": d.isunlimited()}
                for n, d in ds.dimensions.items()
            },
            "variables": variables,
            "global_attributes": attributes_of(ds),
            "axes": {role: axis.as_json() for role, axis in axes.items()},
        }


def parse_point_table(text: str, label: str = "volcano") -> JsonDict:
    """CATChem's `catchem_emis_read_points` rule: rows inside `<label>::` ... `::`, '#' comments, five numbers per row."""
    rows: list[list[float]] = []
    malformed = 0
    in_table = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if not in_table:
            in_table = f"{label}::" in line
            continue
        if line == "::":
            break
        try:
            numbers = [float(p) for p in line.split()[:5]]
        except ValueError:
            numbers = []
        if len(numbers) != 5:
            malformed += 1
            continue
        rows.append(numbers)
    columns = np.asarray(rows, dtype=np.float64).reshape(len(rows), 5)
    ranges = {
        c: (
            [float(columns[:, i].min()), float(columns[:, i].max())]
            if rows
            else [0.0, 0.0]
        )
        for i, c in enumerate(POINT_COLUMNS)
    }
    return {
        "label": label,
        "columns": list(POINT_COLUMNS),
        "units": list(POINT_UNITS),
        "row_count": len(rows),
        "malformed_rows": malformed,
        "ranges": ranges,
        "total_emission": float(columns[:, 2].sum()) if rows else 0.0,
        "rows": rows,
    }


def describe_file(
    rel: str,
    role: str,
    categories: Sequence[str],
    root: Path,
    with_stats: bool,
    max_bytes: int,
    tables_dir: Path | None,
) -> JsonDict:
    path = root / rel.removeprefix("ExtData/")
    out: JsonDict = {
        "path": rel,
        "role": role,
        "categories": sorted(categories),
        "exists": path.exists(),
        "size": None,
        "mtime": None,
        "model": None,
        "reader": None,
        "dimensions": {},
        "variables": {},
        "global_attributes": {},
        "axes": {},
        "point_table": None,
        "error": None,
    }
    if not path.exists():
        out["error"] = f"not found under {root}"
        return out
    stat = path.stat()
    out["size"] = stat.st_size
    out["mtime"] = dt.datetime.fromtimestamp(stat.st_mtime, dt.UTC).isoformat(
        timespec="seconds"
    )
    try:
        if role == "point_table":
            out["reader"] = "text"
            out["point_table"] = parse_point_table(path.read_text(errors="replace"))
            if tables_dir is not None:
                tables_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, tables_dir / path.name)
        else:
            out.update(describe_netcdf(path, with_stats, max_bytes))
    except (OSError, ValueError, RuntimeError) as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def describe(
    catalog_path: Path,
    root: Path,
    with_stats: bool,
    max_bytes: int,
    tables_dir: Path | None,
) -> JsonDict:
    raw = catalog_path.read_bytes()
    catalog = json.loads(raw)
    files = [
        describe_file(
            str(record["path"]),
            str(record["role"]),
            list(record["categories"]),
            root,
            with_stats,
            max_bytes,
            tables_dir,
        )
        for record in catalog["files"]
        if str(record["path"]).startswith("ExtData/")
    ]
    return {
        "generated_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "python": sys.version.split()[0],
        "extdata_root": str(root),
        "catalog_digest": hashlib.sha256(raw).hexdigest(),
        "files": files,
    }


# --- command line ------------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="extdata_catalog", description=__doc__.split("\n\n")[0]
    )
    sub = parser.add_subparsers(dest="command", required=True)
    scan = sub.add_parser(
        "scan-log", help="catalog the files a run opened from its PET log(s)"
    )
    scan.add_argument(
        "logs", nargs="+", type=Path, help="PET log files (PET000.ESMF_LogFile ...)"
    )
    scan.add_argument("--out", required=True, type=Path, help="catalog JSON to write")
    desc = sub.add_parser("describe", help="describe every catalogued ExtData file")
    desc.add_argument(
        "--extdata-root",
        required=True,
        type=Path,
        help="the ExtData directory (.../GOCART/p8/ExtData)",
    )
    desc.add_argument("--catalog", required=True, type=Path, help="the scan-log JSON")
    desc.add_argument("--out", required=True, type=Path, help="headers JSON to write")
    desc.add_argument(
        "--copy-tables",
        type=Path,
        default=None,
        metavar="DIR",
        help="copy every point table verbatim into DIR",
    )
    desc.add_argument("--no-stats", action="store_true", help="skip value statistics")
    desc.add_argument(
        "--stats-max-bytes",
        type=int,
        default=8 << 30,
        help="skip statistics of a variable whose unpacked size exceeds this (default 8 GiB)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "scan-log":
        missing = [str(p) for p in args.logs if not p.exists()]
        if missing:
            sys.stderr.write(f"extdata_catalog: log not found: {', '.join(missing)}\n")
            return 2
        document = scan_logs(args.logs)
    else:
        if not args.extdata_root.is_dir():
            sys.stderr.write(f"extdata_catalog: not a directory: {args.extdata_root}\n")
            return 2
        if not args.catalog.exists():
            sys.stderr.write(f"extdata_catalog: catalog not found: {args.catalog}\n")
            return 2
        document = describe(
            args.catalog,
            args.extdata_root,
            not args.no_stats,
            args.stats_max_bytes,
            args.copy_tables,
        )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(document, indent=1) + "\n")
    count = len(document["files"])  # type: ignore[arg-type]
    sys.stderr.write(f"extdata_catalog: wrote {args.out} ({count} files)\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
