"""The per-run window file of a daily inventory series (issue #18).

CECE's cap path reads one file per stream (`cece_driver_facade.cpp` BuildStreamConfig refuses a list, the AMIO
manifest has a single path, every cache is keyed per file) and has no date tokens yet (ufs-community/CECE#165),
while CATChem's daily inventories (CEDS, QFED, MEGAN) are one file per day. The generator therefore joins the
per-day files of the run window (the clock's days plus its padding days, so CECE's linear blend is bracketed at
both ends) into one NetCDF under `<out>/streams/`, record for record: same variable names, dtype, fill value and
units, the lon/lat coordinates of the first file, a monotone `time` axis in hours since the first day's midnight
and the source files in a global attribute. The real daily files stay where the mock step wrote them. The window
goes away once CECE reads a dated series itself.
"""

from __future__ import annotations

import itertools
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import netCDF4
import numpy as np
import xarray as xr
from pydantic import AwareDatetime, Field

from tools import log
from tools import manifest as mf
from tools.inventories import XARRAY_FORMATS
from tools.manifest import DATE_TOKENS, Strict

logger = log.get_logger("stream_window")

_TOKEN_RUN = re.compile(
    r"[._-]?(?:" + "|".join(re.escape(t) for t in DATE_TOKENS) + r")+[._-]?"
)
TIME_NAME = "time"


class WindowError(ValueError):
    """The per-day files cannot be joined (missing, out of order, disagreeing), named."""


class WindowRecord(Strict):
    path: Path = Field(description="The window file written.")
    files: list[Path] = Field(description="The per-day files joined, in record order.")
    stamps: list[AwareDatetime] = Field(
        description="The record times of the window (UTC)."
    )
    variables: list[str] = Field(
        description="The data variables carried, in file order."
    )


def window_name(template: Path) -> str:
    """The window file's name: the template's file name with its date tokens (and the separators around them) dropped."""
    return _TOKEN_RUN.sub(".", template.name).replace("..", ".")


def _open(path: Path) -> xr.Dataset:
    if not path.is_file():
        raise WindowError(f"daily file {path} does not exist")
    return xr.open_dataset(path, decode_times=True)


def _stamp(value: object) -> datetime:
    stamp = cast(datetime, value)
    return stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp.astimezone(UTC)


def write_window(files: list[Path], out: Path) -> WindowRecord:
    """Join the per-day files along `time` into `out` and return what was written."""
    if not files:
        raise WindowError("a window needs at least one daily file")
    datasets = [_open(f) for f in files]
    try:
        first = datasets[0]
        if TIME_NAME not in first.dims:
            raise WindowError(f"{files[0]} has no {TIME_NAME!r} dimension")
        data_vars = [str(v) for v in first.data_vars if TIME_NAME in first[v].dims]
        for path, ds in zip(files[1:], datasets[1:], strict=True):
            if [str(v) for v in ds.data_vars if TIME_NAME in ds[v].dims] != data_vars:
                raise WindowError(f"{path} does not carry the variables of {files[0]}")
        joined = xr.concat(
            datasets,
            dim=TIME_NAME,
            data_vars="minimal",
            coords="minimal",
            compat="override",
        )
        stamps = [
            _stamp(s)
            for s in joined[TIME_NAME].values.astype("datetime64[us]").tolist()
        ]
        if any(b <= a for a, b in itertools.pairwise(stamps)):
            raise WindowError(
                f"the records of {[str(f) for f in files]} are not in ascending time order"
            )
        day0 = datetime(stamps[0].year, stamps[0].month, stamps[0].day, tzinfo=UTC)
        hours = [(s - day0) / timedelta(hours=1) for s in stamps]
        joined = joined.assign_coords(
            {
                TIME_NAME: (
                    TIME_NAME,
                    np.array(hours, dtype=np.float64),
                    first[TIME_NAME].attrs,
                )
            }
        )
        joined[TIME_NAME].attrs = {
            k: v
            for k, v in first[TIME_NAME].attrs.items()
            if k not in ("units", "calendar", "bounds")
        } | {"units": f"hours since {day0:%Y-%m-%d %H:%M:%S}", "calendar": "standard"}
        encoding: dict[str, dict[str, object]] = {
            str(c): {"_FillValue": None} for c in joined.coords
        }
        encoding[TIME_NAME] = {"_FillValue": None, "dtype": "float64"}
        for name in data_vars:
            source = first[name].encoding
            encoding[name] = {
                "dtype": source.get("dtype", first[name].dtype),
                "_FillValue": source.get("_FillValue"),
            }
            for key in ("zlib", "complevel", "shuffle"):
                if key in source:
                    encoding[name][key] = source[key]
        joined.attrs = dict(first.attrs) | {
            "title": f"run window of {', '.join(data_vars)}: {len(files)} daily files joined by tools.gen_configs",
            "source_files": "\n".join(str(f) for f in files),
        }
        out.parent.mkdir(parents=True, exist_ok=True)
        with netCDF4.Dataset(files[0]) as nc:
            model = cast(mf.NetcdfModel, nc.data_model)
        joined.to_netcdf(out, format=XARRAY_FORMATS[model], encoding=encoding)
    finally:
        for ds in datasets:
            ds.close()
    logger.info("wrote %s (%d records from %d files)", out, len(stamps), len(files))
    return WindowRecord(path=out, files=list(files), stamps=stamps, variables=data_vars)
