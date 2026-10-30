import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from app.toe_advisor import advise, phrases, plan, read_toe  # noqa: E402

SETUP = {"Chassis > Front > ToeIn": "+1.3 mm", "Chassis > LeftRear > ToeIn": "+1.4 mm",
         "Chassis > RightRear > ToeIn": "+0.5 mm"}


def test_reads_front_pair_and_rear_per_wheel():
    assert read_toe(SETUP) == {"F": 1.3, "LR": 1.4, "RR": 0.5}


def test_loose_in_left_handers_adds_toe_to_outside_rear():
    # Cola suelta en curvas a la izquierda: la trasera exterior es la derecha
    recs = advise(SETUP, laps=5, by_side={"exit_slides_izq": 6})
    assert recs == [{"rueda": "RR", "de": 0.5, "a": 0.7, "por": recs[0]["por"]}]


def test_unneeded_asymmetry_is_equalized():
    recs = advise(SETUP, laps=5, by_side={})
    assert {(r["rueda"], r["a"]) for r in recs} == {("LR", 0.9), ("RR", 0.9)}


def test_stable_car_with_high_rear_toe_gets_less_toe():
    setup = dict(SETUP, **{"Chassis > LeftRear > ToeIn": "+1.2 mm", "Chassis > RightRear > ToeIn": "+1.2 mm"})
    recs = advise(setup, laps=5, by_side={})
    assert {(r["rueda"], r["a"]) for r in recs} == {("LR", 1.0), ("RR", 1.0)}


def test_entry_understeer_reduces_front_toe_in():
    setup = dict(SETUP, **{"Chassis > LeftRear > ToeIn": "+0.5 mm"})
    recs = advise(setup, laps=4, by_side={"entry_under_izq": 2, "entry_under_der": 2})
    front = [r for r in recs if r["rueda"] == "F"]
    assert front and front[0]["a"] == 0.8
    assert "milímetros" in phrases(front)[0]


def test_history_points_to_fastest_toe():
    history = [{"ritmo": 131.2, "setup": SETUP},
               {"ritmo": 130.6, "setup": dict(SETUP, **{"Chassis > LeftRear > ToeIn": "+0.9 mm",
                                                       "Chassis > RightRear > ToeIn": "+0.9 mm"})}]
    recs = advise(SETUP, laps=5, by_side={}, history=history)
    assert "+0.9 / +0.9" in recs[0]["historial"]


def test_reads_toe_in_inches():
    toe = read_toe({"Chassis > Front > ToeIn": '+1/16"', "Chassis > LeftRear > ToeIn": "+0.05 in"})
    assert toe["F"] == pytest.approx(1.5875) and toe["LR"] == pytest.approx(1.27)


def test_phrases_in_driver_units():
    recs = [{"rueda": "LR", "de": 1.27, "a": 1.47, "por": "x"}]
    assert "pulgadas" in phrases(recs, "in")[0] and "+0.050" in phrases(recs, "in")[0]


def test_front_per_wheel_toe_gets_half_step_each():
    setup = {"Chassis > LeftFront > ToeIn": "+0.6 mm", "Chassis > RightFront > ToeIn": "+0.6 mm"}
    recs = advise(setup, laps=4, by_side={"entry_under_izq": 2, "entry_under_der": 2})
    assert {(r["rueda"], r["a"]) for r in recs} == {("LF", 0.35), ("RF", 0.35)}


def test_hotter_rear_with_more_toe_is_part_of_the_reason():
    temps = {"LF": (70, 74, 77), "RF": (70, 74, 77), "LR": (76, 80, 83), "RR": (70, 74, 77)}
    recs = advise(SETUP, laps=5, by_side={}, temps=temps)            # LR +1.4 mm vs RR +0.5 mm
    assert "más caliente" in recs[0]["por"]


def test_plan_splits_front_total_between_wheels():
    recs = [{"rueda": "F", "de": 1.3, "a": 0.8, "por": ""}, {"rueda": "RR", "de": 0.5, "a": 0.7, "por": ""}]
    assert plan(recs) == pytest.approx({"LF": -0.25, "RF": -0.25, "RR": 0.2})


def test_rear_toe_never_below_minimum():
    setup = {"Chassis > LeftRear > ToeIn": "+0.1 mm", "Chassis > RightRear > ToeIn": "+0.6 mm"}
    recs = advise(setup, laps=5, by_side={}, rear_min=0.0)
    assert all(r["a"] >= 0.0 for r in recs)


def test_imperial_toe_moves_in_garage_fractions():
    setup = {"Chassis > Front > ToeIn": '-2/32"', "Chassis > LeftRear > ToeIn": '+1/64"',
             "Chassis > RightRear > ToeIn": '+1/64"'}
    recs = advise(setup, laps=5, by_side={"exit_slides_izq": 6})        # +0.2 mm pedido: menos que 1/64"
    assert recs[0]["rueda"] == "RR" and recs[0]["a"] == pytest.approx(2 * 25.4 / 64)
    assert "de +1/64 a +2/64 pulgadas" in phrases(recs, "in", 64)[0]
