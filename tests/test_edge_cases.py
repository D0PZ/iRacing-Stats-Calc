"""Sesiones raras: no deben romper el analizador ni la app"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ibt_fixture import synthetic_session, write_ibt  # noqa: E402
from app.ibt_reader import IBTFile  # noqa: E402
from app.telemetry_analyzer import TelemetryAnalyzer, format_report  # noqa: E402


@pytest.fixture(autouse=True)
def no_disk_writes(monkeypatch):
    monkeypatch.setattr("app.telemetry_analyzer.save_tracker", lambda *a, **k: None)
    monkeypatch.setattr("app.telemetry_analyzer.save_references", lambda *a, **k: None)


def _write(tmp_path, channels, yaml_text, name="car_track 2026-01-01 10-00-00.ibt"):
    path = str(tmp_path / name)
    write_ibt(path, channels, yaml_text)
    return path


def _analyze(path):
    res = TelemetryAnalyzer([path]).analyze()
    text = format_report(res)          # el reporte tampoco debe fallar
    assert "RECOMENDACIONES" in text
    return res


def test_no_complete_laps(tmp_path):
    ch, y = synthetic_session(laps=1)
    cut = len(ch["Speed"]) // 2                     # media vuelta (salió del pit y se bajó)
    ch = {k: v[:cut] for k, v in ch.items()}
    res = _analyze(_write(tmp_path, ch, y))
    assert res.best_lap is None
    assert any("No hay vueltas válidas" in r.title for r in res.recommendations)


def test_only_pit_laps(tmp_path):
    ch, y = synthetic_session(laps=3)
    ch["OnPitRoad"][:] = True
    res = _analyze(_write(tmp_path, ch, y))
    assert res.best_lap is None


def test_without_gps_and_velocity_channels(tmp_path):
    ch, y = synthetic_session(laps=3)
    for k in ("Lat", "Lon", "VelocityX", "VelocityY", "YawNorth", "LFspeed", "RFspeed", "LRspeed", "RRspeed"):
        ch.pop(k)
    res = _analyze(_write(tmp_path, ch, y))
    assert res.best_lap is not None and res.limit_zones == []


def test_truncated_file(tmp_path):
    ch, y = synthetic_session(laps=3)
    path = _write(tmp_path, ch, y)
    size = os.path.getsize(path)
    with open(path, "r+b") as f:                    # iRacing se cerró a mitad de escritura
        f.truncate(size - size // 3)
    f = IBTFile(path)
    assert 0 < len(f) < len(ch["Speed"])
    _analyze(path)


def test_broken_session_yaml(tmp_path):
    ch, y = synthetic_session(laps=3)
    res = _analyze(_write(tmp_path, ch, "---\nWeekendInfo: [roto\n..."))
    assert res.best_lap is not None                 # sin YAML igual se analizan las vueltas


def test_view_renders_empty_result(tmp_path):
    tk = pytest.importorskip("tkinter")
    ch, y = synthetic_session(laps=1)
    ch = {k: v[: len(v) // 2] for k, v in ch.items()}
    res = TelemetryAnalyzer([_write(tmp_path, ch, y)]).analyze()
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("sin pantalla")
    root.withdraw()
    try:
        from app.gui import IracingStatsApp
        app = IracingStatsApp(root)
        app.telemetry_view.result = res
        app.show_telemetry()
        root.update()
    finally:
        root.destroy()
