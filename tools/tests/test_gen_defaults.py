"""The generator's parameter set: tools/defaults/gen_configs.yaml and its models."""

import hashlib
import inspect
import json
from pathlib import Path

import pydantic
import pytest
import yaml

from tools import gen_defaults as gd
from tools import manifest as mf
from tools.settings import get_settings

# sha256 of the canonical JSON dump of GenDefaults from tools/defaults/gen_configs.yaml (task 5; task 6 added
# hosted_imports anthro/SO2, `CECE -> MET` in run_sequence, catchem_units, diagnostics off, Verbosity low; the
# performance task cece_amio; issue #18 the model defaults scale_factor, inactive and time_interpolation, the file unchanged;
# issue #16 the labels ATM and CHM and the weather model's two-phase order in run_sequence, and the
# model field wm_driver, null here).
GEN_DEFAULTS_DIGEST = "8e4d5e8212a8af8965a198820aaada2ae2e33cf2de84792a0aa664d76abd42c5"


def test_every_field_of_every_model_has_a_description() -> None:
    models = [
        cls
        for _, cls in inspect.getmembers(gd, inspect.isclass)
        if issubclass(cls, pydantic.BaseModel) and cls is not pydantic.BaseModel
    ]
    assert len(models) >= 4
    for model in models:
        assert model.model_config.get("extra") == "forbid", model.__name__
        for name, field in model.model_fields.items():
            assert field.description, f"{model.__name__}.{name} has no description"


def test_checked_in_defaults_cover_every_manifest_category_and_variable() -> None:
    defaults = gd.GenDefaults.from_yaml(get_settings().gen_defaults_file)
    manifest_defaults = mf.ManifestDefaults.from_yaml(get_settings().defaults_file)
    assert set(defaults.categories) == set(manifest_defaults.categories)
    for name, category in manifest_defaults.categories.items():
        assert set(defaults.categories[name].variables) == set(category.variables), name
    assert defaults.categories["gmi"].apply_method == "replace"
    assert defaults.categories["anthro"].apply_method == "add"
    assert defaults.categories["fire"].vertical_dist == "Ppbl"
    assert defaults.processes == ["seasalt"]
    # The ufs-weather-model's sequence with CECE (ATM's two run phases around the chemistry), plus the one
    # connector that feeds ATM's hosted totals.
    assert defaults.run_sequence == [
        "ATM phase1",
        "CECE",
        "ATM -> CHM",
        "CECE -> CHM",
        "CHM",
        "CHM -> ATM",
        "CECE -> ATM",
        "ATM phase2",
    ]
    assert defaults.cece_amio == gd.CeceAmio(
        worker_threads=1,
        staging_buffer_count=2,
        staging_buffer_capacity_bytes=4194304,
        prefetch_depth=1,
    )


def test_amio_sizes_are_positive() -> None:
    with pytest.raises(pydantic.ValidationError, match="greater than 0"):
        gd.CeceAmio(
            worker_threads=1,
            staging_buffer_count=0,
            staging_buffer_capacity_bytes=1,
            prefetch_depth=1,
        )


def test_checked_in_defaults_are_pinned() -> None:
    defaults = gd.GenDefaults.from_yaml(get_settings().gen_defaults_file)
    canonical = json.dumps(
        defaults.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    )
    assert hashlib.sha256(canonical.encode()).hexdigest() == GEN_DEFAULTS_DIGEST


def test_map_and_scale_lengths_must_agree() -> None:
    with pytest.raises(pydantic.ValidationError, match="scale"):
        gd.EmissionMapVariable(long_name="x", scale=[1.0, 2.0], map=["so2"])
    ok = gd.EmissionMapVariable(long_name="x", scale=[], map=[])
    assert ok.map == []


def test_unknown_keys_are_rejected(tmp_path: Path) -> None:
    doc = yaml.safe_load(get_settings().gen_defaults_file.read_text())
    doc["categories"]["anthro"]["colour"] = "red"
    path = tmp_path / "gen.yaml"
    path.write_text(yaml.safe_dump(doc))
    with pytest.raises(pydantic.ValidationError, match="colour"):
        gd.GenDefaults.from_yaml(path)


REALISTIC_GEN_DEFAULTS = Path("tools/defaults/gen_configs_realistic.yaml")


def test_scale_factor_and_inactive_defaults() -> None:
    plain = gd.CategoryDefaults(apply_method="add", variables={})
    assert plain.scale_factor == 1.0
    assert plain.inactive is False
    off = gd.CategoryDefaults(apply_method="add", variables={}, inactive=True)
    assert off.inactive
    with pytest.raises(pydantic.ValidationError, match="inactive"):
        gd.CategoryDefaults(
            apply_method="add",
            inactive=True,
            variables={
                "x": gd.EmissionMapVariable(long_name="x", scale=[1.0], map=["so2"])
            },
        )


def test_realistic_defaults_cover_every_realistic_manifest_variable() -> None:
    """Issue #18: the production categories, map and units of CATChem's config, for the realistic manifest."""
    defaults = gd.GenDefaults.from_yaml(REALISTIC_GEN_DEFAULTS)
    manifest = mf.ManifestDefaults.from_yaml(get_settings().realistic_defaults_file)
    assert set(defaults.categories) == set(manifest.categories)
    for name, category in manifest.categories.items():
        entry = defaults.categories[name]
        assert set(entry.variables) == set(category.variables), name
        for variable in category.variables.values():
            assert variable.units in defaults.catchem_units, (name, variable.units)
    assert defaults.categories["volcano"].inactive
    assert defaults.categories["fire"].scale_factor == 0.7778
    assert defaults.categories["fire"].plume_rise == "sofiev"
    assert defaults.categories["fire"].vertical_dist == "Ppbl"
    assert defaults.categories["anthro1"].vertical_dist == "P100"
    assert defaults.categories["anthro2"].vertical_dist == "P500"
    assert defaults.categories["so2_aviation_crs"].vertical_dist == "aviation_crs"
    assert defaults.categories["gmi"].apply_method == "replace"
    assert defaults.categories["dust"].apply_method == "replace"
    biomass = defaults.categories["fire"].variables["biomass"]
    assert biomass.map == ["so2", "oc1", "oc2", "bc1", "bc2"]
    assert biomass.scale == [1.0, 0.9, 0.9, 0.8, 0.2]
    so2 = defaults.categories["anthro1"].variables["SO2"]
    assert (so2.map, so2.scale) == (["so2", "so4"], [0.97, 0.045])
    assert defaults.categories["dust"].variables["sep"].map == ["MET_SSM"]
    assert defaults.categories["fengsha"].variables["PC"].map == ["MET_RDRAG"]
    assert [(h.category, h.variable) for h in defaults.hosted_imports] == [
        ("anthro1", "SO2"),
        ("dust", "sep"),
    ]
    # the production config's time_interpolation: absent (none) holds a record, linear blends two
    held = {n for n, c in defaults.categories.items() if c.time_interpolation == "none"}
    assert held == {"anthro1", "anthro2", "ship", "gmi"}
    assert (
        gd.CategoryDefaults(apply_method="add", variables={}).time_interpolation
        == "linear"
    )
    assert defaults.catchem_units["kg s-1 m-2"] == "kg/m2/s"
    assert defaults.catchem_units["nmol l-1"] == "nmol/l"
    assert defaults.catchem_units["-"] == "1"
    assert defaults.catchem_units["count"] == "1"


def test_the_two_defaults_files_agree_on_the_shared_keys() -> None:
    analytic = gd.GenDefaults.from_yaml(get_settings().gen_defaults_file)
    realistic = gd.GenDefaults.from_yaml(REALISTIC_GEN_DEFAULTS)
    for key in (
        "prototype",
        "verbosity",
        "processes",
        "seasalt_scheme",
        "run_sequence",
        "cece_amio",
        "diagnostics",
    ):
        assert getattr(analytic, key) == getattr(realistic, key), key
