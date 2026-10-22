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

BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "setups")
WHEELS = {"LF": "LeftFront", "RF": "RightFront", "LR": "LeftRear", "RR": "RightRear"}
MAX_STINTS = 300
MIN_FIT_STINTS = 3   # con menos salidas medidas, una recta no es confiable: se usa la última + el modelo
# Campos de medición (no son setup): se guardan aparte
MEASURE_KEYS = ("LastTemps", "LastHotPressure", "TreadRemaining", "UpdateCount")
# Campos que importan para la conversación (para no gastar tokens con todo el setup)
KEY_FIELDS = re.compile(r"StartingPressure|Camber|ToeIn|ArbSetting|WingAngle|BrakePadMu|BrakePressureBias|"
                        r"TcSetting|AbsSetting|FuelLevel|RideHeight|SpringPerchOffset|CrossWeight|"
                        r"Spring|Damper|Bump|Rebound|Diff|Preload", re.I)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_") or "x"


def _nums(text) -> List[float]:
    return [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", str(text))]


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
                vals, order = _nums(v), k[len("LastTemps"):]
                if len(vals) == 3 and sorted(order) == ["I", "M", "O"]:
                    named = dict(zip(order, vals))
                    temps = [named["O"], named["M"], named["I"]]
        wear = _nums(t.get("TreadRemaining", ""))
        hot = _nums(t.get("LastHotPressure", ""))
        out[w] = {"temps": temps, "hot": hot[0] if hot else None, "wear_min": min(wear) if wear else None}
    vals = [x for d in out.values() if d["temps"] for x in d["temps"]]
    if len(vals) != 12 or max(vals) - min(vals) < 2:
        return None   # iRacing no actualizó las temperaturas (no hubo salida medida)
    return out


def wheel_setup(flat: Dict[str, str], wheel: str) -> dict:
    """Camber y presión en frío de una rueda desde el setup aplanado"""
    key = WHEELS[wheel]
    camber = next((_nums(v)[0] for k, v in flat.items() if key in k and k.endswith("Camber") and _nums(v)), None)
    cold = next((_nums(v)[0] for k, v in flat.items()
                 if key in k and k.endswith(("StartingPressure", "ColdPressure")) and _nums(v)), None)
    toe = next((_nums(v)[0] for k, v in flat.items() if key in k and k.endswith("ToeIn") and _nums(v)), None)
    return {"camber": camber, "cold": cold, "toe": toe}


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
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

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
        # No duplicar la misma medición (p. ej. reporte en pits + salir del auto)
        if self.stints and rec["medicion"] and self.stints[-1].get("medicion") == rec["medicion"] \
                and self.stints[-1].get("setup") == rec["setup"]:
            return self.stints[-1]
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

    def ideals(self, targets: Dict[str, float], sens: Optional[Dict[str, dict]] = None) -> Dict[str, dict]:
        """
        Presión en frío y camber ideales por rueda usando todas las salidas medidas:
        - presión: centro = promedio de los bordes (contacto plano)
        - camber: interior sobre exterior = objetivo del eje
        Con 2+ salidas con valores distintos se ajusta una recta a tus datos; con una sola,
        se usa la sensibilidad del modelo de neumáticos.
        """
        out = {}
        for w in WHEELS:
            rows = []
            for s in self.measured():
                m = s["medicion"].get(w) or {}
                ws = wheel_setup(s["setup"], w)
                if m.get("temps") and (ws["cold"] is not None or ws["camber"] is not None):
                    o, mid, i = m["temps"]
                    rows.append({"cold": ws["cold"], "camber": ws["camber"], "mid": mid - (o + i) / 2,
                                 "spread": i - o, "hot": m.get("hot"), "temps": m["temps"]})
            if not rows:
                continue
            target = targets["front" if w[1] == "F" else "rear"]
            s_w = (sens or {}).get(w, {"camber": 15.0, "pressure": 0.35})
            last = rows[-1]
            res = {"salidas": len(rows), "metodo": "última salida"}
            # Presión
            pr = [(r["cold"], r["mid"]) for r in rows if r["cold"] is not None]
            if len(pr) >= MIN_FIT_STINTS and len({c for c, _ in pr}) >= 2 \
                    and max(c for c, _ in pr) - min(c for c, _ in pr) >= 2:
                b, a = np.polyfit([c for c, _ in pr], [m for _, m in pr], 1)
                if b > 0.05:
                    lo, hi = min(c for c, _ in pr) - 15, max(c for c, _ in pr) + 15
                    res["presion_frio"] = round(float(np.clip(-a / b, lo, hi)))
                    res["metodo"] = "ajuste con tus salidas"
            if "presion_frio" not in res and last["cold"] is not None:
                res["presion_frio"] = round(last["cold"] - last["mid"] / s_w["pressure"])
            # Camber
            cr = [(r["camber"], r["spread"]) for r in rows if r["camber"] is not None]
            if len(cr) >= MIN_FIT_STINTS and len({c for c, _ in cr}) >= 2 \
                    and max(c for c, _ in cr) - min(c for c, _ in cr) >= 0.1:
                b, a = np.polyfit([c for c, _ in cr], [sp for _, sp in cr], 1)
                if b < -2:   # más camber negativo -> más diferencia interior/exterior
                    lo, hi = min(c for c, _ in cr) - 0.8, max(c for c, _ in cr) + 0.8
                    res["camber"] = round(float(np.clip((target - a) / b, lo, hi)), 1)
                    res["metodo"] = "ajuste con tus salidas"
            if "camber" not in res and last["camber"] is not None:
                res["camber"] = round(last["camber"] + (last["spread"] - target) / s_w["camber"], 1)
            res["ultima_medicion"] = {"ext_centro_int": [round(x) for x in last["temps"]],
                                      "presion_caliente": last["hot"]}
            out[w] = res
        return out

    def summary(self, targets: Dict[str, float], sens=None, last_n: int = 3) -> dict:
        """Resumen compacto para el ingeniero y para Claude"""
        if not self.stints:
            return {}
        recent = []
        for s in self.stints[-last_n:]:
            item = {"fecha": s["fecha"], "sesion": s.get("sesion"), "vueltas": s["vueltas"], "mejor": s.get("mejor"),
                    "ritmo": s.get("ritmo"), "pista_c": s.get("pista_c"), "setup": key_setup(s["setup"])}
            if s.get("medicion"):
                item["medicion"] = {w: {"ext_centro_int": [round(x) for x in d["temps"]], "caliente": d["hot"]}
                                    for w, d in s["medicion"].items() if d.get("temps")}
            if s.get("manejo"):
                item["manejo"] = s["manejo"]
            recent.append(item)
        best = self.best()
        out = {"salidas_guardadas": len(self.stints), "ultimas": recent,
               "ideales_por_rueda": self.ideals(targets, sens)}
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
