"""
Referencias por curva (dónde frenar, velocidad de vértice, dónde ir a fondo) por pista y auto.
Las genera el análisis de .ibt y las usa el ingeniero en vivo.
"""

import json
import os
import re
from typing import Optional

BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "references")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_") or "x"


def path_for(track: str, car: str) -> str:
    return os.path.join(BASE, f"{_slug(track)}__{_slug(car)}.json")


def save_references(track: str, car: str, track_len: float, corners,
                    fuel_per_lap: float = 0.0) -> Optional[str]:
    """corners: CornerResult del analizador; fuel_per_lap: consumo medido (L/vuelta)"""
    if not corners:
        return None
    os.makedirs(BASE, exist_ok=True)
    data = {"track": track, "car": car, "track_len": track_len,
            "fuel_per_lap": round(fuel_per_lap, 3) if fuel_per_lap else None, "corners": [
        {"n": c.number, "apex_m": c.apex_m, "brake_at_m": c.brake_at_m, "brake_m_before": c.brake_m_best,
         "brake_peak": c.brake_peak_best, "min_kmh": c.min_speed_best, "gear": c.gear,
         "throttle_m": c.throttle_m_best, "kind": c.kind}
        for c in corners]}
    path = path_for(track, car)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
    return path


def load_references(track: str, car: str, track_len: float) -> Optional[dict]:
    try:
        with open(path_for(track, car), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if abs(data.get("track_len", 0) - track_len) > 20:
        return None  # otra configuración de la pista
    return data
