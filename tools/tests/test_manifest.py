"""The run manifest: the contract between the mock data step and the later tasks."""

import hashlib
import inspect
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pydantic
import pytest
import yaml

from tools import manifest as mf
from tools.settings import get_settings

START = datetime(2021, 3, 22, 6, tzinfo=UTC)


def make(
    out_dir: Path,
    *,
    start: datetime | None = None,
    step_seconds: int | None = None,
    inventory_resolution_deg: float | None = None,
) -> mf.Manifest:
    return mf.default_manifest(
        resolution="C48",
        out_dir=out_dir,
        command="test",
        start=start,
        step_seconds=step_seconds,
        inventory_resolution_deg=inventory_resolution_deg,
    )


def test_every_field_of_every_model_has_a_description() -> None:
    models = [
        cls
        for _, cls in inspect.getmembers(mf, inspect.isclass)
        if issubclass(cls, pydantic.BaseModel) and cls is not pydantic.BaseModel
    ]
    assert len(models) >= 10
    for model in models:
        assert model.model_config.get("extra") == "forbid", model.__name__
        for name, field in model.model_fields.items():
            assert field.description, f"{model.__name__}.{name} has no description"


def test_default_manifest_is_complete(tmp_path: Path) -> None:
    m = make(tmp_path)
    assert m.resolution == "C48"
    assert m.n == 48
    assert m.clock.start == START
    assert m.clock.end == datetime(2021, 3, 23, 6, tzinfo=UTC)
    assert m.clock.n_steps == 120
    assert set(m.categories) == set(mf.CATEGORY_NAMES)
    assert set(m.grids) == {"inventory", "gmi"}
    assert m.categories["gmi"].is_3d
    assert m.categories["gmi"].grid == "gmi"
    assert m.categories["fengsha"].cadence == "monthly"
    assert m.categories["dust"].cadence == "static"
    assert len(m.fix.grid_tiles) == 6
    assert len(m.fix.grid_spec_tiles) == 6
    assert m.fix.mosaic == tmp_path / "fix" / "C48_mosaic.nc"
    assert m.land_mask.file == tmp_path / "inventories" / "landmask.nc"


def test_cece_mapalgo_is_derived_from_the_regrid_method(tmp_path: Path) -> None:
    m = make(tmp_path)
    assert m.categories["anthro"].cece_mapalgo == "consd"
    assert m.categories["dust"].cece_mapalgo == "bilinear"
    none = m.categories["dust"].model_copy(
        update={"regrid_method": "none", "cece_mapalgo": None}
    )
    assert mf.Category.model_validate(none.model_dump()).cece_mapalgo == "passthrough"
    bad = m.categories["anthro"].model_dump() | {"cece_mapalgo": "bilinear"}
    with pytest.raises(pydantic.ValidationError, match="cece_mapalgo"):
        mf.Category.model_validate(bad)


def test_yaml_round_trip_is_lossless(tmp_path: Path) -> None:
    m = make(tmp_path)
    path = tmp_path / "manifest.yaml"
    m.to_yaml(path)
    again = mf.Manifest.from_yaml(path)
    assert again == m
    assert again.model_dump(mode="json") == m.model_dump(mode="json")


def test_unknown_keys_are_rejected_at_every_level(tmp_path: Path) -> None:
    m = make(tmp_path)
    path = tmp_path / "manifest.yaml"
    m.to_yaml(path)
    doc = yaml.safe_load(path.read_text())
    top = dict(doc, surprise=1)
    with pytest.raises(pydantic.ValidationError, match="surprise"):
        mf.Manifest.model_validate(top)
    doc["categories"]["anthro"]["variables"]["NO"]["colour"] = "red"
    with pytest.raises(pydantic.ValidationError, match="colour"):
        mf.Manifest.model_validate(doc)


def test_naive_start_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(pydantic.ValidationError, match="timezone"):
        make(tmp_path, start=datetime(2021, 3, 22, 6))  # noqa: DTZ001  (the point of the test)


def test_step_must_divide_the_duration(tmp_path: Path) -> None:
    with pytest.raises(pydantic.ValidationError, match="divide"):
        make(tmp_path, step_seconds=700)


def test_grid_must_cover_the_globe() -> None:
    with pytest.raises(pydantic.ValidationError, match="360"):
        mf.LatLonGrid(
            dlon=1.0, dlat=1.0, lon_first=-179.5, lat_first=-89.5, nlon=359, nlat=180
        )
    with pytest.raises(pydantic.ValidationError, match="90"):
        mf.LatLonGrid(
            dlon=1.0, dlat=1.0, lon_first=-179.5, lat_first=-89.5, nlon=360, nlat=181
        )


def test_category_grid_must_exist(tmp_path: Path) -> None:
    doc = make(tmp_path).model_dump(mode="json")
    doc["categories"]["dms"]["grid"] = "nope"
    with pytest.raises(pydantic.ValidationError, match="unknown grid"):
        mf.Manifest.model_validate(doc)
    doc = make(tmp_path).model_dump(mode="json")
    doc["land_mask"]["grid"] = "nope"
    with pytest.raises(
        pydantic.ValidationError, match="land_mask references unknown grid"
    ):
        mf.Manifest.model_validate(doc)


def test_inventory_resolution_override(tmp_path: Path) -> None:
    m = make(tmp_path, inventory_resolution_deg=2.0)
    g = m.grids["inventory"]
    assert (g.nlon, g.nlat, g.lon_first, g.lat_first) == (180, 90, -179.0, -89.0)
    with pytest.raises(pydantic.ValidationError, match="360"):
        make(tmp_path, inventory_resolution_deg=7.0)


def test_c96_paths(tmp_path: Path) -> None:
    m = mf.default_manifest(resolution="C96", out_dir=tmp_path, command="x")
    assert m.n == 96
    assert m.fix.grid_tiles[5].name == "C96_grid.tile6.nc"
    assert m.fix.grid_spec_tiles[0].name == "C96_grid_spec.tile1.nc"
    assert m.fix.source_url == get_settings().fix_url.format(resolution="C96")


# --- the met section (task 3) ----------------------------------------------------------------------------------


def test_met_params_carry_what_the_met_component_reads(tmp_path: Path) -> None:
    met = make(tmp_path).met
    assert met.n_soil_layers == 4
    assert met.pressure_top_pa < met.surface_pressure_pa
    assert met.roughness_length_ocean_cm < met.roughness_length_land_cm
    names = [t.name for t in met.tracers]
    assert names[0] == "sphum"
    # task 6: the species the emission map applies with `add`, plus the three gmi oxidants; dms_in is
    # CATChem-internal (neither gas nor aerosol in the species file, so the cap would reject it as a tracer)
    assert names[1:] == [
        "seas1",
        "seas2",
        "seas3",
        "seas4",
        "seas5",
        "so2",
        "no3",
        "oh",
        "h2o2",
        "so4",
        "oc1",
        "oc2",
        "bc1",
        "bc2",
    ]
    assert "dms_in" not in names
    assert all(t.units for t in met.tracers)
    assert {t.units for t in met.tracers} == {"kg/kg", "ug/kg"}


def test_tracers_need_sphum_exactly_once_and_unique_names(tmp_path: Path) -> None:
    doc = make(tmp_path).met.model_dump(mode="json")
    doc["tracers"] = [{"name": "seas1", "units": "ug/kg"}]
    with pytest.raises(pydantic.ValidationError, match="sphum"):
        mf.MetParams.model_validate(doc)
    doc["tracers"] = [
        {"name": "sphum", "units": "kg/kg"},
        {"name": "SPHUM", "units": "kg/kg"},
    ]
    with pytest.raises(pydantic.ValidationError, match="unique"):
        mf.MetParams.model_validate(doc)
    doc["tracers"] = []
    with pytest.raises(pydantic.ValidationError, match="tracers"):
        mf.MetParams.model_validate(doc)


def test_pressure_top_must_be_below_the_surface_pressure(tmp_path: Path) -> None:
    doc = make(tmp_path).met.model_dump(mode="json")
    doc["pressure_top_pa"] = doc["surface_pressure_pa"]
    with pytest.raises(pydantic.ValidationError, match="pressure_top_pa"):
        mf.MetParams.model_validate(doc)


# --- the defaults file -------------------------------------------------------------------------------------

# sha256 of the canonical JSON dump of ManifestDefaults from tools/defaults/manifest.yaml. Task 2's value
# (4a9c0448…) equalled the defaults reviewed in PR #4's first commit (51f1cea, Python literals) except for four
# amplitudes rounded to their intended literals; task 3 added the twenty `met` parameters the MET component
# reads (soil layers, column, surface, geography, tracers); task 6 added nine host tracers (so2, no3, oh, h2o2,
# so4, oc1, oc2, bc1, bc2). Issue #17 changed the dump (1327c798… -> 4b4d6887…) without touching the file: the
# models grew `pole_centred` and `layout`, whose defaults now appear in the dump; DEFAULTS_FILE_DIGEST pins the
# bytes of the file itself.
DEFAULTS_DIGEST = "4b4d688783d9fdae015f8ebcfaabc79bfa28609778228378e0d2eda0982377c0"
DEFAULTS_FILE_DIGEST = (
    "2b484572253b9d41fce2850b80a2cd05ac9d3b81773048a55523d0d936ca5043"
)


def test_checked_in_defaults_validate_and_are_pinned() -> None:
    defaults = mf.ManifestDefaults.from_yaml(get_settings().defaults_file)
    canonical = json.dumps(
        defaults.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    )
    assert hashlib.sha256(canonical.encode()).hexdigest() == DEFAULTS_DIGEST
    file_digest = hashlib.sha256(get_settings().defaults_file.read_bytes()).hexdigest()
    assert file_digest == DEFAULTS_FILE_DIGEST
    assert not defaults.land_mask.file.is_absolute()
    assert all(not c.file.is_absolute() for c in defaults.categories.values())


def test_default_manifest_roots_relative_paths_and_keeps_absolute_ones(
    tmp_path: Path,
) -> None:
    m = make(tmp_path)
    assert m.categories["gmi"].file == tmp_path / "inventories" / "gmi.nc"
    assert m.inventories_dir == tmp_path / "inventories"
    custom = tmp_path / "defaults.yaml"
    doc = yaml.safe_load(get_settings().defaults_file.read_text())
    doc["categories"]["dms"]["file"] = str(tmp_path / "elsewhere" / "dms.nc")
    doc["clock"]["step_seconds"] = 1800
    custom.write_text(yaml.safe_dump(doc))
    m2 = mf.default_manifest(
        resolution="C48", out_dir=tmp_path / "out", command="x", defaults_file=custom
    )
    assert m2.categories["dms"].file == tmp_path / "elsewhere" / "dms.nc"
    assert (
        m2.categories["anthro"].file == tmp_path / "out" / "inventories" / "anthro.nc"
    )
    assert m2.clock.step_seconds == 1800


def test_defaults_file_from_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    custom = tmp_path / "d.yaml"
    shutil.copy(get_settings().defaults_file, custom)
    monkeypatch.setenv("PROTOS_DEFAULTS", str(custom))
    get_settings.cache_clear()
    assert get_settings().defaults_file == custom
    assert make(tmp_path).clock.n_levels == 127


def test_a_broken_defaults_file_is_a_validation_error(tmp_path: Path) -> None:
    broken = tmp_path / "broken.yaml"
    doc = yaml.safe_load(get_settings().defaults_file.read_text())
    doc["met"]["colour"] = "red"
    broken.write_text(yaml.safe_dump(doc))
    with pytest.raises(pydantic.ValidationError, match="colour"):
        mf.default_manifest(
            resolution="C48", out_dir=tmp_path, command="x", defaults_file=broken
        )


def test_strict_models_round_trip_through_yaml_generically(tmp_path: Path) -> None:
    grid = mf.LatLonGrid(
        dlon=2.0, dlat=2.0, lon_first=-179.0, lat_first=-89.0, nlon=180, nlat=90
    )
    grid.to_yaml(tmp_path / "sub" / "grid.yaml")
    assert mf.LatLonGrid.from_yaml(tmp_path / "sub" / "grid.yaml") == grid


def test_default_tracers_are_gas_or_aerosol_species_with_matching_units(
    tmp_path: Path,
) -> None:
    """The cap requires a mechanism tracer to be exactly one of gas (kg/kg, ppm) or aerosol (ug/kg, kg/kg, ppm)."""
    path = Path(__file__).resolve().parents[2] / get_settings().catchem_species_file
    species = {s["name"]: s for s in yaml.safe_load(path.read_text())}
    for tracer in make(tmp_path).met.tracers:
        if tracer.name == "sphum":
            continue
        entry = species[tracer.name]
        gas = bool(entry.get("__is_gas", False))
        aerosol = bool(entry.get("__is_aerosol", False))
        assert gas != aerosol, f"{tracer.name}: gas={gas} aerosol={aerosol}"
        assert tracer.units == ("kg/kg" if gas else "ug/kg"), tracer.name


# --- the realistic profile's schema (issue #17) ------------------------------------------------------------


def test_any_category_name_is_allowed_but_shared_files_must_agree(
    tmp_path: Path,
) -> None:
    doc = make(tmp_path).model_dump(mode="json")
    doc["categories"]["ship"] = dict(doc["categories"]["dms"], file="ship.nc")
    assert "ship" in mf.Manifest.model_validate(doc).categories
    # two categories writing the same file must agree on grid, cadence, levels and layout, and not share names
    doc = make(tmp_path).model_dump(mode="json")
    anthro2 = dict(doc["categories"]["anthro"])
    anthro2["variables"] = {"SO2_elev": doc["categories"]["anthro"]["variables"]["SO2"]}
    doc["categories"]["anthro2"] = anthro2
    m = mf.Manifest.model_validate(doc)
    assert m.categories_by_file()[m.categories["anthro"].file] == ["anthro", "anthro2"]
    doc["categories"]["anthro2"]["cadence"] = "monthly"
    with pytest.raises(pydantic.ValidationError, match="share the file"):
        mf.Manifest.model_validate(doc)
    doc["categories"]["anthro2"]["cadence"] = "hourly"
    doc["categories"]["anthro2"]["variables"] = {
        "SO2": anthro2["variables"]["SO2_elev"]
    }
    with pytest.raises(
        pydantic.ValidationError, match=r"share the file .* and a variable name"
    ):
        mf.Manifest.model_validate(doc)


def test_neareststod_maps_to_nn() -> None:
    doc = {
        "file": "x.nc",
        "cadence": "static",
        "regrid_method": "neareststod",
        "grid": "g",
        "is_3d": False,
        "masked_by": "none",
        "variables": {
            "v": {"units": "1", "long_name": "v", "background": 1.0, "sources": []}
        },
    }
    assert mf.Category.model_validate(doc).cece_mapalgo == "nn"
    with pytest.raises(pydantic.ValidationError, match="cece_mapalgo"):
        mf.Category.model_validate(doc | {"cece_mapalgo": "consd"})


def daily_category(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "file": "ExtData/CEDS/v2019/%y4/CEDS.2019.emis.%y4%m2%d2.nc",
        "cadence": "daily",
        "regrid_method": "conserve",
        "grid": "inventory",
        "is_3d": False,
        "masked_by": "land",
        "variables": {
            "SO2": {
                "units": "kg m-2 s-1",
                "long_name": "s",
                "background": 0.0,
                "sources": [],
            }
        },
        "layout": {
            "model": "NETCDF3_64BIT_OFFSET",
            "write_bounds_and_area": False,
            "fill_value": -1.0e20,
        },
    }
    return base | overrides


def test_daily_categories_expand_their_date_tokens(tmp_path: Path) -> None:
    clock = make(tmp_path).clock  # 2021-03-22T06Z, 24 h
    category = mf.Category.model_validate(daily_category())
    assert category.files_for(clock) == [
        Path("ExtData/CEDS/v2019/2021/CEDS.2019.emis.20210322.nc"),
        Path("ExtData/CEDS/v2019/2021/CEDS.2019.emis.20210323.nc"),
    ]
    midnight = clock.model_copy(update={"start": datetime(2021, 3, 22, tzinfo=UTC)})
    assert len(category.files_for(midnight)) == 1
    assert mf.expand_date_tokens(
        Path("a/%y4/%m2/%d2.nc"), datetime(2021, 3, 2, tzinfo=UTC).date()
    ) == Path("a/2021/03/02.nc")
    static = mf.Category.model_validate(
        daily_category(cadence="static", file="dust.nc")
    )
    assert static.files_for(clock) == [Path("dust.nc")]
    with pytest.raises(pydantic.ValidationError, match="date tokens"):
        mf.Category.model_validate(daily_category(file="no_tokens.nc"))
    with pytest.raises(pydantic.ValidationError, match="date tokens"):
        mf.Category.model_validate(daily_category(cadence="hourly"))


def test_layout_rules() -> None:
    layout = mf.FileLayout()
    assert (layout.model, layout.dtype, layout.lon_name, layout.daily_record_hour) == (
        "NETCDF4",
        "float32",
        "lon",
        12.0,
    )
    assert layout.time_axis is None
    table = mf.Category.model_validate(
        daily_category(
            file="ExtData/volcanic/so2_volcanic_emissions_Carns.%y4%m2%d2.rc",
            variables={},
            layout={"format": "point_table"},
        )
    )
    assert table.is_point_table
    with pytest.raises(pydantic.ValidationError, match="no variables"):
        mf.Category.model_validate(daily_category(layout={"format": "point_table"}))
    with pytest.raises(pydantic.ValidationError, match="not 3-D"):
        mf.Category.model_validate(
            daily_category(variables={}, is_3d=True, layout={"format": "point_table"})
        )
    with pytest.raises(pydantic.ValidationError, match="at least one variable"):
        mf.Category.model_validate(daily_category(variables={}))
    axis = {"units": "days since 2010-01-01 00:00:00", "values": [15.5, 45.0]}
    with pytest.raises(pydantic.ValidationError, match="explicit time_axis"):
        mf.Category.model_validate(daily_category(layout={"time_axis": axis}))
    monthly = mf.Category.model_validate(
        daily_category(cadence="monthly", file="htap.nc4", layout={"time_axis": axis})
    )
    assert monthly.layout is not None
    assert monthly.layout.time_axis is not None
    assert monthly.layout.time_axis.calendar == "standard"
    assert not monthly.is_point_table


def test_pole_centred_grids_and_coarsening() -> None:
    with pytest.raises(pydantic.ValidationError, match="90"):
        mf.LatLonGrid(
            dlon=1.0, dlat=1.0, lon_first=-180.0, lat_first=-90.0, nlon=360, nlat=181
        )
    merra = mf.LatLonGrid(
        dlon=1.0,
        dlat=1.0,
        lon_first=-180.0,
        lat_first=-90.0,
        nlon=360,
        nlat=181,
        pole_centred=True,
    )
    coarse = merra.coarsened(10)
    assert (coarse.nlon, coarse.nlat, coarse.dlon, coarse.dlat) == (36, 19, 10.0, 10.0)
    assert (coarse.lon_first, coarse.lat_first, coarse.pole_centred) == (
        -180.0,
        -90.0,
        True,
    )
    regular = mf.inventory_grid(0.1)
    ten = regular.coarsened(10)
    assert (ten.nlon, ten.nlat, ten.lon_first, ten.lat_first) == (
        360,
        180,
        -179.5,
        -89.5,
    )
    assert regular.coarsened(1) is regular
    with pytest.raises(ValueError, match="does not divide"):
        regular.coarsened(7)
    with pytest.raises(ValueError, match="does not divide"):
        merra.coarsened(7)
    with pytest.raises(ValueError, match="at least 1"):
        regular.coarsened(0)


def test_grid_factor_reaches_every_grid(tmp_path: Path) -> None:
    m = mf.default_manifest(
        resolution="C48", out_dir=tmp_path, command="x", grid_factor=2
    )
    assert m.grids["inventory"].nlon == 180
    assert m.grids["gmi"].nlon == 72
    with pytest.raises(ValueError, match="does not divide"):
        mf.default_manifest(
            resolution="C48", out_dir=tmp_path, command="x", grid_factor=7
        )
