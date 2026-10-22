"""
Balance del auto por gradiente de subviraje K (°/g) y su relación con barras y alerón.

Objetivos (neutro con leve subviraje = rápido y estable):
  K en curvas lentas  -> +0.2 °/g  (lo controlan las barras: balance mecánico)
  K en curvas rápidas -> +0.4 °/g  (lo controla el alerón: balance aerodinámico; algo más de estabilidad)

Efecto por clic (positivo = más subviraje), estimación inicial que se corrige con TUS salidas:
  barra delantera +1 nivel de dureza -> K lenta +0.3
  barra trasera   +1                 -> K lenta -0.3
  alerón          +1                 -> K rápida +0.25 (más apoyo atrás = más estable en lo rápido)
"""

from typing import Dict, List, Optional

import numpy as np

from app.chassis_advisor import FRONT_ARB_ORDER, REAR_ARB_RANGE, read_chassis

TARGET_K = {"lenta": 0.2, "rapida": 0.4}
TOLERANCE = 0.3                   # °/g: dentro de esto ya está en el objetivo
MIN_SAMPLES = 200
MAX_CLICKS = 2
PRIOR = {"arb_front": 0.3, "arb_rear": -0.3, "wing": 0.25}
BAND_OF = {"arb_front": "lenta", "arb_rear": "lenta", "wing": "rapida"}


def _stiffness(param: str, value) -> Optional[float]:
    """Posición en la escala de dureza (la barra delantera del 992 va 1-2-3-4-5-7-6)"""
    if value is None:
        return None
    if param == "arb_front":
        return float(FRONT_ARB_ORDER.index(int(value))) if int(value) in FRONT_ARB_ORDER else None
    return float(value)


def _from_stiffness(param: str, pos: float):
    if param == "arb_front":
        return FRONT_ARB_ORDER[int(np.clip(round(pos), 0, len(FRONT_ARB_ORDER) - 1))]
    if param == "arb_rear":
        return int(np.clip(round(pos), *REAR_ARB_RANGE))
    return round(pos)


def learn_sensitivities(stints: List[dict]) -> Dict[str, dict]:
    """
    Compara salidas consecutivas con K medido donde cambió UN parámetro (barra delantera, trasera o alerón):
    ΔK / Δclic. Devuelve {param: {'sens': valor, 'fuente': 'tus salidas'|'estimación inicial', 'n': k}}.
    """
    obs = {p: [] for p in PRIOR}
    rows = []
    for s in stints:
        K = (s.get("manejo") or {}).get("gradiente") or {}
        c = read_chassis(s.get("setup", {}))
        rows.append((K, {p: _stiffness(p, c.get(p)) for p in PRIOR}))
    for (k0, c0), (k1, c1) in zip(rows, rows[1:]):
        changed = [p for p in PRIOR if c0[p] is not None and c1[p] is not None and c0[p] != c1[p]]
        if len(changed) != 1:
            continue
        p = changed[0]
        band = BAND_OF[p]
        if band in k0 and band in k1 and k0[band].get("n", 0) >= MIN_SAMPLES and k1[band].get("n", 0) >= MIN_SAMPLES:
            d = (k1[band]["K"] - k0[band]["K"]) / (c1[p] - c0[p])
            if np.sign(d) == np.sign(PRIOR[p]) and abs(d) < 3:      # descartar mediciones sin sentido físico
                obs[p].append(d)
    out = {}
    for p, prior in PRIOR.items():
        if obs[p]:
            # Promedio con la estimación inicial como una observación más (suaviza el ruido)
            out[p] = {"sens": round(float(np.mean(obs[p] + [prior])), 3), "fuente": "tus salidas", "n": len(obs[p])}
        else:
            out[p] = {"sens": prior, "fuente": "estimación inicial", "n": 0}
    return out


def recommend(flat_setup: Dict[str, str], gradients: Dict[str, dict], history: Optional[List[dict]] = None) -> List[dict]:
    """Clics exactos de barras y alerón para llevar K a su objetivo en cada banda"""
    c = read_chassis(flat_setup)
    sens = learn_sensitivities(history or [])
    recs = []
    # Curvas lentas: barras
    g = gradients.get("lenta")
    if g and g.get("n", 0) >= MIN_SAMPLES and abs(g["K"] - TARGET_K["lenta"]) > TOLERANCE:
        err = TARGET_K["lenta"] - g["K"]          # >0: falta subviraje (sobrevira); <0: sobra subviraje
        # Sobreviraje mecánico: primero ablandar atrás (tracción); subviraje: primero ablandar adelante
        order = ["arb_rear", "arb_front"] if err > 0 else ["arb_front", "arb_rear"]
        for p in order:
            cur = _stiffness(p, c.get(p))
            if cur is None:
                continue
            clicks = float(np.clip(err / sens[p]["sens"], -MAX_CLICKS, MAX_CLICKS))
            new = _from_stiffness(p, cur + clicks)
            if new != c.get(p):
                predicted = g["K"] + (_stiffness(p, new) - cur) * sens[p]["sens"]
                recs.append({"param": "Barra delantera" if p == "arb_front" else "Barra trasera",
                             "de": c.get(p), "a": new, "K": g["K"], "objetivo": TARGET_K["lenta"],
                             "K_previsto": round(predicted, 2), "banda": "lentas", "fuente": sens[p]["fuente"]})
                break
    # Curvas rápidas: alerón
    g = gradients.get("rapida")
    if g and g.get("n", 0) >= MIN_SAMPLES and c.get("wing") is not None \
            and abs(g["K"] - TARGET_K["rapida"]) > TOLERANCE:
        err = TARGET_K["rapida"] - g["K"]
        clicks = int(np.clip(round(err / sens["wing"]["sens"]), -MAX_CLICKS, MAX_CLICKS))
        if clicks:
            new = c["wing"] + clicks
            recs.append({"param": "Alerón", "de": c["wing"], "a": new, "K": g["K"], "objetivo": TARGET_K["rapida"],
                         "K_previsto": round(g["K"] + clicks * sens["wing"]["sens"], 2), "banda": "rápidas",
                         "fuente": sens["wing"]["fuente"]})
    return recs


def describe_k(K: float) -> str:
    if K > 0.8:
        return "subviraje marcado"
    if K > 0.3:
        return "leve subviraje"
    if K > -0.3:
        return "neutro"
    if K > -0.8:
        return "leve sobreviraje"
    return "sobreviraje marcado"


def phrases(recs: List[dict]) -> List[str]:
    out = []
    for r in recs:
        out.append(f"{r['param']}: de {r['de']:g} a {r['a']:g}. En curvas {r['banda']} el balance es "
                   f"{describe_k(r['K'])} (K {r['K']:+.1f} grados por g, objetivo {r['objetivo']:+.1f}); con el cambio "
                   f"debería quedar en {r['K_previsto']:+.1f} ({r['fuente']}).")
    return out
