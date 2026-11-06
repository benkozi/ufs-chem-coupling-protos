"""Grids, the analytic land mask, the analytic inventories and their NetCDF files."""

import math
from datetime import UTC, datetime
from pathlib import Path

import netCDF4
import numpy as np
import pytest
import xarray as xr

from tools import inventories as inv
from tools import manifest as mf
from tools.settings import get_settings

R = get_settings().earth_radius_m


def small_manifest(
    out_dir: Path, *, n_levels: int = 5, resolution_deg: float = 10.0
) -> mf.Manifest:
    """The default manifest shrunk to a 10° grid and five levels so every test runs in milliseconds."""
    m = mf.default_manifest(
        resolution="C48",
        out_dir=out_dir,
        command="test",
        n_levels=n_levels,
        inventory_resolution_deg=resolution_deg,
    )
    coarse_gmi = mf.LatLonGrid(
        dlon=10.0, dlat=10.0, lon_first=-175.0, lat_first=-85.0, nlon=36, nlat=18
    )
    return m.model_copy(update={"grids": {**m.grids, "gmi": coarse_gmi}})


@pytest.fixture
def manifest(tmp_path: Path) -> mf.Manifest:
    return small_manifest(tmp_path)


# --- grid --------------------------------------------------------------------------------------------------


def test_grid_centres_and_bounds() -> None:
    g = mf.LatLonGrid(
        dlon=1.0, dlat=1.0, lon_first=-179.5, lat_first=-89.5, nlon=360, nlat=180
    )
    lon, lat = inv.lon_centres(g), inv.lat_centres(g)
    assert lon[0] == -179.5
    assert lon[-1] == 179.5
    assert lat[0] == -89.5
    assert lat[-1] == 89.5
    lon_b, lat_b = inv.lon_bounds(g), inv.lat_bounds(g)
    assert lon_b.shape == (360, 2)
    assert lat_b.shape == (180, 2)
    assert lon_b[0].tolist() == [-180.0, -179.0]
    assert lat_b[-1].tolist() == [89.0, 90.0]


def test_cell_area_sums_to_the_sphere_and_is_symmetric() -> None:
    g = mf.LatLonGrid(
        dlon=2.0, dlat=2.0, lon_first=-179.0, lat_first=-89.0, nlon=180, nlat=90
    )
    area = inv.cell_area(g, R)
    assert area.shape == (90, 180)
    assert math.isclose(float(area.sum()), 4 * math.pi * R**2, rel_tol=1e-9)
    assert np.allclose(area, area[::-1, :])
    assert np.allclose(area, area[:, ::1][:, :1])  # zonally constant
    assert area[45, 0] > area[0, 0]


# --- land mask ---------------------------------------------------------------------------------------------


def test_land_fraction_is_bounded_and_peaks_on_the_continents(
    manifest: mf.Manifest,
) -> None:
    g = manifest.grids["inventory"]
    f = inv.land_fraction(g, manifest.land_mask)
    assert f.shape == (g.nlat, g.nlon)
    assert f.min() >= 0.0
    assert f.max() <= 1.0
    c = manifest.land_mask.continents[0]
    i, j = inv.nearest_cell(g, c.lon, c.lat)
    assert f[i, j] > 0.99
    i, j = inv.nearest_cell(
        g, -150.0, -60.0
    )  # open Southern Pacific in the default mask
    assert f[i, j] < 0.01


def test_land_mask_file(manifest: mf.Manifest) -> None:
    path = inv.write_land_mask(manifest)
    assert path == manifest.land_mask.file
    with xr.open_dataset(path) as ds:
        assert ds["land_fraction"].dims == ("lat", "lon")
        assert ds["land_mask"].dtype == np.int8
        assert set(np.unique(ds["land_mask"].values)) <= {0, 1}
        assert bool(
            ((ds["land_fraction"].values >= 0.5) == (ds["land_mask"].values == 1)).all()
        )
        assert ds["lon"].attrs["bounds"] == "lon_bnds"
        assert "area" in ds


def test_a_continent_can_be_rotated_and_stretched() -> None:
    g = mf.LatLonGrid(
        dlon=1.0, dlat=1.0, lon_first=-179.5, lat_first=-89.5, nlon=360, nlat=180
    )
    narrow = mf.LandMask(
        grid="inventory",
        file=Path("x.nc"),
        continents=[
            mf.Continent(lon=0.0, lat=0.0, semi_axis_lon=30.0, semi_axis_lat=5.0)
        ],
    )
    rotated = narrow.model_copy(
        update={
            "continents": [
                narrow.continents[0].model_copy(update={"rotation_deg": 90.0})
            ]
        }
    )
    f0, f90 = inv.land_fraction(g, narrow), inv.land_fraction(g, rotated)
    i, j = inv.nearest_cell(g, 25.0, 0.0)
    assert f0[i, j] > 0.5 > f90[i, j]
    i, j = inv.nearest_cell(g, 0.0, 25.0)
    assert f90[i, j] > 0.5 > f0[i, j]


# --- time axes ---------------------------------------------------------------------------------------------


def test_hourly_axis_has_25_centred_records(manifest: mf.Manifest) -> None:
    axis = inv.time_axis("hourly", manifest.clock)
    assert axis.values == [float(h) for h in range(25)]
    assert axis.bounds[0] == (-0.5, 0.5)
    assert axis.bounds[-1] == (23.5, 24.5)
    assert axis.units == "hours since 2021-03-22 06:00:00"
    assert axis.calendar == "standard"
    assert axis.stamps[-1] == manifest.clock.end


def test_monthly_axis_covers_the_start_year(manifest: mf.Manifest) -> None:
    axis = inv.time_axis("monthly", manifest.clock)
    assert len(axis.values) == 12
    assert axis.units == "days since 2021-01-01 00:00:00"
    assert axis.bounds[0] == (0.0, 31.0)
    assert axis.bounds[1] == (31.0, 59.0)
    assert axis.values[0] == 15.5
    assert axis.bounds[-1][1] == 365.0
    assert [s.month for s in axis.stamps] == list(range(1, 13))


def test_static_axis_is_one_record_at_start(manifest: mf.Manifest) -> None:
    axis = inv.time_axis("static", manifest.clock)
    assert axis.values == [0.0]
    assert axis.bounds == [(0.0, 24.0)]
    assert axis.stamps == [manifest.clock.start]


# --- fields ------------------------------------------------------------------------------------------------


def unmasked_variable(**kwargs: object) -> mf.Variable:
    base = {
        "units": "kg m-2 s-1",
        "long_name": "test",
        "background": 0.0,
        "sources": [mf.GaussianSource(lon=0.0, lat=0.0, sigma_deg=5.0, amplitude=1.0)],
    }
    return mf.Variable.model_validate(base | kwargs)


def test_single_gaussian_total_matches_the_analytic_integral(
    manifest: mf.Manifest,
) -> None:
    g = mf.LatLonGrid(
        dlon=1.0, dlat=1.0, lon_first=-179.5, lat_first=-89.5, nlon=360, nlat=180
    )
    category = manifest.categories["dust"].model_copy(
        update={
            "grid": "inventory",
            "masked_by": "none",
            "variables": {"v": unmasked_variable()},
        }
    )
    axis = inv.time_axis("static", manifest.clock)
    values = inv.field_values(
        unmasked_variable(), category, g, manifest.clock, None, axis
    )
    assert values.shape == (1, 180, 360)
    assert values.dtype == np.float32
    sigma = math.radians(5.0)
    analytic = 2 * math.pi * sigma**2 * R**2 * math.exp(-(sigma**2) / 2)
    numeric = float((values[0].astype(float) * inv.cell_area(g, R)).sum())
    assert abs(numeric / analytic - 1) < 0.005


def test_diurnal_cycle_peaks_at_the_local_hour(manifest: mf.Manifest) -> None:
    g = manifest.grids["inventory"]
    var = unmasked_variable(
        background=1.0,
        sources=[],
        diurnal=mf.Diurnal(relative_amplitude=0.5, peak_local_hour=14.0),
    )
    category = manifest.categories["anthro"].model_copy(update={"masked_by": "none"})
    axis = inv.time_axis("hourly", manifest.clock)
    values = inv.field_values(var, category, g, manifest.clock, None, axis)
    assert values.shape == (25, g.nlat, g.nlon)

    # Record 8 is 14:00 UTC. Local solar time at a cell is 14:00 + lon/15 h; the factor is 1 + 0.5 cos(2 pi (local - 14) / 24).
    def expected(utc_hour: float, lon: float) -> float:
        return 1.0 + 0.5 * math.cos(
            2 * math.pi * (((utc_hour + lon / 15.0) % 24.0) - 14.0) / 24.0
        )

    i, j0 = inv.nearest_cell(g, 0.0, 0.0)
    _, j180 = inv.nearest_cell(g, 180.0, 0.0)
    lon0, lon180 = float(inv.lon_centres(g)[j0]), float(inv.lon_centres(g)[j180])
    assert values[8, i, j0] == pytest.approx(expected(14.0, lon0), abs=1e-5)
    assert values[8, i, j180] == pytest.approx(expected(14.0, lon180), abs=1e-5)
    assert values[0, i, j0] == pytest.approx(expected(6.0, lon0), abs=1e-5)
    assert (
        values[8].max() > 1.49 > values[8].min() + 0.9
    )  # both the maximum and the minimum of the cycle are on the grid


def test_seasonal_cycle_follows_the_record_month(manifest: mf.Manifest) -> None:
    g = manifest.grids["inventory"]
    var = unmasked_variable(
        background=1.0,
        sources=[],
        seasonal=mf.Seasonal(relative_amplitude=0.3, peak_month=7),
    )
    category = manifest.categories["dms"].model_copy(update={"masked_by": "none"})
    axis = inv.time_axis("monthly", manifest.clock)
    values = inv.field_values(var, category, g, manifest.clock, None, axis)
    assert values.shape == (12, g.nlat, g.nlon)
    assert values[6].max() == pytest.approx(1.3, abs=1e-5)
    assert values[0].max() == pytest.approx(0.7, abs=1e-5)


def test_masking_by_land_and_ocean(manifest: mf.Manifest) -> None:
    g = manifest.grids["inventory"]
    land = inv.land_fraction(g, manifest.land_mask)
    var = unmasked_variable(background=1.0, sources=[])
    axis = inv.time_axis("static", manifest.clock)
    on_land = inv.field_values(
        var, manifest.categories["dust"], g, manifest.clock, land, axis
    )[0]
    on_ocean = inv.field_values(
        var,
        manifest.categories["dms"].model_copy(update={"cadence": "static"}),
        g,
        manifest.clock,
        land,
        axis,
    )[0]
    assert np.allclose(on_land + on_ocean, 1.0, atol=1e-6)
    assert np.allclose(on_land, land.astype(np.float32), atol=1e-6)


def test_three_d_profile_and_level_order(manifest: mf.Manifest) -> None:
    g = manifest.grids["gmi"]
    var = unmasked_variable(
        background=1.0, sources=[], vertical=mf.Vertical(scale_height_levels=2.0)
    )
    bottom_up = manifest.categories["gmi"].model_copy(update={"cadence": "static"})
    axis = inv.time_axis("static", manifest.clock)
    values = inv.field_values(var, bottom_up, g, manifest.clock, None, axis)
    assert values.shape == (1, 5, g.nlat, g.nlon)
    assert values[0, 1, 0, 0] / values[0, 0, 0, 0] == pytest.approx(
        math.exp(-0.5), rel=1e-5
    )
    top_down = bottom_up.model_copy(update={"level_order": "top_down"})
    flipped = inv.field_values(var, top_down, g, manifest.clock, None, axis)
    assert np.array_equal(flipped[:, ::-1], values)


# --- files -------------------------------------------------------------------------------------------------


def test_write_category_file_layout(manifest: mf.Manifest) -> None:
    land = inv.land_fraction(manifest.grids["inventory"], manifest.land_mask)
    totals = inv.write_category(manifest, "anthro", land)
    path = manifest.categories["anthro"].file
    with netCDF4.Dataset(path) as ds:
        assert ds["NO"].dimensions == ("time", "lat", "lon")
        assert ds["NO"].dtype == np.float32
        assert ds["NO"].units == "kg m-2 s-1"
        assert ds["NO"].filters()["zlib"] is True
        assert ds["time"].units == "hours since 2021-03-22 06:00:00"
        assert ds["time"].bounds == "time_bnds"
        assert ds["time_bnds"].shape == (25, 2)
        assert ds["lat"].bounds == "lat_bnds"
        assert ds["area"].units == "m2"
        assert ds.getncattr("Conventions") == get_settings().cf_conventions
        assert ds.getncattr("category") == "anthro"
        assert ds.getncattr("cece_mapalgo") == "consd"
        assert set(totals.variables) == set(manifest.categories["anthro"].variables)
        assert totals.times is not None
        assert len(totals.times) == 25
        assert totals.times[0] == manifest.clock.start
    with xr.open_dataset(path) as decoded:
        assert str(decoded["time"].values[0])[:16] == "2021-03-22T06:00"
        assert str(decoded["time"].values[-1])[:16] == "2021-03-23T06:00"


def test_gmi_file_is_three_d(manifest: mf.Manifest) -> None:
    inv.write_category(manifest, "gmi", None)
    with netCDF4.Dataset(manifest.categories["gmi"].file) as ds:
        assert ds["oh"].dimensions == ("time", "lev", "lat", "lon")
        assert ds["oh"].shape == (12, 5, 18, 36)
        assert ds["lev"].positive == "up"
        assert ds["time"].units == "days since 2021-01-01 00:00:00"


def test_totals_match_the_fields_and_round_trip(manifest: mf.Manifest) -> None:
    land = inv.land_fraction(manifest.grids["inventory"], manifest.land_mask)
    totals = inv.write_category(manifest, "fire", land)
    with xr.open_dataset(manifest.categories["fire"].file, decode_times=False) as ds:
        area = ds["area"].values
        expected = (ds["CO"].values.astype(float) * area).sum(axis=(1, 2))
    assert np.allclose(totals.variables["CO"].records, expected[:, None], rtol=1e-6)
    assert totals.variables["CO"].units == "kg m-2 s-1"
    path = manifest.inventories_dir / get_settings().totals_filename
    inv.Totals(categories={"fire": totals}).to_yaml(path)
    assert inv.Totals.from_yaml(path).categories["fire"] == totals


def test_generate_inventories_writes_everything(manifest: mf.Manifest) -> None:
    totals = inv.generate_inventories(manifest)
    for category in manifest.categories.values():
        assert category.file.exists()
    assert manifest.land_mask.file.exists()
    assert (manifest.inventories_dir / get_settings().totals_filename).exists()
    assert set(totals.categories) == set(manifest.categories)
    assert totals.categories["gmi"].variables["oh"].records[0].__len__() == 5


def test_regeneration_from_the_written_manifest_is_bit_identical(
    tmp_path: Path,
) -> None:
    first = small_manifest(tmp_path / "a")
    first.to_yaml(tmp_path / "a" / "manifest.yaml")
    again = mf.Manifest.from_yaml(tmp_path / "a" / "manifest.yaml").model_copy(
        update={
            "land_mask": first.land_mask.model_copy(
                update={"file": tmp_path / "b" / "landmask.nc"}
            )
        }
    )
    land_a = inv.land_fraction(first.grids["inventory"], first.land_mask)
    land_b = inv.land_fraction(again.grids["inventory"], again.land_mask)
    axis = inv.time_axis("hourly", first.clock)
    cat = first.categories["anthro"]
    va = inv.field_values(
        cat.variables["NO"], cat, first.grids["inventory"], first.clock, land_a, axis
    )
    vb = inv.field_values(
        again.categories["anthro"].variables["NO"],
        again.categories["anthro"],
        again.grids["inventory"],
        again.clock,
        land_b,
        axis,
    )
    assert np.array_equal(va, vb)


def test_masked_category_needs_the_land_fraction(manifest: mf.Manifest) -> None:
    axis = inv.time_axis("static", manifest.clock)
    with pytest.raises(ValueError, match="land fraction"):
        inv.field_values(
            unmasked_variable(),
            manifest.categories["dust"],
            manifest.grids["inventory"],
            manifest.clock,
            None,
            axis,
        )


def test_file_attributes_leave_out_none_and_stringify() -> None:
    attrs = inv.FileAttributes(Conventions="CF-1.8", title="t").as_attrs()
    assert attrs == {
        "Conventions": "CF-1.8",
        "title": "t",
        "source": "tools.make_mock_data",
    }
    full = inv.FileAttributes(
        Conventions="CF-1.8",
        title="t",
        category="dms",
        cadence="monthly",
        regrid_method="bilinear",
        cece_mapalgo="bilinear",
        masked_by="ocean",
        resolution="C48",
    ).as_attrs()
    assert full["masked_by"] == "ocean"
    assert all(isinstance(v, str) for v in full.values())


def test_deflate_settings_reach_the_files(
    manifest: mf.Manifest, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PROTOS_DEFLATE_LEVEL", "0")
    get_settings.cache_clear()
    assert inv.VariableEncoding.from_settings().as_encoding() == {
        "zlib": False,
        "complevel": 0,
        "shuffle": True,
    }
    inv.write_category(
        manifest,
        "dust",
        inv.land_fraction(manifest.grids["inventory"], manifest.land_mask),
    )
    with netCDF4.Dataset(manifest.categories["dust"].file) as ds:
        assert ds["sep"].filters()["zlib"] is False


# --- the realistic profile's layouts (issue #17) -----------------------------------------------------------


def with_layout(manifest: mf.Manifest, name: str, **overrides: object) -> mf.Manifest:
    """The manifest with one category replaced by a copy carrying the given field overrides."""
    category = manifest.categories[name].model_copy(update=overrides)
    return manifest.model_copy(
        update={"categories": {**manifest.categories, name: category}}
    )


def test_two_categories_share_one_file(manifest: mf.Manifest) -> None:
    anthro = manifest.categories["anthro"]
    anthro2 = anthro.model_copy(
        update={
            "variables": {f"{v}_elev": spec for v, spec in anthro.variables.items()}
        }
    )
    m = manifest.model_copy(
        update={"categories": {**manifest.categories, "anthro2": anthro2}}
    )
    land = inv.land_fraction(m.grids["inventory"], m.land_mask)
    totals = inv.write_file(m, anthro.file, ["anthro", "anthro2"], land)
    assert set(totals) == {"anthro", "anthro2"}
    assert totals["anthro2"].files == [anthro.file]
    with netCDF4.Dataset(anthro.file) as ds:
        assert set(anthro.variables) <= set(ds.variables)
        assert "SO2_elev" in ds.variables
        assert ds.getncattr("category") == "anthro,anthro2"
    everything = inv.generate_inventories(m)
    assert (
        everything.categories["anthro2"].variables["NO_elev"].records
        == totals["anthro2"].variables["NO_elev"].records
    )


def test_daily_category_writes_one_file_per_day(tmp_path: Path) -> None:
    manifest = small_manifest(tmp_path)
    layout = mf.FileLayout(write_bounds_and_area=False, daily_record_hour=12.0)
    m = with_layout(
        manifest,
        "fire",
        cadence="daily",
        file=tmp_path
        / "ExtData"
        / "QFED"
        / "%y4"
        / "%m2"
        / "qfed2.emis_so2.006.%y4%m2%d2.nc4",
        layout=layout,
    )
    totals = inv.generate_inventories(m).categories["fire"]
    assert totals.files == [
        tmp_path
        / "ExtData"
        / "QFED"
        / "2021"
        / "03"
        / "qfed2.emis_so2.006.20210322.nc4",
        tmp_path
        / "ExtData"
        / "QFED"
        / "2021"
        / "03"
        / "qfed2.emis_so2.006.20210323.nc4",
    ]
    assert totals.times == [
        datetime(2021, 3, 22, 12, tzinfo=UTC),
        datetime(2021, 3, 23, 12, tzinfo=UTC),
    ]
    assert len(totals.variables["CO"].records) == 2
    assert totals.file == m.categories["fire"].file
    with netCDF4.Dataset(totals.files[1]) as ds:
        assert ds["time"].units == "hours since 2021-03-23 00:00:00"
        assert ds["time"][:].tolist() == [12.0]
        assert "time_bnds" not in ds.variables
        assert "area" not in ds.variables
        assert "bounds" not in ds["lat"].ncattrs()
        assert ds["CO"].shape == (1, 18, 36)
    with pytest.raises(ValueError, match="needs the day"):
        inv.time_axis("daily", m.clock, layout)


def test_explicit_time_axes_are_written_verbatim(manifest: mf.Manifest) -> None:
    htap = mf.ExplicitTimeAxis(
        units="days since 2010-01-01 00:00:00", values=[15.5, 45.0, 74.5]
    )
    m = with_layout(manifest, "dms", layout=mf.FileLayout(time_axis=htap))
    axis = inv.time_axis("monthly", m.clock, m.categories["dms"].layout)
    assert axis.values == [15.5, 45.0, 74.5]
    assert axis.units == htap.units
    assert axis.bounds == [(0.75, 30.25), (30.25, 59.75), (59.75, 89.25)]
    assert [s.month for s in axis.stamps] == [1, 2, 3]
    assert axis.stamps[0] == datetime(2010, 1, 16, 12, tzinfo=UTC)
    ocean = 1.0 - inv.land_fraction(m.grids["inventory"], m.land_mask)
    totals = inv.write_category(m, "dms", ocean)
    assert len(totals.variables["dms"].records) == 3
    with netCDF4.Dataset(m.categories["dms"].file) as ds:
        assert ds["time"][:].tolist() == [15.5, 45.0, 74.5]
        assert ds["time"].units == htap.units
        assert ds["time_bnds"][0].tolist() == [0.75, 30.25]
    single = inv.explicit_time_axis(
        "static",
        mf.ExplicitTimeAxis(units="hours since 2021-03-22 06:00:00", values=[0.0]),
    )
    assert single.bounds == [(-0.5, 0.5)]
    assert single.stamps == [manifest.clock.start]


def test_layout_names_types_model_and_fill(tmp_path: Path) -> None:
    manifest = small_manifest(tmp_path)
    layout = mf.FileLayout(
        model="NETCDF3_64BIT_OFFSET",
        lon_name="longitude",
        lat_name="latitude",
        lev_name="level",
        time_name="t",
        dtype="float64",
        fill_value=-1.0e20,
        lev_before_time=True,
        write_bounds_and_area=False,
        time_axis=mf.ExplicitTimeAxis(
            units="days since 2021-01-01 00:00:00", values=[15.5]
        ),
    )
    m = with_layout(manifest, "gmi", layout=layout, level_order="top_down")
    totals = inv.write_category(m, "gmi", None)
    assert len(totals.variables["oh"].records) == 1
    assert len(totals.variables["oh"].records[0]) == 5
    with netCDF4.Dataset(m.categories["gmi"].file) as ds:
        assert ds.data_model == "NETCDF3_64BIT_OFFSET"
        assert ds["oh"].dimensions == ("level", "t", "latitude", "longitude")
        assert ds["oh"].dtype == np.float64
        assert ds["oh"].getncattr("_FillValue") == -1.0e20
        assert ds["level"].positive == "down"
        assert ds["t"][:].tolist() == [15.5]
        assert set(ds.variables) == {
            "longitude",
            "latitude",
            "level",
            "t",
            "oh",
            "h2o2",
            "no3",
        }
    # the swap puts file level 0 at the top (top_down): compare with the (time, lev) orientation
    plain = with_layout(
        manifest,
        "gmi",
        layout=mf.FileLayout(time_axis=layout.time_axis),
        level_order="top_down",
        file=manifest.categories["gmi"].file.with_name("gmi_plain.nc"),
    )
    inv.write_category(plain, "gmi", None)
    with (
        netCDF4.Dataset(m.categories["gmi"].file) as swapped,
        netCDF4.Dataset(plain.categories["gmi"].file) as straight,
    ):
        assert np.allclose(swapped["oh"][:, 0], straight["oh"][0])


def test_point_tables_are_copied_from_the_checked_in_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_dir = tmp_path / "defaults" / "volcanic"
    source_dir.mkdir(parents=True)
    (source_dir / "so2_volcanic_emissions_Carns.20210322.rc").write_text(
        "volcano::\n 1 2 3 4 5\n::\n"
    )
    (source_dir / "so2_volcanic_emissions_Carns.20210323.rc").write_text(
        "volcano::\n::\n"
    )
    monkeypatch.setenv("PROTOS_EXTDATA_DEFAULTS_DIR", str(tmp_path / "defaults"))
    get_settings.cache_clear()
    manifest = small_manifest(tmp_path / "out")
    volcano = mf.Category(
        file=tmp_path
        / "out"
        / "ExtData"
        / "volcanic"
        / "so2_volcanic_emissions_Carns.%y4%m2%d2.rc",
        cadence="daily",
        regrid_method="none",
        grid="inventory",
        is_3d=False,
        masked_by="none",
        variables={},
        layout=mf.FileLayout(format="point_table"),
    )
    m = manifest.model_copy(
        update={"categories": {"dust": manifest.categories["dust"], "volcano": volcano}}
    )
    totals = inv.generate_inventories(m)
    assert set(totals.categories) == {"dust"}
    copied = (
        tmp_path
        / "out"
        / "ExtData"
        / "volcanic"
        / "so2_volcanic_emissions_Carns.20210322.rc"
    )
    assert copied.read_text() == "volcano::\n 1 2 3 4 5\n::\n"
    assert (copied.parent / "so2_volcanic_emissions_Carns.20210323.rc").exists()
    (source_dir / "so2_volcanic_emissions_Carns.20210323.rc").unlink()
    with pytest.raises(ValueError, match="not checked in"):
        inv.copy_point_table(m, "volcano")


def test_pole_centred_grid_bounds_are_clipped_and_areas_close() -> None:
    g = mf.LatLonGrid(
        dlon=10.0,
        dlat=10.0,
        lon_first=-180.0,
        lat_first=-90.0,
        nlon=36,
        nlat=19,
        pole_centred=True,
    )
    b = inv.lat_bounds(g)
    assert b[0].tolist() == [-90.0, -85.0]
    assert b[-1].tolist() == [85.0, 90.0]
    assert math.isclose(
        float(inv.cell_area(g, R).sum()), 4 * math.pi * R**2, rel_tol=1e-9
    )
