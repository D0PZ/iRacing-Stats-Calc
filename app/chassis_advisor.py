"""
Asesor de chasis: barras estabilizadoras, alerón y spring perch (altura / cross weight).

Qué ajuste corrige qué, según DÓNDE aparece el problema:
- Barras (mecánico, curvas lentas y medias):
  subviraje en apoyo -> barra delantera más blanda (o trasera más dura si la delantera ya está al mínimo);
  sobreviraje / falta de tracción en la salida -> barra trasera más blanda.
  992 Cup: barra delantera 1 (blanda) ... 6 (dura) y 7 algo más blanda que 6 -> orden real 1-2-3-4-5-7-6.
- Alerón (aerodinámico, curvas rápidas > 160 km/h):
  sobreviraje rápido -> +1; subviraje rápido -> -1; estable en lo rápido -> -1 para velocidad punta.
- Spring perch:
  cross weight a 50 %: más cross weight = subvira en curvas a la izquierda y se suelta en las de la derecha;
  rake: subir ambas traseras = más apoyo delantero (ayuda al subviraje rápido) y al revés.
"""

import re
from typing import Dict, List, Optional

EVENT_RATE = 0.8          # eventos por vuelta para considerar que hay una tendencia
FRONT_ARB_ORDER = [1, 2, 3, 4, 5, 7, 6]     # 992 Cup: de más blanda a más dura
REAR_ARB_RANGE = (0, 6)
PERCH_STEP = 0.5          # mm


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
           history: Optional[List[dict]] = None) -> List[dict]:
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
    if cross is not None:
        # Solo con una diferencia clara y suficientes vueltas (si no, el consejo salta de una salida a otra)
        enough = laps >= 3
        tighter_left = enough and under_l - over_l > under_r - over_r + 1.0    # subvira más a la izquierda
        tighter_right = enough and under_r - over_r > under_l - over_l + 1.0
        if tighter_left:
            recs.append({"param": "Cross weight", "de": cross, "a": round(min(cross, 50.0) - 0.5, 1),
                         "por": "subviras más en curvas a la izquierda: baja el cross weight subiendo la perch "
                                f"delantera izquierda o la trasera derecha de a {PERCH_STEP} milímetros"})
        elif tighter_right:
            recs.append({"param": "Cross weight", "de": cross, "a": round(max(cross, 50.0) + 0.5, 1),
                         "por": "subviras más en curvas a la derecha: sube el cross weight subiendo la perch "
                                f"delantera derecha o la trasera izquierda de a {PERCH_STEP} milímetros"})
        elif abs(cross - 50.0) > 0.7:
            side = ("delantera derecha o la trasera izquierda" if cross < 50 else
                    "delantera izquierda o la trasera derecha")
            recs.append({"param": "Cross weight", "de": cross, "a": 50.0,
                         "por": f"el auto no necesita la asimetría: sube la perch {side} de a {PERCH_STEP} "
                                "milímetros hasta ver 50 por ciento en el garage"})
    # --- Spring perch: rake (balance aerodinámico en lo rápido), solo si el alerón no lo resolvió
    perch = c.get("perch", {})
    if "LR" in perch and "RR" in perch and not any(x["param"] == "Alerón" and x["a"] < x["de"] for x in recs):
        if fast_under >= EVENT_RATE:
            recs.append({"param": "Perch traseras", "de": (perch["LR"], perch["RR"]),
                         "a": (perch["LR"] + PERCH_STEP, perch["RR"] + PERCH_STEP),
                         "por": "más inclinación (rake) mueve apoyo hacia adelante en curvas rápidas"})

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
    for x in recs:
        de, a = x["de"], x["a"]
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
