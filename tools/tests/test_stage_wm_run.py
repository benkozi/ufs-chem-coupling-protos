"""Staging a run directory for a checked-in weather-model configuration set (tools/stage_wm_run.py)."""

import logging
import shutil
from pathlib import Path

import pytest
import yaml

from tools import driver_config as dc
from tools import log
from tools import manifest as mf
from tools import stage_wm_run as sw
from tools.settings import get_settings

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_SET = REPO_ROOT / "configs/wm/catchem_cece_control"
SET_YAML = (
    "cece.yaml",
    "CATChem_new_config.yml",
    "CATChem_emission.yml",
    "CATChem_field_mapping.yml",
    "fd_extemis.yaml",
)


@pytest.fixture(autouse=True)
def _configured_logger() -> None:
    log.configure("INFO")


@pytest.fixture
def inputs(tmp_path: Path) -> dict[str, Path]:
    """A manifest, an ExtData directory and a streams directory, as the fixtures of a real run leave them."""
    data = tmp_path / "data"
    manifest = mf.default_manifest(resolution="C48", out_dir=data, command="test")
    manifest.to_yaml(data / "manifest.yaml")
    (data / "ExtData").mkdir()
    streams = tmp_path / "gen" / "streams"
    streams.mkdir(parents=True)
    return {
        "manifest": data / "manifest.yaml",
        "extdata": data / "ExtData",
        "streams": streams,
        "out": tmp_path / "run",
    }


def request(inputs: dict[str, Path], **changes: object) -> sw.StageRequest:
    return sw.StageRequest.model_validate(
        {
            "config_set": CONFIG_SET,
            "out": inputs["out"],
            "manifest": inputs["manifest"],
            "extdata": inputs["extdata"],
            "streams": inputs["streams"],
            "pets": 6,
            "prototype": "wm_catchem_cece_control",
            "data_root": REPO_ROOT,
        }
        | changes
    )


def argv(inputs: dict[str, Path], *extra: str) -> list[str]:
    return [
        "--set",
        str(CONFIG_SET),
        "--out",
        str(inputs["out"]),
        "--manifest",
        str(inputs["manifest"]),
        "--extdata",
        str(inputs["extdata"]),
        "--streams",
        str(inputs["streams"]),
        *extra,
    ]


def test_the_set_is_staged_unmodified(inputs: dict[str, Path]) -> None:
    sw.stage(request(inputs))
    out = inputs["out"]
    for name in SET_YAML:
        assert (out / name).read_bytes() == (CONFIG_SET / name).read_bytes()
    species = get_settings().catchem_species_file
    assert (out / species.name).read_bytes() == (REPO_ROOT / species).read_bytes()
    assert (out / "ExtData").is_symlink()
    assert (out / "ExtData").resolve() == inputs["extdata"].resolve()
    assert (out / "streams").resolve() == inputs["streams"].resolve()
    assert sw.differences(request(inputs)) == []


def test_the_configure_file_is_the_filled_template_then_the_prototype_keys(
    inputs: dict[str, Path],
) -> None:
    sw.stage(request(inputs))
    text = (inputs["out"] / "ufs.configure").read_text()
    template = (CONFIG_SET / "ufs.configure.atmaero_cece.IN").read_text()
    head, delimiter, tail = text.partition(sw.DELIMITER)
    assert delimiter
    assert "@[" not in head
    # the head is the template, line for line, with only its placeholders replaced
    template_lines = template.splitlines()
    head_lines = head.splitlines()
    assert len(head_lines) == len(template_lines)
    changed = [
        (old, new)
        for old, new in zip(template_lines, head_lines, strict=True)
        if old != new
    ]
    assert all("@[" in old for old, _ in changed)
    assert "ATM_model:                      proto_met" in head_lines
    assert "CHM_model:                      catchem" in head_lines
    assert "CECE_model:                     cece" in head_lines
    assert "CECE_petlist_bounds:            0 5" in head_lines
    assert "ATM_omp_num_threads:            1" in head_lines
    assert "logKindFlag:            ESMF_LOGKIND_MULTI" in head_lines
    assert "globalResourceControl:  false" in head_lines
    step = mf.Manifest.from_yaml(inputs["manifest"]).clock.step_seconds
    assert f"  @{step}" in head_lines
    # the pushed run sequence, with no line added
    start = head_lines.index(f"  @{step}")
    assert [line.strip() for line in head_lines[start + 1 : start + 8]] == [
        "ATM phase1",
        "CECE",
        "ATM -> CHM",
        "CECE -> CHM",
        "CHM",
        "CHM -> ATM",
        "ATM phase2",
    ]
    keys = dict(
        line.split(": ", 1)
        for line in tail.splitlines()
        if ": " in line and line[0] != "#"
    )
    assert keys == {
        "prototype": "wm_catchem_cece_control",
        "manifest": str(inputs["manifest"].resolve()),
        "data_root": str(REPO_ROOT),
        "field_dictionary": "fd.yaml",
        "grid_source": "mosaic",
    }
    # the path keys are left to the driver's run-directory defaults
    assert "catchem_config_file" not in text
    assert "cece_config_file" not in text
    assert "met_hosted_imports" not in text


def test_the_dictionary_is_the_base_with_the_fragment_appended(
    inputs: dict[str, Path],
) -> None:
    sw.stage(request(inputs))
    staged = dc.load_field_dictionary(inputs["out"] / "fd.yaml")
    base = dc.load_field_dictionary(REPO_ROOT / get_settings().base_field_dictionary)
    fragment = yaml.safe_load((CONFIG_SET / "fd_extemis.yaml").read_text())
    fragment_names = [
        e["standard_name"] for e in fragment["field_dictionary"]["entries"]
    ]
    names = [e.standard_name for e in staged.field_dictionary.entries]
    assert names[-len(fragment_names) :] == fragment_names
    assert len(names) == len(set(names))
    kept = [
        e.standard_name
        for e in base.field_dictionary.entries
        if e.standard_name not in fragment_names
    ]
    assert names[: len(kept)] == kept
    assert "inst_tracer_mass_frac" in names


def test_staging_again_replaces_a_staged_directory_only(
    inputs: dict[str, Path],
) -> None:
    sw.stage(request(inputs))
    (inputs["out"] / "PET0.ESMF_LogFile").write_text("a run's output\n")
    sw.stage(request(inputs))
    assert not (inputs["out"] / "PET0.ESMF_LogFile").exists()
    other = inputs["out"].parent / "not_a_run"
    other.mkdir()
    (other / "precious.txt").write_text("keep\n")
    with pytest.raises(sw.StageError, match="was not staged by this tool"):
        sw.stage(request(inputs, out=other))
    assert (other / "precious.txt").exists()
    empty = inputs["out"].parent / "empty"
    empty.mkdir()
    sw.stage(request(inputs, out=empty))
    assert (empty / "ufs.configure").exists()


def test_a_changed_file_is_reported_by_name(inputs: dict[str, Path]) -> None:
    sw.stage(request(inputs))
    out = inputs["out"]
    with (out / "cece.yaml").open("a") as stream:
        stream.write("# edited after staging\n")
    (out / "CATChem_emission.yml").unlink()
    text = (out / "ufs.configure").read_text()
    (out / "ufs.configure").write_text(
        text.replace("ATM phase2", "ATM phase2\n    CECE -> ATM")
    )
    (out / "ExtData").unlink()
    found = sw.differences(request(inputs))
    assert "cece.yaml differs from the set's copy" in found
    assert "CATChem_emission.yml is missing from the run directory" in found
    assert (
        "ufs.configure does not start with the filled ufs.configure.atmaero_cece.IN"
        in found
    )
    assert any(d.startswith("ExtData is not a link to") for d in found)
    assert not any("streams" in d for d in found)
    (out / "ufs.configure").unlink()
    assert "ufs.configure is missing from the run directory" in sw.differences(
        request(inputs)
    )


def test_an_unknown_placeholder_is_refused(
    inputs: dict[str, Path], tmp_path: Path
) -> None:
    changed = tmp_path / "set"
    shutil.copytree(CONFIG_SET, changed)
    template = changed / "ufs.configure.atmaero_cece.IN"
    template.write_text(template.read_text() + "OCN_model: @[ocn_model]\n")
    with pytest.raises(sw.StageError, match=r"unknown placeholder\(s\) @\[ocn_model\]"):
        sw.stage(request(inputs, config_set=changed))


def test_missing_inputs_are_refused_by_name(inputs: dict[str, Path]) -> None:
    with pytest.raises(sw.StageError, match=r"ExtData directory .* does not exist"):
        sw.stage(request(inputs, extdata=inputs["extdata"] / "nope"))
    with pytest.raises(sw.StageError, match=r"streams directory .* does not exist"):
        sw.stage(request(inputs, streams=inputs["streams"] / "nope"))
    with pytest.raises(sw.StageError, match=r"cece\.yaml is not in the set"):
        sw.stage(request(inputs, config_set=inputs["streams"]))


def test_cli_stages_and_verifies(
    inputs: dict[str, Path], caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger=log.ROOT_NAME):
        assert sw.main(argv(inputs)) == 0
        assert sw.main(argv(inputs, "--verify")) == 0
    assert "staged" in caplog.text
    assert "is the set, unmodified" in caplog.text
    text = (inputs["out"] / "ufs.configure").read_text()
    # defaults: 6 PETs, the prototype named after the set, the working directory as the data root
    assert "CHM_petlist_bounds:             0 5" in text
    assert "prototype: wm_catchem_cece_control" in text
    assert f"data_root: {Path.cwd().resolve()}" in text


def test_cli_reports_a_modified_run_directory(
    inputs: dict[str, Path], caplog: pytest.LogCaptureFixture
) -> None:
    assert sw.main(argv(inputs, "--pets", "12", "--prototype", "p")) == 0
    assert (
        "ATM_petlist_bounds:             0 11"
        in (inputs["out"] / "ufs.configure").read_text()
    )
    (inputs["out"] / "cece.yaml").write_text("changed\n")
    with caplog.at_level(logging.ERROR, logger=log.ROOT_NAME):
        assert (
            sw.main(argv(inputs, "--pets", "12", "--prototype", "p", "--verify")) == 1
        )
    assert "cece.yaml differs from the set's copy" in caplog.text


def test_cli_usage_and_input_errors(
    inputs: dict[str, Path], caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR, logger=log.ROOT_NAME):
        assert sw.main(argv(inputs, "--pets", "0")) == 2
        missing = dict(inputs, manifest=inputs["manifest"].with_name("nope.yaml"))
        assert sw.main(argv(missing)) == 1
        gone = dict(inputs, extdata=inputs["extdata"] / "nope")
        assert sw.main(argv(gone)) == 1
    assert "does not exist" in caplog.text
