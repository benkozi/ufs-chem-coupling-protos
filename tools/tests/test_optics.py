"""The synthetic GOCART optics tables of the realistic profile (tools/optics.py)."""

from pathlib import Path

import netCDF4
import numpy as np
import pytest

from tools import extdata as xd
from tools import optics
from tools.settings import get_settings

# What GOCART2G_MieMod's reader requires of a table (extern/CATChem .../GOCART2G_MieMod.F90:191-259).
REQUIRED_DIMENSIONS = ("rh", "lambda", "radius")
REQUIRED_VARIABLES = ("lambda", "rEff", "bext", "bsca", "bbck", "g", "rh")
TABLES = (
    "optics_SS.v3_3.nc",
    "optics_DU.v15_3.nc",
    "optics_BC.v1_3.nc",
    "optics_OC.v1_3.nc",
    "optics_NI.v2_5.nc",
    "optics_SU.v1_3.nc",
)


@pytest.fixture(scope="module")
def headers() -> xd.ExtDataHeaders:
    return optics.load_headers()


def _header(headers: xd.ExtDataHeaders, name: str) -> xd.FileHeader:
    return headers.by_path()[f"ExtData/monochromatic/{name}"]


def test_every_table_of_the_scrape_is_written_at_its_catalog_path(
    tmp_path: Path, headers: xd.ExtDataHeaders
) -> None:
    written = optics.write_optics_tables(headers, tmp_path)
    assert sorted(p.relative_to(tmp_path).as_posix() for p in written) == sorted(
        f"ExtData/monochromatic/{name}" for name in TABLES
    )
    # megabytes, not the real files' hundreds: the phase-function moments are left out
    assert sum(p.stat().st_size for p in written) < 8_000_000


def test_a_table_has_what_the_gocart_reader_requires(
    tmp_path: Path, headers: xd.ExtDataHeaders
) -> None:
    for path in optics.write_optics_tables(headers, tmp_path):
        header = _header(headers, path.name)
        with netCDF4.Dataset(path) as nc:
            assert nc.data_model == header.model
            for name in REQUIRED_DIMENSIONS:
                assert len(nc.dimensions[name]) == header.dimensions[name]["size"]
            # the moments' dimension shrinks to one entry; the polarisation dimension keeps its size
            assert len(nc.dimensions["nMom"]) == 1
            assert len(nc.dimensions["nPol"]) == header.dimensions["nPol"]["size"]
            for name in REQUIRED_VARIABLES:
                variable = nc.variables[name]
                assert variable.dtype == np.dtype(header.variables[name].dtype)
                assert list(variable.dimensions) == header.variables[name].dimensions
                assert variable.units == header.variables[name].attributes["units"]
                assert np.all(np.isfinite(variable[:]))
            assert "pmom" not in nc.variables
            assert "pback" not in nc.variables
            assert "synthetic" in nc.source


def test_values_are_physical_and_inside_the_scraped_ranges(
    tmp_path: Path, headers: xd.ExtDataHeaders
) -> None:
    for path in optics.write_optics_tables(headers, tmp_path):
        header = _header(headers, path.name)
        with netCDF4.Dataset(path) as nc:
            rh = np.asarray(nc.variables["rh"][:], dtype=np.float64)
            assert rh[0] == 0.0
            assert rh[-1] < 1.0
            assert np.all(np.diff(rh) > 0)
            assert np.all(np.diff(nc.variables["lambda"][:]) > 0)
            # GOCART finds a channel by exact match within 1e-9 m, and the settling scheme asks for 550 nm
            wavelengths = np.asarray(nc.variables["lambda"][:], dtype=np.float64)
            assert np.abs(wavelengths - 550.0e-9).min() < 1.0e-12
            reff = np.asarray(nc.variables["rEff"][:], dtype=np.float64)
            stats = header.variables["rEff"].stats
            assert stats is not None
            assert reff.min() >= stats.min * (1 - 1e-6)
            assert reff.max() <= stats.max * (1 + 1e-6)
            # larger bins are larger, and a particle grows with humidity
            assert np.all(np.diff(reff, axis=0) > 0)
            assert np.all(np.diff(reff, axis=1) > 0)
            assert np.all(nc.variables["rLow"][:] < nc.variables["radius"][:])
            assert np.all(nc.variables["rUp"][:] > nc.variables["radius"][:])
            assert np.all(nc.variables["bsca"][:] <= nc.variables["bext"][:])
            if "rhop" in nc.variables:
                rhop = np.asarray(nc.variables["rhop"][:], dtype=np.float64)
                rhop_stats = header.variables["rhop"].stats
                assert rhop_stats is not None
                assert rhop.max() == pytest.approx(rhop_stats.max)
                assert rhop.min() >= 1000.0
                assert np.all(np.diff(rhop, axis=1) < 0)
                assert np.all(nc.variables["growth_factor"][:, 0] == 1.0)


def test_the_tables_are_reproducible(
    tmp_path: Path, headers: xd.ExtDataHeaders
) -> None:
    first = optics.write_optics_tables(headers, tmp_path / "a")
    second = optics.write_optics_tables(headers, tmp_path / "b")
    for one, two in zip(first, second, strict=True):
        assert one.read_bytes() == two.read_bytes()


def test_a_variable_without_statistics_is_refused_by_name(
    tmp_path: Path, headers: xd.ExtDataHeaders
) -> None:
    header = _header(headers, "optics_SU.v1_3.nc")
    variables = dict(header.variables)
    variables["bext"] = variables["bext"].model_copy(update={"stats": None})
    broken = headers.model_copy(
        update={"files": [header.model_copy(update={"variables": variables})]}
    )
    with pytest.raises(
        ValueError, match=r"optics_SU\.v1_3\.nc: no statistics for bext"
    ):
        optics.write_optics_tables(broken, tmp_path)


def test_a_table_without_a_data_model_is_refused(
    tmp_path: Path, headers: xd.ExtDataHeaders
) -> None:
    header = _header(headers, "optics_SU.v1_3.nc").model_copy(update={"model": None})
    with pytest.raises(ValueError, match="no NetCDF data model"):
        optics.write_table(header, tmp_path / "x.nc")


def test_a_scrape_without_optics_tables_is_refused(
    tmp_path: Path, headers: xd.ExtDataHeaders
) -> None:
    empty = headers.model_copy(
        update={"files": [f for f in headers.files if f.role != "optics"]}
    )
    with pytest.raises(ValueError, match="names no optics table"):
        optics.write_optics_tables(empty, tmp_path)


def test_load_headers_reads_the_settings_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PROTOS_EXTDATA_DEFAULTS_DIR", str(tmp_path))
    get_settings.cache_clear()
    with pytest.raises(FileNotFoundError):
        optics.load_headers()
