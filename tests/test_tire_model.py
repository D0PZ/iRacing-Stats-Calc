import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.tire_model as tm  # noqa: E402


@pytest.fixture
def model(tmp_path, monkeypatch):
    monkeypatch.setattr(tm, "BASE", str(tmp_path))
    return tm.TireModel("Porsche 911 Cup (992.2)")


def _data(spread_front, spread_rear, mid=0.0, camber=-3.0, cold=180.0):
    def temps(sp):  # exterior, centro, interior
        o, i = 70.0, 70.0 + sp
        return (o, (o + i) / 2 + mid, i)
    return {c: {"temps": temps(spread_front if c[1] == "F" else spread_rear), "camber": camber, "cold": cold,
                "hot": None} for c in tm.CORNERS}


def test_finds_optimum_spread_from_lap_times(model):
    # Ritmo = parábola con mínimo en 6 °C adelante y 4 °C atrás
    for sf, sr in ((3, 2), (6, 4), (10, 7), (8, 5)):
        pace = 130 + 0.02 * (sf - 6) ** 2 + 0.02 * (sr - 4) ** 2
        model.record_stint(_data(sf, sr), [pace, pace + 0.3, pace + 0.5], track="COTA")
    assert model.target_spread("LF", "COTA") == pytest.approx(6.0, abs=0.3)
    assert model.target_spread("LR", "COTA") == pytest.approx(4.0, abs=0.5)
    assert model.target_source("front_spread", "COTA") == "aprendido de tus tiempos"


def test_explores_when_faster_at_an_edge(model):
    for sf in (4, 6, 8):                    # cada vez más rápido con más diferencia: seguir probando
        model.record_stint(_data(sf, 5), [131 - 0.2 * sf, 131.5 - 0.2 * sf], track="COTA")
    assert model.target_spread("LF", "COTA") > 8


def test_starting_targets_before_data(model):
    assert model.target_spread("LF") == 7.0 and model.target_spread("RR") == 5.0
    assert model.target_source("front_spread") == "punto de partida"


def test_on_target_says_dont_touch(model):
    recs = model.recommend(_data(7.0, 5.0))
    assert model.phrases(recs) == ["Las cuatro ruedas están en el objetivo: contacto parejo, "
                                   "no toques camber ni presiones."]


def test_learns_camber_sensitivity_from_your_change(model):
    model.learn(_data(13.0, 9.0, camber=-3.0))
    model.learn(_data(9.0, 9.0, camber=-2.6))     # quitar 0.4° bajó la diferencia 4 °C -> 10 °C/grado
    assert model.state["sens"]["LF"]["camber"] == pytest.approx((15 + 10) / 2)
