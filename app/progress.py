"""
Seguimiento de progreso por pista y auto, sesión a sesión.

Cada sesión guarda: mejor vuelta, vuelta ideal (tus mejores pasos por curva/minisector), ritmo (media de las
3 mejores), consistencia (desviación estándar de las vueltas limpias), vueltas, salidas de pista, incidentes
y balance (gradiente de subviraje). La tendencia se calcula con una regresión lineal de la mejor vuelta
contra el número de sesión (segundos ganados por sesión).
"""

import json
import os
import re
import time
from typing import Dict, List, Optional

import numpy as np

BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "progress")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_") or "x"


def fmt(t: Optional[float]) -> str:
    if not t:
        return "--"
    m = int(t // 60)
    return f"{m}:{t - 60 * m:06.3f}" if m else f"{t:.3f}"


class ProgressDB:
    def __init__(self, track: str, car: str, base: Optional[str] = None):
        self.track, self.car = track, car
        self.path = os.path.join(base or BASE, f"{_slug(track)}__{_slug(car)}.json")
        self.data = {"track": track, "car": car, "sessions": []}
        try:
            with open(self.path, encoding="utf-8") as f:
                self.data.update(json.load(f))
        except (OSError, ValueError):
            pass

    @property
    def sessions(self) -> List[dict]:
        return self.data["sessions"]

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    def add(self, session_id: str, when: str, kind: str, lap_times: List[float], ideal: Optional[float] = None,
            offtracks: int = 0, incidents: int = 0, balance: Optional[dict] = None, source: str = "") -> Optional[dict]:
        """Agrega o actualiza (mismo session_id) una sesión. lap_times = vueltas válidas (sin pits)."""
        laps = [t for t in lap_times if t and t > 0]
        if not laps:
            return None
        best = min(laps)
        clean = [t for t in laps if t <= best * 1.03]
        top = sorted(clean)[:3]
        entry = {"id": session_id, "fecha": when, "tipo": kind, "fuente": source, "vueltas": len(laps),
                 "mejor": round(best, 3), "ideal": round(ideal, 3) if ideal else None,
                 "ritmo": round(float(np.mean(top)), 3),
                 "consistencia": round(float(np.std(clean, ddof=1)), 3) if len(clean) >= 3 else None,
                 "salidas_pista": offtracks, "incidentes": incidents, "balance": balance or {}}
        for i, s in enumerate(self.sessions):
            if s["id"] == session_id:
                self.sessions[i] = entry
                break
        else:
            self.sessions.append(entry)
        self.sessions.sort(key=lambda s: s["fecha"])
        self.save()
        return entry

    def summary(self) -> dict:
        if not self.sessions:
            return {}
        ss = self.sessions
        best = min(ss, key=lambda s: s["mejor"])
        out = {"sesiones": len(ss), "primera": ss[0], "ultima": ss[-1], "record": best}
        if len(ss) >= 3:
            y = np.array([s["mejor"] for s in ss])
            out["tendencia_s_por_sesion"] = round(float(np.polyfit(np.arange(len(y)), y, 1)[0]), 3)
        return out

    def phrases(self) -> List[str]:
        sm = self.summary()
        if not sm:
            return []
        first, last, rec = sm["primera"], sm["ultima"], sm["record"]
        out = [f"En {self.track}: récord {fmt(rec['mejor'])} ({rec['fecha'][:10]})"]
        if sm["sesiones"] >= 2:
            gain = first["mejor"] - rec["mejor"]
            out[0] += f", {gain:.2f} segundos más rápido que tu primera sesión ({sm['sesiones']} sesiones)"
        out[0] += "."
        if last.get("ideal") and last["ideal"] < last["mejor"]:
            out.append(f"Última sesión: mejor {fmt(last['mejor'])}, ideal {fmt(last['ideal'])}: tienes "
                       f"{last['mejor'] - last['ideal']:.2f} segundos por juntar.")
        if last.get("consistencia") is not None:
            prev = [s["consistencia"] for s in self.sessions[:-1] if s.get("consistencia") is not None]
            txt = f"Consistencia más o menos {last['consistencia']:.2f} segundos"
            if prev:
                txt += " (mejor que antes)" if last["consistencia"] < np.mean(prev) else " (peor que antes)"
            out.append(txt + ".")
        if "tendencia_s_por_sesion" in sm:
            t = sm["tendencia_s_por_sesion"]
            out.append(f"Tendencia: {'bajas' if t < 0 else 'subes'} {abs(t):.2f} segundos por sesión.")
        return out


def session_id_from_path(path: str) -> str:
    return os.path.splitext(os.path.basename(path))[0]


def when_from_path(path: str) -> str:
    m = re.search(r"(\d{4}-\d{2}-\d{2}) (\d{2})-(\d{2})", os.path.basename(path))
    return f"{m.group(1)} {m.group(2)}:{m.group(3)}" if m else time.strftime("%Y-%m-%d %H:%M")
