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
    assert m.fix.source_url == get_settings().fix_url.format(resolution="C96")


# --- the defaults file -------------------------------------------------------------------------------------

# sha256 of the canonical JSON dump of ManifestDefaults from tools/defaults/manifest.yaml. It equals the
# defaults reviewed in the PR's first commit (51f1cea, Python literals) except for four amplitudes whose
# computed values (2e-11 * 1.25 and the like) were rounded to the intended literals (one ULP, 1.3e-16 relative).
DEFAULTS_DIGEST = "4a9c04487f21475f05961112ed0b351d664d30d0ea0bc11ae0deae24af47a93c"


def test_checked_in_defaults_validate_and_are_pinned() -> None:
    defaults = mf.ManifestDefaults.from_yaml(get_settings().defaults_file)
    canonical = json.dumps(
        defaults.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    )
    assert hashlib.sha256(canonical.encode()).hexdigest() == DEFAULTS_DIGEST
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
