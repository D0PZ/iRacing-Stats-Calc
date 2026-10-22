"""
Contacto perfecto del neumático: camber y presión exactos por rueda.

Objetivos (medidos al entrar a pits, de exterior a interior):
- Interior más caliente que el exterior por `spread` °C (en curva la banda se aplana y apoya completa).
- Centro igual al promedio de los bordes (ni abombado por exceso de presión ni hundido por falta).

El ajuste se calcula con la sensibilidad de cada rueda (°C por grado de camber, °C por kPa),
que se aprende de tus propios cambios entre salidas y se guarda por auto.

Objetivo de camber (diferencia interior - exterior):
- Punto de partida: 7 °C adelante (guía de neumáticos de iRacing: interior más caliente, no más de ~7 °C)
  y 5 °C atrás (manual del 992 Cup: atrás algo menos de camber porque también tracciona).
- Después se busca TU óptimo: cada salida guarda la diferencia medida y tus tiempos; con 3+ salidas
  distintas se ajusta una parábola tiempo de vuelta vs diferencia por eje y se toma su mínimo.
  Si aún no hay mínimo claro, se propone la siguiente prueba hacia donde fuiste más rápido.
Presión: objetivo exacto centro = promedio de los bordes (banda plana, contacto completo).
"""

import json
import os
import re
from typing import Dict, List, Optional

import numpy as np

BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "tire_model")
CORNERS = {"LF": "LeftFront", "RF": "RightFront", "LR": "LeftRear", "RR": "RightRear"}
NAMES = {"LF": "delantera izquierda", "RF": "delantera derecha", "LR": "trasera izquierda", "RR": "trasera derecha"}

DEFAULT_TARGETS = {"front_spread": 7.0, "rear_spread": 5.0, "mid": 0.0}
SPREAD_RANGE = (2.0, 12.0)   # objetivos plausibles de diferencia interior-exterior (°C)
EXPLORE_STEP = 1.5           # cuánto mover el objetivo al explorar (°C)
MIN_STINTS = 3
TOLERANCE = 1.0              # °C: por debajo de esto es ruido de medición, ya está en el objetivo
CAMBER_STEP = 0.1            # grados por clic en el garage
PRESSURE_STEP = 1.0          # kPa por clic
# Sensibilidades iniciales (se corrigen con tus datos): °C de diferencia por unidad de ajuste
DEFAULT_CAMBER_SENS = 15.0   # °C de (interior - exterior) por grado de camber negativo
DEFAULT_PRESSURE_SENS = 0.35  # °C de (centro - bordes) por kPa
MAX_CAMBER_MOVE = 0.5        # tope por salida: acercarse en pasos seguros y volver a medir
MAX_PRESSURE_MOVE = 8.0


def _nums(text) -> List[float]:
    return [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", str(text))]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_") or "auto"


def read_setup(setup: dict) -> Dict[str, dict]:
    """Por rueda: temperaturas exterior/centro/interior, camber y presión en frío del setup"""
    out = {}
    tires = (setup or {}).get("Tires", {}) or {}
    chassis = (setup or {}).get("Chassis", {}) or {}
    for c, key in CORNERS.items():
        t = tires.get(key, {}) or {}
        temps = None
        for k, v in t.items():
            if k.startswith("LastTemps"):
                vals = _nums(v)
                order = k[len("LastTemps"):]
                if len(vals) == 3 and sorted(order) == ["I", "M", "O"]:
                    named = dict(zip(order, vals))
                    temps = (named["O"], named["M"], named["I"])
        camber = _nums((chassis.get(key, {}) or {}).get("Camber", ""))
        cold = _nums(t.get("StartingPressure", t.get("ColdPressure", "")))
        out[c] = {"temps": temps, "camber": camber[0] if camber else None,
                  "cold": cold[0] if cold else None, "hot": (_nums(t.get("LastHotPressure", "")) or [None])[0]}
    return out


def measured(data: Dict[str, dict]) -> bool:
    """iRacing deja las temperaturas iguales hasta que haces una salida y entras a pits"""
    vals = [x for d in data.values() if d["temps"] for x in d["temps"]]
    return len(vals) == 12 and max(vals) - min(vals) >= 2


class TireModel:
    def __init__(self, car: str, targets: Optional[dict] = None):
        self.car = car
        self.path = os.path.join(BASE, f"{_slug(car)}.json")
        self.state = {"sens": {c: {"camber": DEFAULT_CAMBER_SENS, "pressure": DEFAULT_PRESSURE_SENS}
                               for c in CORNERS}, "last": None, "stints": [], "learned": {}}
        try:
            with open(self.path, encoding="utf-8") as f:
                self.state.update(json.load(f))
        except (OSError, ValueError):
            pass
        self.targets = dict(DEFAULT_TARGETS, **(targets or {}))   # targets explícitos (config) mandan
        self.forced = set(targets or {})

    def save(self):
        os.makedirs(BASE, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=1)

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

    # ----------------------------------------------- búsqueda del óptimo
    @staticmethod
    def axle_spread(data: Dict[str, dict], axle: str) -> Optional[float]:
        vals = [TireModel.metrics(data[c]["temps"])[0] for c in CORNERS
                if c[1] == axle and data.get(c) and data[c]["temps"]]
        return float(np.mean(vals)) if vals else None

    def record_stint(self, data: Dict[str, dict], lap_times: List[float], track: str = ""):
        """Guarda una salida: diferencia medida por eje y tu ritmo (necesita 2+ vueltas limpias)"""
        laps = sorted(t for t in lap_times if t and t > 0)
        if len(laps) < 2 or not measured(data):
            return
        pace = float(np.mean(laps[:3]))
        self.state.setdefault("stints", []).append({
            "track": track, "front": self.axle_spread(data, "F"), "rear": self.axle_spread(data, "R"),
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
        return report

    def learn(self, data: Dict[str, dict]):
        """Compara con la salida anterior: si cambiaste camber o presión, mide su efecto real"""
        last = self.state.get("last")
        if last and all((last.get(c) or {}).get("temps") == (list(d["temps"]) if d["temps"] else None)
                        for c, d in data.items()):
            return  # misma medición que ya procesé
        if last:
            for c in CORNERS:
                a, b = last.get(c), data.get(c)
                if not a or not b or not a.get("temps") or not b["temps"]:
                    continue
                (s0, m0), (s1, m1) = self.metrics(a["temps"]), self.metrics(b["temps"])
                sens = self.state["sens"][c]
                if a.get("camber") is not None and b["camber"] is not None and abs(b["camber"] - a["camber"]) >= 0.1 \
                        and (a.get("cold") == b["cold"]):
                    obs = (s1 - s0) / (a["camber"] - b["camber"])        # más negativo -> más diferencia
                    if 5 <= obs <= 40:
                        sens["camber"] = round(0.5 * sens["camber"] + 0.5 * obs, 2)
                if a.get("cold") is not None and b["cold"] is not None and abs(b["cold"] - a["cold"]) >= 2 \
                        and (a.get("camber") == b["camber"]):
                    obs = (m1 - m0) / (b["cold"] - a["cold"])
                    if 0.1 <= obs <= 1.5:
                        sens["pressure"] = round(0.5 * sens["pressure"] + 0.5 * obs, 3)
        self.state["last"] = {c: {"temps": list(d["temps"]) if d["temps"] else None, "camber": d["camber"],
                                  "cold": d["cold"]} for c, d in data.items()}
        self.save()

    def recommend(self, data: Dict[str, dict], track: str = "") -> Dict[str, dict]:
        """Ajuste exacto por rueda hacia el objetivo"""
        recs = {}
        for c, d in data.items():
            if not d["temps"]:
                continue
            spread, mid = self.metrics(d["temps"])
            target = self.target_spread(c, track)
            sens = self.state["sens"][c]
            r = {"spread": spread, "mid": mid, "target": target, "camber_now": d["camber"], "cold_now": d["cold"]}
            err = spread - target
            if abs(err) > TOLERANCE:
                raw = max(-MAX_CAMBER_MOVE, min(MAX_CAMBER_MOVE, err / sens["camber"]))
                delta = round(round(raw / CAMBER_STEP) * CAMBER_STEP, 2)   # + quita camber negativo, - agrega
                if delta:
                    r["camber_delta"] = delta
                    if d["camber"] is not None:
                        r["camber_new"] = round(d["camber"] + delta, 2)
            perr = mid - self.targets["mid"]
            if abs(perr) > TOLERANCE:
                raw = max(-MAX_PRESSURE_MOVE, min(MAX_PRESSURE_MOVE, perr / sens["pressure"]))
                delta = round(raw / PRESSURE_STEP) * PRESSURE_STEP
                if delta:
                    r["pressure_delta"] = -delta
                    if d["cold"] is not None:
                        r["cold_new"] = round(d["cold"] - delta, 1)
            recs[c] = r
        return recs

    def phrases(self, recs: Dict[str, dict]) -> List[str]:
        """Frases para la radio, una por rueda que necesita ajuste"""
        out = []
        for c, r in recs.items():
            parts = []
            if "camber_new" in r:
                parts.append(f"camber de {r['camber_now']:.1f} a {r['camber_new']:.1f}")
            elif "camber_delta" in r:
                d = r["camber_delta"]
                parts.append(f"{'quita' if d > 0 else 'agrega'} {abs(d):.1f} grados de camber negativo")
            if "cold_new" in r:
                parts.append(f"presión de {r['cold_now']:.0f} a {r['cold_new']:.0f}")
            elif "pressure_delta" in r:
                d = r["pressure_delta"]
                parts.append(f"{'sube' if d > 0 else 'baja'} {abs(d):.0f} kilopascales en frío")
            if parts:
                why = []
                if "camber_delta" in r:
                    why.append(f"interior {r['spread']:+.0f} sobre el exterior, objetivo {r['target']:.0f}")
                if "pressure_delta" in r:
                    why.append(f"centro {r['mid']:+.0f} contra los bordes")
                out.append(f"{NAMES[c].capitalize()}: {' y '.join(parts)} ({'; '.join(why)}).")
        if recs and not out:
            out.append("Las cuatro ruedas están en el objetivo: contacto parejo, no toques camber ni presiones.")
        return out
