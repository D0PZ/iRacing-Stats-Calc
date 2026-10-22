"""
Asesor de chasis: barras estabilizadoras, alerón y spring perch (altura / cross weight).

Qué ajuste corrige qué, según DÓNDE aparece el problema:
- Barras (mecánico, curvas lentas y medias):
  subviraje en apoyo -> barra delantera más blanda (o trasera más dura si la delantera ya está al mínimo);
  sobreviraje / falta de tracción en la salida -> barra trasera más blanda.
  992 Cup: barra delantera 1 (blanda) ... 6 (dura) y 7 algo más blanda que 6 -> orden real 1-2-3-4-5-7-6.
- Alerón (aerodinámico, curvas rápidas > 160 km/h):
  sobreviraje rápido -> +1; subviraje rápido -> -1; estable en lo rápido -> -1 para velocidad punta.
- Spring perch (en este auto, MÁS perch = esa esquina más baja y con MENOS peso; medido en COTA:
  delantera derecha de 0 a 15 mm bajó el cross weight de 48.4 a 40 %, unos 0.55 % por mm):
  cross weight = (delantera derecha + trasera izquierda) / total. Se corrige en DIAGONAL con las 4 ruedas
  (delantera izquierda y trasera derecha en un sentido, delantera derecha y trasera izquierda en el otro,
  misma cantidad) para no cambiar la altura media de cada eje ni el rake.
  Más cross weight = subvira en curvas a la izquierda y se suelta en las de la derecha.
  Rake: bajar la perch de ambas traseras sube la cola = más apoyo delantero en curvas rápidas.
"""

import re
from typing import Dict, List, Optional

EVENT_RATE = 0.8          # eventos por vuelta para considerar que hay una tendencia
FRONT_ARB_ORDER = [1, 2, 3, 4, 5, 7, 6]     # 992 Cup: de más blanda a más dura
REAR_ARB_RANGE = (0, 6)
PERCH_STEP = 0.25         # mm (paso del garage)
CROSS_SENS = 0.55         # % de cross weight por mm de perch por esquina (medido en el 992 Cup, se aprende)


def learn_cross_sens(points: List[dict]) -> Dict[str, float]:
    """
    points: [{'perch': {'LF':..,'RF':..,'LR':..,'RR':..}, 'cross': 48.4}, ...] vistos en el garage.
    Modelo: cross = c0 + kf * (LF - RF) + kr * (RR - LR). Devuelve {'front': kf, 'rear': kr}.
    """
    import numpy as np
    rows = [p for p in points if p.get("cross") is not None and all(w in p["perch"] for w in ("LF", "RF", "LR", "RR"))]
    sens = {"front": CROSS_SENS, "rear": CROSS_SENS}
    if len(rows) < 2:
        return sens
    xf = np.array([p["perch"]["LF"] - p["perch"]["RF"] for p in rows])
    xr = np.array([p["perch"]["RR"] - p["perch"]["LR"] for p in rows])
    y = np.array([p["cross"] for p in rows])
    if np.ptp(xf) > 0.2 and np.ptp(xr) > 0.2 and len(rows) >= 3:
        A = np.column_stack([np.ones(len(y)), xf, xr])
        _, kf, kr = np.linalg.lstsq(A, y, rcond=None)[0]
        if 0.1 < kf < 2 and 0.1 < kr < 2:
            return {"front": round(float(kf), 3), "rear": round(float(kr), 3)}
    for key, x in (("front", xf), ("rear", xr)):
        if np.ptp(x) > 0.2:
            k = float(np.polyfit(x, y, 1)[0])
            if 0.1 < k < 2:
                sens[key] = round(k, 3)
                other = "rear" if key == "front" else "front"
                if np.ptp(xr if key == "front" else xf) <= 0.2:
                    sens[other] = sens[key]   # sin datos del otro eje: misma sensibilidad
    return sens


def cross_plan(perch: Dict[str, float], cross: float, target: float, sens: Optional[Dict[str, float]] = None) -> dict:
    """Movimiento en diagonal (misma cantidad en las 4 ruedas) para llevar el cross weight al objetivo"""
    sens = sens or {"front": CROSS_SENS, "rear": CROSS_SENS}
    k = 2 * sens["front"] + 2 * sens["rear"]          # % por mm moviendo las cuatro esquinas
    d = (target - cross) / k
    d = round(d / PERCH_STEP) * PERCH_STEP
    new = {"LF": perch["LF"] + d, "RR": perch["RR"] + d, "RF": perch["RF"] - d, "LR": perch["LR"] - d}
    predicted = cross + d * k
    return {"mm": d, "perch": {w: round(v, 2) for w, v in new.items()}, "cross_previsto": round(predicted, 1)}


def _num(text) -> Optional[float]:
    m = re.search(r"[-+]?\d+(?:\.\d+)?", str(text or ""))
    return float(m.group()) if m else None


def read_chassis(flat: Dict[str, str]) -> dict:
    out = {"perch": {}}
    for k, v in flat.items():
        last = k.split(" > ")[-1]
        val = _num(v)
        if val is None:
            continue
        if last == "ArbSetting":
            out["arb_front" if " Front " in f" {k} " or k.startswith("Chassis > Front") else "arb_rear"] = int(val)
        elif last == "WingAngle":
            out["wing"] = val
        elif last == "CrossWeight":
            out["cross"] = val
        elif last == "SpringPerchOffset":
            for w, key in (("LF", "LeftFront"), ("RF", "RightFront"), ("LR", "LeftRear"), ("RR", "RightRear")):
                if key in k:
                    out["perch"][w] = val
    return out


def _softer_front(cur: int) -> Optional[int]:
    if cur not in FRONT_ARB_ORDER:
        return None
    i = FRONT_ARB_ORDER.index(cur)
    return FRONT_ARB_ORDER[i - 1] if i > 0 else None


def _stiffer_front(cur: int) -> Optional[int]:
    if cur not in FRONT_ARB_ORDER:
        return None
    i = FRONT_ARB_ORDER.index(cur)
    return FRONT_ARB_ORDER[i + 1] if i < len(FRONT_ARB_ORDER) - 1 else None


def advise(flat: Dict[str, str], laps: int, counts: Dict[str, int],
           history: Optional[List[dict]] = None, cross_sens: Optional[Dict[str, float]] = None) -> List[dict]:
    """
    counts: eventos de la salida por velocidad y sentido, p. ej.
      {'mid_under_lenta': 4, 'exit_slides_media': 3, 'entry_slides_rapida': 1, 'mid_under_izq': 2, ...}
    """
    c = read_chassis(flat)
    laps = max(laps, 1)
    r = lambda key: counts.get(key, 0) / laps                          # noqa: E731
    slow_under = r("mid_under_lenta") + r("mid_under_media")
    slow_exit_over = r("exit_slides_lenta") + r("exit_slides_media")
    fast_over = r("entry_slides_rapida") + r("exit_slides_rapida")
    fast_under = r("mid_under_rapida") + r("entry_under_rapida")
    recs: List[dict] = []

    # --- Barras
    fa, ra = c.get("arb_front"), c.get("arb_rear")
    if slow_under >= EVENT_RATE and slow_exit_over < EVENT_RATE:
        softer = _softer_front(fa) if fa is not None else None
        if softer is not None:
            recs.append({"param": "Barra delantera", "de": fa, "a": softer,
                         "por": f"subviras en curvas lentas y medias ({slow_under:.1f} por vuelta)"})
        elif ra is not None and ra < REAR_ARB_RANGE[1]:
            recs.append({"param": "Barra trasera", "de": ra, "a": ra + 1,
                         "por": f"subviras en curvas lentas ({slow_under:.1f} por vuelta) y la delantera ya está al mínimo"})
    elif slow_exit_over >= EVENT_RATE and slow_under < EVENT_RATE:
        if ra is not None and ra > REAR_ARB_RANGE[0]:
            recs.append({"param": "Barra trasera", "de": ra, "a": ra - 1,
                         "por": f"se te va la cola al acelerar en curvas lentas ({slow_exit_over:.1f} por vuelta): "
                                "más tracción"})
        else:
            stiffer = _stiffer_front(fa) if fa is not None else None
            if stiffer is not None:
                recs.append({"param": "Barra delantera", "de": fa, "a": stiffer,
                             "por": "sobreviraje en salida con la trasera ya al mínimo"})

    # --- Alerón
    wing = c.get("wing")
    if wing is not None:
        if fast_over >= EVENT_RATE * 0.5:
            recs.append({"param": "Alerón", "de": wing, "a": wing + 1,
                         "por": f"se mueve la cola en curvas rápidas ({fast_over:.1f} por vuelta): más apoyo atrás"})
        elif fast_under >= EVENT_RATE:
            recs.append({"param": "Alerón", "de": wing, "a": wing - 1,
                         "por": f"subviras en curvas rápidas ({fast_under:.1f} por vuelta): menos apoyo atrás "
                                "equilibra el auto"})
        elif fast_over + fast_under < 0.2 and laps >= 3:
            recs.append({"param": "Alerón", "de": wing, "a": wing - 1,
                         "por": "estable en lo rápido: un punto menos te da velocidad punta en las rectas (prueba)"})

    # --- Spring perch: cross weight
    cross = c.get("cross")
    under_l = r("mid_under_izq") + r("entry_under_izq")
    under_r = r("mid_under_der") + r("entry_under_der")
    over_l = r("entry_slides_izq") + r("exit_slides_izq")
    over_r = r("entry_slides_der") + r("exit_slides_der")
    perch = c.get("perch", {})
    if cross is not None and all(w in perch for w in ("LF", "RF", "LR", "RR")):
        # Solo con una diferencia clara y suficientes vueltas (si no, el consejo salta de una salida a otra)
        enough = laps >= 3
        tighter_left = enough and under_l - over_l > under_r - over_r + 1.0    # subvira más a la izquierda
        tighter_right = enough and under_r - over_r > under_l - over_l + 1.0
        target, why = None, ""
        if tighter_left:
            target, why = round(min(cross, 50.0) - 0.5, 1), "subviras más en curvas a la izquierda: menos cross weight"
        elif tighter_right:
            target, why = round(max(cross, 50.0) + 0.5, 1), "subviras más en curvas a la derecha: más cross weight"
        elif abs(cross - 50.0) > 0.7:
            target, why = 50.0, "el auto no necesita la asimetría: cross weight a 50 por ciento"
        if target is not None:
            plan = cross_plan(perch, cross, target, cross_sens)
            if plan["mm"]:
                recs.append({"param": "Cross weight", "de": cross, "a": plan["cross_previsto"], "plan": plan,
                             "actual": dict(perch), "por": why})
    # --- Spring perch: rake (balance aerodinámico en lo rápido), solo si el alerón no lo resolvió.
    #     Menos perch atrás = cola más alta = más inclinación = más apoyo delantero.
    if "LR" in perch and "RR" in perch and not any(x["param"] == "Alerón" and x["a"] < x["de"] for x in recs) \
            and not any(x["param"] == "Cross weight" for x in recs):
        if fast_under >= EVENT_RATE:
            recs.append({"param": "Perch traseras", "de": (perch["LR"], perch["RR"]),
                         "a": (perch["LR"] - 2 * PERCH_STEP, perch["RR"] - 2 * PERCH_STEP),
                         "por": "subes la cola (más rake) para llevar apoyo adelante en curvas rápidas; "
                                "mismo valor en ambas para no tocar el cross weight. Revisa las alturas en el garage"})

    # --- Historial: ritmo según barras y alerón en esta pista
    if history:
        for x in recs:
            key = {"Barra delantera": "arb_front", "Barra trasera": "arb_rear", "Alerón": "wing"}.get(x["param"])
            if not key:
                continue
            by_val = {}
            for s in history:
                val = read_chassis(s.get("setup", {})).get(key)
                if s.get("ritmo") and val is not None:
                    by_val.setdefault(val, []).append(s["ritmo"])
            if len(by_val) >= 2:
                best = min(by_val, key=lambda v: min(by_val[v]))
                x["historial"] = f"tu mejor ritmo aquí fue con {x['param'].lower()} en {best:g}"
    return recs


def phrases(recs: List[dict]) -> List[str]:
    out = []
    names = {"LF": "delantera izquierda", "RF": "delantera derecha", "LR": "trasera izquierda", "RR": "trasera derecha"}
    for x in recs:
        de, a = x["de"], x["a"]
        if x["param"] == "Cross weight" and x.get("plan"):
            plan, cur = x["plan"], x["actual"]
            moves = ", ".join(f"{names[w]} de {cur[w]:+.2f} a {plan['perch'][w]:+.2f}" for w in ("LF", "RF", "LR", "RR"))
            out.append(f"Cross weight de {de:.1f} a unos {a:.1f} por ciento ({x['por']}). Perch en diagonal, "
                       f"{abs(plan['mm']):.2f} milímetros en cada rueda: {moves}. Confirma el valor en el garage.")
            continue
        if isinstance(de, tuple):
            txt = f"{x['param']}: de {de[0]:+.1f} y {de[1]:+.1f} a {a[0]:+.1f} y {a[1]:+.1f} milímetros, {x['por']}."
        elif x["param"] == "Cross weight" and "milímetros" in x["por"]:
            txt = f"{x['param']}: de {de:.1f} a {a:.1f} por ciento, {x['por']}."
        else:
            txt = f"{x['param']}: de {de:g} a {a:g}, {x['por']}."
        if x.get("historial"):
            txt += f" Ojo: {x['historial']}."
        out.append(txt)
    return out
