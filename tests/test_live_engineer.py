import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.live_engineer as le  # noqa: E402
from app.track_limits import TrackLimitTracker  # noqa: E402

pytestmark = pytest.mark.skipif(not le.IRSDK_AVAILABLE, reason="pyirsdk no instalado")


class FakeIR:
    """Imita pyirsdk.IRSDK: diccionario de valores en vivo + info de sesión"""
    var_headers_names = ["SessionInfoUpdate"]

    def __init__(self, **overrides):
        n = 64
        self.v = {
            "SessionInfoUpdate": 1, "SessionTime": 100.0, "SessionNum": 0, "SessionState": 4, "SessionFlags": 0,
            "IsOnTrack": True, "OnPitRoad": False, "PlayerTrackSurface": 3, "Speed": 40.0, "LapDist": 500.0,
            "LapDistPct": 0.25, "LapCurrentLapTime": 20.0, "SteeringWheelAngle": 0.0, "YawRate": 0.0,
            "LatAccel": 0.0, "Throttle": 1.0, "Brake": 0.0, "Gear": 4, "RPM": 7000.0, "FuelLevel": 20.0,
            "LapCompleted": 3, "LapLastLapTime": 61.0, "LapBestLapTime": 60.5, "PlayerCarMyIncidentCount": 0,
            "EngineWarnings": 0, "PitRepairLeft": 0.0, "PitOptRepairLeft": 0.0, "dcBrakeBias": 54.5,
            "dcTractionControl": 3.0, "dcABS": 2.0, "SessionLapsRemainEx": 8, "SessionTimeRemain": 600.0,
            "WaterTemp": 90.0, "OilTemp": 105.0, "OilPress": 5.0, "VelocityX": 40.0, "VelocityY": 0.0,
            "CarIdxLap": [3] * n, "CarIdxLapDistPct": [-1.0] * n, "CarIdxLastLapTime": [-1.0] * n,
            "CarIdxClassPosition": [0] * n, "CarIdxTrackSurface": [-1] * n, "CarIdxOnPitRoad": [False] * n,
            "CarIdxBestLapTime": [-1.0] * n, "CarIdxEstTime": [0.0] * n,
            "WeekendInfo": {"TrackLength": "4.00 km", "TrackDisplayName": "Pista de Prueba",
                            "TrackPitSpeedLimit": "60.00 kph", "WeekendOptions": {"IncidentLimit": 17}},
            "DriverInfo": {"DriverCarIdx": 0, "DriverCarEstLapTime": 60.0,
                           "Drivers": [{"CarIdx": 0, "CarNumber": "7", "CarClassID": 1},
                                       {"CarIdx": 1, "CarNumber": "23", "CarClassID": 1}]},
            "SessionInfo": {"Sessions": [{"SessionType": "Race"}]},
        }
        self.v["CarIdxLapDistPct"][0] = 0.25
        self.v["CarIdxClassPosition"][0] = 2
        self.v["CarIdxTrackSurface"][0] = 3
        self.v.update(overrides)

    def __getitem__(self, k):
        return self.v.get(k)

    def freeze_var_buffer_latest(self):
        pass

    def unfreeze_var_buffer_latest(self):
        pass


@pytest.fixture
def eng(monkeypatch, tmp_path):
    # No leer ni escribir los mapas de límites guardados en data/
    monkeypatch.setattr(le, "load_tracker", lambda name, length: TrackLimitTracker(length))
    monkeypatch.setattr(le, "save_tracker", lambda *a, **k: None)
    msgs = []
    e = le.LiveEngineer(speak=False, neural_voice=None, on_message=lambda t, p: msgs.append((t, p)))
    e.ir = FakeIR()
    e.connected = True
    e.msgs = msgs
    yield e
    e.voice.stop()


def tick(e, **changes):
    e.ir.v.update(changes)
    e.ir.v["SessionTime"] += 1 / 60
    e._tick()


def texts(e):
    return [t for t, _ in e.msgs]


def test_old_flags_not_announced_on_connect(eng):
    tick(eng, SessionFlags=int(le.irsdk.Flags.checkered))
    assert not any("cuadros" in t for t in texts(eng))


def test_yellow_flag_is_announced(eng):
    tick(eng)
    tick(eng, SessionFlags=int(le.irsdk.Flags.yellow))
    assert any("amarilla" in t.lower() for t in texts(eng))


def test_pit_limiter_and_speed(eng):
    tick(eng)
    tick(eng, OnPitRoad=True, Speed=70 / 3.6, EngineWarnings=0)
    out = texts(eng)
    assert any("Limitador" in t for t in out)
    assert any("Velocidad en pits" in t for t in out)


def test_questions(eng):
    tick(eng)
    eng.my_laps = [{"time": 61.0, "raw": 61.0, "fuel": 2.5}, {"time": 60.5, "raw": 60.5, "fuel": 2.5}]
    for q in ("¿cuánto combustible me queda?", "¿tengo daño?", "balance de frenos", "¿cuántas vueltas faltan?",
              "¿cuántos incidentes llevo?", "temperatura del motor"):
        eng.ask(q)
    tick(eng)
    out = " | ".join(texts(eng))
    assert "20.0 litros" in out and "8 vueltas" in out           # combustible: 8 vueltas de 2.5 L
    assert "Sin daño" in out
    assert "54.5" in out and "control de tracción 3" in out
    assert "Quedan 8 vueltas" in out
    assert "límite de 17" in out
    assert "agua 90" in out


def test_mute_keeps_safety_calls(eng):
    tick(eng)
    eng.ask("silencio")
    tick(eng)
    eng.emit("info poco importante", le.P_INFO)
    eng.emit("¡Cuidado! peligro", le.P_SAFETY)
    queued = []
    while not eng.voice.q.empty():
        queued.append(eng.voice.q.get().text)
    assert "info poco importante" not in queued
    assert "¡Cuidado! peligro" in queued


def test_session_change_resets_fuel_history(eng):
    tick(eng)
    eng.my_laps = [{"time": 60.0, "raw": 60.0, "fuel": 9.0}]
    eng.fuel_at_lap_start = 50.0
    tick(eng, SessionNum=1)
    # Historial borrado y referencia de combustible tomada de la sesión nueva (20 L), no la vieja (50 L)
    assert eng.my_laps == []
    assert eng.fuel_at_lap_start == pytest.approx(20.0)
