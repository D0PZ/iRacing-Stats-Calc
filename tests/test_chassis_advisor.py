import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.chassis_advisor import advise, phrases, read_chassis  # noqa: E402

SETUP = {"Chassis > Front > ArbSetting": "5", "Chassis > Rear > ArbSetting": "2", "Chassis > Rear > WingAngle": "10",
         "Chassis > InCarDials > CrossWeight": "48.4%", "Chassis > LeftRear > SpringPerchOffset": "3.00 mm",
         "Chassis > RightRear > SpringPerchOffset": "-0.50 mm", "Chassis > LeftFront > SpringPerchOffset": "0.00 mm",
         "Chassis > RightFront > SpringPerchOffset": "0.00 mm"}


def _params(recs):
    return {(r["param"], r["a"]) for r in recs}


def test_reads_setup():
    c = read_chassis(SETUP)
    assert c["arb_front"] == 5 and c["arb_rear"] == 2 and c["wing"] == 10 and c["cross"] == 48.4
    assert c["perch"] == {"LF": 0.0, "RF": 0.0, "LR": 3.0, "RR": -0.5}


def test_slow_corner_understeer_softens_front_arb():
    assert ("Barra delantera", 4) in _params(advise(SETUP, 4, {"mid_under_lenta": 4, "mid_under_media": 2}))


def test_front_arb_order_7_is_softer_than_6():
    setup = dict(SETUP, **{"Chassis > Front > ArbSetting": "6"})
    assert ("Barra delantera", 7) in _params(advise(setup, 4, {"mid_under_lenta": 5}))


def test_exit_oversteer_softens_rear_arb():
    assert ("Barra trasera", 1) in _params(advise(SETUP, 4, {"exit_slides_lenta": 4}))


def test_fast_corner_oversteer_adds_wing():
    assert ("Alerón", 11) in _params(advise(SETUP, 4, {"entry_slides_rapida": 2}))


def test_stable_fast_corners_trims_wing_for_top_speed():
    assert ("Alerón", 9) in _params(advise(SETUP, 4, {}))


def test_cross_weight_corrected_in_diagonal_with_four_wheels():
    recs = advise(SETUP, 4, {})
    cross = [r for r in recs if r["param"] == "Cross weight"][0]
    perch = cross["plan"]["perch"]
    # Subir cross: menos perch en delantera derecha y trasera izquierda, más en las otras dos, misma cantidad
    assert perch["RF"] < 0.0 and perch["LR"] < 3.0 and perch["LF"] > 0.0 and perch["RR"] > -0.5
    d = cross["plan"]["mm"]
    assert perch == {"LF": d, "RF": -d, "LR": 3.0 - d, "RR": -0.5 + d}
    # La altura media de cada eje no cambia
    assert perch["LF"] + perch["RF"] == 0.0 and perch["LR"] + perch["RR"] == 2.5
    assert abs(cross["a"] - 50.0) < 0.4


def test_learns_sensitivity_from_driver_tests():
    from app.chassis_advisor import learn_cross_sens
    # Pruebas reales en COTA: delantera derecha 0 / 15 / 4.25 mm -> 48.4 / 40.0 / 46.1 %
    pts = [{"perch": {"LF": 0, "RF": rf, "LR": 3.0, "RR": -0.5}, "cross": c}
           for rf, c in ((0, 48.4), (15, 40.0), (4.25, 46.1))]
    sens = learn_cross_sens(pts)
    assert abs(sens["front"] - 0.56) < 0.03


def test_cross_weight_follows_direction_balance():
    recs = advise(SETUP, 4, {"mid_under_izq": 6})            # subvira más a la izquierda -> bajar cross
    cross = [r for r in recs if r["param"] == "Cross weight"][0]
    assert cross["a"] < 48.4
    assert "diagonal" in phrases(recs)[[r["param"] for r in recs].index("Cross weight")]
