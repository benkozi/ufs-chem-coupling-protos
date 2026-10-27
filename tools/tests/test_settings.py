"""The shared settings object: every environment interaction and tunable of the tooling."""

from pathlib import Path

import pydantic
import pytest

from tools import settings as st


def test_every_field_is_described_and_unknown_keys_are_rejected() -> None:
    for name, field in st.Settings.model_fields.items():
        assert field.description, name
    assert st.Settings.model_config.get("extra") == "forbid"
    assert st.Settings.model_config.get("env_prefix") == "PROTOS_"
    with pytest.raises(pydantic.ValidationError, match="surprise"):
        st.Settings(surprise=1)  # type: ignore[call-arg]


def test_defaults() -> None:
    s = st.Settings()
    assert s.log_level == "INFO"
    assert s.fix_url.endswith("/fix_fv3/{resolution}/")
    assert s.earth_radius_m == 6371.0e3
    assert s.defaults_file == Path("tools/defaults/manifest.yaml")
    assert s.fixture_dir == Path("data/fixtures/C48")
    assert s.deflate_level == 1
    assert s.cf_conventions.startswith("CF-")


def test_environment_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROTOS_LOG_LEVEL", "debug")
    monkeypatch.setenv("PROTOS_FIX_URL", "http://mirror.example/{resolution}/")
    monkeypatch.setenv("PROTOS_DEFLATE_LEVEL", "4")
    s = st.Settings()
    assert s.log_level == "DEBUG"
    assert s.fix_url == "http://mirror.example/{resolution}/"
    assert s.deflate_level == 4


def test_invalid_environment_values_fail_loudly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROTOS_LOG_LEVEL", "LOUD")
    with pytest.raises(pydantic.ValidationError, match="log_level"):
        st.Settings()
    monkeypatch.delenv("PROTOS_LOG_LEVEL")
    monkeypatch.setenv("PROTOS_DEFLATE_LEVEL", "12")
    with pytest.raises(pydantic.ValidationError, match="deflate_level"):
        st.Settings()


def test_get_settings_is_cached_until_cleared(monkeypatch: pytest.MonkeyPatch) -> None:
    first = st.get_settings()
    assert st.get_settings() is first
    monkeypatch.setenv("PROTOS_LOG_LEVEL", "WARNING")
    assert st.get_settings() is first
    st.get_settings.cache_clear()
    assert st.get_settings().log_level == "WARNING"


def test_settings_are_frozen() -> None:
    s = st.Settings()
    with pytest.raises(pydantic.ValidationError):
        s.log_level = "DEBUG"  # type: ignore[misc]
