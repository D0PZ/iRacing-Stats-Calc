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
            "IsDiskLoggingEnabled": True, "IsDiskLoggingActive": False,
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
        self.commands = []

    def __getitem__(self, k):
        return self.v.get(k)

    def freeze_var_buffer_latest(self):
        pass

    def telem_command(self, mode):
        self.commands.append(mode)
        self.v["IsDiskLoggingActive"] = mode == le.irsdk.TelemCommandMode.start

    def unfreeze_var_buffer_latest(self):
        pass


@pytest.fixture
def eng(monkeypatch, tmp_path):
    # No leer ni escribir los mapas de límites guardados en data/
    monkeypatch.setattr(le, "load_tracker", lambda name, length: TrackLimitTracker(length))
    monkeypatch.setattr(le, "save_tracker", lambda *a, **k: None)
    msgs = []
    e = le.LiveEngineer(speak=False, neural_voice=None, on_message=lambda t, p: msgs.append((t, p)),
                        auto_analysis=False)
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


def test_gap_phrasing():
    f = le.LiveEngineer.fmt_gap
    assert f(0.04) == "una décima"
    assert f(0.43) == "4 décimas"
    assert f(0.97) == "un segundo"
    assert f(1.04) == "un segundo"
    assert f(2.37) == "2.4 segundos"


def test_main_brake_per_corner(eng):
    tick(eng)
    eng.track_len = 4000.0
    eng.corners = [1000 / 4000, 1300 / 4000]     # vértices en 1000 m y 1300 m
    brakes = [[700.0, 0.9],      # frenada fuerte para la curva 1
              [950.0, 0.2],      # toque de freno cerca del vértice: no es la principal
              [1150.0, 0.7]]     # curva 2 (no puede tomar la frenada de la curva 1: está antes de su vértice previo)
    by = eng._brakes_by_corner(brakes)
    assert by == {0: 700.0, 1: 1150.0}


def test_corner_reference_question(eng):
    tick(eng)
    eng.track_len = 4000.0
    eng.refs = {"corners": [{"n": 1, "apex_m": 1000.0, "brake_at_m": 880.0, "brake_m_before": 120.0,
                             "brake_peak": 70.0, "min_kmh": 85.0, "gear": 2, "throttle_m": 25.0}]}
    eng.corners = [0.25]
    eng.ask("¿dónde freno en la curva 1?")
    tick(eng)
    out = texts(eng)[-1]
    assert "metro 880" in out and "120 metros antes" in out and "85" in out


def test_auto_recording_starts_in_car_and_stops_after_leaving(eng, monkeypatch):
    start, stop = le.irsdk.TelemCommandMode.start, le.irsdk.TelemCommandMode.stop
    for _ in range(61):
        tick(eng)                                   # en el auto: inicia la grabación
    assert eng.ir.commands == [start]
    clock = [1000.0]
    monkeypatch.setattr(le.time, "time", lambda: clock[0])
    tick(eng, IsOnTrack=False)                      # se baja del auto
    clock[0] += 3
    tick(eng)
    assert eng.ir.commands == [start]               # todavía no: espera 5 s
    clock[0] += 3
    tick(eng)
    assert eng.ir.commands == [start, stop]


def test_auto_recording_respects_manual_logging(eng):
    eng.ir.v["IsDiskLoggingActive"] = True          # ya grabando con Alt+L
    for _ in range(61):
        tick(eng)
    assert eng.ir.commands == []


def _event(eng, current):
    """Evento con práctica y carrera de 12 vueltas; consumo de 3.09 L/vuelta como en COTA"""
    eng.ir.v["SessionInfo"] = {"Sessions": [{"SessionType": "Practice"},
                                            {"SessionType": "Race", "SessionLaps": "12"}]}
    eng.ir.v["SessionNum"] = current
    eng.ir.v["CarSetup"] = {"Chassis": {"Front": {"FuelLevel": "12.0 L"}}}
    eng.refs = {"fuel_per_lap": 3.09, "corners": []}


def test_setup_fuel_warning_in_practice(eng):
    tick(eng)
    _event(eng, current=0)
    eng.is_race = False
    for _ in range(12):
        tick(eng)
    warn = [t for t in texts(eng) if "Ojo con la carrera" in t]
    assert len(warn) == 1, texts(eng)
    assert "12 litros" in warn[0] and "41" in warn[0]          # 3.09 * 13 + 0.5 = 40.7


def test_race_briefing_on_grid(eng):
    tick(eng)
    _event(eng, current=1)
    eng.is_race = True
    for _ in range(12):
        tick(eng, SessionState=int(le.irsdk.SessionState.parade_laps), FuelLevel=12.0)
    brief = [t for t in texts(eng) if t.startswith("Briefing")]
    assert len(brief) == 1, texts(eng)
    assert "12 vueltas" in brief[0] and "parar" in brief[0]


def test_race_fuel_question(eng):
    tick(eng)
    _event(eng, current=0)
    eng.ask("¿cuánto cargo para la carrera?")
    tick(eng)
    assert "carga 41 litros" in texts(eng)[-1]


def test_answers_are_spoken_while_muted(eng):
    tick(eng)
    eng.ask("silencio")
    tick(eng)
    while not eng.voice.q.empty():
        eng.voice.q.get()
    eng.ask("¿cuántas vueltas faltan?")
    tick(eng)
    queued = []
    while not eng.voice.q.empty():
        queued.append(eng.voice.q.get().text)
    assert any("Quedan 8 vueltas" in q for q in queued)


def test_learned_limits_saved_on_disconnect(eng, monkeypatch):
    saved = []
    monkeypatch.setattr(le, "save_tracker", lambda name, tr: saved.append(name))
    tick(eng)
    eng.limits_dirty = True
    eng.ir.is_initialized, eng.ir.is_connected = True, False
    eng.ir.shutdown = lambda: None
    eng._check_connection()
    assert saved == ["Pista de Prueba"]


def test_question_without_own_car_gets_an_answer(eng):
    eng.ir.v["DriverInfo"] = dict(eng.ir.v["DriverInfo"], DriverCarIdx=-1)
    eng.session_info_tick = -2
    eng.ask("¿cuánto combustible me queda?")
    tick(eng)
    assert "no estás en un auto" in texts(eng)[-1]
