import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.toe_advisor import advise, phrases, read_toe  # noqa: E402

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
