"""The CECE configuration the generator emits (the task-4 multi-tile form, every export nz deep)."""

import inspect
from pathlib import Path

import pydantic
import pytest
import yaml

from tools import cece_yaml as cz
from tools import gen_defaults as gd
from tools import manifest as mf

AMIO = gd.CeceAmio(
    worker_threads=1,
    staging_buffer_count=2,
    staging_buffer_capacity_bytes=4194304,
    prefetch_depth=1,
)


@pytest.fixture
def manifest() -> mf.Manifest:
    return mf.default_manifest(
        resolution="C48", out_dir=Path("data/C48"), command="test"
    )


def test_every_field_of_every_model_has_a_description() -> None:
    models = [
        cls
        for _, cls in inspect.getmembers(cz, inspect.isclass)
        if issubclass(cls, pydantic.BaseModel) and cls is not pydantic.BaseModel
    ]
    assert len(models) >= 8
    for model in models:
        assert model.model_config.get("extra") == "forbid", model.__name__
        for name, field in model.model_fields.items():
            assert field.description, f"{model.__name__}.{name} has no description"


def test_config_follows_the_manifest(manifest: mf.Manifest) -> None:
    config = cz.cece_config(manifest, data_root=Path("/root"), amio=AMIO)
    assert config.driver.start_time == "2021-03-22T06:00:00"
    assert config.driver.end_time == "2021-03-23T06:00:00"
    assert config.driver.timestep_seconds == 720
    assert config.driver.grid.nz == 127
    assert config.driver.gridspec_file is None
    assert config.output.enabled is False
    streams = {s.name: s for s in config.cece_data.streams}
    assert set(streams) == set(manifest.categories)
    assert streams["anthro"].file == "/root/data/C48/inventories/anthro.nc"
    assert (streams["anthro"].cadence, streams["anthro"].taxmode) == ("series", "limit")
    assert (streams["gmi"].cadence, streams["gmi"].taxmode) == ("monthly", "limit")
    assert (streams["dust"].cadence, streams["dust"].taxmode) == ("series", "extend")
    assert all(s.tintalgo == "linear" for s in streams.values())
    assert streams["anthro"].mapalgo == "consd"
    assert streams["gmi"].mapalgo == "bilinear"
    assert [v.levels for v in streams["gmi"].variables] == [127, 127, 127]
    # a variable without `levels` inherits driver.grid.nz in CECE (cece_io.cpp), so a 2-D one declares 1
    assert all(v.levels == 1 for v in streams["anthro"].variables)
    assert [v.model for v in streams["anthro"].variables][:2] == [
        "anthro_NO",
        "anthro_SO2",
    ]
    assert config.species["anthro_NO"] == [
        cz.SpeciesLayer(field="anthro_NO", operation="add")
    ]
    assert config.nuopc.export_fields["gmi_oh"] == cz.ExportField(
        standard_name="extemis_gmi_oh", units="cm-3"
    )
    assert len(config.species) == 20
    assert len(config.nuopc.export_fields) == 20


def test_yaml_quotes_the_boolean_looking_names(
    manifest: mf.Manifest, tmp_path: Path
) -> None:
    path = tmp_path / "cece.yaml"
    cz.cece_config(manifest, data_root=Path("/root"), amio=AMIO).to_yaml(path)
    text = path.read_text()
    assert "file: 'NO'" in text
    doc = yaml.safe_load(text)
    assert doc["cece_data"]["streams"][0]["variables"][0]["file"] == "NO"
    assert "local_time" not in doc
    assert "gridspec_file" not in doc["driver"]
    assert doc["driver"]["grid"] == {"nz": 127}
    assert doc["cece_data"]["streams"][0]["variables"][0]["levels"] == 1
    # CECE's AMIO staging pools, one per data file, sized by the generator (the defaults fill 2.1 GB per PET)
    assert doc["driver"]["amio_worker_threads"] == 1
    assert doc["driver"]["amio_staging_buffer_count"] == 2
    assert doc["driver"]["amio_staging_buffer_capacity_bytes"] == 4194304
    assert doc["driver"]["amio_prefetch_depth"] == 1


def test_an_unknown_cadence_cannot_be_mapped(manifest: mf.Manifest) -> None:
    # model_copy does not validate: the point of the test is the value the type forbids.
    weekly = manifest.categories["anthro"].model_copy(update={"cadence": "weekly"})
    with pytest.raises(ValueError, match="cadence"):
        cz.stream_timing(weekly, manifest.clock)


def test_omitted_variables_are_neither_read_nor_exported(manifest: mf.Manifest) -> None:
    config = cz.cece_config(
        manifest, data_root=Path("/root"), amio=AMIO, omit=frozenset({("anthro", "NO")})
    )
    streams = {s.name: s for s in config.cece_data.streams}
    assert "anthro_NO" not in [v.model for v in streams["anthro"].variables]
    assert "anthro_SO2" in [v.model for v in streams["anthro"].variables]
    assert "anthro_NO" not in config.species
    assert "anthro_NO" not in config.nuopc.export_fields
    assert len(config.nuopc.export_fields) == 19


def test_a_category_with_every_variable_omitted_has_no_stream(
    manifest: mf.Manifest,
) -> None:
    omit = frozenset(("dms", v) for v in manifest.categories["dms"].variables)
    config = cz.cece_config(manifest, data_root=Path("/root"), amio=AMIO, omit=omit)
    assert "dms" not in {s.name for s in config.cece_data.streams}
    assert not any(k.startswith("dms_") for k in config.species)


# --- issue #18: stream timing follows each file's own time coordinate ---------------------------------------


def realistic_manifest(tmp_path: Path) -> mf.Manifest:
    from tools.settings import get_settings

    return mf.default_manifest(
        resolution="C48",
        out_dir=tmp_path / "data" / "C48_realistic",
        command="test",
        defaults_file=get_settings().realistic_defaults_file,
    )


def test_timing_rules_on_the_realistic_manifest(tmp_path: Path) -> None:
    manifest = realistic_manifest(tmp_path)
    clock = manifest.clock
    cats = manifest.categories
    # daily series: a window file the generator writes, limit keeps a missing day loud
    assert cz.stream_timing(cats["anthro1"], clock) == ("daily", "limit")
    assert cz.stream_timing(cats["fire"], clock) == ("daily", "limit")
    # climatologies whose explicit axis lies outside the run wrap by year
    assert cz.stream_timing(cats["so2_aviation_lto"], clock) == ("monthly", "cycle")
    assert cz.stream_timing(cats["dms"], clock) == ("monthly", "cycle")
    assert cz.stream_timing(cats["fengsha"], clock) == ("monthly", "cycle")
    # the GMI axis (Dec 2020 to Jan 2022) brackets the run
    assert cz.stream_timing(cats["gmi"], clock) == ("monthly", "limit")
    # the timeless soil inputs: one record
    assert cz.stream_timing(cats["dust"], clock) == ("series", "extend")


def test_timing_rules_on_the_analytic_manifest(manifest: mf.Manifest) -> None:
    cats = manifest.categories
    assert cz.stream_timing(cats["anthro"], manifest.clock) == ("series", "limit")
    assert cz.stream_timing(cats["gmi"], manifest.clock) == ("monthly", "limit")
    assert cz.stream_timing(cats["dust"], manifest.clock) == ("series", "extend")


def test_realistic_config_uses_window_files_and_skips_point_tables(
    tmp_path: Path,
) -> None:
    manifest = realistic_manifest(tmp_path)
    windows = {
        manifest.categories["anthro1"].file: tmp_path / "gen/streams/CEDS.2019.emis.nc",
        manifest.categories["fire"].file: tmp_path
        / "gen/streams/qfed2.emis_so2.006.nc4",
        manifest.categories["bio"].file: tmp_path
        / "gen/streams/MEGAN.OFFLINE.BIOVOC.emis.nc",
    }
    config = cz.cece_config(manifest, data_root=tmp_path, amio=AMIO, windows=windows)
    streams = {s.name: s for s in config.cece_data.streams}
    assert "volcano" not in streams
    assert len(streams) == 18
    assert streams["anthro1"].file == str(tmp_path / "gen/streams/CEDS.2019.emis.nc")
    assert streams["ship"].file == streams["anthro1"].file
    assert streams["fire"].cadence == "daily"
    assert streams["so2_aviation_lto"].taxmode == "cycle"
    assert streams["dust"].mapalgo == "nn"
    assert streams["dust"].file == str(
        tmp_path
        / "data/C48_realistic/ExtData/dust/FENGSHA_2022_NESDIS_inputs_10km_v3.2.nc"
    )
    assert len(config.nuopc.export_fields) == 33
    assert [v.levels for v in streams["gmi"].variables] == [127, 127, 127]
    assert config.nuopc.export_fields["dms_conc"].units == "nmol l-1"


def test_a_daily_category_without_a_window_file_is_an_error(tmp_path: Path) -> None:
    manifest = realistic_manifest(tmp_path)
    with pytest.raises(ValueError, match="window file"):
        cz.cece_config(manifest, data_root=tmp_path, amio=AMIO)


def test_time_interpolation_decides_the_time_algorithm(tmp_path: Path) -> None:
    """Issue #18 follow-up: CATChem holds a CEDS day and a GMI month (no time_interpolation), CECE must not blend them."""
    manifest = realistic_manifest(tmp_path)
    windows = {
        manifest.categories[c].file: tmp_path / f"{c}.nc"
        for c in ("anthro1", "fire", "bio")
    }
    config = cz.cece_config(
        manifest,
        data_root=tmp_path,
        amio=AMIO,
        windows=windows,
        time_interpolation={"anthro1": "none", "gmi": "none", "fire": "linear"},
    )
    streams = {s.name: s for s in config.cece_data.streams}
    assert streams["anthro1"].tintalgo == "nearest"
    assert streams["gmi"].tintalgo == "nearest"
    assert streams["fire"].tintalgo == "linear"
    assert streams["bio"].tintalgo == "linear"  # not named: linear, as before
