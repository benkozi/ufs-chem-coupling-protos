"""The config generator's command line: outputs, exit codes, the validators, the record file."""

import importlib.util
import logging
import sys
from pathlib import Path
from types import ModuleType

import pytest
import yaml

from tools import gen_configs as gc
from tools import log
from tools import manifest as mf
from tools.settings import get_settings

REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_NAMES = (
    "cece.yaml",
    "fd.yaml",
    "CATChem_new_config.yml",
    "CATChem_emission.yml",
    "CATChem_field_mapping.yml",
    "esm.configure",
    "gen_configs_manifest.yaml",
)


@pytest.fixture(autouse=True)
def _configured_logger() -> None:
    log.configure("INFO")


@pytest.fixture
def manifest_file(tmp_path: Path) -> Path:
    """A C48 manifest with paths relative to its data root (the layout make_mock_data writes)."""
    manifest = mf.default_manifest(
        resolution="C48", out_dir=Path("data/C48"), command="test"
    )
    path = tmp_path / "data" / "C48" / "manifest.yaml"
    manifest.to_yaml(path)
    return path


def run(manifest_file: Path, out: Path, *extra: str) -> int:
    return gc.main(
        ["--manifest", str(manifest_file), "--out", str(out), "--pets", "6", *extra]
    )


def test_success_writes_the_seven_files(manifest_file: Path, tmp_path: Path) -> None:
    out = tmp_path / "gen"
    assert run(manifest_file, out, "--data-root", str(tmp_path)) == 0
    assert sorted(p.name for p in out.iterdir()) == sorted(OUTPUT_NAMES)
    record = gc.GeneratedSet.from_yaml(out / "gen_configs_manifest.yaml")
    assert record.pets == 6
    assert record.regdecomp is None
    assert set(record.outputs) == set(OUTPUT_NAMES)
    assert record.manifest == manifest_file.resolve()
    assert len(record.manifest_digest) == 64
    assert record.command.startswith("gen_configs --manifest")
    cece = yaml.safe_load((out / "cece.yaml").read_text())
    assert cece["cece_data"]["streams"][0]["file"] == str(
        tmp_path / "data/C48/inventories/anthro.nc"
    )
    driver = (out / "esm.configure").read_text()
    # MET hosts one CECE export as a second consumer and writes its totals (task 6 conservation check)
    assert (
        "met_hosted_imports::\n  extemis_anthro_SO2 anthro SO2 "
        f"{tmp_path / 'data/C48/inventories/anthro.nc'}\n::\n"
    ) in driver
    assert "    CECE\n    CECE -> MET\n    MET\n" in driver
    # NUOPC's `high` Verbosity dumps every import on every Run (56 MB per PET log in 24 h at C48)
    assert "Verbosity = low\n" in driver
    assert "Verbosity = high" not in driver
    assert f"manifest: {manifest_file.resolve()}\n" in driver
    assert f"data_root: {tmp_path}\n" in driver
    assert f"cece_config_file: {out / 'cece.yaml'}\n" in driver
    assert f"catchem_config_file: {out / 'CATChem_new_config.yml'}\n" in driver
    science = yaml.safe_load((out / "CATChem_new_config.yml").read_text())
    # diagnostics off: aqmio's NetCDF output of every step fills the disk of a 24 h run (task 6)
    assert science["diagnostics"]["output"]["enabled"] is False
    assert science["simulation"]["emission_filename"] == str(
        out / "CATChem_emission.yml"
    )
    assert science["simulation"]["species_filename"] == str(
        (REPO_ROOT / get_settings().catchem_species_file).resolve()
    )
    mapping = yaml.safe_load((out / "CATChem_field_mapping.yml").read_text())
    assert (
        sum(e["catchem_var"].startswith("extemis/") for e in mapping["import_fields"])
        == 20
    )
    fd = yaml.safe_load((out / "fd.yaml").read_text())
    assert (
        sum(
            e["standard_name"].startswith("extemis_")
            for e in fd["field_dictionary"]["entries"]
        )
        == 20
    )


def test_data_root_defaults_to_the_current_directory(
    manifest_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    out = tmp_path / "gen"
    assert run(manifest_file, out) == 0
    cece = yaml.safe_load((out / "cece.yaml").read_text())
    assert cece["cece_data"]["streams"][0]["file"] == str(
        tmp_path / "data/C48/inventories/anthro.nc"
    )


def test_regdecomp_reaches_the_driver_config(
    manifest_file: Path, tmp_path: Path
) -> None:
    out = tmp_path / "gen"
    argv = [
        "--manifest",
        str(manifest_file),
        "--out",
        str(out),
        "--pets",
        "12",
        "--regdecomp",
        "1",
        "2",
    ]
    assert gc.main(argv) == 0
    assert "regdecomp_per_tile: 1 2\n" in (out / "esm.configure").read_text()
    assert gc.GeneratedSet.from_yaml(out / "gen_configs_manifest.yaml").regdecomp == (
        1,
        2,
    )


def test_pet_rule_is_a_usage_error(
    manifest_file: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR, logger=log.ROOT_NAME):
        assert (
            gc.main(
                [
                    "--manifest",
                    str(manifest_file),
                    "--out",
                    str(tmp_path / "g"),
                    "--pets",
                    "4",
                ]
            )
            == 2
        )
    assert "multiple of 6" in caplog.text
    with caplog.at_level(logging.ERROR, logger=log.ROOT_NAME):
        assert (
            gc.main(
                [
                    "--manifest",
                    str(manifest_file),
                    "--out",
                    str(tmp_path / "g"),
                    "--pets",
                    "12",
                    "--regdecomp",
                    "2",
                    "2",
                ]
            )
            == 2
        )
    assert "regdecomp" in caplog.text
    assert not (tmp_path / "g").exists()


def test_invalid_manifest_is_a_usage_error(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    bad = tmp_path / "manifest.yaml"
    bad.write_text("clock: {}\n")
    with caplog.at_level(logging.ERROR, logger=log.ROOT_NAME):
        assert run(bad, tmp_path / "g") == 2
    assert "manifest" in caplog.text
    with caplog.at_level(logging.ERROR, logger=log.ROOT_NAME):
        assert run(tmp_path / "missing.yaml", tmp_path / "g") == 2
    assert "missing.yaml" in caplog.text


def test_a_category_without_defaults_is_a_usage_error(
    manifest_file: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    doc = yaml.safe_load(get_settings().gen_defaults_file.read_text())
    del doc["categories"]["fire"]
    custom = tmp_path / "gen.yaml"
    custom.write_text(yaml.safe_dump(doc))
    with caplog.at_level(logging.ERROR, logger=log.ROOT_NAME):
        assert run(manifest_file, tmp_path / "g", "--defaults", str(custom)) == 2
    assert "fire" in caplog.text


def test_write_failure_exits_1(
    manifest_file: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    blocker = tmp_path / "gen"
    blocker.write_text("not a directory")
    with caplog.at_level(logging.ERROR, logger=log.ROOT_NAME):
        assert run(manifest_file, blocker) == 1
    assert "gen" in caplog.text


def test_log_level_option(manifest_file: Path, tmp_path: Path) -> None:
    assert run(manifest_file, tmp_path / "gen", "--log-level", "DEBUG") == 0
    assert logging.getLogger(log.ROOT_NAME).level == logging.DEBUG


def test_defaults_from_the_environment(
    manifest_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = yaml.safe_load(get_settings().gen_defaults_file.read_text())
    doc["prototype"] = "renamed"
    custom = tmp_path / "gen.yaml"
    custom.write_text(yaml.safe_dump(doc))
    monkeypatch.setenv("PROTOS_GEN_DEFAULTS", str(custom))
    get_settings.cache_clear()
    assert run(manifest_file, tmp_path / "gen") == 0
    assert "prototype: renamed\n" in (tmp_path / "gen" / "esm.configure").read_text()


def _cece_validator() -> ModuleType:
    script = REPO_ROOT / get_settings().cece_validator
    if not script.is_file():
        pytest.skip(f"CECE validator not present at {script} (submodule missing)")
    spec = importlib.util.spec_from_file_location("validate_cece_config", script)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_emitted_cece_yaml_passes_cece_validator(
    manifest_file: Path, tmp_path: Path
) -> None:
    out = tmp_path / "gen"
    assert run(manifest_file, out, "--data-root", str(tmp_path)) == 0
    validator = _cece_validator()
    errors = validator.validate_file(
        out / "cece.yaml", "cece.yaml", validator.load_config_module(), set()
    )
    assert errors == []


def test_emitted_cece_yaml_passes_the_generator_validation_hook(
    manifest_file: Path, tmp_path: Path
) -> None:
    """The generator itself runs CECE's schema when the submodule is there (exit 1 on a schema error)."""
    out = tmp_path / "gen"
    assert run(manifest_file, out, "--data-root", str(tmp_path)) == 0
    assert gc.validate_cece_file(out / "cece.yaml") == []
    (out / "cece.yaml").write_text("driver: {start_time: 1}\nsurprise: 2\n")
    assert gc.validate_cece_file(out / "cece.yaml") != []


def test_missing_validator_is_a_warning_not_an_error(
    manifest_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("PROTOS_CECE_VALIDATOR", str(tmp_path / "nowhere.py"))
    get_settings.cache_clear()
    with caplog.at_level(logging.WARNING, logger=log.ROOT_NAME):
        assert run(manifest_file, tmp_path / "gen") == 0
    assert "not validated" in caplog.text


def test_validator_errors_exit_1(
    manifest_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(gc, "validate_cece_file", lambda path: [f"{path}:1: boom"])
    with caplog.at_level(logging.ERROR, logger=log.ROOT_NAME):
        assert run(manifest_file, tmp_path / "gen") == 1
    assert "boom" in caplog.text
    assert not (tmp_path / "gen" / "gen_configs_manifest.yaml").exists()


@pytest.mark.parametrize(("category", "variable"), [("ship", "NO"), ("anthro", "XYZ")])
def test_a_hosted_import_the_manifest_lacks_is_a_usage_error(
    manifest_file: Path,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    category: str,
    variable: str,
) -> None:
    doc = yaml.safe_load(get_settings().gen_defaults_file.read_text())
    doc["hosted_imports"] = [{"category": category, "variable": variable}]
    custom = tmp_path / "gen.yaml"
    custom.write_text(yaml.safe_dump(doc))
    with caplog.at_level(logging.ERROR, logger=log.ROOT_NAME):
        assert run(manifest_file, tmp_path / "g", "--defaults", str(custom)) == 2
    assert f"hosted import {category}/{variable}" in caplog.text


def test_no_hosted_imports_writes_no_table(manifest_file: Path, tmp_path: Path) -> None:
    doc = yaml.safe_load(get_settings().gen_defaults_file.read_text())
    doc["hosted_imports"] = []
    custom = tmp_path / "gen.yaml"
    custom.write_text(yaml.safe_dump(doc))
    out = tmp_path / "g"
    assert run(manifest_file, out, "--defaults", str(custom)) == 0
    assert "met_hosted_imports" not in (out / "esm.configure").read_text()


def test_hosted_imports_need_a_cece_to_met_connector(
    manifest_file: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Without `CECE -> MET` in runSeq:: the driver adds no connector and MET's hosted import stays unconnected."""
    doc = yaml.safe_load(get_settings().gen_defaults_file.read_text())
    doc["run_sequence"] = [
        line for line in doc["run_sequence"] if line != "CECE -> MET"
    ]
    custom = tmp_path / "gen.yaml"
    custom.write_text(yaml.safe_dump(doc))
    with caplog.at_level(logging.ERROR, logger=log.ROOT_NAME):
        assert run(manifest_file, tmp_path / "g", "--defaults", str(custom)) == 2
    assert "CECE -> MET" in caplog.text


def test_source_files_is_the_file_path_run(manifest_file: Path, tmp_path: Path) -> None:
    """--catchem-source-files: source: file, the inventory paths, and no emission import advertised."""
    out = tmp_path / "gen"
    assert (
        run(manifest_file, out, "--data-root", str(tmp_path), "--catchem-source-files")
        == 0
    )
    science = yaml.safe_load((out / "CATChem_new_config.yml").read_text())
    ext = science["processes"]["extemis"]
    assert ext["source"] == "file"
    assert ext["anthro"]["source_file"] == str(
        tmp_path / "data/C48/inventories/anthro.nc"
    )
    assert ext["point"] == {"activate": False, "gridded": False}
    mapping = yaml.safe_load((out / "CATChem_field_mapping.yml").read_text())
    emission = [
        e for e in mapping["import_fields"] if e["catchem_var"].startswith("extemis/")
    ]
    assert len(emission) == 20
    assert not any(e["advertise"] for e in emission)


def test_the_default_run_is_imports_only(manifest_file: Path, tmp_path: Path) -> None:
    out = tmp_path / "gen"
    assert run(manifest_file, out, "--data-root", str(tmp_path)) == 0
    ext = yaml.safe_load((out / "CATChem_new_config.yml").read_text())["processes"][
        "extemis"
    ]
    assert ext["source"] == "nuopc_import"
    assert all("source_file" not in ext[name] for name in ("anthro", "gmi", "dust"))
    mapping = yaml.safe_load((out / "CATChem_field_mapping.yml").read_text())
    assert all(
        e["advertise"]
        for e in mapping["import_fields"]
        if e["catchem_var"].startswith("extemis/")
    )


def test_cece_omit_leaves_one_import_unconnected(
    manifest_file: Path, tmp_path: Path
) -> None:
    """CECE does not provide the omitted field; CATChem still advertises it (the unconnected failure case)."""
    out = tmp_path / "gen"
    assert (
        run(
            manifest_file,
            out,
            "--data-root",
            str(tmp_path),
            "--cece-omit",
            "anthro/NO",
        )
        == 0
    )
    cece = yaml.safe_load((out / "cece.yaml").read_text())
    assert "anthro_NO" not in cece["nuopc"]["export_fields"]
    assert "anthro_NO" not in cece["species"]
    assert len(cece["nuopc"]["export_fields"]) == 19
    mapping = yaml.safe_load((out / "CATChem_field_mapping.yml").read_text())
    assert "extemis_anthro_NO" in {e["standard_name"] for e in mapping["import_fields"]}
    validator = _cece_validator()
    errors = validator.validate_file(
        out / "cece.yaml", "cece.yaml", validator.load_config_module(), set()
    )
    assert errors == []


@pytest.mark.parametrize(
    ("request_", "message"),
    [
        ("anthro/XYZ", "not a <category>/<variable> of the manifest"),
        ("ship/NO", "not a <category>/<variable> of the manifest"),
        ("anthroNO", "not a <category>/<variable> of the manifest"),
        ("anthro/SO2", "MET hosts this import"),
    ],
)
def test_a_bad_cece_omit_is_a_usage_error(
    manifest_file: Path,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    request_: str,
    message: str,
) -> None:
    with caplog.at_level(logging.ERROR, logger=log.ROOT_NAME):
        assert run(manifest_file, tmp_path / "g", "--cece-omit", request_) == 2
    assert message in caplog.text
