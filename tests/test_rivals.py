"""
Escenarios con varios autos en movimiento (lo que el replay de un .ibt no puede probar:
el archivo solo trae los datos de tu auto).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app.live_engineer as le  # noqa: E402
from app.track_limits import TrackLimitTracker  # noqa: E402
from test_live_engineer import FakeIR  # noqa: E402

pytestmark = pytest.mark.skipif(not le.IRSDK_AVAILABLE, reason="pyirsdk no instalado")

L = 4000.0
HZ = 60


class Race:
    """Simula autos a velocidad constante (m/s) sobre una pista de 4 km"""

    def __init__(self, monkeypatch, cars):
        monkeypatch.setattr(le, "load_tracker", lambda name, length: TrackLimitTracker(length))
        monkeypatch.setattr(le, "save_tracker", lambda *a, **k: None)
        self.msgs = []
        self.e = le.LiveEngineer(speak=False, neural_voice=None, on_message=lambda t, p: self.msgs.append(t))
        drivers = [{"CarIdx": i, "CarNumber": str(10 + i), "CarClassID": c.get("cls", 1)} for i, c in enumerate(cars)]
        self.ir = FakeIR(DriverInfo={"DriverCarIdx": 0, "DriverCarEstLapTime": 80.0, "Drivers": drivers})
        self.e.ir = self.ir
        self.e.connected = True
        self.cars = cars                     # dicts: pos (m acumulados), v (m/s), opcional surface, pit
        self.t = 100.0
        self.cross = {i: None for i in range(len(cars))}   # momento del último cruce de meta
        self.last = [-1.0] * 64

    def step(self, seconds: float, me_lap_time: float = 80.0):
        for _ in range(int(seconds * HZ)):
            self.t += 1 / HZ
            for i, c in enumerate(self.cars):
                before = int(c["pos"] // L)
                c["pos"] += c["v"] / HZ
                if int(c["pos"] // L) > before:          # cruzó la meta
                    if self.cross[i] is not None:
                        self.last[i] = self.t - self.cross[i]
                    self.cross[i] = self.t
            n = 64
            laps, pcts = [0] * n, [-1.0] * n
            surf, pit = [-1] * n, [False] * n
            order = sorted(range(len(self.cars)), key=lambda i: -self.cars[i]["pos"])
            cpos = [0] * n
            for rank, i in enumerate(order, start=1):
                cpos[i] = rank
            for i, c in enumerate(self.cars):
                laps[i] = int(c["pos"] // L)
                pcts[i] = (c["pos"] % L) / L
                surf[i] = c.get("surface", 3)
                pit[i] = c.get("pit", False)
            me = self.cars[0]
            self.ir.v.update({
                "SessionTime": self.t, "CarIdxLap": laps, "CarIdxLapDistPct": pcts, "CarIdxTrackSurface": surf,
                "CarIdxOnPitRoad": pit, "CarIdxClassPosition": cpos, "Speed": me["v"],
                "LapDist": me["pos"] % L, "LapDistPct": pcts[0], "LapCompleted": laps[0],
                "CarIdxLastLapTime": list(self.last), "LapLastLapTime": self.last[0],
                "LapCurrentLapTime": self.t - (self.cross[0] or 0.0), "FuelLevel": 30.0,
            })
            self.e._tick()

    def said(self, *words):
        return [m for m in self.msgs if all(w.lower() in m.lower() for w in words)]


def test_stopped_car_ahead(monkeypatch):
    r = Race(monkeypatch, [{"pos": 1000.0, "v": 50.0}, {"pos": 1500.0, "v": 45.0}])
    r.step(2.0)                              # el rival venía rápido...
    r.cars[1]["v"] = 0.0                     # ...y se detiene en pista
    r.step(3.0)
    hits = r.said("detenido")
    assert hits, r.msgs
    assert "metros" in hits[0]


def test_braking_rival_is_not_a_crash(monkeypatch):
    """Frenar fuerte para una horquilla (de 60 a 22 m/s) no debe disparar alarma de accidente"""
    r = Race(monkeypatch, [{"pos": 1000.0, "v": 50.0}, {"pos": 1400.0, "v": 60.0}])
    r.step(2.0)
    r.cars[1]["v"] = 22.0
    r.step(3.0)
    assert not r.said("detenido") and not r.said("trompeado"), r.msgs


def test_rival_ahead_enters_pits(monkeypatch):
    r = Race(monkeypatch, [{"pos": 1000.0, "v": 50.0}, {"pos": 1100.0, "v": 50.0}])
    r.step(1.0)
    r.cars[1]["pit"] = True
    r.step(0.5)
    assert r.said("entró a pits", "adelante"), r.msgs


def test_rival_ahead_goes_off(monkeypatch):
    r = Race(monkeypatch, [{"pos": 1000.0, "v": 50.0}, {"pos": 1100.0, "v": 50.0}])
    r.step(1.0)
    r.cars[1]["surface"] = 0
    r.step(0.5)
    assert r.said("se salió", "aprovecha"), r.msgs


def test_far_off_track_car_is_ignored(monkeypatch):
    """Un auto fuera de pista a 2 km no interesa"""
    r = Race(monkeypatch, [{"pos": 1000.0, "v": 50.0}, {"pos": 3000.0, "v": 50.0}])
    r.step(1.0)
    r.cars[1]["surface"] = 0
    r.cars[1]["v"] = 5.0
    r.step(2.0)
    assert not r.said("metros"), r.msgs


def test_attack_call_when_rival_ahead_is_slower(monkeypatch):
    # Rival 3 s adelante y 1.6 s por vuelta más lento: el ingeniero debe empujarte a atacar
    r = Race(monkeypatch, [{"pos": 3900.0, "v": 50.0}, {"pos": 4050.0, "v": 49.0}])
    r.step(80 * 3.2)
    hits = r.said("adelante")
    assert any("Adelántalo" in m or "descuentas" in m for m in hits), r.msgs


def test_defend_call_when_rival_behind_is_closing(monkeypatch):
    # Rival 1.6 s atrás y 1 s por vuelta más rápido
    r = Race(monkeypatch, [{"pos": 3900.0, "v": 50.0}, {"pos": 3820.0, "v": 50.6}])
    r.step(80 * 3.2)
    assert r.said("te presiona"), r.msgs


def test_no_battle_calls_in_practice(monkeypatch):
    r = Race(monkeypatch, [{"pos": 3900.0, "v": 50.0}, {"pos": 3820.0, "v": 50.6}])
    r.ir.v["SessionInfo"] = {"Sessions": [{"SessionType": "Practice"}]}
    r.step(80 * 3.2)
    assert not r.said("te presiona") and not r.said("adelántalo"), r.msgs
