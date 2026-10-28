"""The driver configuration (ufs.configure shape) and the field dictionary the generator emits."""

import inspect
from pathlib import Path

import pydantic
import pytest
import yaml

from tools import driver_config as dc
from tools import manifest as mf
from tools.settings import get_settings

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def manifest() -> mf.Manifest:
    return mf.default_manifest(
        resolution="C48", out_dir=Path("data/C48"), command="test"
    )


def driver(**overrides: object) -> dc.DriverConfig:
    base: dict[str, object] = {
        "prototype": "cece_met_catchem",
        "components": ["CECE", "MET", "CATCHEM"],
        "pets": 6,
        "verbosity": "high",
        "manifest": Path("/root/data/C48/manifest.yaml"),
        "data_root": Path("/root"),
        "field_dictionary": Path("/out/fd.yaml"),
        "catchem_config_file": Path("/out/CATChem_new_config.yml"),
        "catchem_field_mapping_file": Path("/out/CATChem_field_mapping.yml"),
        "cece_config_file": Path("/out/cece.yaml"),
        "step_seconds": 720,
        "run_sequence": [
            "CECE",
            "MET",
            "CECE -> CATCHEM",
            "MET -> CATCHEM",
            "CATCHEM",
            "CATCHEM -> MET",
        ],
    }
    return dc.DriverConfig.model_validate(base | overrides)


def test_every_field_of_every_model_has_a_description() -> None:
    models = [
        cls
        for _, cls in inspect.getmembers(dc, inspect.isclass)
        if issubclass(cls, pydantic.BaseModel) and cls is not pydantic.BaseModel
    ]
    assert len(models) >= 4
    for model in models:
        assert model.model_config.get("extra") == "forbid", model.__name__
        for name, field in model.model_fields.items():
            assert field.description, f"{model.__name__}.{name} has no description"


def test_rendered_driver_config(tmp_path: Path) -> None:
    text = driver().render()
    assert "prototype: cece_met_catchem\n" in text
    assert "EARTH_component_list: CECE MET CATCHEM\n" in text
    for prefix in ("CECE", "MET", "CATCHEM"):
        assert f"{prefix}_petlist_bounds: 0 5\n" in text
        assert f"{prefix}_attributes::\n  Verbosity = high\n::\n" in text
    assert "manifest: /root/data/C48/manifest.yaml\n" in text
    assert "data_root: /root\n" in text
    assert "field_dictionary: /out/fd.yaml\n" in text
    assert "cece_config_file: /out/cece.yaml\n" in text
    assert "grid_source: mosaic\n" in text
    assert "regdecomp_per_tile" not in text
    assert "met_hosted_imports" not in text
    assert (
        "runSeq::\n  @720\n    CECE\n    MET\n    CECE -> CATCHEM\n    MET -> CATCHEM\n    CATCHEM\n    CATCHEM -> MET\n  @\n::\n"
        in text
    )
    path = tmp_path / "esm.configure"
    driver().write(path)
    assert path.read_text() == text


def test_regdecomp_is_written_when_given() -> None:
    text = driver(pets=12, regdecomp=(1, 2)).render()
    assert "regdecomp_per_tile: 1 2\n" in text
    assert "CECE_petlist_bounds: 0 11\n" in text


def test_driver_config_rules() -> None:
    with pytest.raises(pydantic.ValidationError, match="multiple of 6"):
        driver(pets=4)
    with pytest.raises(pydantic.ValidationError, match="regdecomp"):
        driver(pets=12, regdecomp=(1, 1))
    with pytest.raises(pydantic.ValidationError, match="components"):
        driver(components=["CECE", "OCN"])


def test_field_dictionary_gains_the_emission_entries(
    manifest: mf.Manifest, tmp_path: Path
) -> None:
    base = dc.load_field_dictionary(REPO_ROOT / get_settings().base_field_dictionary)
    met_entries = [
        e
        for e in base.field_dictionary.entries
        if not e.standard_name.startswith("extemis_")
    ]
    assert len(met_entries) == 47
    merged = dc.with_emission_entries(base, manifest)
    assert merged.field_dictionary.entries[: len(met_entries)] == met_entries
    emission = merged.field_dictionary.entries[len(met_entries) :]
    assert [e.standard_name for e in emission][:2] == [
        "extemis_anthro_NO",
        "extemis_anthro_SO2",
    ]
    assert len(emission) == 20
    assert all(e.canonical_units for e in merged.field_dictionary.entries)
    assert {e.standard_name: e.canonical_units for e in emission}[
        "extemis_dust_clayfrac"
    ] == "1"
    assert dc.with_emission_entries(merged, manifest) == merged
    alias = [e for e in merged.field_dictionary.entries if e.alias]
    assert alias
    assert alias[0].standard_name == "ice_fraction_in_atm"
    path = tmp_path / "fd.yaml"
    merged.to_yaml(path)
    doc = yaml.safe_load(path.read_text())
    assert doc["field_dictionary"]["version_number"] == 0.1
    assert "alias" not in doc["field_dictionary"]["entries"][0]
    assert dc.load_field_dictionary(path) == merged


def test_checked_in_dictionary_already_carries_the_default_entries(
    manifest: mf.Manifest,
) -> None:
    """The prescribed fd_protos.yaml and the generator agree for the default manifest (the ci runs use the file)."""
    base = dc.load_field_dictionary(REPO_ROOT / get_settings().base_field_dictionary)
    assert dc.with_emission_entries(base, manifest) == base


def test_hosted_imports_are_written_as_the_met_table() -> None:
    rows = [
        dc.HostedImportRow(
            standard_name="extemis_anthro_SO2",
            category="anthro",
            variable="SO2",
            file=Path("/root/data/C48/inventories/anthro.nc"),
        )
    ]
    text = driver(hosted_imports=rows).render()
    assert (
        "met_hosted_imports::\n"
        "  extemis_anthro_SO2 anthro SO2 /root/data/C48/inventories/anthro.nc\n"
        "::\n"
    ) in text
    assert text.index("met_hosted_imports::") < text.index("runSeq::")


def test_hosted_import_fields_must_be_single_tokens() -> None:
    with pytest.raises(pydantic.ValidationError, match="whitespace"):
        dc.HostedImportRow(
            standard_name="a b", category="anthro", variable="SO2", file=Path("/f.nc")
        )
