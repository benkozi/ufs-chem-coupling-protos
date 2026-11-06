"""tools/extdata_catalog.py: the log scanner, the NetCDF describer and the point-table parser (issue #17)."""

import json
import re
import tomllib
from pathlib import Path

import netCDF4
import numpy as np
import pytest

from tools import extdata as xd
from tools import extdata_catalog as cat

FIXTURES = Path(__file__).parent / "fixtures"
LOG = FIXTURES / "catchem_file_read_PET000.txt"
CEDS_22 = "ExtData/CEDS/v2019/2021/CEDS.2019.emis.20210322.nc"
CEDS_23 = "ExtData/CEDS/v2019/2021/CEDS.2019.emis.20210323.nc"
VOLCANO_22 = "ExtData/volcanic/so2_volcanic_emissions_Carns.20210322.rc"


# --- scan-log ----------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def catalog() -> xd.FileCatalog:
    return xd.FileCatalog.model_validate(cat.scan_logs([LOG]))


def test_scan_log_finds_the_thirty_unique_files_of_the_issue_log(
    catalog: xd.FileCatalog,
) -> None:
    assert len(catalog.files) == 30
    assert len(catalog.by_role("config")) == 4
    assert len(catalog.by_role("optics")) == 6
    assert len(catalog.by_role("inventory")) == 18
    assert len(catalog.by_role("point_table")) == 2
    assert catalog.line_shapes == ["catchem_file_read"]
    assert catalog.logs == [str(LOG)]
    assert sum(f.read_count for f in catalog.files) == 110
    assert all(not f.path.startswith("./") for f in catalog.files)
    assert "ExtData/monochromatic/optics_SS.v3_3.nc" in {f.path for f in catalog.files}
    assert all(f.pets == [0] for f in catalog.files)
    assert catalog.files[0].path == "CATChem_field_mapping.yml"
    assert catalog.files[0].first_seen.startswith("20261105 232505")


def test_ceds_is_attributed_to_three_categories_with_their_read_times(
    catalog: xd.FileCatalog,
) -> None:
    by_path = {f.path: f for f in catalog.files}
    ceds = by_path[CEDS_22]
    assert ceds.format == "netcdf"
    assert ceds.read_count == 16
    assert ceds.sources == {
        "AQMIO_ReadTimeCoord": 3,
        "catchem_emis_detect_field_ranks": 3,
        "catchem_regrid_field": 10,
    }
    assert ceds.categories == {
        "anthro1": ["2021-03-22T06:00:00"],
        "anthro2": ["2021-03-22T06:00:00"],
        "ship": ["2021-03-22T06:00:00"],
    }
    assert set(by_path[CEDS_23].categories) == {"anthro1", "anthro2", "ship"}
    assert by_path[CEDS_23].categories["ship"] == ["2021-03-23T00:00:00"]
    htap = by_path[
        "ExtData/PIESA/sfc/HTAP/v2.2/htap-v2.2.emis_so2.aviation_lto.x3600_y1800_t12.2010.nc4"
    ]
    assert htap.categories == {"so2_aviation_lto": ["init", "2021-03-22T06:00:00"]}
    assert by_path["CATChem_new_config.yml"].categories == {}


def test_point_tables_carry_the_point_count(catalog: xd.FileCatalog) -> None:
    tables = catalog.by_role("point_table")
    assert {t.path for t in tables} == {
        VOLCANO_22,
        "ExtData/volcanic/so2_volcanic_emissions_Carns.20210323.rc",
    }
    assert all(t.point_count == 97 for t in tables)
    assert all(t.format == "point_ascii" for t in tables)
    assert tables[0].categories == {"volcano": ["2021-03-22T06:00:00"]}
    assert all(f.point_count is None for f in catalog.by_role("inventory"))


def test_companion_line_shape_and_unrelated_lines(tmp_path: Path) -> None:
    log = tmp_path / "PET003.ESMF_LogFile"
    lines = (
        "20261106 100000.000 INFO             PET003 CATChem: Running CATChem for dt = 720.00 seconds",
        "not a log line at all",
        "20261106 100000.001 INFO             PET003 catchem_emis_update: reading emission for anthro @ 2021-03-22T06:00:00",
        "20261106 100000.002 DEBUG            PET003 CATChem: emission open category=anthro reader=time_coord path=./data/anthro.nc",
        "20261106 100000.003 DEBUG            PET003 CATChem: emission open category=anthro reader=regrid path=data/anthro.nc",
        "20261106 100000.005 INFO             PET003 CATCHEM_FILE_READ: format=netcdf source=AQMIO_ReadTimeCoord path=data/anthro.nc",
        "20261106 100000.004 DEBUG            PET003 CATChem: emission open category=volcano reader=point_rc path=ExtData/volcanic/v.rc",
        "",
    )
    log.write_text("\n".join(lines))
    doc = xd.FileCatalog.model_validate(cat.scan_logs([log]))
    assert doc.line_shapes == ["catchem_file_read", "emission_open"]
    assert [f.path for f in doc.files] == ["data/anthro.nc", "ExtData/volcanic/v.rc"]
    anthro, volcano = doc.files
    assert anthro.read_count == 3
    assert anthro.sources == {"time_coord": 1, "regrid": 1, "AQMIO_ReadTimeCoord": 1}
    assert anthro.categories == {"anthro": ["2021-03-22T06:00:00"]}
    assert anthro.pets == [3]
    assert volcano.format == "point_ascii"
    assert volcano.role == "point_table"
    assert volcano.categories == {"volcano": ["2021-03-22T06:00:00"]}


def test_two_logs_merge_by_path(tmp_path: Path) -> None:
    second = tmp_path / "PET001.ESMF_LogFile"
    second.write_text(
        "20261105 232506.900 INFO             PET001 CATCHEM_FILE_READ: format=netcdf source=catchem_regrid_field path=./"
        + CEDS_22
        + "\n"
    )
    doc = xd.FileCatalog.model_validate(cat.scan_logs([LOG, second]))
    assert len(doc.files) == 30
    ceds = next(f for f in doc.files if f.path == CEDS_22)
    assert ceds.read_count == 17
    assert ceds.pets == [0, 1]


def test_normalise_and_roles() -> None:
    assert cat.normalise("././a/b.nc") == "a/b.nc"
    assert cat.normalise("a/./b.nc") == "a/./b.nc"
    assert cat.role_of("yaml", "catchem_nuopc_init") == "config"
    assert cat.role_of("netcdf", "init_mie_data") == "optics"
    assert cat.role_of("point_ascii", "catchem_emis_read_points") == "point_table"
    assert cat.role_of("netcdf", "point_rc") == "point_table"
    assert cat.role_of("netcdf", "catchem_regrid_field") == "inventory"


def test_scan_log_command_writes_the_catalog(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "sub" / "catalog.json"
    assert cat.main(["scan-log", str(LOG), "--out", str(out)]) == 0
    assert "30 files" in capsys.readouterr().err
    doc = xd.FileCatalog.model_validate_json(out.read_text())
    assert len(doc.files) == 30
    assert cat.main(["scan-log", str(tmp_path / "missing.log"), "--out", str(out)]) == 2
    assert "log not found" in capsys.readouterr().err


# --- describe ----------------------------------------------------------------------------------------------


def write_inventory(path: Path, *, pole_centred: bool = True) -> None:
    """A tiny file with the layouts the real inventories mix: unlimited time, a packed 2-D variable, a top-down 3-D one."""
    with netCDF4.Dataset(path, "w", format="NETCDF4_CLASSIC") as ds:
        ds.createDimension("time", None)
        ds.createDimension("lev", 2)
        ds.createDimension("lat", 3)
        ds.createDimension("lon", 4)
        ds.title = "fixture"
        ds.version = 3
        time = ds.createVariable("time", "f8", ("time",))
        time.units = "hours since 2021-03-22 00:00:00"
        time.calendar = "standard"
        time[:] = [12.0, 36.0]
        lev = ds.createVariable("lev", "f4", ("lev",))
        lev.positive = "down"
        lev[:] = [1000.0, 500.0]
        lat = ds.createVariable("lat", "f8", ("lat",))
        lat.units = "degrees_north"
        lat[:] = [-90.0, 0.0, 90.0] if pole_centred else [-60.0, 0.0, 60.0]
        lon = ds.createVariable("lon", "f8", ("lon",))
        lon.units = "degrees_east"
        lon[:] = [0.0, 90.0, 180.0, 270.0]
        packed = ds.createVariable("SO2", "i2", ("time", "lat", "lon"), fill_value=-1)
        packed.scale_factor = 0.5
        packed.add_offset = 0.0
        packed.units = "kg m-2 s-1"
        values = np.arange(24, dtype=np.float64).reshape(2, 3, 4)
        packed[:] = values  # auto-scaled: stored as 2 * value
        packed[0, 0, 0] = np.ma.masked  # one fill value
        three_d = ds.createVariable("oh", "f4", ("time", "lev", "lat", "lon"))
        three_d.units = "mol/mol"
        three_d.valid_range = np.array([0.0, 1.0], dtype=np.float32)
        three_d[:] = np.ones((2, 2, 3, 4))
        static = ds.createVariable("clayfrac", "f4", ("lat", "lon"))
        static[:] = np.full((3, 4), 0.25)


TABLE = """# volcanic SO2, degassing
# lat lon sulfur elevation cloud_top
volcano::
  -0.37   -91.55   8.33e+00    1000.0   1000.0
  45.00  -122.00   1.00e+00    2000.0   3500.0
  this row is malformed
  12.0  12.0  12.0
::
  99.0 99.0 99.0 99.0 99.0
"""


def write_catalog(
    path: Path, files: list[tuple[str, str, dict[str, list[str]]]]
) -> None:
    doc = {
        "generated_utc": "2026-11-06T00:00:00+00:00",
        "logs": ["PET000.ESMF_LogFile"],
        "line_shapes": ["catchem_file_read"],
        "files": [
            {
                "path": p,
                "format": "point_ascii" if role == "point_table" else "netcdf",
                "role": role,
                "sources": {"x": 1},
                "categories": cats,
                "pets": [0],
                "read_count": 1,
                "first_seen": "20261105 232505.145",
                "point_count": None,
            }
            for p, role, cats in files
        ],
    }
    xd.FileCatalog.model_validate(doc)
    path.write_text(json.dumps(doc))


@pytest.fixture
def extdata_root(tmp_path: Path) -> Path:
    root = tmp_path / "ExtData"
    (root / "CEDS").mkdir(parents=True)
    (root / "volcanic").mkdir()
    write_inventory(root / "CEDS" / "ceds.nc")
    (root / "volcanic" / "v.rc").write_text(TABLE)
    return root


def test_describe_records_layout_axes_and_stats(
    extdata_root: Path, tmp_path: Path
) -> None:
    catalog_path = tmp_path / "catalog.json"
    write_catalog(
        catalog_path,
        [
            ("CATChem_new_config.yml", "config", {}),
            (
                "ExtData/CEDS/ceds.nc",
                "inventory",
                {"ship": ["init"], "anthro1": ["init"]},
            ),
            ("ExtData/volcanic/v.rc", "point_table", {"volcano": ["init"]}),
            ("ExtData/CEDS/missing.nc", "inventory", {"fire": ["init"]}),
        ],
    )
    tables = tmp_path / "tables"
    doc = xd.ExtDataHeaders.model_validate(
        cat.describe(catalog_path, extdata_root, True, 8 << 30, tables)
    )
    assert doc.extdata_root == str(extdata_root)
    assert len(doc.catalog_digest) == 64
    headers = doc.by_path()
    assert set(headers) == {
        "ExtData/CEDS/ceds.nc",
        "ExtData/volcanic/v.rc",
        "ExtData/CEDS/missing.nc",
    }

    ceds = headers["ExtData/CEDS/ceds.nc"]
    assert ceds.exists
    assert ceds.reader == "netCDF4"
    assert ceds.model == "NETCDF4_CLASSIC"
    assert ceds.categories == ["anthro1", "ship"]
    assert ceds.size is not None
    assert ceds.size > 0
    assert ceds.mtime is not None
    assert ceds.dimensions == {
        "time": {"size": 2, "unlimited": True},
        "lev": {"size": 2, "unlimited": False},
        "lat": {"size": 3, "unlimited": False},
        "lon": {"size": 4, "unlimited": False},
    }
    assert ceds.global_attributes == {"title": "fixture", "version": 3}
    assert set(ceds.axes) == {"time", "lev", "lat", "lon"}
    assert ceds.axes["time"].stamps == ["2021-03-22T12:00:00", "2021-03-23T12:00:00"]
    assert ceds.axes["time"].units == "hours since 2021-03-22 00:00:00"
    assert ceds.axes["time"].calendar == "standard"
    assert ceds.axes["time"].spacing == 24.0
    assert ceds.axes["lev"].direction == "decreasing"
    assert ceds.axes["lev"].stamps is None
    assert ceds.axes["lat"].uniform
    assert ceds.axes["lat"].spacing == 90.0
    assert ceds.axes["lon"].values == [0.0, 90.0, 180.0, 270.0]
    assert ceds.axes["lon"].direction == "increasing"

    so2 = ceds.variables["SO2"]
    assert so2.dtype == "int16"
    assert so2.dimensions == ["time", "lat", "lon"]
    assert so2.shape == [2, 3, 4]
    assert so2.attributes["scale_factor"] == 0.5
    assert so2.attributes["_FillValue"] == -1
    assert so2.stats is not None
    assert so2.stats.fill_fraction == pytest.approx(1 / 24)
    assert so2.stats.min == 1.0
    assert so2.stats.max == 23.0
    assert so2.stats.nonzero_fraction == 1.0
    assert so2.stats.record_sums == [sum(range(1, 12)), sum(range(12, 24))]
    assert so2.stats.area_weighted_sums is not None
    assert len(so2.stats.area_weighted_sums) == 2
    assert so2.stats.area_weighted_sums[1] > so2.stats.area_weighted_sums[0] > 0
    oh = ceds.variables["oh"]
    assert oh.attributes["valid_range"] == [0.0, 1.0]
    assert oh.stats is not None
    assert oh.stats.record_sums == [24.0, 24.0]
    assert oh.stats.std == pytest.approx(0.0)
    assert oh.stats.area_weighted_sums is not None
    assert oh.stats.area_weighted_sums[0] == pytest.approx(
        2 * 4 * np.pi * cat.EARTH_RADIUS_M**2, rel=1e-9
    )
    clay = ceds.variables["clayfrac"]
    assert clay.stats is not None
    assert clay.stats.record_sums == [pytest.approx(3.0)]
    assert clay.stats.mean == pytest.approx(0.25)
    assert ceds.variables["lat"].stats is None
    assert ceds.variables["lat"].stats_skipped_reason is None

    table = headers["ExtData/volcanic/v.rc"]
    assert table.reader == "text"
    assert table.model is None
    assert table.point_table is not None
    assert table.point_table.row_count == 2
    assert table.point_table.malformed_rows == 2
    assert table.point_table.rows[0] == [-0.37, -91.55, 8.33, 1000.0, 1000.0]
    assert table.point_table.total_emission == pytest.approx(9.33)
    assert table.point_table.ranges["lat"] == [-0.37, 45.0]
    assert table.point_table.columns == ["lat", "lon", "emission", "bottom", "top"]
    assert table.point_table.units[2] == "kg S s-1"
    assert (tables / "v.rc").read_text() == TABLE

    missing = headers["ExtData/CEDS/missing.nc"]
    assert not missing.exists
    assert missing.error is not None
    assert "not found" in missing.error
    assert missing.size is None


def test_describe_without_stats_and_with_a_size_cap(
    extdata_root: Path, tmp_path: Path
) -> None:
    catalog_path = tmp_path / "catalog.json"
    write_catalog(catalog_path, [("ExtData/CEDS/ceds.nc", "inventory", {})])
    skipped = xd.ExtDataHeaders.model_validate(
        cat.describe(catalog_path, extdata_root, False, 8 << 30, None)
    ).by_path()["ExtData/CEDS/ceds.nc"]
    assert skipped.variables["SO2"].stats is None
    assert skipped.variables["SO2"].stats_skipped_reason == "--no-stats"
    capped = xd.ExtDataHeaders.model_validate(
        cat.describe(catalog_path, extdata_root, True, 100, None)
    ).by_path()["ExtData/CEDS/ceds.nc"]
    assert capped.variables["clayfrac"].stats is not None  # 12 cells x 8 B fit
    assert capped.variables["oh"].stats is None
    reason = capped.variables["oh"].stats_skipped_reason
    assert reason is not None
    assert "exceed --stats-max-bytes 100" in reason


def test_non_uniform_grid_has_no_area_weighted_sums(tmp_path: Path) -> None:
    root = tmp_path / "ExtData"
    root.mkdir()
    path = root / "odd.nc"
    with netCDF4.Dataset(path, "w") as ds:
        ds.createDimension("lat", 3)
        ds.createDimension("lon", 2)
        lat = ds.createVariable("latitude", "f8", ("lat",))
        lat.standard_name = "latitude"
        lat[:] = [-45.0, 0.0, 60.0]
        lon = ds.createVariable("longitude", "f8", ("lon",))
        lon.axis = "X"
        lon[:] = [0.0, 180.0]
        conc = ds.createVariable("conc", "f8", ("lat", "lon"))
        conc[:] = np.zeros((3, 2))
        weights = ds.createVariable(
            "weights", "f8", ("lat",)
        )  # 1-D but not a coordinate
        weights[:] = [1.0, 2.0, 3.0]
    header = cat.describe_netcdf(path, True, 8 << 30)
    axes = xd.FileHeader.model_validate(
        {
            "path": "ExtData/odd.nc",
            "role": "inventory",
            "categories": [],
            "exists": True,
        }
        | header
    ).axes
    assert axes["lat"].name == "latitude"
    assert not axes["lat"].uniform
    assert axes["lon"].name == "longitude"
    assert axes["lon"].spacing == 180.0
    assert set(axes) == {"lat", "lon"}
    variables = header["variables"]
    assert isinstance(variables, dict)
    stats = variables["conc"]["stats"]
    assert stats["area_weighted_sums"] is None
    assert stats["nonzero_fraction"] == 0.0
    assert stats["min"] == 0.0
    assert stats["record_sums"] == [0.0]


def test_unreadable_time_units_and_empty_variables(tmp_path: Path) -> None:
    path = tmp_path / "t.nc"
    with netCDF4.Dataset(path, "w") as ds:
        ds.createDimension("time", 1)
        ds.createDimension("lat", 1)
        ds.createDimension("lon", 1)
        time = ds.createVariable("time", "f8", ("time",))
        time.units = "fortnights"
        time[:] = [0.0]
        v = ds.createVariable("v", "f8", ("time", "lat", "lon"), fill_value=9.0)
        v[:] = 9.0  # everything fill
    header = cat.describe_netcdf(path, True, 8 << 30)
    axes = header["axes"]
    assert isinstance(axes, dict)
    assert axes["time"]["stamps"] is None
    assert axes["time"]["spacing"] is None
    assert axes["time"]["direction"] == "constant"
    variables = header["variables"]
    assert isinstance(variables, dict)
    stats = variables["v"]["stats"]
    assert stats["fill_fraction"] == 1.0
    assert stats["mean"] == 0.0
    assert stats["max"] == 0.0
    assert cat.decode_time(np.array([0.0]), None, None) is None


def test_describe_reports_a_broken_file_by_name(tmp_path: Path) -> None:
    root = tmp_path / "ExtData"
    root.mkdir()
    (root / "broken.nc").write_text("this is not NetCDF")
    out = cat.describe_file(
        "ExtData/broken.nc", "inventory", ["fire"], root, True, 1, None
    )
    assert out["exists"] is True
    assert out["error"] is not None
    assert "OSError" in str(out["error"])
    xd.FileHeader.model_validate(out)


def test_parse_point_table_edge_cases() -> None:
    empty = xd.PointTable.model_validate(cat.parse_point_table("nothing here"))
    assert empty.row_count == 0
    assert empty.total_emission == 0.0
    assert empty.ranges["lon"] == [0.0, 0.0]
    unterminated = cat.parse_point_table("volcano::\n 1 2 3 4 5\n 1 2 3 4 5 6 7\n")
    assert unterminated["row_count"] == 2
    assert unterminated["malformed_rows"] == 0
    other = cat.parse_point_table(TABLE, label="other")
    assert other["row_count"] == 0


def test_json_safe_conversions() -> None:
    assert cat.json_safe(np.float32(1.5)) == 1.5
    assert cat.json_safe(np.array([1, 2], dtype=np.int16)) == [1, 2]
    assert cat.json_safe(b"bytes") == "bytes"
    assert cat.json_safe((1, "a")) == [1, "a"]
    assert cat.json_safe(Path("x")) == "x"


def test_describe_command_exit_codes(
    extdata_root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    catalog_path = tmp_path / "catalog.json"
    write_catalog(
        catalog_path, [("ExtData/volcanic/v.rc", "point_table", {"volcano": ["init"]})]
    )
    out = tmp_path / "headers.json"
    assert (
        cat.main(
            [
                "describe",
                "--extdata-root",
                str(extdata_root),
                "--catalog",
                str(catalog_path),
                "--out",
                str(out),
            ]
        )
        == 0
    )
    assert "1 files" in capsys.readouterr().err
    doc = xd.ExtDataHeaders.model_validate_json(out.read_text())
    assert doc.files[0].point_table is not None
    assert (
        cat.main(
            [
                "describe",
                "--extdata-root",
                str(tmp_path / "nope"),
                "--catalog",
                str(catalog_path),
                "--out",
                str(out),
            ]
        )
        == 2
    )
    assert "not a directory" in capsys.readouterr().err
    assert (
        cat.main(
            [
                "describe",
                "--extdata-root",
                str(extdata_root),
                "--catalog",
                str(tmp_path / "nope.json"),
                "--out",
                str(out),
            ]
        )
        == 2
    )
    assert "catalog not found" in capsys.readouterr().err
    with pytest.raises(SystemExit) as exc:
        cat.main([])
    assert exc.value.code == 2


def test_script_metadata_declares_its_third_party_imports() -> None:
    """The PEP 723 block lets `uv run --script` install the dependencies on Ursa; it must name every import."""
    source = Path(cat.__file__).read_text()
    block = re.search(
        r"^# /// script\n(?P<body>(?:^#(?: .*)?\n)+)^# ///$", source, re.MULTILINE
    )
    assert block is not None
    toml = "\n".join(
        line[2:] if line.startswith("# ") else line[1:]
        for line in block.group("body").splitlines()
    )
    meta = tomllib.loads(toml)
    assert meta["requires-python"] == ">=3.12"
    deps = {re.split(r"[<>=!~]", d)[0] for d in meta["dependencies"]}
    imported = set(re.findall(r"^import (\w+)|^from (\w+)", source, re.MULTILINE))
    third_party = {a or b for a, b in imported} - set(
        __import__("sys").stdlib_module_names
    )
    assert third_party == {"netCDF4", "numpy"}
    assert deps == third_party
    assert "from tools" not in source
