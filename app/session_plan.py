"""
Plan de qualy y carrera: combustible y pastillas de freno a partir de tu telemetría.

Pastillas: la fricción de la pastilla no cambia la frenada máxima (la limita el neumático / ABS),
cambia CUÁNTO PEDAL necesitas para llegar a ese límite. Se mide con qué pedal entra el ABS en cada
frenada y se elige la pastilla que te deja más recorrido para dosificar sin que, en las frenadas
fuertes, el ABS quede por encima de lo que realmente alcanzas a pisar. Al cambiar la pastilla
delantera cambia el reparto de frenada, así que se recalcula el balance para mantenerlo.
"""

import re
from typing import Dict, List, Optional

import numpy as np

# Opciones de pastilla delantera por auto (lo que ofrece el garage). Ampliable en data/ingeniero.json
PAD_OPTIONS = {
    "porsche 911 cup (992": [0.63, 0.58, 0.55, 0.53, 0.51, 0.48, 0.44],
}
PEDAL_HEADROOM = 0.95   # las frenadas fuertes deben llegar al ABS con <= 95% de tu pedal máximo real


def _num(text) -> Optional[float]:
    m = re.search(r"-?\d+(?:\.\d+)?", str(text))
    return float(m.group()) if m else None


def pad_options_for(car: str, extra: Optional[Dict[str, List[float]]] = None) -> Optional[List[float]]:
    table = dict(PAD_OPTIONS, **{k.lower(): v for k, v in (extra or {}).items()})
    for key, opts in table.items():
        if key in (car or "").lower():
            return sorted(opts, reverse=True)
    return None


def brake_profile(files, laps) -> Optional[dict]:
    """Pedal al entrar el ABS (todas y frenadas fuertes) y tu pedal máximo real"""
    onset, onset_fast, peaks = [], [], []
    for lap in laps:
        f = files[lap.file_idx]
        sl = slice(lap.i0, lap.i1)
        pedal = f.get("BrakeRaw") if f.get("BrakeRaw") is not None else f.get("Brake")
        absa, v = f.get("BrakeABSactive"), f.get("Speed")
        if pedal is None or absa is None or v is None:
            return None
        b, a, s = pedal[sl], absa[sl], v[sl]
        on = (b > 0.1) & (s > 15)
        edges = np.diff(np.concatenate(([0], on.astype(int), [0])))
        for i0, i1 in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)):
            if i1 - i0 < 20:
                continue
            peaks.append(float(b[i0:i1].max()))
            k = np.flatnonzero(a[i0:i1])
            if len(k):
                onset.append(float(b[i0 + k[0]]))
                if s[i0] * 3.6 > 200:
                    onset_fast.append(float(b[i0 + k[0]]))
    if len(onset) < 5:
        return None
    hard = onset_fast if len(onset_fast) >= 3 else onset
    return {"abs_share": len(onset) / max(len(peaks), 1), "onset_p50": float(np.median(onset)),
            "hard_p90": float(np.percentile(hard, 90)), "pedal_max": float(np.percentile(peaks, 98))}


def pad_plan(profile: dict, mu_front: float, mu_rear: float, bias: float, options: List[float]) -> Optional[dict]:
    """bias en fracción (0.49). Devuelve pastilla y balance para carrera y qualy."""
    if not profile or not options or not mu_front or not mu_rear or not bias:
        return None
    share = bias * mu_front / (bias * mu_front + (1 - bias) * mu_rear)   # reparto real delantero
    limit = profile["pedal_max"] * PEDAL_HEADROOM

    def info(mu):
        scale = mu_front / mu
        b_new = share * mu_rear / (mu - share * mu + share * mu_rear)
        return {"mu": mu, "onset": profile["onset_p50"] * scale, "hard": profile["hard_p90"] * scale,
                "bias": b_new}

    table = [info(mu) for mu in options]
    safe = [t for t in table if t["hard"] <= limit]
    if not safe:
        race = max(table, key=lambda t: t["mu"])
    else:
        race = min(safe, key=lambda t: t["mu"])          # la más suave que aún llega al ABS fuerte
    idx = options.index(race["mu"])
    qualy = table[idx - 1] if idx > 0 and table[idx - 1]["hard"] <= limit else race
    return {"share": share, "limit": limit, "race": race, "qualy": qualy, "table": table,
            "current": info(mu_front)}


def qualy_fuel(per_lap: float, qualy_laps: int) -> float:
    """Vuelta de salida + vueltas lanzadas + 0.5 L de margen: lo mínimo para ir liviano"""
    return per_lap * (qualy_laps + 1) + 0.5


def race_fuel(per_lap: float, race_laps: int) -> float:
    """Vueltas de carrera + vuelta de formación + 0.5 L de margen"""
    return per_lap * (race_laps + 1) + 0.5


def session_laps(session_info: dict, kind: str) -> Optional[int]:
    """kind: 'race' o 'qual'. Vueltas de esa sesión si son fijas."""
    try:
        for s in session_info["SessionInfo"]["Sessions"]:
            if kind in str(s.get("SessionType", "")).lower():
                laps = str(s.get("SessionLaps", ""))
                return int(laps) if laps.isdigit() else None
    except (KeyError, TypeError):
        pass
    return None


def describe(plan: dict, profile: dict, fuel: dict) -> List[str]:
    """Líneas del plan para reporte y radio"""
    out = []
    if fuel.get("qualy"):
        out.append(f"Qualy: carga {fuel['qualy']:.1f} litros ({fuel['qualy_laps']} vueltas lanzadas + salida).")
    if fuel.get("race"):
        out.append(f"Carrera: carga {fuel['race']:.1f} litros para {fuel['race_laps']} vueltas sin parar.")
    if fuel.get("s_per_liter"):
        out.append(f"Cada litro de más cuesta unos {fuel['s_per_liter'] * 1000:.0f} milésimas por vuelta "
                   f"(peso del combustible): no cargues de más en qualy.")
    if plan:
        cur = plan["current"]
        q, r = plan["qualy"], plan["race"]
        out.append(f"Frenos: hoy el ABS entra con {profile['onset_p50'] * 100:.0f}% de pedal y en las frenadas fuertes "
                   f"con {profile['hard_p90'] * 100:.0f}%; tu pedal máximo real es {profile['pedal_max'] * 100:.0f}%.")
        if r["mu"] == cur["mu"] and q["mu"] == cur["mu"]:
            out.append(f"Pastilla delantera {cur['mu']:.2f}: ya es la adecuada para qualy y carrera.")
        else:
            out.append(f"Pastilla delantera qualy {q['mu']:.2f} con balance {q['bias'] * 100:.1f}%; "
                       f"carrera {r['mu']:.2f} con balance {r['bias'] * 100:.1f}% "
                       f"(mismo reparto de frenada que hoy, más recorrido para dosificar).")
    return out
