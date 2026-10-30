import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ibt_fixture import synthetic_session, write_ibt  # noqa: E402
from app.ibt_reader import IBTFile  # noqa: E402
from app.telemetry_analyzer import TelemetryAnalyzer, format_report  # noqa: E402
from app.track_limits import TrackLimitTracker  # noqa: E402


@pytest.fixture(scope="module")
def ibt_path(tmp_path_factory):
    channels, yaml_text = synthetic_session()
    path = str(tmp_path_factory.mktemp("ibt") / "testcar_testring 2026-01-01 10-00-00.ibt")
    write_ibt(path, channels, yaml_text)
    return path


def test_reader_roundtrip(ibt_path):
    f = IBTFile(ibt_path)
    channels, _ = synthetic_session()
    assert len(f) == len(channels["Speed"])
    np.testing.assert_allclose(f.get("Speed"), channels["Speed"])
    assert f.get("Lap").dtype == np.int32
    assert f.session_info["WeekendInfo"]["TrackDisplayName"] == "Test Ring"
    assert f.session_info["DriverInfo"]["Drivers"][0]["CarNumber"] == "7"


def test_analyzer_laps_corners_fuel(ibt_path, monkeypatch, tmp_path):
    # No escribir el mapa de límites en data/ del repo durante la prueba
    monkeypatch.setattr("app.telemetry_analyzer.save_tracker", lambda *a, **k: None)
    monkeypatch.setattr("app.telemetry_analyzer.save_references", lambda *a, **k: None)
    monkeypatch.setattr("app.progress.BASE", str(tmp_path / "progress"))
    res = TelemetryAnalyzer([ibt_path]).analyze()
    assert res.track == "Test Ring"
    assert len(res.laps) == 4
    assert res.best_lap.number == 2                      # la vuelta 2 es 2% más rápida
    assert len(res.corners) == 2                         # dos frenadas por vuelta
    assert 2.3 < res.fuel_per_lap < 2.7                  # 2.5 L por vuelta
    lap3 = next(l for l in res.laps if l.number == 3)
    assert lap3.offtrack
    assert sum(c.offtracks for c in res.corners) >= 1
    for c in res.corners:                                  # referencias de frenada y acelerador
        assert c.brake_at_m is not None and 0 <= c.brake_at_m < 2000
        assert c.brake_peak_best == pytest.approx(80, abs=1)   # el sintético frena al 80%
        assert c.throttle_m_best is not None
    text = format_report(res)
    assert "VUELTAS" in text and "RECOMENDACIONES" in text and "REFERENCIAS" in text


def test_track_limit_margin_with_known_offset():
    """Con desplazamiento lateral conocido (modo GPS) el margen sale exacto"""
    L = 1000.0
    tr = TrackLimitTracker(L)
    for i in range(int(L / 5) + 1):          # rumbo de referencia: recta hacia el Este
        for _ in range(MIN_SAMPLES):
            tr.learn(i * 5.0, 0.0)
    dt, v = 1 / 60, 30.0

    def drive(lap, peak_e, offtrack_from=None):
        s = 0.0
        events = []
        while s < L:
            e = -peak_e * max(0.0, 1 - abs(s - 500) / 40)          # se abre a la derecha cerca de 500 m
            off = offtrack_from is not None and e <= offtrack_from
            events += tr.step(lap, s, v, 0.0, off, dt, e_override=e)
            s += v * dt
        return events

    drive(1, 0.6, offtrack_from=-0.5)                       # se pasa: el límite está en -0.5 m
    assert len(tr.zones) == 1
    assert tr.zones[0]["side"] == -1
    assert abs(TrackLimitTracker.limit_of(tr.zones[0]) + 0.5) < 0.05
    ev = drive(2, 0.3)                                      # vuelta limpia, deja 20 cm
    exits = [e for e in ev if e[0] == "exit"]
    assert exits and abs(exits[0][2] - 0.2) < 0.05 and not exits[0][4]


MIN_SAMPLES = 12


def test_tires_use_loaded_wheel_and_pit_carcass(tmp_path, monkeypatch):
    """Óvalo a la izquierda: trabajan las derechas. La carcasa medida al entrar a pits manda, en psi"""
    import app.tire_model as tm
    monkeypatch.setattr("app.telemetry_analyzer.save_tracker", lambda *a, **k: None)
    monkeypatch.setattr("app.telemetry_analyzer.save_references", lambda *a, **k: None)
    monkeypatch.setattr("app.progress.BASE", str(tmp_path / "progress"))
    monkeypatch.setattr(tm, "BASE", str(tmp_path / "tire_model"))
    channels, yaml_text = synthetic_session()
    n = len(channels["Speed"])
    lat = channels["LatAccel"]
    carcass = {"LF": (65.0, 76.0, 78.0), "RF": (78.0, 76.0, 65.0), "LR": (67.0, 72.0, 76.0), "RR": (76.0, 72.0, 67.0)}
    for c, (l, m, r) in carcass.items():
        hot = np.where(lat > 0.8 * 9.81, 90.0, 60.0).astype(np.float32)     # superficie: caliente al cargar
        for side, v in (("L", l), ("M", m), ("R", r)):
            channels[f"{c}temp{side}"] = hot + (v - 70.0)
            arr = np.full(n, 44.0, dtype=np.float32)
            arr[-50:] = v                                                    # entrada a pits al final
            channels[f"{c}tempC{side}"] = arr
        channels[f"{c}pressure"] = np.full(n, 193.0, dtype=np.float32)
    setup = ("CarSetup:\n Tires:\n"
             + "".join(f"  {w}:\n   StartingPressure: 26.5 psi\n   LastHotPressure: 26.5 psi\n"
                       for w in ("LeftFront", "RightFront", "LeftRear", "RightRear"))
             + " Chassis:\n"
             + "".join(f"  {w}:\n   Camber: -3.0 deg\n" for w in ("LeftFront", "RightFront", "LeftRear", "RightRear"))
             + "  Front:\n   ArbSetting: 3\n   ToeIn: +1.3 mm\n  Rear:\n   ArbSetting: 4\n   WingAngle: 8\n...\n")
    yaml_text = yaml_text[:yaml_text.index("CarSetup:")] + setup
    path = str(tmp_path / "testcar_testring 2026-01-01 10-00-00.ibt")
    write_ibt(path, channels, yaml_text)
    res = TelemetryAnalyzer([path]).analyze()
    assert res.pressure_unit == "psi"
    # Superficie de la rueda cargada: la derecha solo con aceleración lateral alta (90 °C de base + 6);
    # la izquierda nunca va por fuera en este óvalo y usa toda la vuelta
    assert res.tires["RF"]["middle"] == pytest.approx(96.0)
    assert res.tires["LF"]["middle"] < 90.0
    assert res.tires["LF"]["carcass"]["inner"] == pytest.approx(78.0)
    texts = [r.detail for r in res.recommendations if r.category == "Neumáticos"]
    lf = next(t for t in texts if t.startswith("Delantera izquierda"))
    assert "camber de -3.0 a -2.6" in lf and "presión de 26.5 a 25.5" in lf
