"""
Pruebas A/B de setup: ¿el cambio te hizo realmente más rápido o es ruido?

- Vueltas limpias de cada salida (descarta las de más de 103 % de la mejor de la salida).
- Corrección por combustible: t_corr = t − s·litros_en_esa_vuelta (s = segundos por litro): compara
  ambas salidas como si tuvieran el mismo combustible.
- Prueba t de Welch (varianzas distintas) con intervalo de confianza del 95 % para la diferencia de medias.
- Si no alcanza para concluir: vueltas necesarias por salida para detectar esa diferencia con 80 % de potencia,
  n ≈ 2 (z₀.₉₇₅ + z₀.₈)² σ² / Δ².
"""

from typing import Dict, List, Optional

import numpy as np

from app.setup_db import key_setup

IGNORE = ("FuelLevel", "FuelLowWarning", "DisplayPage", "CornerWeight", "RideHeight", "FrontWeight", "CrossWeight",
          "TcMode")
ALPHA = 0.05


def setup_changes(a: Dict[str, str], b: Dict[str, str]) -> List[tuple]:
    """(campo, antes, después) de los campos de setup que cambiaron (sin mediciones ni derivados)"""
    ka, kb = key_setup(a), key_setup(b)
    out = []
    for k in sorted(set(ka) | set(kb)):
        if any(x in k for x in IGNORE):
            continue
        if ka.get(k) != kb.get(k):
            out.append((k, ka.get(k), kb.get(k)))
    # balance de frenos / TC / ABS viven en los diales del auto
    for k in ("BrakePressureBias", "TcSetting", "AbsSetting"):
        va = next((v for kk, v in a.items() if kk.endswith(k)), None)
        vb = next((v for kk, v in b.items() if kk.endswith(k)), None)
        if va != vb and not any(c[0].endswith(k) for c in out):
            out.append((k, va, vb))
    return out


def clean_times(stint: dict, seconds_per_liter: float = 0.0) -> List[float]:
    times = [t for t in (stint.get("tiempos") or []) if t and t > 0]
    if len(times) < 2:
        return []
    best = min(times)
    fuel0, fpl = stint.get("combustible_inicio"), stint.get("combustible_por_vuelta")
    out = []
    for i, t in enumerate(times):
        if t > best * 1.03:
            continue
        if seconds_per_liter and fuel0 is not None and fpl:
            liters = max(fuel0 - (i + 0.5) * fpl, 0.0)
            t -= seconds_per_liter * liters          # como si fuera con el estanque vacío
        out.append(t)
    return out


def compare(a: dict, b: dict, seconds_per_liter: float = 0.0) -> Optional[dict]:
    """Salida A (antes) contra salida B (después). diff < 0 = B más rápida."""
    from scipy import stats
    ta, tb = clean_times(a, seconds_per_liter), clean_times(b, seconds_per_liter)
    if len(ta) < 2 or len(tb) < 2:
        return None
    ma, mb = float(np.mean(ta)), float(np.mean(tb))
    va, vb = float(np.var(ta, ddof=1)), float(np.var(tb, ddof=1))
    diff = mb - ma
    se = np.sqrt(va / len(ta) + vb / len(tb))
    if se < 1e-6:
        se = 1e-6
    df = (va / len(ta) + vb / len(tb)) ** 2 / max(
        (va / len(ta)) ** 2 / max(len(ta) - 1, 1) + (vb / len(tb)) ** 2 / max(len(tb) - 1, 1), 1e-12)
    tcrit = float(stats.t.ppf(1 - ALPHA / 2, df))
    p = float(2 * stats.t.sf(abs(diff) / se, df))
    lo, hi = diff - tcrit * se, diff + tcrit * se
    sd = float(np.sqrt((va + vb) / 2))
    effect = max(abs(diff), 0.05)
    n_needed = int(np.ceil(2 * (1.96 + 0.84) ** 2 * sd ** 2 / effect ** 2))
    if p < ALPHA:
        verdict = "mejor" if diff < 0 else "peor"
    else:
        verdict = "sin diferencia demostrable"
    return {"n_a": len(ta), "n_b": len(tb), "media_a": round(ma, 3), "media_b": round(mb, 3),
            "diferencia": round(diff, 3), "ic95": (round(lo, 3), round(hi, 3)), "p": round(p, 3),
            "veredicto": verdict, "vueltas_necesarias": n_needed, "corregido_combustible": bool(seconds_per_liter),
            "cambios": setup_changes(a.get("setup", {}), b.get("setup", {}))}


def last_ab(stints: List[dict], seconds_per_liter: float = 0.0) -> Optional[dict]:
    """Compara la última salida con la anterior que tenía OTRO setup (y suficientes vueltas)"""
    if len(stints) < 2:
        return None
    b = stints[-1]
    for a in reversed(stints[:-1]):
        if setup_changes(a.get("setup", {}), b.get("setup", {})) and len(a.get("tiempos") or []) >= 2:
            return compare(a, b, seconds_per_liter)
    return None


def describe(r: dict) -> str:
    def short(field: str) -> str:
        return field.split(" > ")[-1] if " > " not in field else " ".join(field.split(" > ")[-2:])
    changes = ", ".join(f"{short(k)} de {va} a {vb}" for k, va, vb in r["cambios"][:4]) or "sin cambios de setup"
    d = r["diferencia"]
    lo, hi = r["ic95"]
    base = (f"Prueba de setup ({changes}): {r['n_b']} vueltas contra {r['n_a']}"
            + (", corregido por combustible" if r["corregido_combustible"] else "") + ". ")
    if r["veredicto"] == "mejor":
        return base + (f"El cambio te hizo más rápido: {abs(d):.2f} segundos por vuelta, con 95 por ciento de "
                       f"confianza entre {abs(hi):.2f} y {abs(lo):.2f}. Déjalo.")
    if r["veredicto"] == "peor":
        return base + (f"El cambio te hizo más lento: {d:.2f} segundos por vuelta (entre {lo:.2f} y {hi:.2f}). "
                       f"Vuelve al setup anterior.")
    return base + (f"Diferencia de {d:+.2f} segundos, pero todavía es ruido (p {r['p']:.2f}). Para saberlo necesitas "
                   f"unas {r['vueltas_necesarias']} vueltas limpias por salida.")
