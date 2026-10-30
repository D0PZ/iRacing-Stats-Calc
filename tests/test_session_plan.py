import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import session_plan as sp  # noqa: E402

OPTS = [0.63, 0.58, 0.55, 0.53, 0.51, 0.48, 0.44]


def test_softest_pad_that_still_reaches_abs_in_hard_stops():
    # Frenadas fuertes llegan al ABS con 60% de pedal, máximo real 90%: hay margen para pastillas más suaves
    prof = {"onset_p50": 0.37, "hard_p90": 0.60, "pedal_max": 0.90, "abs_share": 0.8}
    plan = sp.pad_plan(prof, 0.58, 0.42, 0.49, OPTS)
    assert plan["race"]["mu"] == 0.44                    # 0.60*0.58/0.44 = 0.79 <= 0.90*0.95
    assert plan["qualy"]["mu"] == 0.48
    # el balance se compensa para mantener el reparto de frenada
    share = lambda mu, b: b * mu / (b * mu + (1 - b) * 0.42)
    assert share(0.44, plan["race"]["bias"]) == pytest.approx(plan["share"], abs=1e-6)


def test_keeps_pad_when_already_at_pedal_limit():
    # Caso real COTA: ABS fuerte al 72% y máximo 78%: no bajar la pastilla
    prof = {"onset_p50": 0.37, "hard_p90": 0.72, "pedal_max": 0.78, "abs_share": 0.8}
    plan = sp.pad_plan(prof, 0.58, 0.42, 0.492, OPTS)
    assert plan["race"]["mu"] == 0.58 and plan["qualy"]["mu"] == 0.63


def test_fuel_plans():
    assert sp.qualy_fuel(3.09, 2) == pytest.approx(9.77)
    assert sp.race_fuel(3.09, 12) == pytest.approx(39.7)          # lanzada: media vuelta tras el pace car
    # Hungaroring, largada detenida: la carrera gastó 38.96 L (0.16 en la largada), +1 L de margen
    assert sp.race_fuel(2.585, 15, standing_start=True) == pytest.approx(40.0)


def test_race_pace_fuel_ignores_slow_laps():
    # Práctica lenta (2:03-2:04) gasta menos que el ritmo de carrera (2:00)
    laps = [(2.30, 123.6), (2.32, 124.0), (2.55, 120.7), (2.58, 121.0), (2.52, 120.9)]
    assert sp.race_pace_fuel(laps) == pytest.approx(2.55)
    assert sp.race_pace_fuel([(2.4, 121.0)]) == 2.4
    assert sp.race_pace_fuel([]) is None


def test_pad_options_extend_from_config():
    extra = {"BMW M4 GT3": [0.5, 0.45]}
    assert sp.pad_options_for("BMW M4 GT3 EVO", extra) == [0.5, 0.45]
    assert sp.pad_options_for("BMW M4 GT3 EVO") is None
    assert sp.pad_options_for("Porsche 911 Cup (992.2)", extra)[0] == 0.63
