import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import balance_model as bm  # noqa: E402
from app import vehicle_dynamics as vd  # noqa: E402

SETUP = {"Chassis > Front > ArbSetting": "1", "Chassis > Rear > ArbSetting": "2", "Chassis > Rear > WingAngle": "10"}


def test_slow_corner_oversteer_softens_rear_bar():
    # Caso COTA: K -1.2 en lentas (sobreviraje) -> barra trasera más blanda
    recs = bm.recommend(SETUP, {"lenta": {"K": -1.2, "n": 1500}})
    assert recs[0]["param"] == "Barra trasera" and recs[0]["a"] < 2
    assert recs[0]["K_previsto"] > -1.2


def test_slow_oversteer_with_rear_at_minimum_stiffens_front():
    setup = dict(SETUP, **{"Chassis > Rear > ArbSetting": "0"})
    recs = bm.recommend(setup, {"lenta": {"K": -1.2, "n": 1500}})
    assert recs[0]["param"] == "Barra delantera" and recs[0]["a"] in (2, 3)


def test_fast_corner_understeer_reduces_wing():
    # Caso COTA: K +0.8 en rápidas (subviraje) -> menos alerón
    recs = bm.recommend(SETUP, {"rapida": {"K": 0.81, "n": 2000}})
    assert recs[0]["param"] == "Alerón" and recs[0]["a"] < 10


def test_on_target_no_change():
    assert bm.recommend(SETUP, {"lenta": {"K": 0.25, "n": 900}, "rapida": {"K": 0.45, "n": 900}}) == []


def test_not_enough_samples_no_physics_advice():
    assert bm.recommend(SETUP, {"lenta": {"K": -2.0, "n": 50}}) == []


def test_learns_bar_effect_from_your_stints():
    def stint(rear, K):
        return {"setup": dict(SETUP, **{"Chassis > Rear > ArbSetting": str(rear)}),
                "manejo": {"gradiente": {"lenta": {"K": K, "n": 800}}}}
    s = bm.learn_sensitivities([stint(4, -1.4), stint(2, -0.6)])    # 2 clics más blanda: +0.8 -> -0.4 por clic
    assert s["arb_rear"]["fuente"] == "tus salidas"
    assert s["arb_rear"]["sens"] == pytest.approx((-0.4 + -0.3) / 2)


def test_gradient_accumulator_matches_batch_fit():
    import numpy as np
    rng = np.random.default_rng(0)
    acc = vd.GradientAccumulator()
    acc.ratio = 15.0
    K_true = 0.7
    for _ in range(3000):
        v = rng.uniform(20, 40)
        a = rng.uniform(0.4, 1.6)
        yaw = a * 9.81 / v
        road = vd.WHEELBASE * yaw / v + np.radians(K_true * a)
        acc.add(v, road * 15.0, yaw, a * 9.81, 0.0)
    res = acc.result()
    assert res["lenta"]["K"] == pytest.approx(K_true, abs=0.05)


def test_braking_potential_straight_line_matches_kinematics():
    """Recta (sin curvatura): frenando a 15 m/s² desde 60 a 20 m/s se necesitan (60²-20²)/(2·15) = 106.7 m.
    Si el piloto frena a 160 m del vértice, puede frenar ~53 m más tarde (con margen 1.0)."""
    import numpy as np
    step = 2.0
    n = 200
    s = np.arange(n) * step
    apex = 180
    brake = apex - 80                                  # frena 160 m antes del vértice
    v = np.full(n, 60.0)
    decel = (60.0 ** 2 - 20.0 ** 2) / (2 * 160.0)      # frenada suave y constante del piloto
    for i in range(brake, apex + 1):
        v[i] = np.sqrt(max(60.0 ** 2 - 2 * decel * (i - brake) * step, 20.0 ** 2))
    yaw = np.zeros(n)
    env = (np.array([10.0, 80.0]), np.array([15.0, 15.0]))
    lat = (np.array([10.0, 80.0]), np.array([15.0, 15.0]))
    pot = vd.braking_potential(v, yaw, brake, apex, step, env, lat, margin=1.0)
    assert pot["meters_later"] == pytest.approx(160 - 106.7, abs=step + 0.5)
    assert pot["gain_s"] > 0
