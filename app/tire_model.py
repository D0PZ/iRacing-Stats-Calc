"""
Contacto perfecto del neumático: camber, presión en frío y toe por rueda desde las temperaturas O/M/I.

Lo que iRacing mide al entrar a pits (garage): temperatura de carcasa exterior/centro/interior,
presión en caliente y desgaste. De ahí salen dos números por rueda:
- spread = interior - exterior          -> camber (y, en menor medida, toe)
- mid    = centro - promedio de bordes  -> presión en frío (centro caliente = sobreinflada)

Objetivos:
- spread: punto de partida 7 °C adelante (guía de neumáticos de iRacing: interior más caliente, no más
  de ~7 °C) y 5 °C atrás (manual del 992 Cup: atrás algo menos de camber porque también tracciona).
  Después se busca TU óptimo: cada salida guarda el spread medido y tus tiempos; con 3+ salidas
  distintas se ajusta una parábola tiempo de vuelta vs spread por eje y se toma su mínimo.
  Si aún no hay mínimo claro, se propone la siguiente prueba hacia donde fuiste más rápido.
- mid = 0: centro igual al promedio de los bordes (banda plana, contacto completo).
- presión en caliente: si hay una ventana (configurada, o aprendida de tus tiempos) se verifica dónde
  quedará con la nueva presión en frío usando la ley de los gases (la presión absoluta escala igual).

Modelo por rueda, lineal alrededor del setup actual:
    Δspread = s_c · (−Δcamber) + s_t · Δtoe          Δmid = s_p · Δpresión_frío
s_c: °C de spread por grado de camber negativo. s_t: °C de spread por mm de toe-in de esa rueda
(el toe-in "friega" el borde exterior, HP Academy / Elephant Racing: baja el spread). s_p: °C por kPa
(regla de Elephant Racing: 1 psi por cada 5 °F de centro contra bordes ≈ 0,4 °C/kPa).
Parten de esos valores y se corrigen con un filtro de Kalman con cada par de salidas medidas: si
cambiaste camber, toe y presión a la vez, el filtro reparte el efecto según cuánto sabe de cada uno
(en vez de atribuírselo todo al camber) y descarta lo que no calza con nada (más de 3 desviaciones:
otra temperatura de pista, tráfico, una vuelta de entrada lenta).

Perfil por auto (manual de iRacing del Porsche 911 Cup 992.2):
- "minimum air pressures will likely provide the most performance, these minimums have been set such
  that the hot pressures reach a typical real-world hot pressure target": se apunta a la mínima del
  garage y la temperatura del centro solo la sube si muestra falta de presión clara.
- "For short races it is advised to run as much camber as possible at both ends": solo se quita camber
  si el interior se sobrecalienta (más de CAMBER_MAX_BAND sobre el objetivo; HP Academy pone el punto
  dulce del interior 10-15 °C sobre el exterior), mientras el objetivo no venga de tus propios tiempos.
- Atrás solo hay toe-in, por rueda; adelante el toe es total del eje.
"""

import json
import os
from typing import Dict, List, Optional

import numpy as np

from app.paths import data_dir
from app.units import (ATM_KPA, PRESSURE_CLICK_KPA, PRESSURE_WORDS, fmt_pressure, fmt_temp_delta, length_mm,
                       pressure_kpa, pressure_unit, snap_pressure, temp_unit, temps_c)
from app.util import WHEELS, atomic_write_json, num as _num, slug

BASE = data_dir("tire_model")
CORNERS = WHEELS
NAMES = {"LF": "delantera izquierda", "RF": "delantera derecha", "LR": "trasera izquierda", "RR": "trasera derecha"}

DEFAULT_TARGETS = {"front_spread": 7.0, "rear_spread": 5.0, "mid": 0.0}
SPREAD_RANGE = (2.0, 12.0)   # objetivos plausibles de diferencia interior-exterior (°C)
EXPLORE_STEP = 1.5           # cuánto mover el objetivo al explorar (°C)
MIN_STINTS = 3
TOLERANCE = 1.0              # °C: por debajo de esto es ruido de medición, ya está en el objetivo
CAMBER_STEP = 0.1            # grados por clic en el garage
MAX_CAMBER_MOVE = 0.5        # tope por salida: acercarse en pasos seguros y volver a medir
MAX_PRESSURE_CLICKS = 2      # 1 psi (6,9 kPa) por salida
# Una goma que no trabajó muestra las diferencias aplastadas (antes de cualquier salida el garage muestra
# 44 °C en todo): la medición sirve solo con calor real, sobre la pista o un mínimo si no se sabe la pista.
MIN_HEAT_RISE = 30.0
MIN_WARM_C = 55.0
# Sensibilidades a priori (media, desviación típica); el filtro de Kalman las corrige con tus salidas.
# El piso del camber evita que datos ruidosos (camber cambiado junto con presión y clima) lo dejen casi en
# cero y conviertan un error chico en un cambio enorme.
PRIOR = {"camber": (15.0, 6.0), "toe": (-1.0, 1.5), "pressure": (0.35, 0.2)}
BOUNDS = {"camber": (6.0, 40.0), "toe": (-6.0, 6.0), "pressure": (0.1, 2.0)}
CAMBER_MAX_BAND = 5.0        # °C sobre el objetivo antes de quitar camber en autos de "máximo camber"
UNDERINFLATED = 3.0          # °C de centro bajo los bordes para subir de la mínima en autos de "mínima rápida"
DRIFT = {"camber": 0.5, "toe": 0.1, "pressure": 0.02}   # por salida: el neumático cambia con pista y clima
NOISE_SPREAD = 1.5           # °C de ruido en una medición (redondeo del garage, tráfico, vuelta de entrada)
NOISE_MID = 1.0
# Límites y perfil por auto (clave = parte del nombre del auto). Ampliable con "tire_limits" en ingeniero.json:
# cold_min_kpa / cold_max_kpa, camber_front / camber_rear [mín, máx], hot_window_kpa {"front": [a, b], "rear": [a, b]},
# min_pressure_fastest, camber_bias "max", rear_toe_min_mm
TIRE_LIMITS: Dict[str, dict] = {
    "porsche 911 cup (992": {
        "cold_min_kpa": 159,           # 23,0 psi: el setup base de iRacing y el piso que se ve en el garage
        "min_pressure_fastest": True,  # manual 992.2 (ver arriba)
        "camber_bias": "max",          # manual 992.2: máximo camber en carreras cortas
        "rear_toe_min_mm": 0.0,        # manual 992.2: atrás solo toe-in
    },
}


def _slug(text: str) -> str:
    return slug(text, default="auto")


def limits_for(car: str, extra: Optional[Dict[str, dict]] = None) -> dict:
    table = dict(TIRE_LIMITS, **{k.lower(): v for k, v in (extra or {}).items()})
    for key, lim in table.items():
        if key in (car or "").lower():
            return lim
    return {}


def read_setup(setup: dict) -> Dict[str, dict]:
    """Por rueda: temperaturas (ext, centro, int) en °C, camber, toe en mm, presiones en kPa y unidades del garage"""
    out = {}
    tires = (setup or {}).get("Tires", {}) or {}
    chassis = (setup or {}).get("Chassis", {}) or {}
    front_toe = length_mm((chassis.get("Front", {}) or {}).get("ToeIn"))
    for c, key in CORNERS.items():
        t = tires.get(key, {}) or {}
        temps, t_unit = None, "C"
        for k, v in t.items():
            if k.startswith("LastTemps"):
                vals = temps_c(v)
                order = k[len("LastTemps"):]
                if len(vals) == 3 and sorted(order) == ["I", "M", "O"]:
                    named = dict(zip(order, vals))
                    temps, t_unit = (named["O"], named["M"], named["I"]), temp_unit(v)
        ch = chassis.get(key, {}) or {}
        toe = length_mm(ch.get("ToeIn"))
        if toe is None and c[1] == "F" and front_toe is not None:
            toe = front_toe / 2          # autos como el 992 Cup ajustan el toe delantero total del eje
        cold_text = t.get("StartingPressure", t.get("ColdPressure", ""))
        out[c] = {"temps": temps, "camber": _num(ch.get("Camber")), "toe": toe, "cold": pressure_kpa(cold_text),
                  "hot": pressure_kpa(t.get("LastHotPressure")), "unit": pressure_unit(cold_text), "temp_unit": t_unit}
    return out


def measured(data: Dict[str, dict]) -> bool:
    """iRacing deja las temperaturas iguales hasta que haces una salida y entras a pits"""
    vals = [x for d in data.values() if d["temps"] for x in d["temps"]]
    return len(vals) == 12 and max(vals) - min(vals) >= 2


def worked(data: Dict[str, dict], track_temp: Optional[float] = None) -> bool:
    """Las gomas trabajaron lo suficiente para que la medición diga algo del camber y la presión"""
    vals = [x for d in data.values() if d.get("temps") for x in d["temps"]]
    if not vals:
        return False
    mean = float(np.mean(vals))
    return mean - track_temp >= MIN_HEAT_RISE if track_temp is not None else mean >= MIN_WARM_C


def hot_after(cold_now: float, hot_now: float, cold_new: float) -> float:
    """Presión en caliente que dará la nueva presión en frío (volumen ~constante: escala la absoluta)"""
    return (hot_now + ATM_KPA) * (cold_new + ATM_KPA) / (cold_now + ATM_KPA) - ATM_KPA


def cold_for_hot(cold_now: float, hot_now: float, hot_target: float) -> float:
    return (hot_target + ATM_KPA) * (cold_now + ATM_KPA) / (hot_now + ATM_KPA) - ATM_KPA


def kalman_update(mean, cov, x, y: float, var: float):
    """Una observación y = x·θ + ruido(var): (media, covarianza) nuevas, o None si es atípica (> 3σ)"""
    x, m, P = np.asarray(x, float), np.asarray(mean, float), np.asarray(cov, float)
    s = float(x @ P @ x) + var
    innov = y - float(x @ m)
    if abs(innov) > 3 * np.sqrt(s):
        return None
    k = P @ x / s
    return m + k * innov, P - np.outer(k, x @ P)


def bayes_fit(X, y, prior_mean, prior_sd, noise_sd: float, weights=None) -> np.ndarray:
    """Regresión lineal bayesiana: con pocos datos se queda cerca de lo que ya sabemos, con muchos manda la data"""
    X, y = np.asarray(X, float), np.asarray(y, float)
    w = np.ones(len(y)) if weights is None else np.asarray(weights, float)
    prec0 = np.diag(1.0 / np.square(np.asarray(prior_sd, float)))
    A = prec0 + (X.T * w) @ X / noise_sd ** 2
    b = prec0 @ np.asarray(prior_mean, float) + (X.T * w) @ y / noise_sd ** 2
    return np.linalg.solve(A, b)


def camber_error(spread: float, target: float, limits: dict, learned: bool = False) -> float:
    """
    Spread a corregir con camber (+ quitar, − agregar). En autos de "máximo camber" y con el objetivo de
    partida (no aprendido de tus tiempos) solo se quita lo que pasa de objetivo + CAMBER_MAX_BAND.
    """
    err = spread - target
    if err > 0 and not learned and limits.get("camber_bias") == "max":
        err = max(0.0, err - CAMBER_MAX_BAND)
    return err


def _delta(a, b) -> Optional[float]:
    return None if a is None or b is None else b - a


class TireModel:
    def __init__(self, car: str, targets: Optional[dict] = None, limits: Optional[dict] = None):
        self.car = car
        self.path = os.path.join(BASE, f"{_slug(car)}.json")
        self.state = {"sens": {}, "cov": {}, "last": None, "stints": [], "learned": {}}
        try:
            with open(self.path, encoding="utf-8") as f:
                self.state.update(json.load(f))
        except (OSError, ValueError):
            pass
        for c in CORNERS:
            sens = self.state["sens"].setdefault(c, {})
            for k, (mu, _) in PRIOR.items():
                sens.setdefault(k, mu)
            cov = self.state.setdefault("cov", {}).setdefault(c, {})
            cov.setdefault("spread", [[PRIOR["camber"][1] ** 2, 0.0], [0.0, PRIOR["toe"][1] ** 2]])
            cov.setdefault("pressure", PRIOR["pressure"][1] ** 2)
        self.targets = dict(DEFAULT_TARGETS, **(targets or {}))   # targets explícitos (config) mandan
        self.forced = set(targets or {})
        self.limits = limits if limits is not None else limits_for(car)

    def save(self):
        atomic_write_json(self.path, self.state, indent=1)

    @staticmethod
    def metrics(temps) -> tuple:
        o, m, i = temps
        return i - o, m - (i + o) / 2    # (diferencia interior-exterior, centro vs bordes)

    def target_spread(self, corner: str, track: str = "") -> float:
        key = "front_spread" if corner[1] == "F" else "rear_spread"
        if key not in self.forced:
            learned = self.state.get("learned", {}).get(track or "*", {}).get(key)
            if learned is None:
                learned = self.state.get("learned", {}).get("*", {}).get(key)
            if learned is not None:
                return learned
        return self.targets[key]

    def target_source(self, axle_key: str, track: str = "") -> str:
        if axle_key in self.forced:
            return "configurado"
        learned = self.state.get("learned", {})
        if axle_key in learned.get(track or "*", {}) or axle_key in learned.get("*", {}):
            return "aprendido de tus tiempos"
        return "punto de partida"

    def hot_window(self, corner: str, track: str = "") -> Optional[tuple]:
        """Ventana de presión en caliente (kPa): la configurada, o tu óptimo aprendido ± un clic"""
        axle = "front" if corner[1] == "F" else "rear"
        win = (self.limits.get("hot_window_kpa") or {}).get(axle)
        if win:
            return float(win[0]), float(win[1])
        learned = self.state.get("learned", {}).get(track or "*", {}).get(f"{axle}_hot")
        if learned is not None:
            return learned - PRESSURE_CLICK_KPA, learned + PRESSURE_CLICK_KPA
        return None

    # ----------------------------------------------- búsqueda del óptimo
    @staticmethod
    def axle_spread(data: Dict[str, dict], axle: str) -> Optional[float]:
        vals = [TireModel.metrics(data[c]["temps"])[0] for c in CORNERS
                if c[1] == axle and data.get(c) and data[c]["temps"]]
        return float(np.mean(vals)) if vals else None

    @staticmethod
    def axle_hot(data: Dict[str, dict], axle: str) -> Optional[float]:
        vals = [data[c]["hot"] for c in CORNERS if c[1] == axle and data.get(c) and data[c].get("hot")]
        return round(float(np.mean(vals)), 1) if vals else None

    def record_stint(self, data: Dict[str, dict], lap_times: List[float], track: str = "",
                     track_temp: Optional[float] = None):
        """Guarda una salida: diferencia medida por eje, presión en caliente y tu ritmo (2+ vueltas limpias)"""
        laps = sorted(t for t in lap_times if t and t > 0)
        if len(laps) < 2 or not measured(data) or not worked(data, track_temp):
            return
        pace = float(np.mean(laps[:3]))
        self.state.setdefault("stints", []).append({
            "track": track, "front": self.axle_spread(data, "F"), "rear": self.axle_spread(data, "R"),
            "front_hot": self.axle_hot(data, "F"), "rear_hot": self.axle_hot(data, "R"),
            "pace": round(pace, 3), "best": round(laps[0], 3), "laps": len(laps)})
        self.state["stints"] = self.state["stints"][-60:]
        self.optimize(track)
        self.save()

    def optimize(self, track: str = "") -> Dict[str, dict]:
        """Para cada eje: mínimo de la parábola tiempo vs diferencia, o siguiente prueba a explorar"""
        stints = [x for x in self.state.get("stints", []) if x["track"] == track] or self.state.get("stints", [])
        report = {}
        learned = self.state.setdefault("learned", {}).setdefault(track or "*", {})
        for axle, key in (("front", "front_spread"), ("rear", "rear_spread")):
            pts = [(x[axle], x["pace"]) for x in stints if x.get(axle) is not None]
            if len(pts) < MIN_STINTS:
                report[key] = {"status": "faltan datos", "stints": len(pts)}
                continue
            xs, ys = np.array([p[0] for p in pts]), np.array([p[1] for p in pts])
            if xs.max() - xs.min() < 2.0:
                # Todas las pruebas con casi la misma diferencia: explorar hacia un lado
                nxt = float(np.clip(xs.mean() + EXPLORE_STEP, *SPREAD_RANGE))
                learned[key] = round(nxt, 1)
                report[key] = {"status": "explorando", "next": learned[key]}
                continue
            a, b, _ = np.polyfit(xs, ys, 2)
            if a > 1e-4:
                opt = float(np.clip(-b / (2 * a), xs.min() - EXPLORE_STEP, xs.max() + EXPLORE_STEP))
                opt = float(np.clip(opt, *SPREAD_RANGE))
                learned[key] = round(opt, 1)
                report[key] = {"status": "óptimo", "target": learned[key]}
            else:
                # Sin curva (más rápido en un extremo): seguir en esa dirección
                best_x = float(xs[np.argmin(ys)])
                direction = 1.0 if best_x >= xs.mean() else -1.0
                nxt = float(np.clip(best_x + direction * EXPLORE_STEP, *SPREAD_RANGE))
                learned[key] = round(nxt, 1)
                report[key] = {"status": "explorando", "next": learned[key]}
        # Presión en caliente: solo se adopta un mínimo claro dentro de lo probado (nunca se explora a ciegas)
        for axle in ("front_hot", "rear_hot"):
            pts = [(x[axle], x["pace"]) for x in stints if x.get(axle) is not None]
            if len(pts) < MIN_STINTS + 1:
                continue
            xs, ys = np.array([p[0] for p in pts]), np.array([p[1] for p in pts])
            if xs.max() - xs.min() < 2 * PRESSURE_CLICK_KPA:
                continue
            a, b, _ = np.polyfit(xs, ys, 2)
            opt = -b / (2 * a) if a > 1e-5 else None
            if opt is not None and xs.min() - PRESSURE_CLICK_KPA <= opt <= xs.max() + PRESSURE_CLICK_KPA:
                learned[axle] = round(float(opt), 1)
                report[axle] = {"status": "óptimo", "target": learned[axle]}
        return report

    def learn(self, data: Dict[str, dict], track_temp: Optional[float] = None):
        """Compara con la salida anterior: lo que cambiaste (camber, toe, presión) y cuánto se movieron las temperaturas"""
        if not worked(data, track_temp):
            return  # gomas frías: no sirve para aprender ni como referencia
        last = self.state.get("last")
        if last and all((last.get(c) or {}).get("temps") == (list(d["temps"]) if d["temps"] else None)
                        for c, d in data.items()):
            return  # misma medición que ya procesé
        if last:
            t0 = self.state.get("last_track_temp")
            extra = (0.15 * (track_temp - t0)) ** 2 if track_temp is not None and t0 is not None else 0.0
            for c in CORNERS:
                a, b = last.get(c), data.get(c)
                if not a or not b or not a.get("temps") or not b["temps"]:
                    continue
                (s0, m0), (s1, m1) = self.metrics(a["temps"]), self.metrics(b["temps"])
                d_cam, d_toe = _delta(a.get("camber"), b["camber"]), _delta(a.get("toe"), b.get("toe"))
                d_cold = _delta(a.get("cold"), b["cold"])
                geometry = (d_cam is not None and abs(d_cam) >= 0.05) or (d_toe is not None and abs(d_toe) >= 0.05)
                if geometry and d_cam is not None:
                    # Δspread = s_c·(−Δcamber) + s_t·Δtoe (toe desconocido = sin cambio, con más ruido)
                    var = 2 * NOISE_SPREAD ** 2 + extra + (0.0 if d_toe is not None else 1.0)
                    self._update(c, "spread", [-d_cam, d_toe or 0.0], s1 - s0, var)
                if d_cold is not None and abs(d_cold) >= 2:
                    var = 2 * NOISE_MID ** 2 + extra + (1.0 if geometry else 0.0)
                    self._update(c, "pressure", [d_cold], m1 - m0, var)
        self.state["last"] = {c: {"temps": list(d["temps"]) if d["temps"] else None, "camber": d["camber"],
                                  "toe": d.get("toe"), "cold": d["cold"]} for c, d in data.items()}
        self.state["last_track_temp"] = track_temp
        self.save()

    def _update(self, c: str, model: str, x: list, y: float, var: float):
        sens, cov = self.state["sens"][c], self.state["cov"][c]
        keys = ("camber", "toe") if model == "spread" else ("pressure",)
        mean = [sens[k] for k in keys]
        P = np.atleast_2d(cov[model]) + np.diag([DRIFT[k] ** 2 for k in keys])
        res = kalman_update(mean, P, x, y, var)
        if res is None:
            return   # no calza con el modelo: otra condición, no se aprende de esto
        m, P = res
        for k, v in zip(keys, m):
            sens[k] = round(float(np.clip(v, *BOUNDS[k])), 3)
        cov[model] = P.round(5).tolist() if model == "spread" else round(float(P[0, 0]), 5)

    # ------------------------------------------------- recomendación
    def _camber_limits(self, corner: str) -> Optional[tuple]:
        lim = self.limits.get("camber_front" if corner[1] == "F" else "camber_rear")
        return (float(lim[0]), float(lim[1])) if lim else None

    def _set_cold(self, r: dict, cold: float, wanted: float, why: str):
        new = snap_pressure(wanted)
        lo, hi = self.limits.get("cold_min_kpa"), self.limits.get("cold_max_kpa")
        if lo is not None:
            new = max(new, snap_pressure(lo))
        if hi is not None:
            new = min(new, snap_pressure(hi))
        if abs(new - cold) < PRESSURE_CLICK_KPA / 2:
            if lo is not None and wanted < cold - PRESSURE_CLICK_KPA / 2 and cold <= snap_pressure(lo) + 0.5:
                r["at_min"] = True
            return
        r["cold_new"], r["pressure_delta"], r["pressure_why"] = new, new - cold, why

    def recommend(self, data: Dict[str, dict], track: str = "",
                  toe_plan: Optional[Dict[str, float]] = None) -> Dict[str, dict]:
        """
        Ajuste por rueda hacia el objetivo.
        toe_plan: cambios de toe (mm por rueda) que se harán junto con estos; el camber los compensa.
        """
        recs = {}
        max_move = MAX_PRESSURE_CLICKS * PRESSURE_CLICK_KPA
        for c, d in data.items():
            if not d["temps"]:
                continue
            spread, mid = self.metrics(d["temps"])
            target = self.target_spread(c, track)
            sens = self.state["sens"][c]
            r = {"spread": spread, "mid": mid, "target": target, "camber_now": d["camber"], "cold_now": d["cold"],
                 "toe_now": d.get("toe"), "unit": d.get("unit", "kPa"), "temp_unit": d.get("temp_unit", "C")}
            # Camber hacia el spread objetivo, contando lo que moverá el toe que también se va a cambiar
            toe_shift = sens["toe"] * (toe_plan or {}).get(c, 0.0)
            if toe_shift:
                r["toe_shift"] = toe_shift
            learned = self.target_source("front_spread" if c[1] == "F" else "rear_spread", track) != "punto de partida"
            err = camber_error(spread + toe_shift, target, self.limits, learned)
            if err != spread + toe_shift - target:
                r["camber_ceiling"] = target + CAMBER_MAX_BAND
            if abs(err) > TOLERANCE:
                raw = max(-MAX_CAMBER_MOVE, min(MAX_CAMBER_MOVE, err / sens["camber"]))
                delta = round(round(raw / CAMBER_STEP) * CAMBER_STEP, 2)   # + quita camber negativo, - agrega
                if d["camber"] is not None:
                    new = round(d["camber"] + delta, 2)
                    lim = self._camber_limits(c)
                    if lim:
                        new = min(max(new, lim[0]), lim[1])
                    delta = round(new - d["camber"], 2)
                    if delta:
                        r["camber_new"] = new
                if delta:
                    r["camber_delta"] = delta
            # Presión en frío: centro = promedio de los bordes, en pasos del garage
            cold = d["cold"]
            perr = mid - self.targets["mid"]
            min_fast = (self.limits.get("min_pressure_fastest") and self.limits.get("cold_min_kpa") is not None
                        and self.hot_window(c, track) is None)
            if min_fast and cold is not None:
                # La mínima del garage es la más rápida en este auto: solo se sube si falta presión clara
                floor = snap_pressure(self.limits["cold_min_kpa"])
                if -perr > UNDERINFLATED:
                    self._set_cold(r, cold, cold + PRESSURE_CLICK_KPA, "centro")
                elif cold > floor + PRESSURE_CLICK_KPA / 2:
                    self._set_cold(r, cold, max(floor, cold - max_move), "minima")
                elif perr > UNDERINFLATED:       # centro claramente caliente y ya no se puede bajar más
                    r["at_min"] = True
            elif abs(perr) > TOLERANCE:
                move = max(-max_move, min(max_move, -perr / sens["pressure"]))
                if cold is not None:
                    self._set_cold(r, cold, cold + move, "centro")
                elif snap_pressure(move):
                    r["pressure_delta"] = snap_pressure(move)
            # Presión en caliente: dónde quedará y, si el perfil ya está bien, llevarla a la ventana
            hot = d.get("hot")
            if hot is not None and cold is not None and hot > cold:
                win = self.hot_window(c, track)
                r["hot_now"] = hot
                if win:
                    r["hot_window"] = win
                    pred = hot_after(cold, hot, r.get("cold_new", cold))
                    if "cold_new" not in r and not win[0] <= pred <= win[1]:
                        want = cold_for_hot(cold, hot, (win[0] + win[1]) / 2)
                        self._set_cold(r, cold, cold + max(-max_move, min(max_move, want - cold)), "caliente")
                    r["hot_pred"] = hot_after(cold, hot, r.get("cold_new", cold))
            recs[c] = r
        return recs

    def phrases(self, recs: Dict[str, dict]) -> List[str]:
        """Frases para la radio, una por rueda que necesita ajuste"""
        out = []
        for c, r in recs.items():
            u, tu = r.get("unit", "kPa"), r.get("temp_unit", "C")
            parts = []
            if "camber_new" in r:
                parts.append(f"camber de {r['camber_now']:.1f} a {r['camber_new']:.1f}")
            elif "camber_delta" in r:
                d = r["camber_delta"]
                parts.append(f"{'quita' if d > 0 else 'agrega'} {abs(d):.1f} grados de camber negativo")
            if "cold_new" in r:
                parts.append(f"presión de {fmt_pressure(r['cold_now'], u)} a {fmt_pressure(r['cold_new'], u)}")
            elif "pressure_delta" in r:
                d = r["pressure_delta"]
                parts.append(f"{'sube' if d > 0 else 'baja'} {fmt_pressure(abs(d), u)} {PRESSURE_WORDS[u]} en frío")
            if parts:
                why = []
                if "camber_delta" in r:
                    goal = (f"máximo útil {fmt_temp_delta(r['camber_ceiling'], tu, sign=False)}" if "camber_ceiling" in r
                            else f"objetivo {fmt_temp_delta(r['target'], tu, sign=False)}")
                    txt = f"interior {fmt_temp_delta(r['spread'], tu)} sobre el exterior, {goal}"
                    if r.get("toe_shift"):
                        txt += f", contando que el cambio de toe lo mueve {fmt_temp_delta(r['toe_shift'], tu)}"
                    why.append(txt)
                if r.get("pressure_why") == "caliente":
                    lo, hi = r["hot_window"]
                    why.append(f"en caliente {fmt_pressure(r['hot_now'], u)}, ventana {fmt_pressure(lo, u)} "
                               f"a {fmt_pressure(hi, u)}")
                elif r.get("pressure_why") == "minima":
                    why.append("en este auto la mínima del garage es la más rápida")
                elif "pressure_delta" in r:
                    why.append(f"centro {fmt_temp_delta(r['mid'], tu)} contra los bordes")
                    if "hot_window" in r and not r["hot_window"][0] <= r["hot_pred"] <= r["hot_window"][1]:
                        why.append(f"ojo, en caliente quedaría en {fmt_pressure(r['hot_pred'], u)}, fuera de la ventana")
                out.append(f"{NAMES[c].capitalize()}: {' y '.join(parts)} ({'; '.join(why)}).")
            elif r.get("at_min"):
                out.append(f"{NAMES[c].capitalize()}: el centro va {fmt_temp_delta(r['mid'], tu)} sobre los bordes "
                           f"pero la presión ya está en el mínimo del garage.")
        if recs and not out:
            out.append("Las cuatro ruedas están en el objetivo: contacto parejo, no toques camber ni presiones.")
        return out
