"""
Base de datos de setups por pista y auto.

Cada salida (stint) guarda el setup completo que usaste, lo que se midió al entrar a pits
(temperaturas exterior/centro/interior, presión en caliente, desgaste), tu ritmo y cómo se
comportó el auto. Así el ingeniero no depende de la sesión actual: en una sesión nueva en la
misma pista con el mismo auto ya sabe qué funcionó y estima los valores ideales con TODO el historial.
"""

import json
import os
import re
import time
from typing import Dict, List, Optional

import numpy as np

from app.paths import data_dir
from app.tire_model import (BOUNDS, NOISE_MID, NOISE_SPREAD, PRIOR, UNDERINFLATED, bayes_fit, camber_error,
                             worked)
from app.units import PRESSURE_CLICK_KPA, in_unit, length_mm, pressure_kpa, pressure_unit, snap_pressure, temps_c
from app.util import WHEELS, atomic_write_json, nums as _nums, slug as _slug

BASE = data_dir("setups")
MAX_STINTS = 300
RECENCY = 0.85       # peso de cada salida anterior en los ideales: lo reciente pesa más (pista y goma evolucionan)
MAX_IDEAL_MOVE = 0.5  # grados: el ideal no se aleja más que esto de tu camber actual (datos ruidosos)
# Campos de medición (no son setup): se guardan aparte
MEASURE_KEYS = ("LastTemps", "LastHotPressure", "TreadRemaining", "UpdateCount")
# Campos que importan para la conversación (para no gastar tokens con todo el setup)
KEY_FIELDS = re.compile(r"StartingPressure|Camber|ToeIn|ArbSetting|WingAngle|BrakePadMu|BrakePressureBias|"
                        r"TcSetting|AbsSetting|FuelLevel|RideHeight|SpringPerchOffset|CrossWeight|"
                        r"Spring|Damper|Bump|Rebound|Diff|Preload", re.I)


def flatten_setup(setup: dict) -> Dict[str, str]:
    """{'Tires > LeftFront > StartingPressure': '183 kPa', ...} sin campos de medición"""
    out = {}

    def walk(d, path=()):
        if isinstance(d, dict):
            for k, v in d.items():
                walk(v, path + (str(k),))
        elif path and not any(path[-1].startswith(m) for m in MEASURE_KEYS):
            out[" > ".join(path)] = str(d)
    walk(setup or {})
    return out


def measurements(setup: dict) -> Optional[Dict[str, dict]]:
    """Temperaturas (ext, centro, int), presión en caliente y desgaste por rueda, si están medidas"""
    tires = (setup or {}).get("Tires", {}) or {}
    out = {}
    for w, key in WHEELS.items():
        t = tires.get(key, {}) or {}
        temps = None
        for k, v in t.items():
            if k.startswith("LastTemps"):
                vals, order = temps_c(v), k[len("LastTemps"):]
                if len(vals) == 3 and sorted(order) == ["I", "M", "O"]:
                    named = dict(zip(order, vals))
                    temps = [round(named[x], 1) for x in "OMI"]
        wear = _nums(t.get("TreadRemaining", ""))
        hot = pressure_kpa(t.get("LastHotPressure"))
        out[w] = {"temps": temps, "hot": None if hot is None else round(hot, 1), "wear_min": min(wear) if wear else None}
    vals = [x for d in out.values() if d["temps"] for x in d["temps"]]
    if len(vals) != 12 or max(vals) - min(vals) < 2:
        return None   # iRacing no actualizó las temperaturas (no hubo salida medida)
    return out


def wheel_setup(flat: Dict[str, str], wheel: str) -> dict:
    """Camber, presión en frío (kPa), toe (mm) y unidad de presión de una rueda desde el setup aplanado"""
    key = WHEELS[wheel]
    camber = next((_nums(v)[0] for k, v in flat.items() if key in k and k.endswith("Camber") and _nums(v)), None)
    cold_text = next((v for k, v in flat.items()
                      if key in k and k.endswith(("StartingPressure", "ColdPressure")) and _nums(v)), None)
    toe = next((length_mm(v) for k, v in flat.items() if key in k and k.endswith("ToeIn") and _nums(v)), None)
    if toe is None and wheel[1] == "F":   # toe delantero total del eje (992 Cup): la mitad por rueda
        total = next((length_mm(v) for k, v in flat.items() if k.endswith("Front > ToeIn") and _nums(v)), None)
        toe = None if total is None else total / 2
    return {"camber": camber, "cold": pressure_kpa(cold_text), "toe": toe, "unit": pressure_unit(cold_text)}


def key_setup(flat: Dict[str, str]) -> Dict[str, str]:
    """Solo los campos que importan, con nombres cortos"""
    out = {}
    for k, v in flat.items():
        if KEY_FIELDS.search(k.split(" > ")[-1]):
            short = k.replace("Chassis > ", "").replace("Tires > ", "").replace("InCarDials > ", "")
            out[short] = v
    return out


class SetupDB:
    def __init__(self, track: str, car: str, base: Optional[str] = None):
        self.track, self.car = track, car
        self.path = os.path.join(base or BASE, f"{_slug(track)}__{_slug(car)}.json")
        self.data = {"track": track, "car": car, "stints": []}
        try:
            with open(self.path, encoding="utf-8") as f:
                self.data.update(json.load(f))
        except (OSError, ValueError):
            pass

    @property
    def stints(self) -> List[dict]:
        return self.data["stints"]

    def save(self):
        atomic_write_json(self.path, self.data, ensure_ascii=False, indent=1)

    def add_stint(self, setup: dict, lap_times: List[float], session: str = "", track_temp=None, air_temp=None,
                  handling: Optional[dict] = None, fuel_per_lap=None, offtracks: int = 0,
                  recommendations: Optional[List[str]] = None, when: Optional[float] = None,
                  source: str = "vivo", fuel_start: Optional[float] = None) -> dict:
        laps = sorted(t for t in lap_times if t and t > 0)
        rec = {
            "fecha": time.strftime("%Y-%m-%d %H:%M", time.localtime(when or time.time())),
            "sesion": session, "fuente": source,
            "pista_c": None if track_temp is None else round(float(track_temp), 1),
            "aire_c": None if air_temp is None else round(float(air_temp), 1),
            "vueltas": len(laps), "mejor": round(laps[0], 3) if laps else None,
            "ritmo": round(float(np.mean(laps[:3])), 3) if len(laps) >= 2 else None,
            "setup": flatten_setup(setup), "medicion": measurements(setup),
            "manejo": handling or {}, "combustible_por_vuelta": fuel_per_lap,
            "salidas_pista": offtracks, "recomendaciones": recommendations or [],
            "tiempos": [round(float(t), 3) for t in lap_times if t and t > 0],      # en orden, para pruebas A/B
            "combustible_inicio": None if fuel_start is None else round(float(fuel_start), 2),
        }
        # Misma medición que la salida anterior: el garage no registró temperaturas nuevas (iRacing no
        # refrescó el setup). Las vueltas sí son nuevas y se guardan; la medición no se cuenta dos veces.
        last = self.stints[-1] if self.stints else None
        if last and rec["medicion"] and last.get("medicion") == rec["medicion"] and last.get("setup") == rec["setup"]:
            if not laps:
                return last
            rec["medicion"] = None
        self.stints.append(rec)
        self.data["stints"] = self.stints[-MAX_STINTS:]
        self.save()
        return rec

    # ------------------------------------------- perch vs cross weight
    def add_cross_point(self, perch: Dict[str, float], cross: float) -> bool:
        """Cada combinación de perch que pruebas en el garage y el cross weight que resulta"""
        pts = self.data.setdefault("cross_points", [])
        point = {"perch": {w: round(v, 2) for w, v in perch.items()}, "cross": round(cross, 2)}
        if point in pts:
            return False
        pts.append(point)
        self.data["cross_points"] = pts[-100:]
        self.save()
        return True

    @property
    def cross_points(self) -> List[dict]:
        return self.data.get("cross_points", [])

    # --------------------------------------------------------- consultas
    def measured(self) -> List[dict]:
        return [s for s in self.stints if s.get("medicion")]

    def best(self) -> Optional[dict]:
        timed = [s for s in self.stints if s.get("ritmo")]
        return min(timed, key=lambda s: s["ritmo"]) if timed else None

    def ideals(self, targets: Dict[str, float], sens: Optional[Dict[str, dict]] = None,
               unit: Optional[str] = None, limits: Optional[dict] = None) -> Dict[str, dict]:
        """
        Presión en frío y camber ideales por rueda con todas las salidas medidas en que las gomas trabajaron:
        - presión: centro = promedio de los bordes (contacto plano), en pasos del garage
        - camber: interior sobre exterior = objetivo del eje, con el toe que tienes ahora
        Regresión bayesiana por rueda (spread ~ camber + toe, centro ~ presión) que parte de las
        sensibilidades del modelo de neumáticos: con una salida equivale a "última medición + sensibilidad",
        con varias manda tu data, y si cambiaste camber y toe a la vez no le atribuye todo al camber.
        Las salidas recientes pesan más. limits: perfil del auto (mínima más rápida, máximo camber).
        targets: {"front", "rear", "learned"} (learned = objetivo sacado de tus tiempos)
        """
        limits = limits or {}
        out = {}
        stints = [s for s in self.measured() if worked(s["medicion"], s.get("pista_c"))]
        for w in WHEELS:
            rows = []
            for s in stints:
                m = s["medicion"].get(w) or {}
                ws = wheel_setup(s["setup"], w)
                if m.get("temps") and (ws["cold"] is not None or ws["camber"] is not None):
                    o, mid, i = m["temps"]
                    rows.append(dict(ws, mid=mid - (o + i) / 2, spread=i - o, hot=m.get("hot"), temps=m["temps"]))
            if not rows:
                continue
            target = targets["front" if w[1] == "F" else "rear"]
            s_w = dict({k: mu for k, (mu, _) in PRIOR.items()}, **((sens or {}).get(w) or {}))
            last = rows[-1]
            u = unit or last["unit"]
            res = {"salidas": len(rows), "metodo": "última salida" if len(rows) == 1 else "ajuste con tus salidas",
                   "unidad": u}
            # Presión: centro = b + s_p·(frío − media)
            pr = [r for r in rows if r["cold"] is not None]
            if pr:
                x = np.array([r["cold"] for r in pr])
                x0 = float(x.mean())
                b, s_p = bayes_fit(np.column_stack([np.ones(len(x)), x - x0]), [r["mid"] for r in pr],
                                   [0.0, s_w["pressure"]], [50.0, PRIOR["pressure"][1]], NOISE_MID,
                                   RECENCY ** np.arange(len(pr))[::-1])
                s_p = max(s_p, BOUNDS["pressure"][0])
                ideal = float(np.clip(x0 - b / s_p, x.min() - 15, x.max() + 15))
                floor = limits.get("cold_min_kpa")
                if floor is not None:
                    floor = snap_pressure(floor)
                    if limits.get("min_pressure_fastest"):
                        # La mínima, salvo que lo MEDIDO en la mínima muestre el centro claramente frío
                        near = [r["mid"] for r in pr if r["cold"] <= floor + PRESSURE_CLICK_KPA / 2]
                        under = bool(near) and float(np.mean(near[-3:])) < -UNDERINFLATED
                        ideal = floor + (PRESSURE_CLICK_KPA if under else 0.0)
                        res["metodo_presion"] = "mínima del garage (manual del auto)"
                    ideal = max(ideal, floor)
                res["presion_frio"] = in_unit(snap_pressure(ideal), u)
            # Camber: spread = a + s_c·(−(camber − media)) + s_t·(toe − media)
            cr = [r for r in rows if r["camber"] is not None]
            if cr:
                cam = np.array([r["camber"] for r in cr])
                c0 = float(cam.mean())
                cols, prior_m, prior_sd = [np.ones(len(cr)), -(cam - c0)], [0.0, s_w["camber"]], [50.0, PRIOR["camber"][1]]
                use_toe = all(r["toe"] is not None for r in cr)
                if use_toe:
                    toe = np.array([r["toe"] for r in cr])
                    t0 = float(toe.mean())
                    cols.append(toe - t0)
                    prior_m.append(s_w["toe"])
                    prior_sd.append(PRIOR["toe"][1])
                coef = bayes_fit(np.column_stack(cols), [r["spread"] for r in cr], prior_m, prior_sd, NOISE_SPREAD,
                                 RECENCY ** np.arange(len(cr))[::-1])
                toe_term = coef[2] * (cr[-1]["toe"] - t0) if use_toe else 0.0
                s_c, now = max(coef[1], BOUNDS["camber"][0]), cr[-1]["camber"]
                spread_now = coef[0] - s_c * (now - c0) + toe_term          # lo que da tu camber actual, según el ajuste
                err = camber_error(spread_now, target, limits, bool(targets.get("learned")))
                ideal = now + err / s_c                                    # + quita camber negativo
                lo, hi = max(cam.min() - 0.5, now - MAX_IDEAL_MOVE), min(cam.max() + 0.5, now + MAX_IDEAL_MOVE)
                lim = limits.get("camber_front" if w[1] == "F" else "camber_rear")
                if lim:
                    lo, hi = max(lo, lim[0]), min(hi, lim[1])
                res["camber"] = round(float(np.clip(ideal, lo, hi)), 1)
                if use_toe:
                    res["toe_mm"] = round(cr[-1]["toe"], 2)
            res["ultima_medicion"] = {"ext_centro_int": [round(x) for x in last["temps"]],
                                      "presion_caliente": in_unit(last["hot"], u)}
            out[w] = res
        return out

    def pressure_unit(self) -> str:
        """Unidad de presión del garage en la última salida guardada"""
        for s in reversed(self.stints):
            u = wheel_setup(s.get("setup", {}), "LF")
            if u["cold"] is not None:
                return u["unit"]
        return "kPa"

    def summary(self, targets: Dict[str, float], sens=None, last_n: int = 3, limits: Optional[dict] = None) -> dict:
        """Resumen compacto para el ingeniero y para Claude"""
        if not self.stints:
            return {}
        unit = self.pressure_unit()
        recent = []
        for s in self.stints[-last_n:]:
            item = {"fecha": s["fecha"], "sesion": s.get("sesion"), "vueltas": s["vueltas"], "mejor": s.get("mejor"),
                    "ritmo": s.get("ritmo"), "pista_c": s.get("pista_c"), "setup": key_setup(s["setup"])}
            if s.get("medicion"):
                item["medicion"] = {w: {"ext_centro_int": [round(x) for x in d["temps"]], "caliente": in_unit(d["hot"], unit)}
                                    for w, d in s["medicion"].items() if d.get("temps")}
            if s.get("manejo"):
                item["manejo"] = s["manejo"]
            recent.append(item)
        best = self.best()
        out = {"salidas_guardadas": len(self.stints), "unidad_presion": unit, "ultimas": recent,
               "ideales_por_rueda": self.ideals(targets, sens, unit, limits)}
        if best:
            out["mejor_salida"] = {"fecha": best["fecha"], "ritmo": best["ritmo"], "mejor": best["mejor"],
                                   "setup": key_setup(best["setup"])}
        return out


def import_setup_report(db: SetupDB, path: str) -> Optional[dict]:
    """Recupera una salida desde un reporte logs/setup_*.txt (setup completo + temperaturas)"""
    try:
        text = open(path, encoding="utf-8").read()
    except OSError:
        return None
    if "Setup actual:" not in text:
        return None
    setup: dict = {}
    for line in text.split("Setup actual:", 1)[1].splitlines():
        line = line.strip()
        if " > " not in line or ": " not in line:
            continue
        keypath, value = line.rsplit(": ", 1) if line.count(": ") == 1 else line.split(": ", 1)
        node = setup
        parts = keypath.split(" > ")
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = value
    m = re.search(r"\((\d+) vueltas analizadas\)", text)
    laps = int(m.group(1)) if m else 0
    m = re.search(r"setup_(\d{8})_(\d{6})", os.path.basename(path))
    when = time.mktime(time.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")) if m else None
    rec = db.add_stint(setup, [], session="", when=when, source="reporte recuperado")
    rec["vueltas"] = laps
    db.save()
    return rec
