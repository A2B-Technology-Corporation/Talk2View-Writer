"""Phase A smoke tests — non-UNO imports compile."""

from __future__ import annotations

import pytest


@pytest.mark.unit
def test_package_imports() -> None:
    """The top-level package and version constant are reachable."""
    import talk2view_writer

    assert talk2view_writer.__version__ == "1.0.0"


@pytest.mark.unit
def test_extension_module_loads_without_uno() -> None:
    """``extension.py`` must not touch UNO at import time.

    The singleton ``get_extension`` accepts ``ctx`` lazily, so the module
    itself should import cleanly in a non-LibreOffice Python.
    """
    import talk2view_writer.extension as ext

    assert hasattr(ext, "get_extension")
    assert hasattr(ext, "Talk2ViewWriterExtension")


@pytest.mark.unit
def test_config_constants_present() -> None:
    """Config constants needed by the SDK client and sidebar are defined."""
    from talk2view_writer import config

    assert config.PARTNER_KEY.startswith("pk_")
    assert config.BASE_URL.startswith("https://")
    assert config.EXTENSION_ID == "com.talk2view.writer"
    assert config.PROTOCOL_HANDLER_SERVICE_NAME.startswith(config.EXTENSION_ID)
