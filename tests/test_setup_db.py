import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.setup_db import SetupDB, key_setup, flatten_setup  # noqa: E402


def _setup(cold, camber, mid_offset, spread):
    """Setup con presión/camber dados y temperaturas medidas (ext, centro, int)"""
    def temps(sp):
        o, i = 60.0, 60.0 + sp
        return f"{o:.0f}C, {(o + i) / 2 + mid_offset:.0f}C, {i:.0f}C"
    tires, chassis = {}, {}
    for key in ("LeftFront", "RightFront", "LeftRear", "RightRear"):
        order = "OMI" if key.startswith("Left") else "IMO"
        t = temps(spread)
        if order == "IMO":
            o, m, i = t.split(", ")
            t = f"{i}, {m}, {o}"
        tires[key] = {"StartingPressure": f"{cold} kPa", f"LastTemps{order}": t, "LastHotPressure": f"{cold + 10} kPa"}
        chassis[key] = {"Camber": f"{camber} deg", "ToeIn": "+0.3 mm"}
    chassis["Rear"] = {"ArbSetting": "2", "WingAngle": "10"}
    return {"Tires": tires, "Chassis": chassis}


def test_ideal_pressure_fitted_from_several_stints(tmp_path):
    db = SetupDB("COTA", "Porsche", base=str(tmp_path))
    # El centro sube 0.4 °C por kPa: con 180 kPa queda parejo (centro = bordes)
    for cold, mid in ((190, 4), (185, 2), (175, -2)):
        db.add_stint(_setup(cold, -3.0, mid, 7), [130.5, 130.9], session="Practice")
    ideals = db.ideals({"front": 7.0, "rear": 5.0})
    assert ideals["LF"]["presion_frio"] == pytest.approx(180, abs=1)
    assert ideals["LF"]["metodo"] == "ajuste con tus salidas"


def test_survives_new_session_and_reloads_from_disk(tmp_path):
    db = SetupDB("COTA", "Porsche", base=str(tmp_path))
    db.add_stint(_setup(183, -3.0, 4, 13), [131.0, 131.4], session="Practice")
    again = SetupDB("COTA", "Porsche", base=str(tmp_path))       # otro día, otra sesión
    assert len(again.stints) == 1 and again.measured()
    summary = again.summary({"front": 7.0, "rear": 5.0})
    assert summary["ideales_por_rueda"]["LF"]["presion_frio"] < 183   # centro caliente -> bajar presión
    assert "Rear > WingAngle" in summary["ultimas"][0]["setup"]


def test_duplicate_measurement_not_stored_twice(tmp_path):
    db = SetupDB("COTA", "Porsche", base=str(tmp_path))
    s = _setup(183, -3.0, 2, 8)
    db.add_stint(s, [130.0, 130.5])
    db.add_stint(s, [130.0, 130.5])
    assert len(db.stints) == 1


def test_key_setup_keeps_relevant_fields():
    k = key_setup(flatten_setup(_setup(183, -2.8, 0, 6)))
    assert k["LeftFront > Camber"] == "-2.8 deg" and k["LeftFront > StartingPressure"] == "183 kPa"
    assert not any("LastTemps" in x for x in k)
