"""tools/realistic_defaults.py: the realistic profile's parameter set from the scrape and CATChem's configuration (issue #17)."""

import json
import logging
from pathlib import Path

import pytest
import yaml

from tools import extdata as xd
from tools import log
from tools import manifest as mf
from tools import realistic_defaults as rd
from tools.settings import get_settings

CEDS = "ExtData/CEDS/v2019/2021/CEDS.2019.emis.20210322.nc"
CEDS_TEMPLATE = "ExtData/CEDS/v2019/%y4/CEDS.2019.emis.%y4%m2%d2.nc"
GMI = "ExtData/PIESA/L127/gmi.nc"
DMS = "ExtData/MERRA2/sfc/dms.nc4"
VOLCANO = "ExtData/volcanic/so2_volcanic_emissions_Carns.20210322.rc"
VOLCANO_TEMPLATE = "ExtData/volcanic/so2_volcanic_emissions_Carns.%y4%m2%d2.rc"

CATCHEM_CONFIG = {
    "processes": {
        "extemis": {
            "activate": True,
            "anthro1": {
                "source_file": CEDS_TEMPLATE,
                "frequency": "daily",
                "regrid_method": "conserve",
            },
            "ship": {
                "source_file": CEDS_TEMPLATE,
                "frequency": "daily",
                "regrid_method": "conserve",
            },
            "gmi": {
                "source_file": GMI,
                "frequency": "monthly",
                "regrid_method": "conserve",
                "is_2d": False,
                "reverse_vertical": True,
            },
            "dms": {
                "source_file": DMS,
                "frequency": "monthly",
                "regrid_method": "neareststod",
                "lat_name": "latitude",
            },
            "volcano": {
                "source_file": VOLCANO_TEMPLATE,
                "format": "volcano",
                "gridded": False,
                "frequency": "daily",
            },
            "point": {
                "source_file": "INPUT/PT.nc",
                "gridded": False,
                "frequency": "hourly",
            },
        }
    }
}
EMISSION_MAP = {
    "anthro1": {
        "SO2": {
            "long_name": "Sulfur Dioxide",
            "units": "kg/m2/s",
            "scale": [1.0],
            "map": ["so2"],
        }
    },
    "ship": {
        "SO2_ship": {
            "long_name": "Ship SO2",
            "units": "kg/m2/s",
            "scale": [1.0],
            "map": ["so2"],
        }
    },
    "gmi": {
        "oh": {"long_name": "OH", "units": "mol/mol", "scale": [1.0], "map": ["oh"]}
    },
    "dms": {
        "conc": {
            "long_name": "DMS",
            "units": "nmol/l",
            "scale": [1.0],
            "map": ["dms_in"],
        }
    },
    "volcano": {
        "SO2": {
            "long_name": "volcanic",
            "units": "kg/m2/s",
            "scale": [2.0],
            "map": ["so2"],
        }
    },
}


def axis(name: str, values: list[float], **extra: object) -> dict[str, object]:
    spacing = (values[-1] - values[0]) / (len(values) - 1) if len(values) > 1 else None
    return {
        "name": name,
        "count": len(values),
        "first": values[0],
        "last": values[-1],
        "spacing": spacing,
        "uniform": True,
        "direction": "increasing"
        if spacing and spacing > 0
        else ("decreasing" if spacing else "constant"),
        "units": None,
        "calendar": None,
        "values": values,
        "stamps": None,
    } | extra


def variable(
    dims: list[str],
    shape: list[int],
    dtype: str = "float32",
    stats: dict[str, object] | None = None,
    **attrs: object,
) -> dict[str, object]:
    return {
        "dtype": dtype,
        "dimensions": dims,
        "shape": shape,
        "attributes": attrs,
        "stats": stats,
        "stats_skipped_reason": None,
    }


def stats(mean: float, maximum: float) -> dict[str, object]:
    return {
        "min": 0.0,
        "max": maximum,
        "mean": mean,
        "std": 1.0,
        "nonzero_fraction": 0.5,
        "fill_fraction": 0.0,
        "record_sums": [1.0],
        "area_weighted_sums": None,
    }


def file_header(
    path: str, role: str, categories: list[str], **fields: object
) -> dict[str, object]:
    return {
        "path": path,
        "role": role,
        "categories": categories,
        "exists": True,
        "size": 1,
        "mtime": "2026-01-01T00:00:00+00:00",
        "reader": "netCDF4",
        "model": "NETCDF4_CLASSIC",
    } | fields


def lonlat(
    nlon: int,
    nlat: int,
    *,
    pole_centred: bool = False,
    names: tuple[str, str] = ("lon", "lat"),
) -> dict[str, dict[str, object]]:
    dlon = 360.0 / nlon
    lons = [-180.0 + dlon / 2 + i * dlon for i in range(nlon)]
    if pole_centred:
        dlat = 180.0 / (nlat - 1)
        lats = [-90.0 + i * dlat for i in range(nlat)]
    else:
        dlat = 180.0 / nlat
        lats = [-90.0 + dlat / 2 + i * dlat for i in range(nlat)]
    return {
        "lon": axis(names[0], lons, units="degrees_east"),
        "lat": axis(names[1], lats, units="degrees_north"),
    }


def headers_doc(n_levels: int = 5) -> dict[str, object]:
    time_daily = axis(
        "time",
        [12.0],
        units="hours since 2021-03-22 00:00:00",
        calendar="standard",
        stamps=["2021-03-22T12:00:00"],
    )
    gmi_time = axis(
        "time",
        [15.5, 45.0, 74.5],
        units="days since 2021-01-01 00:00:00",
        calendar="standard",
    )
    gmi_lev = axis("lev", [float(n_levels - i) for i in range(n_levels)])
    ceds = file_header(
        CEDS,
        "inventory",
        ["anthro1", "ship"],
        dimensions={
            "time": {"size": 1, "unlimited": True},
            "lat": {"size": 18, "unlimited": False},
            "lon": {"size": 36, "unlimited": False},
        },
        axes=lonlat(36, 18) | {"time": time_daily},
        variables={
            "lon": variable(["lon"], [36]),
            "SO2": variable(
                ["time", "lat", "lon"],
                [1, 18, 36],
                stats=stats(2.0e-11, 5.0e-10),
                units="kg m-2 s-1",
                long_name="SO2 anthro",
                _FillValue=-1.0e20,
            ),
            "SO2_ship": variable(
                ["time", "lat", "lon"],
                [1, 18, 36],
                stats=stats(1.0e-12, 2.0e-11),
                units="kg m-2 s-1",
            ),
        },
    )
    gmi = file_header(
        GMI,
        "inventory",
        ["gmi"],
        model="NETCDF4",
        axes=lonlat(36, 18) | {"time": gmi_time, "lev": gmi_lev},
        variables={
            "oh": variable(
                ["lev", "time", "lat", "lon"],
                [n_levels, 3, 18, 36],
                dtype="float64",
                units="mol/mol",
            )
        },
    )
    dms = file_header(
        DMS,
        "inventory",
        ["dms"],
        model="NETCDF3_64BIT_OFFSET",
        axes=lonlat(36, 19, pole_centred=True, names=("longitude", "latitude"))
        | {"time": axis("time", [15.5], units="days since 2011-01-01 00:00:00")},
        variables={
            "conc": variable(
                ["time", "latitude", "longitude"],
                [1, 19, 36],
                stats=stats(1.5, 6.0),
                units="nmol L-1",
            )
        },
    )
    table = file_header(
        VOLCANO,
        "point_table",
        ["volcano"],
        reader="text",
        model=None,
        point_table={
            "label": "volcano",
            "columns": ["lat", "lon", "emission", "bottom", "top"],
            "units": ["degrees_north", "degrees_east", "kg S s-1", "m", "m"],
            "row_count": 1,
            "malformed_rows": 0,
            "ranges": {},
            "total_emission": 1.0,
            "rows": [[0.0, 0.0, 1.0, 10.0, 20.0]],
        },
    )
    return {
        "generated_utc": "2026-11-06T00:00:00+00:00",
        "python": "3.12.3",
        "extdata_root": "/x/GOCART/p8/ExtData",
        "catalog_digest": "0" * 64,
        "files": [ceds, gmi, dms, table],
    }


def catalog_doc() -> dict[str, object]:
    def read(
        path: str, role: str, categories: dict[str, list[str]]
    ) -> dict[str, object]:
        return {
            "path": path,
            "format": "point_ascii" if role == "point_table" else "netcdf",
            "role": role,
            "sources": {"x": 1},
            "categories": categories,
            "pets": [0],
            "read_count": 1,
            "first_seen": "20261105 232505.145",
            "point_count": None,
        }

    return {
        "generated_utc": "2026-11-06T00:00:00+00:00",
        "logs": ["PET000.ESMF_LogFile.log"],
        "line_shapes": ["catchem_file_read"],
        "files": [
            read("CATChem_new_config.yml", "config", {}),
            read(
                CEDS,
                "inventory",
                {"anthro1": ["2021-03-22T06:00:00"], "ship": ["2021-03-22T06:00:00"]},
            ),
            read(
                CEDS.replace("20210322", "20210323"),
                "inventory",
                {"anthro1": ["2021-03-23T00:00:00"], "ship": ["2021-03-23T00:00:00"]},
            ),
            read(GMI, "inventory", {"gmi": ["init"]}),
            read(DMS, "inventory", {"dms": ["init"]}),
            read(VOLCANO, "point_table", {"volcano": ["2021-03-22T06:00:00"]}),
        ],
    }


@pytest.fixture
def inputs(tmp_path: Path) -> dict[str, Path]:
    paths = {
        "headers": tmp_path / "extdata_headers.json",
        "catalog": tmp_path / "file_catalog.json",
        "config": tmp_path / "CATChem_new_config.yml",
        "map": tmp_path / "CATChem_emission.yml",
        "base": tmp_path / "manifest.yaml",
        "out": tmp_path / "manifest_realistic.yaml",
    }
    paths["headers"].write_text(json.dumps(headers_doc()))
    paths["catalog"].write_text(json.dumps(catalog_doc()))
    paths["config"].write_text(yaml.safe_dump(CATCHEM_CONFIG))
    paths["map"].write_text(yaml.safe_dump(EMISSION_MAP))
    base = yaml.safe_load(get_settings().defaults_file.read_text())
    base["clock"]["n_levels"] = 5
    paths["base"].write_text(yaml.safe_dump(base))
    return paths


def load(paths: dict[str, Path]) -> rd.Inputs:
    return rd.load_inputs(
        paths["headers"], paths["catalog"], paths["config"], paths["map"], paths["base"]
    )


def test_build_mirrors_the_catalogued_inventories(
    inputs: dict[str, Path], caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger=log.ROOT_NAME):
        defaults = rd.build(load(inputs))
    assert set(defaults.categories) == {"anthro1", "ship", "gmi", "dms", "volcano"}
    assert (
        "point" in caplog.text
    )  # in the config, not in the catalog: left out with a warning
    assert set(defaults.grids) == {"inventory", "g36x18", "g36x19p"}
    assert defaults.clock.n_levels == 5
    assert defaults.met == mf.ManifestDefaults.from_yaml(inputs["base"]).met

    anthro = defaults.categories["anthro1"]
    assert anthro.file == Path(CEDS_TEMPLATE)
    assert anthro.cadence == "daily"
    assert anthro.grid == "g36x18"
    assert anthro.masked_by == "land"
    assert not anthro.is_3d
    assert anthro.layout is not None
    assert anthro.layout.model == "NETCDF4_CLASSIC"
    assert anthro.layout.fill_value == -1.0e20
    assert anthro.layout.daily_record_hour == 12.0
    assert anthro.layout.time_axis is None
    assert not anthro.layout.write_bounds_and_area
    so2 = anthro.variables["SO2"]
    assert so2.units == "kg m-2 s-1"
    assert so2.long_name == "SO2 anthro"
    assert so2.background == 2.0e-11
    assert len(so2.sources) == 3
    assert so2.sources[0].amplitude == pytest.approx(5.0e-10 - 2.0e-11)
    assert so2.seasonal is None
    assert so2.vertical is None
    ship = defaults.categories["ship"]
    assert ship.file == anthro.file
    assert ship.layout == anthro.layout
    assert (
        ship.variables["SO2_ship"].long_name == "Ship SO2"
    )  # from the map: the file has none

    gmi = defaults.categories["gmi"]
    assert gmi.is_3d
    assert gmi.level_order == "top_down"
    assert gmi.masked_by == "none"
    assert gmi.layout is not None
    assert gmi.layout.lev_before_time
    assert gmi.layout.dtype == "float64"
    assert gmi.layout.fill_value is None
    assert gmi.layout.time_axis is not None
    assert gmi.layout.time_axis.values == [15.5, 45.0, 74.5]
    oh = gmi.variables["oh"]
    assert oh.background == 0.0
    assert oh.sources == [
        mf.GaussianSource(
            lon=0.0,
            lat=0.0,
            sigma_deg=rd.SOURCE_SIGMA_DEG,
            amplitude=rd.FALLBACK_AMPLITUDE,
        )
    ]
    assert oh.seasonal is not None
    assert oh.vertical is not None
    assert "no statistics" in caplog.text

    dms = defaults.categories["dms"]
    assert dms.regrid_method == "neareststod"
    assert dms.cece_mapalgo == "nn"
    assert dms.masked_by == "ocean"
    assert dms.grid == "g36x19p"
    assert defaults.grids["g36x19p"].pole_centred
    assert defaults.grids["g36x19p"].nlat == 19
    assert dms.layout is not None
    assert (dms.layout.lon_name, dms.layout.lat_name, dms.layout.model) == (
        "longitude",
        "latitude",
        "NETCDF3_64BIT_OFFSET",
    )
    assert len(dms.variables["conc"].sources) == 2

    volcano = defaults.categories["volcano"]
    assert volcano.is_point_table
    assert volcano.file == Path(VOLCANO_TEMPLATE)
    assert volcano.variables == {}

    defaults.to_yaml(inputs["out"])
    assert mf.ManifestDefaults.from_yaml(inputs["out"]) == defaults


def test_grid_from_axes() -> None:
    lon = xd.AxisSummary.model_validate(axis("lon", [179.5, 0.5, -179.5]))
    lat = xd.AxisSummary.model_validate(axis("lat", [-30.0, 30.0]))
    grid = rd.grid_from_axes(lon, lat)
    assert (
        grid.nlon,
        grid.dlon,
        grid.lon_first,
        grid.lat_first,
        grid.dlat,
        grid.nlat,
    ) == (3, 120.0, -179.5, -30.0, 60.0, 2)
    assert not grid.pole_centred
    with pytest.raises(rd.BuildError, match="single value"):
        rd.grid_from_axes(lon, xd.AxisSummary.model_validate(axis("lat", [0.0])))


def test_refusals_are_named(inputs: dict[str, Path]) -> None:
    def rebuild(**changes: object) -> rd.Inputs:
        loaded = load(inputs)
        return loaded.model_copy(update=changes)

    loaded = load(inputs)
    no_config = {k: v for k, v in loaded.extemis.items() if k != "ship"}
    with pytest.raises(rd.BuildError, match=r"'ship'.*no processes.extemis block"):
        rd.build(rebuild(extemis=no_config))
    headers = loaded.headers.model_copy(
        update={"files": [f for f in loaded.headers.files if f.path != GMI]}
    )
    with pytest.raises(rd.BuildError, match=r"'gmi'.*missing or in error"):
        rd.build(rebuild(headers=headers))
    thin_map = {k: v for k, v in loaded.emission_map.items() if k != "dms"}
    with pytest.raises(rd.BuildError, match="'dms' has no emission map entries"):
        rd.build(rebuild(emission_map=thin_map))
    wrong_var = dict(loaded.emission_map) | {
        "ship": {"SO4_ship": rd.EmissionMapEntry(long_name="x", units="kg/m2/s")}
    }
    with pytest.raises(rd.BuildError, match=r"'SO4_ship'.*not in"):
        rd.build(rebuild(emission_map=wrong_var))
    tall = loaded.base.model_copy(
        update={"clock": loaded.base.clock.model_copy(update={"n_levels": 127})}
    )
    with pytest.raises(rd.BuildError, match=r"'gmi'.*has 5 levels, the clock has 127"):
        rd.build(rebuild(base=tall))
    gmi_header = loaded.headers.by_path()[GMI]
    no_axes = gmi_header.model_copy(
        update={"axes": {k: v for k, v in gmi_header.axes.items() if k != "lat"}}
    )
    with pytest.raises(rd.BuildError, match=r"'gmi'.*no longitude/latitude axes"):
        rd.build(
            rebuild(
                headers=loaded.headers.model_copy(
                    update={
                        "files": [
                            no_axes if f.path == GMI else f
                            for f in loaded.headers.files
                        ]
                    }
                )
            )
        )
    odd_model = gmi_header.model_copy(update={"model": "NETCDF5"})
    with pytest.raises(rd.BuildError, match="unsupported NetCDF model"):
        rd.build(
            rebuild(
                headers=loaded.headers.model_copy(
                    update={
                        "files": [
                            odd_model if f.path == GMI else f
                            for f in loaded.headers.files
                        ]
                    }
                )
            )
        )


def test_command_line(
    inputs: dict[str, Path], caplog: pytest.LogCaptureFixture
) -> None:
    argv = [
        "--headers",
        str(inputs["headers"]),
        "--catalog",
        str(inputs["catalog"]),
        "--catchem-config",
        str(inputs["config"]),
        "--emission-map",
        str(inputs["map"]),
        "--base",
        str(inputs["base"]),
        "--out",
        str(inputs["out"]),
    ]
    assert rd.main(argv) == 0
    assert len(mf.ManifestDefaults.from_yaml(inputs["out"]).categories) == 5
    inputs["config"].write_text("processes: {}\n")
    with caplog.at_level(logging.ERROR, logger=log.ROOT_NAME):
        assert rd.main(argv) == 2
    assert "cannot build" in caplog.text
    inputs["config"].write_text(yaml.safe_dump(CATCHEM_CONFIG))
    unwritable = inputs["out"].parent / "dir_as_file"
    unwritable.mkdir()
    with caplog.at_level(logging.ERROR, logger=log.ROOT_NAME):
        assert rd.main([*argv[:-1], str(unwritable)]) == 1
    assert "cannot write" in caplog.text
    parser = rd.build_parser()
    defaults = parser.parse_args(["--catchem-config", "c", "--emission-map", "m"])
    settings = get_settings()
    assert (
        defaults.headers
        == settings.extdata_defaults_dir / settings.extdata_headers_filename
    )
    assert defaults.out == settings.realistic_defaults_file
