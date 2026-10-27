"""Fetching and validating the FV3 fix files (mosaic, supergrid tiles, grid_spec tiles)."""

import io
import math
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Self

import netCDF4
import numpy as np
import pytest

from tools import fixfiles
from tools import manifest as mf

N48 = 48
RADIUS = mf.EARTH_RADIUS_M


# --- synthetic files ---------------------------------------------------------------------------------------


def write_fake_grid(
    path: Path,
    n: int,
    *,
    drop_tile_spec: bool = False,
    projection: str = "cube_gnomonic",
    bad_shape: bool = False,
) -> None:
    m = 2 * n + 1
    with netCDF4.Dataset(path, "w") as ds:
        ds.createDimension("string", 255)
        ds.createDimension("nxp", m)
        ds.createDimension("nyp", m if not bad_shape else m - 1)
        if not drop_tile_spec:
            tile = ds.createVariable("tile", "c", ("string",))
            tile.standard_name = "grid_tile_spec"
            tile.projection = projection
        lon = np.linspace(-45.0, 45.0, m)
        x = ds.createVariable("x", "f8", ("nyp", "nxp"))
        x.standard_name = "geographic_longitude"
        x.units = "degree_east"
        x[:] = np.broadcast_to(lon, x.shape)
        y = ds.createVariable("y", "f8", ("nyp", "nxp"))
        y.standard_name = "geographic_latitude"
        y.units = "degree_north"
        y[:] = np.broadcast_to(np.linspace(-45.0, 45.0, y.shape[0])[:, None], y.shape)


def write_fake_grid_spec(
    path: Path, n: int, *, area_scale: float = 1.0, drop: str | None = None
) -> None:
    with netCDF4.Dataset(path, "w") as ds:
        ds.createDimension("grid_x", n + 1)
        ds.createDimension("grid_y", n + 1)
        ds.createDimension("grid_xt", n)
        ds.createDimension("grid_yt", n)
        for name, dims in (
            ("grid_lon", ("grid_y", "grid_x")),
            ("grid_lat", ("grid_y", "grid_x")),
            ("grid_lont", ("grid_yt", "grid_xt")),
            ("grid_latt", ("grid_yt", "grid_xt")),
        ):
            if name == drop:
                continue
            v = ds.createVariable(name, "f4", dims)
            v[:] = 1.0
        if drop != "area":
            area = ds.createVariable("area", "f4", ("grid_yt", "grid_xt"))
            area[:] = area_scale * (4 * math.pi * RADIUS**2 / 6) / (n * n)


def write_fake_mosaic(
    path: Path, resolution: str, *, ntiles: int = 6, names: list[str] | None = None
) -> None:
    names = names or [f"{resolution}_grid.tile{t}.nc" for t in range(1, ntiles + 1)]
    with netCDF4.Dataset(path, "w") as ds:
        ds.createDimension("ntiles", ntiles)
        ds.createDimension("ncontact", 12)
        ds.createDimension("string", 255)
        files = ds.createVariable("gridfiles", "c", ("ntiles", "string"))
        for i, name in enumerate(names):
            files[i, : len(name)] = netCDF4.stringtochar(
                np.array([name], dtype=f"S{len(name)}")
            )[0]
        contacts = ds.createVariable("contacts", "c", ("ncontact", "string"))
        contacts[:] = " "


def write_fake_set(fix_dir: Path, resolution: str, n: int) -> mf.FixFiles:
    fix_dir.mkdir(parents=True, exist_ok=True)
    fix = fixfiles.fix_files_for(resolution, fix_dir)
    write_fake_mosaic(fix.mosaic, resolution)
    for p in fix.grid_tiles:
        write_fake_grid(p, n)
    for p in fix.grid_spec_tiles:
        write_fake_grid_spec(p, n)
    return fix


# --- a fake urlopen ----------------------------------------------------------------------------------------


class FakeResponse(io.BytesIO):
    def __init__(self, payload: bytes, content_length: int | None) -> None:
        super().__init__(payload)
        self.headers = (
            {} if content_length is None else {"Content-Length": str(content_length)}
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def serving(
    files: dict[str, bytes],
    *,
    truncate: frozenset[str] = frozenset(),
    fail: frozenset[str] = frozenset(),
) -> tuple[Callable[..., FakeResponse], list[str]]:
    calls: list[str] = []

    def opener(url: str, timeout: float = 0) -> FakeResponse:
        del timeout
        calls.append(url)
        name = url.rsplit("/", 1)[1]
        if name in fail:
            raise urllib.error.HTTPError(url, 404, "Not Found", None, None)  # type: ignore[arg-type]
        payload = files[name]
        if name in truncate:
            return FakeResponse(payload[: len(payload) // 2], len(payload))
        return FakeResponse(payload, len(payload))

    return opener, calls


# --- validators on the real fixture ------------------------------------------------------------------------


def test_real_c48_grid_tile_validates(c48_fixture_dir: Path) -> None:
    info = fixfiles.validate_grid(c48_fixture_dir / "C48_grid.tile1.nc", N48)
    assert info.shape == (97, 97)


def test_real_c48_grid_spec_validates(c48_fixture_dir: Path) -> None:
    info = fixfiles.validate_grid_spec(
        c48_fixture_dir / "C48_grid_spec.tile1.nc", N48, RADIUS
    )
    assert info.shape == (48, 48)
    assert abs(info.area_total / (4 * math.pi * RADIUS**2 / 6) - 1) < 0.01


def test_real_c48_mosaic_validates(c48_fixture_dir: Path) -> None:
    names = fixfiles.validate_mosaic(c48_fixture_dir / "C48_mosaic.nc", "C48")
    assert names == [f"C48_grid.tile{t}.nc" for t in range(1, 7)]


# --- validators on synthetic bad files --------------------------------------------------------------------


def test_grid_without_tile_spec_fails(tmp_path: Path) -> None:
    write_fake_grid(tmp_path / "g.nc", 4, drop_tile_spec=True)
    with pytest.raises(fixfiles.FixFileError, match="grid_tile_spec"):
        fixfiles.validate_grid(tmp_path / "g.nc", 4)


def test_grid_with_wrong_projection_fails(tmp_path: Path) -> None:
    write_fake_grid(tmp_path / "g.nc", 4, projection="mercator")
    with pytest.raises(fixfiles.FixFileError, match="cube_gnomonic"):
        fixfiles.validate_grid(tmp_path / "g.nc", 4)


def test_grid_with_wrong_supergrid_shape_fails(tmp_path: Path) -> None:
    write_fake_grid(tmp_path / "g.nc", 4, bad_shape=True)
    with pytest.raises(fixfiles.FixFileError, match=r"\(9, 9\)"):
        fixfiles.validate_grid(tmp_path / "g.nc", 4)
    write_fake_grid(tmp_path / "h.nc", 5)
    with pytest.raises(fixfiles.FixFileError, match=r"\(9, 9\)"):
        fixfiles.validate_grid(tmp_path / "h.nc", 4)


def test_grid_with_out_of_range_coordinates_fails(tmp_path: Path) -> None:
    write_fake_grid(tmp_path / "g.nc", 4)
    with netCDF4.Dataset(tmp_path / "g.nc", "a") as ds:
        ds["y"][0, 0] = 95.0
    with pytest.raises(fixfiles.FixFileError, match="latitude"):
        fixfiles.validate_grid(tmp_path / "g.nc", 4)


def test_grid_spec_missing_variable_fails(tmp_path: Path) -> None:
    for missing in ("grid_lont", "area", "grid_lon"):
        write_fake_grid_spec(tmp_path / "s.nc", 4, drop=missing)
        with pytest.raises(fixfiles.FixFileError, match=missing):
            fixfiles.validate_grid_spec(tmp_path / "s.nc", 4, RADIUS)


def test_grid_spec_wrong_size_fails(tmp_path: Path) -> None:
    write_fake_grid_spec(tmp_path / "s.nc", 5)
    with pytest.raises(fixfiles.FixFileError, match=r"\(4, 4\)"):
        fixfiles.validate_grid_spec(tmp_path / "s.nc", 4, RADIUS)


def test_grid_spec_area_total_is_checked(tmp_path: Path) -> None:
    write_fake_grid_spec(tmp_path / "s.nc", 4, area_scale=1.05)
    with pytest.raises(fixfiles.FixFileError, match="area"):
        fixfiles.validate_grid_spec(tmp_path / "s.nc", 4, RADIUS)


def test_mosaic_with_wrong_tile_count_or_names_fails(tmp_path: Path) -> None:
    write_fake_mosaic(tmp_path / "m.nc", "C48", ntiles=5)
    with pytest.raises(fixfiles.FixFileError, match="ntiles"):
        fixfiles.validate_mosaic(tmp_path / "m.nc", "C48")
    write_fake_mosaic(
        tmp_path / "m2.nc", "C48", names=[f"C96_grid.tile{t}.nc" for t in range(1, 7)]
    )
    with pytest.raises(fixfiles.FixFileError, match="gridfiles"):
        fixfiles.validate_mosaic(tmp_path / "m2.nc", "C48")


def test_unreadable_file_is_a_fix_file_error(tmp_path: Path) -> None:
    (tmp_path / "junk.nc").write_bytes(b"not netcdf")
    with pytest.raises(fixfiles.FixFileError, match=r"junk\.nc"):
        fixfiles.validate_grid(tmp_path / "junk.nc", 4)


def test_validate_set_returns_sizes_and_catches_a_missing_tile(tmp_path: Path) -> None:
    fix = write_fake_set(tmp_path / "fix", "C48", 4)
    sizes = fixfiles.validate_set(fix, 4, "C48", RADIUS)
    assert set(sizes) == set(fixfiles.expected_names("C48"))
    assert all(size > 0 for size in sizes.values())
    fix.grid_tiles[3].unlink()
    with pytest.raises(fixfiles.FixFileError, match="tile4"):
        fixfiles.validate_set(fix, 4, "C48", RADIUS)


# --- fetch -------------------------------------------------------------------------------------------------


def fake_payloads(tmp_path: Path, n: int = 4) -> dict[str, bytes]:
    src = write_fake_set(tmp_path / "server", "C48", n)
    return {
        p.name: p.read_bytes()
        for p in [src.mosaic, *src.grid_tiles, *src.grid_spec_tiles]
    }


def test_fetch_downloads_validates_and_then_skips(tmp_path: Path) -> None:
    payloads = fake_payloads(tmp_path)
    opener, calls = serving(payloads)
    fix = fixfiles.fix_files_for("C48", tmp_path / "fix")
    sizes = fixfiles.fetch(fix, resolution="C48", n=4, radius_m=RADIUS, opener=opener)
    assert len(calls) == 13
    assert sizes == {name: len(data) for name, data in payloads.items()}
    assert not list((tmp_path / "fix").glob("*.part"))
    opener2, calls2 = serving(payloads)
    fixfiles.fetch(fix, resolution="C48", n=4, radius_m=RADIUS, opener=opener2)
    assert calls2 == []


def test_fetch_redownloads_a_present_but_invalid_file(tmp_path: Path) -> None:
    payloads = fake_payloads(tmp_path)
    fix = fixfiles.fix_files_for("C48", tmp_path / "fix")
    fix.directory.mkdir()
    fix.mosaic.write_bytes(b"garbage")
    opener, calls = serving(payloads)
    fixfiles.fetch(fix, resolution="C48", n=4, radius_m=RADIUS, opener=opener)
    assert len(calls) == 13
    assert fixfiles.validate_mosaic(fix.mosaic, "C48")


def test_truncated_download_leaves_nothing_behind(tmp_path: Path) -> None:
    payloads = fake_payloads(tmp_path)
    opener, _ = serving(payloads, truncate=frozenset({"C48_grid.tile2.nc"}))
    fix = fixfiles.fix_files_for("C48", tmp_path / "fix")
    with pytest.raises(fixfiles.FixFileError, match=r"tile2.*bytes"):
        fixfiles.fetch(fix, resolution="C48", n=4, radius_m=RADIUS, opener=opener)
    assert not (fix.directory / "C48_grid.tile2.nc").exists()
    assert not list(fix.directory.glob("*.part"))


def test_http_error_is_a_fix_file_error(tmp_path: Path) -> None:
    payloads = fake_payloads(tmp_path)
    opener, _ = serving(payloads, fail=frozenset({"C48_grid_spec.tile6.nc"}))
    fix = fixfiles.fix_files_for("C48", tmp_path / "fix")
    with pytest.raises(fixfiles.FixFileError, match="404"):
        fixfiles.fetch(fix, resolution="C48", n=4, radius_m=RADIUS, opener=opener)


def test_download_without_content_length_is_accepted(tmp_path: Path) -> None:
    payload = fake_payloads(tmp_path)["C48_mosaic.nc"]
    calls: list[str] = []

    def opener(url: str, timeout: float = 0) -> FakeResponse:
        del timeout
        calls.append(url)
        return FakeResponse(payload, None)

    dest = tmp_path / "out" / "C48_mosaic.nc"
    assert fixfiles.download("http://x/C48_mosaic.nc", dest, opener) == len(payload)
    assert dest.read_bytes() == payload


def test_fetch_uses_urlopen_by_default(tmp_path: Path) -> None:
    fix = fixfiles.fix_files_for("C48", tmp_path / "fix")
    with pytest.raises(AssertionError, match="network access attempted"):
        fixfiles.fetch(fix, resolution="C48", n=4, radius_m=RADIUS)


def test_expected_names_and_urls() -> None:
    names = fixfiles.expected_names("C96")
    assert names[0] == "C96_mosaic.nc"
    assert names[1:7] == [f"C96_grid.tile{t}.nc" for t in range(1, 7)]
    assert names[7:] == [f"C96_grid_spec.tile{t}.nc" for t in range(1, 7)]
    fix = fixfiles.fix_files_for("C96", Path("d"))
    assert (
        fixfiles.url_for(fix, "C96_mosaic.nc")
        == mf.EMC_FIX_URL.format(resolution="C96") + "C96_mosaic.nc"
    )


def test_grid_with_out_of_range_longitude_fails(tmp_path: Path) -> None:
    write_fake_grid(tmp_path / "g.nc", 4)
    with netCDF4.Dataset(tmp_path / "g.nc", "a") as ds:
        ds["x"][0, 0] = 400.0
    with pytest.raises(fixfiles.FixFileError, match="longitude"):
        fixfiles.validate_grid(tmp_path / "g.nc", 4)


def test_grid_spec_with_non_positive_area_fails(tmp_path: Path) -> None:
    write_fake_grid_spec(tmp_path / "s.nc", 4)
    with netCDF4.Dataset(tmp_path / "s.nc", "a") as ds:
        ds["area"][0, 0] = 0.0
    with pytest.raises(fixfiles.FixFileError, match="non-positive"):
        fixfiles.validate_grid_spec(tmp_path / "s.nc", 4, RADIUS)


def test_mosaic_without_gridfiles_or_contacts_fails(tmp_path: Path) -> None:
    with netCDF4.Dataset(tmp_path / "m.nc", "w") as ds:
        ds.createDimension("ntiles", 6)
    with pytest.raises(fixfiles.FixFileError, match="gridfiles"):
        fixfiles.validate_mosaic(tmp_path / "m.nc", "C48")
    write_fake_mosaic(tmp_path / "m2.nc", "C48")
    with (
        netCDF4.Dataset(tmp_path / "m3.nc", "w") as ds,
        netCDF4.Dataset(tmp_path / "m2.nc") as src,
    ):
        ds.createDimension("ntiles", 6)
        ds.createDimension("string", 255)
        ds.createDimension("ncontact", 11)
        files = ds.createVariable("gridfiles", "c", ("ntiles", "string"))
        files[:] = src["gridfiles"][:]
    with pytest.raises(fixfiles.FixFileError, match="ncontact"):
        fixfiles.validate_mosaic(tmp_path / "m3.nc", "C48")
