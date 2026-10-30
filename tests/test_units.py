import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import units as u  # noqa: E402


def test_pressure_in_any_garage_unit_becomes_kpa():
    assert u.pressure_kpa("183 kPa") == 183.0
    assert u.pressure_kpa("26.5 psi") == pytest.approx(182.7, abs=0.1)
    assert u.pressure_kpa("1.83 bar") == pytest.approx(183.0)
    assert [u.pressure_unit(x) for x in ("183 kPa", "26.5 psi", "1.83 bar")] == ["kPa", "psi", "bar"]


def test_garage_pressure_steps_are_half_psi():
    # Presiones que aparecen en el garage del 992 Cup en métrico: todas son múltiplos de 0,5 psi redondeados
    seen = [159, 162, 165, 169, 172, 176, 179, 183, 186, 190, 193, 197, 200]
    assert [round(u.snap_pressure(x)) for x in seen] == seen
    assert u.fmt_pressure(u.snap_pressure(175.0)) == "176"
    assert u.fmt_pressure(u.snap_pressure(175.0), "psi") == "25.5"


def test_fahrenheit_temperatures_become_celsius():
    assert u.temps_c("151F, 167F, 169F") == pytest.approx([66.1, 75.0, 76.1], abs=0.1)
    assert u.temps_c("66C, 75C, 76C") == [66.0, 75.0, 76.0]
    assert u.temp_unit("151F, 167F") == "F" and u.temp_unit("66C, 75C") == "C"
    assert u.fmt_temp_delta(5.0, "F") == "+9"


def test_toe_in_millimeters_or_inches():
    assert u.length_mm("+1.3 mm") == pytest.approx(1.3)
    assert u.length_mm("-0.05 in") == pytest.approx(-1.27)
    assert u.length_mm('+1/16"') == pytest.approx(1.5875)
    assert u.length_mm("+1 1/32 in") == pytest.approx(26.19, abs=0.01)
    assert u.length_unit('+1/16"') == "in" and u.length_unit("+1.3 mm") == "mm"
    assert u.fmt_length(1.27, "in") == "+0.050"
