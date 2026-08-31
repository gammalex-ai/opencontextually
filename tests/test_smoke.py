from importlib.metadata import PackageNotFoundError, version

import pytest

import opencontextually


def test_version_exists():
    assert opencontextually.__version__


def test_version_matches_installed_package_metadata():
    # __version__ must come from installed package metadata, not a
    # hardcoded string that can drift from pyproject.toml's real version.
    try:
        installed = version("opencontextually")
    except PackageNotFoundError:
        pytest.skip("opencontextually is not installed; nothing to compare against")
    assert opencontextually.__version__ == installed
