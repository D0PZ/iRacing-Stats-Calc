import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.tire_model as tm  # noqa: E402


@pytest.fixture
def model(tmp_path, monkeypatch):
    monkeypatch.setattr(tm, "BASE", str(tmp_path))
    return tm.TireModel("Auto de prueba")      # sin perfil de auto: comportamiento genérico


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
    sens = model.state["sens"]["LF"]
    assert 11.0 < sens["camber"] < 14.0             # se mueve hacia 10 según la confianza que ya tenía
    assert sens["toe"] == tm.PRIOR["toe"][0]        # el toe no cambió: no se toca


def _wheels(cam, toe, spread_of, mid=0.0, cold=180.0, hot=None):
    """Las cuatro ruedas con el mismo setup; spread_of(camber, toe) es el neumático 'real'"""
    sp = spread_of(cam, toe)
    return {c: {"temps": (70.0, 70.0 + sp / 2 + mid, 70.0 + sp), "camber": cam, "toe": toe, "cold": cold,
                "hot": hot, "unit": "kPa", "temp_unit": "C"} for c in tm.CORNERS}


def test_camber_and_toe_changed_together_are_separated(model):
    # Neumático real: 10 °C por grado de camber negativo y -2 °C por mm de toe-in
    real = lambda cam, toe: -14 - 10 * cam - 2 * toe     # noqa: E731
    for cam, toe in ((-3.0, 0.5), (-2.6, 0.5), (-2.6, 1.5), (-3.0, 1.0), (-2.8, 0.2), (-2.4, 1.2),
                     (-2.9, 1.8), (-2.5, 0.4)):
        model.learn(_wheels(cam, toe, real))
    sens = model.state["sens"]["LF"]
    assert 9.5 < sens["camber"] < 11.5
    assert -2.5 < sens["toe"] < -1.2


def test_cold_tires_are_not_learned_from(model):
    cold = {c: dict(d, temps=(44.0, 46.0, 49.0)) for c, d in _data(5, 5).items()}
    assert tm.measured(cold) and not tm.worked(cold, track_temp=21.7)
    model.learn(cold, track_temp=21.7)
    assert model.state["last"] is None


def test_pressure_goes_in_garage_clicks(model):
    recs = model.recommend(_data(7.0, 5.0, mid=4.0, cold=182.7))
    r = recs["LF"]
    assert "camber_delta" not in r
    assert round(r["cold_new"]) == 176                      # 26.5 -> 25.5 psi: máximo 2 clics por salida
    assert "presión de 183 a 176" in model.phrases({"LF": r})[0]


def test_psi_driver_hears_psi(model):
    data = _data(7.0, 5.0, mid=4.0, cold=182.7)
    for d in data.values():
        d["unit"] = "psi"
    assert "presión de 26.5 a 25.5" in model.phrases(model.recommend(data))[0]


def test_planned_toe_change_is_compensated_with_camber(model):
    data = _data(7.0, 5.0)                                  # delanteras justo en el objetivo
    assert "camber_delta" not in model.recommend(data)["LF"]
    # +2 mm de toe-in en la delantera izquierda bajará su spread ~2 °C: hay que agregar camber
    r = model.recommend(data, toe_plan={"LF": 2.0})["LF"]
    assert r["camber_delta"] < 0 and r["toe_shift"] == pytest.approx(-2.0)


def test_hot_window_moves_pressure_when_profile_is_flat(tmp_path, monkeypatch):
    monkeypatch.setattr(tm, "BASE", str(tmp_path))
    m = tm.TireModel("x", limits={"hot_window_kpa": {"front": [180, 186], "rear": [180, 186]}})
    data = _data(7.0, 5.0, cold=172.4)
    for d in data.values():
        d["hot"] = 195.0
    r = m.recommend(data)["LF"]
    assert r["pressure_why"] == "caliente" and r["cold_new"] < 172.4
    assert r["hot_pred"] < 195.0
    assert "ventana" in m.phrases({"LF": r})[0]


def test_gas_law_predicts_hot_pressure():
    # Absoluta: (190 + 101.3) / (172 + 101.3) = 1.066 -> con 165.5 en frío, ~183 en caliente
    assert tm.hot_after(172.0, 190.0, 165.5) == pytest.approx(183.1, abs=0.2)
    assert tm.cold_for_hot(172.0, 190.0, 183.1) == pytest.approx(165.5, abs=0.2)


def test_minimum_pressure_is_respected(tmp_path, monkeypatch):
    monkeypatch.setattr(tm, "BASE", str(tmp_path))
    m = tm.TireModel("x", limits={"cold_min_kpa": 159})
    r = m.recommend(_data(7.0, 5.0, mid=5.0, cold=158.6))["LF"]
    assert "cold_new" not in r and r["at_min"]
    assert "mínimo del garage" in m.phrases({"LF": r})[0]


def test_reads_imperial_setup():
    setup = {"Tires": {"LeftFront": {"StartingPressure": "26.5 psi", "LastHotPressure": "28.0 psi",
                                     "LastTempsOMI": "149F, 169F, 172F"},
                       "RightFront": {"StartingPressure": "26.5 psi", "LastTempsIMO": "172F, 169F, 149F"}},
             "Chassis": {"Front": {"ToeIn": '+1/16"'}, "LeftFront": {"Camber": "-3.0 deg"},
                         "LeftRear": {"ToeIn": "+0.05 in"}}}
    d = tm.read_setup(setup)
    assert d["LF"]["cold"] == pytest.approx(182.7, abs=0.1) and d["LF"]["hot"] == pytest.approx(193.1, abs=0.1)
    assert d["LF"]["temps"] == pytest.approx((65.0, 76.1, 77.8), abs=0.1)
    assert d["RF"]["temps"] == d["LF"]["temps"]
    assert d["LF"]["toe"] == pytest.approx(1.5875 / 2)      # toe delantero total repartido
    assert d["LR"]["toe"] == pytest.approx(1.27)
    assert d["LF"]["unit"] == "psi" and d["LF"]["temp_unit"] == "F"


@pytest.fixture
def cup(tmp_path, monkeypatch):
    monkeypatch.setattr(tm, "BASE", str(tmp_path))
    return tm.TireModel("Porsche 911 Cup (992.2)")


def test_cup_keeps_max_camber_unless_inner_overheats(cup):
    assert cup.limits["camber_bias"] == "max"
    assert "camber_delta" not in cup.recommend(_data(10.0, 8.0, cold=158.6))["LF"]     # 10 < 7 + 5: se queda
    r = cup.recommend(_data(14.0, 8.0, cold=158.6))["LF"]                             # 14 > 12: solo hasta 12
    assert r["camber_delta"] == pytest.approx(0.1)
    assert "máximo útil 12" in cup.phrases({"LF": r})[0]
    assert cup.recommend(_data(4.0, 2.0, cold=158.6))["LF"]["camber_delta"] < 0        # falta camber: agrega


def test_cup_heads_to_minimum_pressure(cup):
    r = cup.recommend(_data(7.0, 5.0, mid=0.0, cold=172.4))["LF"]
    assert r["pressure_why"] == "minima" and round(r["cold_new"]) == 165       # 2 clics por salida hacia 159
    assert "la mínima del garage es la más rápida" in cup.phrases({"LF": r})[0]
    at_min = cup.recommend(_data(7.0, 5.0, mid=-4.0, cold=158.6))["LF"]
    assert round(at_min["cold_new"]) == 162                                     # centro claramente frío: +1 clic


def test_cup_ideals_respect_profile(tmp_path):
    import app.setup_db as sdb
    db = sdb.SetupDB("COTA", "Porsche", base=str(tmp_path))
    s = sdb_setup(183, -2.5, 4, 10)
    db.add_stint(s, [131.0, 131.4])
    ideal = db.ideals({"front": 7.0, "rear": 5.0}, limits=tm.TIRE_LIMITS["porsche 911 cup (992"])["LF"]
    assert ideal["presion_frio"] == 159 and ideal["camber"] == -2.5


def sdb_setup(cold, camber, mid_offset, spread):
    tires, chassis = {}, {}
    for key in ("LeftFront", "RightFront", "LeftRear", "RightRear"):
        o, i = 60.0, 60.0 + spread
        m = (o + i) / 2 + mid_offset
        temps = f"{o:.0f}C, {m:.0f}C, {i:.0f}C" if key.startswith("Left") else f"{i:.0f}C, {m:.0f}C, {o:.0f}C"
        tires[key] = {"StartingPressure": f"{cold} kPa", ("LastTempsOMI" if key.startswith("Left") else "LastTempsIMO"): temps}
        chassis[key] = {"Camber": f"{camber} deg"}
    return {"Tires": tires, "Chassis": chassis}
