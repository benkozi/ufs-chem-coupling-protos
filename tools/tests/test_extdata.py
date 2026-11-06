"""tools/extdata.py: the catalog and headers models, and the checked-in JSON documents (issue #17)."""

import inspect
import json
from pathlib import Path

import pydantic
import pytest

from tools import extdata as xd
from tools import extdata_catalog as cat
from tools.settings import get_settings

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_LOG = Path(__file__).parent / "fixtures" / "catchem_file_read_PET000.txt"


def test_every_field_of_every_model_has_a_description() -> None:
    models = [
        cls
        for _, cls in inspect.getmembers(xd, inspect.isclass)
        if issubclass(cls, pydantic.BaseModel) and cls.__module__ == xd.__name__
    ]
    assert len(models) == 8
    for model in models:
        assert model.model_config.get("extra") == "forbid", model.__name__
        for name, field in model.model_fields.items():
            assert field.description, f"{model.__name__}.{name} has no description"


def test_attribute_values_keep_their_types_and_reject_others() -> None:
    header = xd.VariableHeader(
        dtype="float32",
        dimensions=["lat"],
        shape=[2],
        attributes={
            "scale_factor": 0.5,
            "units": "1",
            "valid_range": [0.0, 1.0],
            "n": 3,
        },
    )
    assert header.attributes["scale_factor"] == 0.5
    nested: dict[str, object] = {"nested": {"a": 1}}
    with pytest.raises(pydantic.ValidationError, match="attributes"):
        xd.VariableHeader.model_validate(
            {"dtype": "f", "dimensions": [], "shape": [], "attributes": nested}
        )


def test_point_table_rows_must_match_the_count() -> None:
    good = {
        "label": "volcano",
        "columns": ["lat", "lon", "emission", "bottom", "top"],
        "units": ["degrees_north", "degrees_east", "kg S s-1", "m", "m"],
        "row_count": 1,
        "malformed_rows": 0,
        "ranges": {},
        "total_emission": 3.0,
        "rows": [[1.0, 2.0, 3.0, 4.0, 5.0]],
    }
    xd.PointTable.model_validate(good)
    with pytest.raises(pydantic.ValidationError, match="row_count"):
        xd.PointTable.model_validate(good | {"row_count": 2})
    with pytest.raises(pydantic.ValidationError, match="five"):
        xd.PointTable.model_validate(good | {"rows": [[1.0, 2.0]]})


def test_checked_in_catalog_equals_a_fresh_scan_of_the_issue_log() -> None:
    path = (
        REPO_ROOT
        / get_settings().extdata_defaults_dir
        / get_settings().file_catalog_filename
    )
    checked_in = xd.FileCatalog.model_validate_json(path.read_text())
    fresh = xd.FileCatalog.model_validate(cat.scan_logs([FIXTURE_LOG]))
    assert checked_in.files == fresh.files
    assert checked_in.line_shapes == fresh.line_shapes
    assert len(checked_in.files) == 30
    assert json.loads(path.read_text())["logs"] == ["PET000.ESMF_LogFile.log"]


def test_checked_in_headers_validate_when_present() -> None:
    """The Ursa run's output (workflow step 3); until it exists the test documents what will be checked."""
    settings = get_settings()
    path = REPO_ROOT / settings.extdata_defaults_dir / settings.extdata_headers_filename
    if not path.exists():
        pytest.skip(f"{path} not yet produced on the RT platform")
    headers = xd.ExtDataHeaders.model_validate_json(path.read_text())
    assert headers.extdata_root.endswith("/GOCART/p8/ExtData")
    tables = [f for f in headers.files if f.role == "point_table"]
    assert len(tables) == 2
    for table in tables:
        assert table.point_table is not None
        assert table.point_table.row_count == 97
        assert table.point_table.total_emission == pytest.approx(
            sum(r[2] for r in table.point_table.rows)
        )
        copied = (
            REPO_ROOT
            / settings.extdata_defaults_dir
            / settings.volcanic_dirname
            / Path(table.path).name
        )
        assert (
            xd.PointTable.model_validate(cat.parse_point_table(copied.read_text()))
            == table.point_table
        )
    inventories = [f for f in headers.files if f.role == "inventory"]
    assert len(inventories) == 18
    assert all(f.exists for f in inventories), [
        f.path for f in inventories if not f.exists
    ]
