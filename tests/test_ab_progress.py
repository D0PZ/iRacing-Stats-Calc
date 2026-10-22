import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import ab_test as ab  # noqa: E402
from app.progress import ProgressDB  # noqa: E402

BASE_SETUP = {"Chassis > Rear > WingAngle": "10", "Chassis > Rear > ArbSetting": "2",
              "Tires > LeftFront > StartingPressure": "183 kPa", "Chassis > Front > FuelLevel": "40.0 L"}


def _stint(times, wing="10", fuel0=None, fpl=None):
    return {"tiempos": list(times), "setup": dict(BASE_SETUP, **{"Chassis > Rear > WingAngle": wing}),
            "combustible_inicio": fuel0, "combustible_por_vuelta": fpl}


def test_clear_improvement_is_detected():
    rng = np.random.default_rng(1)
    a = _stint(130.8 + rng.normal(0, 0.15, 8))
    b = _stint(130.3 + rng.normal(0, 0.15, 8), wing="9")
    r = ab.compare(a, b)
    assert r["veredicto"] == "mejor" and r["ic95"][1] < 0
    assert r["cambios"] == [("Rear > WingAngle", "10", "9")]
    assert "más rápido" in ab.describe(r)


def test_noise_is_not_called_an_improvement():
    rng = np.random.default_rng(2)
    a = _stint(130.5 + rng.normal(0, 0.4, 4))
    b = _stint(130.45 + rng.normal(0, 0.4, 4), wing="9")
    r = ab.compare(a, b)
    assert r["veredicto"] == "sin diferencia demostrable"
    assert r["vueltas_necesarias"] > 4 and "ruido" in ab.describe(r)


def test_fuel_correction_removes_weight_effect():
    # Mismo ritmo real; B parece más rápida solo porque va más liviana (20 L menos)
    spl = 0.03
    a = _stint([130.0 + spl * (40 - (i + 0.5) * 3) for i in range(6)], fuel0=40, fpl=3)
    b = _stint([130.0 + spl * (20 - (i + 0.5) * 3) for i in range(6)], wing="9", fuel0=20, fpl=3)
    assert ab.compare(a, b)["diferencia"] < -0.5                     # sin corregir: "mejora" falsa
    assert abs(ab.compare(a, b, seconds_per_liter=spl)["diferencia"]) < 0.01


def test_last_ab_skips_stints_with_same_setup():
    stints = [_stint([131.0, 131.1, 131.2]), _stint([130.9, 131.0]), _stint([130.2, 130.3, 130.25], wing="9")]
    r = ab.last_ab(stints)
    assert r and r["n_a"] == 2 and r["cambios"][0][0] == "Rear > WingAngle"


def test_progress_tracks_best_ideal_and_trend(tmp_path):
    db = ProgressDB("COTA", "Porsche", base=str(tmp_path))
    db.add("s1", "2026-10-21 16:58", "Race", [132.5, 131.8, 131.9], ideal=131.0)
    db.add("s2", "2026-10-22 16:20", "Practice", [131.2, 130.9, 131.0], ideal=130.4)
    db.add("s3", "2026-10-23 18:00", "Practice", [130.7, 130.5, 130.8], ideal=130.1)
    db.add("s3", "2026-10-23 18:00", "Practice", [130.7, 130.4, 130.8], ideal=130.1)   # actualiza, no duplica
    assert len(db.sessions) == 3
    sm = ProgressDB("COTA", "Porsche", base=str(tmp_path)).summary()
    assert sm["record"]["mejor"] == 130.4 and sm["tendencia_s_por_sesion"] < 0
    text = " ".join(db.phrases())
    assert "récord 2:10.400" in text and "1.40 segundos más rápido" in text
