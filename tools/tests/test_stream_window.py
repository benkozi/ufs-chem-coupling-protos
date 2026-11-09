"""The per-run window file of a daily inventory series (issue #18): CECE's cap path reads one file per stream."""

from datetime import UTC, datetime
from pathlib import Path

import netCDF4
import numpy as np
import pytest

from tools import inventories as inv
from tools import manifest as mf
from tools import stream_window as sw
from tools.settings import get_settings


@pytest.fixture
def daily_set(tmp_path: Path) -> tuple[mf.Manifest, Path]:
    """A realistic-profile manifest at grid factor 60 (3600 x 1800 -> 60 x 30) with its daily files written."""
    manifest = mf.default_manifest(
        resolution="C48",
        out_dir=tmp_path / "data",
        command="test",
        defaults_file=get_settings().realistic_defaults_file,
        grid_factor=60,
    )
    keep = {"anthro1", "anthro2", "ship", "fire"}
    manifest = manifest.model_copy(
        update={
            "categories": {k: v for k, v in manifest.categories.items() if k in keep}
        }
    )
    inv.generate_inventories(manifest)
    return manifest, tmp_path / "gen"


def test_window_name_drops_the_date_tokens() -> None:
    assert (
        sw.window_name(Path("ExtData/CEDS/v2019/%y4/CEDS.2019.emis.%y4%m2%d2.nc"))
        == "CEDS.2019.emis.nc"
    )
    assert (
        sw.window_name(Path("ExtData/QFED/%y4/%m2/qfed2.emis_so2.006.%y4%m2%d2.nc4"))
        == "qfed2.emis_so2.006.nc4"
    )
    assert (
        sw.window_name(
            Path(
                "ExtData/MEGAN_OFFLINE_BVOC/v2019-10/%y4/MEGAN.OFFLINE.BIOVOC.%y4.emis.%y4%m2%d2.nc"
            )
        )
        == "MEGAN.OFFLINE.BIOVOC.emis.nc"
    )


def test_window_concatenates_the_padded_days(
    daily_set: tuple[mf.Manifest, Path],
) -> None:
    manifest, out = daily_set
    anthro1 = manifest.categories["anthro1"]
    files = anthro1.files_for(manifest.clock)
    assert len(files) == 4  # daily_padding_days: 1 in the realistic defaults
    record = sw.write_window(files, out / "streams" / sw.window_name(anthro1.file))
    assert record.path == out / "streams" / "CEDS.2019.emis.nc"
    assert record.files == files
    assert record.stamps == [
        datetime(2021, 3, 21, tzinfo=UTC),
        datetime(2021, 3, 22, tzinfo=UTC),
        datetime(2021, 3, 23, tzinfo=UTC),
        datetime(2021, 3, 24, tzinfo=UTC),
    ]
    # the CEDS file is shared by anthro1, anthro2 and ship: every variable is in the window once
    assert set(record.variables) == {
        "SO2",
        "OC",
        "BC",
        "SO2_elev",
        "OC_elev",
        "BC_elev",
        "SO2_ship",
        "SO4_ship",
        "OC_ship",
        "BC_ship",
    }
    with netCDF4.Dataset(record.path) as ds, netCDF4.Dataset(files[2]) as day:
        assert ds.data_model == day.data_model == "NETCDF4_CLASSIC"
        assert ds["time"].units == "hours since 2021-03-21 00:00:00"
        assert ds["time"][:].tolist() == [0.0, 24.0, 48.0, 72.0]
        assert ds["SO2"].dimensions == ("time", "lat", "lon")
        assert ds["SO2"].dtype == day["SO2"].dtype == np.dtype("float32")
        assert ds["SO2"]._FillValue == day["SO2"]._FillValue
        assert ds["SO2"].units == day["SO2"].units
        np.testing.assert_array_equal(ds["SO2"][2], day["SO2"][0])
        np.testing.assert_array_equal(ds["lon"][:], day["lon"][:])
        assert "time_bnds" not in ds.variables
        assert str(files[0]) in ds.source_files
    # QFED records sit at 12Z
    fire = manifest.categories["fire"]
    record = sw.write_window(
        fire.files_for(manifest.clock), out / "streams" / sw.window_name(fire.file)
    )
    assert record.stamps[0] == datetime(2021, 3, 21, 12, tzinfo=UTC)
    with netCDF4.Dataset(record.path) as ds:
        assert ds["time"][:].tolist() == [12.0, 36.0, 60.0, 84.0]


def test_a_missing_day_is_named(daily_set: tuple[mf.Manifest, Path]) -> None:
    manifest, out = daily_set
    files = manifest.categories["fire"].files_for(manifest.clock)
    files[3].unlink()
    with pytest.raises(sw.WindowError, match="20210324"):
        sw.write_window(files, out / "w.nc")


def test_records_must_be_in_time_order(daily_set: tuple[mf.Manifest, Path]) -> None:
    manifest, out = daily_set
    files = manifest.categories["fire"].files_for(manifest.clock)
    with pytest.raises(sw.WindowError, match="ascending"):
        sw.write_window([files[1], files[0]], out / "w.nc")


def test_every_field_has_a_description() -> None:
    for name, field in sw.WindowRecord.model_fields.items():
        assert field.description, name


def test_refusals_are_named(
    daily_set: tuple[mf.Manifest, Path], tmp_path: Path
) -> None:
    manifest, out = daily_set
    with pytest.raises(sw.WindowError, match="at least one"):
        sw.write_window([], out / "w.nc")
    timeless = tmp_path / "timeless.nc"
    with netCDF4.Dataset(timeless, "w") as ds:
        ds.createDimension("lat", 2)
        ds.createVariable("x", "f4", ("lat",))[:] = [1.0, 2.0]
    with pytest.raises(sw.WindowError, match="no 'time' dimension"):
        sw.write_window([timeless], out / "w.nc")
    fire = manifest.categories["fire"].files_for(manifest.clock)
    ceds = manifest.categories["anthro1"].files_for(manifest.clock)
    with pytest.raises(sw.WindowError, match="does not carry the variables"):
        sw.write_window([fire[0], ceds[1]], out / "w.nc")
