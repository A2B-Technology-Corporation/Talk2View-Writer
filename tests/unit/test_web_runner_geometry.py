"""Unit tests for the companion-window docking policy (ADR-0039).

Exercises the pure ``_window_geometry`` decision function across the
platform/session matrix, plus the session detection, int coercion, and
geometry-persistence helpers. No GTK / pywebview / UNO involved — the
docking policy is decided in plain Python so it can be locked down here.
"""

from __future__ import annotations

import pytest

from talk2view_writer.web_runner import (
    _DEFAULT_HEIGHT,
    _DEFAULT_WIDTH,
    _coerce_int,
    _load_geometry,
    _save_geometry,
    _session_type,
    _window_geometry,
)

# LO main-window geometry as get_host_window would report it.
HOST = {"geometry": {"x": 100, "y": 50, "w": 1200, "h": 900}}
_RIGHT_EDGE_X = 100 + 1200 - _DEFAULT_WIDTH  # dock onto LO's right edge


@pytest.mark.unit
class TestWindowGeometry:
    """``_window_geometry`` encodes the per-platform docking matrix."""

    def test_wayland_gets_no_coordinates(self) -> None:
        g = _window_geometry(HOST, {}, "linux", "wayland")
        # Wayland forbids client-side toplevel positioning → no coords.
        assert g["x"] is None
        assert g["y"] is None
        assert g["width"] == _DEFAULT_WIDTH
        assert g["height"] == _DEFAULT_HEIGHT
        assert g["frameless"] is False
        assert g["on_top"] is True

    @pytest.mark.parametrize(
        "platform,session",
        [("linux", "x11"), ("darwin", None), ("win32", None)],
    )
    def test_positionable_platforms_dock_to_right_edge(
        self, platform: str, session: str | None
    ) -> None:
        g = _window_geometry(HOST, {}, platform, session)
        assert g["x"] == _RIGHT_EDGE_X
        assert g["y"] == 50
        assert g["height"] == 900  # matches LO's height on first dock
        assert g["width"] == _DEFAULT_WIDTH

    def test_persisted_size_wins_over_defaults(self) -> None:
        g = _window_geometry(HOST, {"width": 555, "height": 333}, "linux", "x11")
        assert g["width"] == 555
        assert g["height"] == 333

    def test_persisted_position_wins_on_positionable_platform(self) -> None:
        g = _window_geometry(HOST, {"x": 7, "y": 9}, "linux", "x11")
        assert g["x"] == 7
        assert g["y"] == 9

    def test_persisted_position_ignored_on_wayland_but_size_kept(self) -> None:
        g = _window_geometry(
            HOST, {"x": 7, "y": 9, "width": 480}, "linux", "wayland"
        )
        assert g["x"] is None
        assert g["y"] is None
        assert g["width"] == 480  # size still honoured on Wayland

    def test_no_host_geometry_falls_back_to_defaults(self) -> None:
        g = _window_geometry({}, {}, "linux", "x11")
        assert g["x"] is None
        assert g["y"] is None
        assert g["width"] == _DEFAULT_WIDTH
        assert g["height"] == _DEFAULT_HEIGHT

    def test_null_geometry_value_treated_as_absent(self) -> None:
        g = _window_geometry({"geometry": None}, {}, "win32", None)
        assert g["x"] is None
        assert g["height"] == _DEFAULT_HEIGHT


@pytest.mark.unit
class TestCoerceInt:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (5, 5),
            ("5", 5),
            (5.9, 5),
            (None, None),
            ("x", None),
            (True, None),  # bools are not coordinates
            (False, None),
            ([], None),
        ],
    )
    def test_coerce(self, value: object, expected: int | None) -> None:
        assert _coerce_int(value) == expected


@pytest.mark.unit
class TestSessionType:
    def test_non_linux_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("talk2view_writer.web_runner.sys.platform", "darwin")
        assert _session_type() is None

    @pytest.mark.parametrize("value", ["wayland", "x11"])
    def test_xdg_session_type(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        monkeypatch.setattr("talk2view_writer.web_runner.sys.platform", "linux")
        monkeypatch.setenv("XDG_SESSION_TYPE", value)
        assert _session_type() == value

    def test_wayland_display_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("talk2view_writer.web_runner.sys.platform", "linux")
        monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
        monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
        assert _session_type() == "wayland"

    def test_display_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("talk2view_writer.web_runner.sys.platform", "linux")
        monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        monkeypatch.setenv("DISPLAY", ":1")
        assert _session_type() == "x11"

    def test_headless_linux_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("talk2view_writer.web_runner.sys.platform", "linux")
        monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        monkeypatch.delenv("DISPLAY", raising=False)
        assert _session_type() is None


@pytest.mark.unit
class TestGeometryPersistence:
    def test_save_then_load_round_trip(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        path = tmp_path / "geometry.json"
        monkeypatch.setattr(
            "talk2view_writer.web_runner._geometry_path", lambda: path
        )
        geo = {"width": 410, "height": 720, "x": 5, "y": 6}
        _save_geometry(geo)
        assert _load_geometry() == geo

    def test_load_missing_returns_empty(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        monkeypatch.setattr(
            "talk2view_writer.web_runner._geometry_path",
            lambda: tmp_path / "nope.json",
        )
        assert _load_geometry() == {}

    def test_save_empty_is_noop(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        path = tmp_path / "geometry.json"
        monkeypatch.setattr(
            "talk2view_writer.web_runner._geometry_path", lambda: path
        )
        _save_geometry({})
        assert not path.exists()

    def test_load_corrupt_returns_empty(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        path = tmp_path / "geometry.json"
        path.write_text("{ not json")
        monkeypatch.setattr(
            "talk2view_writer.web_runner._geometry_path", lambda: path
        )
        assert _load_geometry() == {}

    def test_load_non_dict_returns_empty(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        path = tmp_path / "geometry.json"
        path.write_text("[1, 2, 3]")
        monkeypatch.setattr(
            "talk2view_writer.web_runner._geometry_path", lambda: path
        )
        assert _load_geometry() == {}
