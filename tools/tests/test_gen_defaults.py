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

# sha256 of the canonical JSON dump of GenDefaults from tools/defaults/gen_configs.yaml (task 5).
GEN_DEFAULTS_DIGEST = "7dc851b3712ca47f4987d916dd08ce9031b0b0f5d295f5d36e362ee1cdb7e981"


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
    assert defaults.run_sequence[0] == "CECE"
    assert defaults.run_sequence[-1] == "CATCHEM -> MET"


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
