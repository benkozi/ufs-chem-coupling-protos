"""The CATChem files the generator emits: field mapping, emission map, science config."""

import inspect
from pathlib import Path

import pydantic
import pytest
import yaml

from tools import catchem_yaml as cy
from tools import gen_defaults as gd
from tools import manifest as mf
from tools.settings import get_settings

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def manifest() -> mf.Manifest:
    return mf.default_manifest(
        resolution="C48", out_dir=Path("data/C48"), command="test"
    )


@pytest.fixture
def defaults() -> gd.GenDefaults:
    return gd.GenDefaults.from_yaml(get_settings().gen_defaults_file)


def test_every_field_of_every_model_has_a_description() -> None:
    models = [
        cls
        for _, cls in inspect.getmembers(cy, inspect.isclass)
        if issubclass(cls, pydantic.BaseModel) and cls is not pydantic.BaseModel
    ]
    assert len(models) >= 8
    for model in models:
        assert model.model_config.get("extra") == "forbid", model.__name__
        for name, field in model.model_fields.items():
            assert field.description, f"{model.__name__}.{name} has no description"


def test_naming_convention() -> None:
    assert cy.standard_name("anthro", "NO") == "extemis_anthro_NO"
    assert cy.species_key("gmi", "oh") == "gmi_oh"
    assert cy.catchem_var("dust", "clayfrac") == "extemis/dust/clayfrac"


def test_emission_entries_follow_the_manifest(manifest: mf.Manifest) -> None:
    entries = cy.emission_entries(manifest)
    assert len(entries) == 20
    by_name = {e.standard_name: e for e in entries}
    no = by_name["extemis_anthro_NO"]
    assert no.catchem_var == "extemis/anthro/NO"
    assert no.dimensions == 3
    assert no.vertical_axis == "level"
    assert no.units == "kg m-2 s-1"
    assert no.optional
    assert no.advertise
    assert by_name["extemis_gmi_oh"].units == "cm-3"
    assert all(cy.is_emission_entry(e) for e in entries)
    assert "extemis_point_NO" not in by_name


def test_generated_entries_equal_the_checked_in_catchem_mapping(
    manifest: mf.Manifest,
) -> None:
    """The CATChem branch's mapping and the generator stay in step (the sync test of the appendix)."""
    base = cy.load_field_mapping(REPO_ROOT / get_settings().catchem_field_mapping)
    checked_in = [e for e in base.import_fields if cy.is_emission_entry(e)]
    assert [e.model_dump() for e in checked_in] == [
        e.model_dump() for e in cy.emission_entries(manifest)
    ]
    assert not any(cy.is_emission_entry(e) for e in base.export_fields)


def test_merged_mapping_keeps_the_base_order_and_is_idempotent(
    manifest: mf.Manifest, tmp_path: Path
) -> None:
    base = cy.load_field_mapping(REPO_ROOT / get_settings().catchem_field_mapping)
    met_entries = [e for e in base.import_fields if not cy.is_emission_entry(e)]
    merged = cy.merged_mapping(base, manifest)
    assert [e.standard_name for e in merged.import_fields[: len(met_entries)]] == [
        e.standard_name for e in met_entries
    ]
    assert len(merged.import_fields) == len(met_entries) + 20
    assert merged.export_fields == base.export_fields
    again = cy.merged_mapping(merged, manifest)
    assert again == merged
    path = tmp_path / "mapping.yml"
    merged.to_yaml(path)
    reloaded = cy.load_field_mapping(path)
    assert reloaded == merged
    doc = yaml.safe_load(path.read_text())
    assert "host_tracer_name" not in doc["import_fields"][0], (
        "unset keys stay out of the file"
    )


def test_emission_map_targets_are_species_or_met_inputs(
    manifest: mf.Manifest, defaults: gd.GenDefaults
) -> None:
    emission_map = cy.emission_map(manifest, defaults)
    species = cy.species_names(REPO_ROOT / get_settings().catchem_species_file)
    assert "so2" in species
    for category, variables in emission_map.categories.items():
        assert set(variables) == set(manifest.categories[category].variables)
        for name, variable in variables.items():
            assert variable.units == manifest.categories[category].variables[name].units
            for target in variable.map:
                assert target in species or target.startswith("MET_"), (
                    f"{category}/{name} -> {target}"
                )
    assert emission_map.categories["dust"]["clayfrac"].map == ["MET_CLAYFRAC"]
    assert emission_map.categories["fire"]["FRP"].map == []
    doc = emission_map.to_mapping()
    assert doc["anthro"]["NO"]["map"] == ["no3"]
    assert cy.EmissionMap.from_mapping(doc) == emission_map


def test_unknown_map_target_is_rejected(
    manifest: mf.Manifest, defaults: gd.GenDefaults
) -> None:
    broken = defaults.model_copy(deep=True)
    broken.categories["anthro"].variables["NO"].map = ["unobtainium"]
    with pytest.raises(cy.ConfigError, match="unobtainium"):
        cy.check_emission_targets(cy.emission_map(manifest, broken), {"so2"})


def test_science_config_switches_the_gridded_categories_to_imports(
    manifest: mf.Manifest, defaults: gd.GenDefaults, tmp_path: Path
) -> None:
    config = cy.science_config(
        manifest,
        defaults,
        species_file=Path("/abs/species.yml"),
        emission_file=Path("/abs/emission.yml"),
    )
    doc = yaml.safe_load(yaml.safe_dump(config.to_mapping()))
    assert doc["simulation"]["start_date"] == "20210322 0600"
    assert doc["simulation"]["end_date"] == "20210323 0600"
    assert doc["simulation"]["species_filename"] == "/abs/species.yml"
    assert doc["simulation"]["emission_filename"] == "/abs/emission.yml"
    assert doc["grid"]["number_of_levels"] == 127
    assert doc["timesteps"]["transport_timestep_in_s"] == 720
    assert doc["run_phases"]["proto"]["processes"] == ["seasalt"]
    assert doc["processes"]["seasalt"]["scheme"] == "geos12"
    ext = doc["processes"]["extemis"]
    assert ext["activate"] is True
    assert ext["point"] == {"activate": False, "gridded": False}
    for name in manifest.categories:
        block = ext[name]
        assert block["activate"] is True
        assert "source_file" not in block
        assert block["time_interpolation"] == "none"
        assert block["reverse_vertical"] is False
        assert block["gridded"] is True
    assert ext["gmi"]["is_2d"] is False
    assert ext["anthro"]["is_2d"] is True
    assert ext["gmi"]["apply_method"] == "replace"
    assert ext["anthro"]["apply_method"] == "add"
    assert ext["dust"]["frequency"] == "static"
    assert ext["anthro"]["regrid_method"] == "conserve"
    assert ext["fire"]["vertical_dist"] == "Ppbl"
    path = tmp_path / "CATChem_new_config.yml"
    config.to_yaml(path)
    reloaded = cy.ScienceConfig.from_mapping(yaml.safe_load(path.read_text()))
    assert reloaded == config


def test_science_config_can_name_the_manifest_files(
    manifest: mf.Manifest, defaults: gd.GenDefaults
) -> None:
    config = cy.science_config(
        manifest,
        defaults,
        species_file=Path("/s.yml"),
        emission_file=Path("/e.yml"),
        source_files=True,
        data_root=Path("/root"),
    )
    doc = yaml.safe_load(yaml.safe_dump(config.to_mapping()))
    assert (
        doc["processes"]["extemis"]["anthro"]["source_file"]
        == "/root/data/C48/inventories/anthro.nc"
    )


def test_science_config_rejects_unknown_extemis_keys() -> None:
    with pytest.raises(pydantic.ValidationError, match="colour"):
        cy.ScienceConfig.from_mapping(
            {"processes": {"extemis": {"activate": True, "anthro": {"colour": 1}}}}
        )


def test_a_variable_without_a_map_entry_is_rejected(
    manifest: mf.Manifest, defaults: gd.GenDefaults
) -> None:
    broken = defaults.model_copy(deep=True)
    del broken.categories["anthro"].variables["NO"]
    with pytest.raises(cy.ConfigError, match="anthro/NO"):
        cy.emission_map(manifest, broken)
