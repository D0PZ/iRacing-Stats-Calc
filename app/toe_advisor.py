"""
Asesor de toe (convergencia) a partir de cómo se comporta el auto.

Efectos (manual del 992 Cup y física del neumático):
- Toe-in trasero: más estabilidad en entrada/salida y menos sobreviraje, a cambio de menos rotación,
  más arrastre (velocidad punta) y más temperatura de neumático. Cada valor trasero es por rueda.
- La trasera EXTERIOR de cada curva es la que estabiliza: en curvas a la derecha trabaja la trasera
  izquierda, y viceversa. Toe asimétrico = el auto se comporta distinto según el sentido de la curva.
- Toe delantero (par): toe-in = estable y perezoso en la entrada; hacia toe-out = entrada más incisiva.

Las recomendaciones son pasos pequeños (0.2 mm por rueda atrás, 0.5 mm total adelante) para medir
el efecto salida a salida.
"""

import re
from typing import Dict, List, Optional

REAR_STEP = 0.2       # mm por rueda
FRONT_STEP = 0.5      # mm total del eje
EVENT_RATE = 0.8      # eventos por vuelta para considerar que hay una tendencia
ASYM_TOLERANCE = 0.2  # mm


def _num(text) -> Optional[float]:
    m = re.search(r"[-+]?\d+(?:\.\d+)?", str(text or ""))
    return float(m.group()) if m else None


def read_toe(flat_setup: Dict[str, str]) -> Dict[str, float]:
    """{'F': total delantero, 'LF'/'RF' si es por rueda, 'LR', 'RR'} en mm"""
    toe = {}
    for k, v in flat_setup.items():
        if not k.endswith("ToeIn"):
            continue
        val = _num(v)
        if val is None:
            continue
        if "LeftRear" in k:
            toe["LR"] = val
        elif "RightRear" in k:
            toe["RR"] = val
        elif "LeftFront" in k:
            toe["LF"] = val
        elif "RightFront" in k:
            toe["RF"] = val
        elif "Front" in k:
            toe["F"] = val
    return toe


def advise(flat_setup: Dict[str, str], laps: int, by_side: Dict[str, int],
           history: Optional[List[dict]] = None) -> List[dict]:
    """
    by_side: conteos de la salida, p. ej. {'entry_slides_izq': 2, 'exit_slides_der': 3, 'entry_under_izq': 1}
    history: salidas de la base de setups (para comparar ritmo con distintos toe)
    Devuelve [{'rueda': 'LR', 'de': 1.4, 'a': 1.2, 'por': '...'}].
    """
    toe = read_toe(flat_setup)
    laps = max(laps, 1)
    rate = lambda key: by_side.get(key, 0) / laps                      # noqa: E731
    over = {s: rate(f"entry_slides_{s}") + rate(f"exit_slides_{s}") for s in ("izq", "der")}
    entry_under = rate("entry_under_izq") + rate("entry_under_der")
    recs: List[dict] = []
    if "LR" in toe and "RR" in toe:
        lr, rr = toe["LR"], toe["RR"]
        # En curvas a la izquierda la trasera exterior es la derecha (RR); a la derecha, la izquierda (LR)
        loose_left, loose_right = over["izq"] >= EVENT_RATE, over["der"] >= EVENT_RATE
        if loose_left and not loose_right:
            recs.append({"rueda": "RR", "de": rr, "a": round(rr + REAR_STEP, 1),
                         "por": f"se te va la cola en curvas a la izquierda ({over['izq']:.1f} por vuelta); "
                                "la trasera derecha es la exterior ahí"})
        elif loose_right and not loose_left:
            recs.append({"rueda": "LR", "de": lr, "a": round(lr + REAR_STEP, 1),
                         "por": f"se te va la cola en curvas a la derecha ({over['der']:.1f} por vuelta); "
                                "la trasera izquierda es la exterior ahí"})
        elif loose_left and loose_right:
            for w, cur in (("LR", lr), ("RR", rr)):
                recs.append({"rueda": w, "de": cur, "a": round(cur + REAR_STEP, 1),
                             "por": "se te va la cola en ambos sentidos: más estabilidad atrás"})
        else:
            if abs(lr - rr) > ASYM_TOLERANCE:
                avg = round((lr + rr) / 2, 1)
                recs.append({"rueda": "LR", "de": lr, "a": avg,
                             "por": "toe trasero asimétrico sin que el auto lo necesite: el auto se comporta "
                                    "distinto en cada sentido de curva"})
                recs.append({"rueda": "RR", "de": rr, "a": avg, "por": "igualar con la izquierda"})
            elif min(lr, rr) >= 1.0 and over["izq"] + over["der"] < 0.3:
                for w, cur in (("LR", lr), ("RR", rr)):
                    recs.append({"rueda": w, "de": cur, "a": round(cur - REAR_STEP, 1),
                                 "por": "atrás va estable: menos toe-in te da rotación y velocidad punta"})
    front_key = "F" if "F" in toe else None
    if front_key and entry_under >= EVENT_RATE:
        recs.append({"rueda": "F", "de": toe["F"], "a": round(toe["F"] - FRONT_STEP, 1),
                     "por": f"subviras al entrar ({entry_under:.1f} por vuelta): menos toe-in adelante da giro "
                            "más incisivo"})
    # Comparación con el historial: ¿con qué toe fuiste más rápido en esta pista?
    if history:
        by_toe = {}
        for s in history:
            if not s.get("ritmo"):
                continue
            t = read_toe(s.get("setup", {}))
            key = (t.get("LR"), t.get("RR"))
            if None not in key:
                by_toe.setdefault(key, []).append(s["ritmo"])
        if len(by_toe) >= 2:
            best = min(by_toe, key=lambda k: min(by_toe[k]))
            for r in recs:
                r["historial"] = f"tu mejor ritmo en esta pista fue con toe trasero {best[0]:+.1f} / {best[1]:+.1f} mm"
    return recs


def phrases(recs: List[dict]) -> List[str]:
    names = {"LR": "trasera izquierda", "RR": "trasera derecha", "F": "delantero total",
             "LF": "delantera izquierda", "RF": "delantera derecha"}
    out = []
    for r in recs:
        txt = f"Toe {names[r['rueda']]}: de {r['de']:+.1f} a {r['a']:+.1f} milímetros, {r['por']}."
        if r.get("historial"):
            txt += f" Ojo: {r['historial']}."
        out.append(txt)
    return out
