import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import driver_inputs as di  # noqa: E402

DT = 1 / 60


def _feed(samples):
    tr = di.InputTracker()
    for thr, brk, steer, ay, v in samples:
        tr.add(DT, thr, brk, steer, ay, v)
    return tr.result()


def test_coasting_and_overlap():
    r = _feed([(0.0, 0.0, 0, 0, 40)] * 60 + [(0.5, 0.5, 0, 0, 40)] * 30)
    assert r["inercia_s"] == np.round(1.0, 2) and r["ambos_pedales_s"] == 0.5


def test_brake_attack_and_stepped_release():
    up = [(0, b, 0, 0, 50) for b in np.linspace(0.05, 0.9, 12)]          # ataque 0.2 s
    down = [(0, b, 0, 0, 40) for b in np.linspace(0.9, 0.4, 20)]
    bump = [(0, 0.5, 0, 0, 35)] * 5                                       # vuelve a pisar mientras suelta
    end = [(0, b, 0, 0, 30) for b in np.linspace(0.5, 0.0, 20)]
    r = _feed(up + down + bump + end)
    assert 0.15 < r["ataque_freno_s"] < 0.25
    assert r["re_presiones"] >= 1


def test_throttle_lift_counted():
    seq = [(t, 0, 0, 0, 40) for t in np.linspace(0, 0.7, 20)] + [(0.3, 0, 0, 0, 40)] * 5 + \
          [(t, 0, 0, 0, 40) for t in np.linspace(0.3, 1.0, 20)]
    assert _feed(seq)["levantadas"] == 1


def test_steering_corrections_inside_one_corner():
    # Curva a la izquierda sostenida (0.9 g), volante oscilando 40 -> 25 -> 40 -> 25 grados con giros rápidos
    seq = []
    for target in (40, 25, 40, 25, 40):
        cur = seq[-1][2] if seq else 0
        for s in np.linspace(cur, target, 8):
            seq.append((0.3, 0, float(s), 0.9, 30))
    r = _feed(seq)
    assert r["correcciones"] >= 3


def test_compare_flags_differences():
    best = {"inercia_s": 1.0, "correcciones": 1, "ataque_freno_s": 0.2, "liberacion_s": 1.2}
    typical = {"inercia_s": 1.8, "correcciones": 3, "ataque_freno_s": 0.35, "liberacion_s": 0.6}
    tips = di.compare(best, typical)
    assert len(tips) == 4
