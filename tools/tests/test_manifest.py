"""The run manifest: the contract between the mock data step and the later tasks."""

import inspect
from datetime import UTC, datetime
from pathlib import Path

import pydantic
import pytest
import yaml

from tools import manifest as mf

START = datetime(2021, 3, 22, 6, tzinfo=UTC)


def make(
    out_dir: Path,
    *,
    start: datetime = mf.DEFAULT_START,
    step_seconds: int = 720,
    inventory_resolution_deg: float = 1.0,
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
    mf.write_manifest(m, path)
    again = mf.read_manifest(path)
    assert again == m
    assert again.model_dump(mode="json") == m.model_dump(mode="json")


def test_unknown_keys_are_rejected_at_every_level(tmp_path: Path) -> None:
    m = make(tmp_path)
    path = tmp_path / "manifest.yaml"
    mf.write_manifest(m, path)
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
    doc["categories"]["bogus"] = doc["categories"]["dms"]
    with pytest.raises(pydantic.ValidationError, match="bogus"):
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
    assert m.fix.source_url.endswith("/C96/")
