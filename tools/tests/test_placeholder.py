"""Placeholder suite so the pytest CI job runs end to end; task 2 replaces it with the generator tests."""

import tools


def test_tools_package_is_importable() -> None:
    assert tools.__name__ == "tools"
