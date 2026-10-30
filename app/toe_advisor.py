"""
Asesor de toe (convergencia) a partir de cómo se comporta el auto.

Efectos (manual del 992 Cup y física del neumático):
- Toe-in trasero: más estabilidad en entrada/salida y menos sobreviraje, a cambio de menos rotación,
  más arrastre (velocidad punta) y más temperatura de neumático. Cada valor trasero es por rueda.
- La trasera EXTERIOR de cada curva es la que estabiliza: en curvas a la derecha trabaja la trasera
  izquierda, y viceversa. Toe asimétrico = el auto se comporta distinto según el sentido de la curva.
- Toe delantero (par): toe-in = estable y perezoso en la entrada; hacia toe-out = entrada más incisiva.

Temperaturas (O/M/I de la última entrada a pits): el toe arrastra la banda de lado, así que más toe =
más calor en esa rueda. Si el eje trasero va claramente más caliente que el delantero, o una trasera más
caliente que la otra con más toe, se suma como razón. El efecto del toe sobre la diferencia interior-
exterior lo compensa el modelo de neumáticos al calcular el camber (tire_model.recommend con toe_plan).

Las recomendaciones son pasos pequeños (0.2 mm por rueda atrás, 0.5 mm total adelante) para medir
el efecto salida a salida.
"""

import math
from typing import Dict, List, Optional, Tuple

from app.units import LENGTH_WORDS, MM_PER_IN, fmt_length, length_denominator, length_mm, length_unit

REAR_STEP = 0.2       # mm por rueda
FRONT_STEP = 0.5      # mm total del eje
EVENT_RATE = 0.8      # eventos por vuelta para considerar que hay una tendencia
ASYM_TOLERANCE = 0.2  # mm
HEAT_GAP = 3.0        # °C de diferencia de temperatura media que ya no es ruido
NAMES = {"LR": "trasera izquierda", "RR": "trasera derecha", "F": "delantero total",
         "LF": "delantera izquierda", "RF": "delantera derecha"}


def read_toe(flat_setup: Dict[str, str]) -> Dict[str, float]:
    """{'F': total delantero, 'LF'/'RF' si es por rueda, 'LR', 'RR'} en mm"""
    toe = {}
    for k, v in flat_setup.items():
        if not k.endswith("ToeIn"):
            continue
        val = length_mm(v)
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


def toe_display(flat_setup: Dict[str, str]) -> Tuple[str, Optional[int]]:
    """('mm', None) o ('in', 32): unidad y denominador de las fracciones con que el garage muestra el toe"""
    vals = [v for k, v in flat_setup.items() if k.endswith("ToeIn")]
    if not vals:
        return "mm", None
    return length_unit(vals[0]), max((length_denominator(v) or 0 for v in vals), default=0) or None


def advise(flat_setup: Dict[str, str], laps: int, by_side: Dict[str, int],
           history: Optional[List[dict]] = None, temps: Optional[Dict[str, tuple]] = None,
           rear_min: Optional[float] = None) -> List[dict]:
    """
    by_side: conteos de la salida, p. ej. {'entry_slides_izq': 2, 'exit_slides_der': 3, 'entry_under_izq': 1}
    history: salidas de la base de setups (para comparar ritmo con distintos toe)
    temps: (ext, centro, int) por rueda medidas en pits, solo si las gomas trabajaron
    rear_min: toe trasero mínimo del auto en mm (el 992 Cup solo admite toe-in atrás: 0)
    Devuelve [{'rueda': 'LR', 'de': 1.4, 'a': 1.2, 'por': '...'}] con el toe en mm.
    """
    toe = read_toe(flat_setup)
    laps = max(laps, 1)
    heat = {w: sum(t) / 3 for w, t in (temps or {}).items() if t}
    rear_hot = None
    if all(w in heat for w in ("LF", "RF", "LR", "RR")):
        rear_hot = (heat["LR"] + heat["RR"]) / 2 - (heat["LF"] + heat["RF"]) / 2
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
                why = ("toe trasero asimétrico sin que el auto lo necesite: el auto se comporta "
                       "distinto en cada sentido de curva")
                more, less = ("LR", "RR") if lr > rr else ("RR", "LR")
                if more in heat and less in heat and heat[more] - heat[less] >= HEAT_GAP:
                    why += (f"; además la {NAMES[more]}, con más toe, va {heat[more] - heat[less]:.0f} grados "
                            "más caliente que la otra: el toe la arrastra")
                recs.append({"rueda": "LR", "de": lr, "a": avg, "por": why})
                recs.append({"rueda": "RR", "de": rr, "a": avg, "por": "igualar con la izquierda"})
            elif min(lr, rr) >= 1.0 and over["izq"] + over["der"] < 0.3:
                why = "atrás va estable: menos toe-in te da rotación y velocidad punta"
                if rear_hot is not None and rear_hot >= HEAT_GAP:
                    why += f", y las traseras van {rear_hot:.0f} grados más calientes que las delanteras por el arrastre"
                for w, cur in (("LR", lr), ("RR", rr)):
                    recs.append({"rueda": w, "de": cur, "a": round(cur - REAR_STEP, 1), "por": why})
    if entry_under >= EVENT_RATE:
        why = f"subviras al entrar ({entry_under:.1f} por vuelta): menos toe-in adelante da giro más incisivo"
        if "F" in toe:
            recs.append({"rueda": "F", "de": toe["F"], "a": round(toe["F"] - FRONT_STEP, 1), "por": why})
        elif "LF" in toe and "RF" in toe:      # toe delantero por rueda: medio paso a cada una
            for w in ("LF", "RF"):
                recs.append({"rueda": w, "de": toe[w], "a": round(toe[w] - FRONT_STEP / 2, 2), "por": why})
    recs = _garage_steps(recs, flat_setup, rear_min)
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
            u, denom = toe_display(flat_setup)
            for r in recs:
                r["historial"] = (f"tu mejor ritmo en esta pista fue con toe trasero {fmt_length(best[0], u, denom)} / "
                                  f"{fmt_length(best[1], u, denom)} {LENGTH_WORDS[u]}")
    return recs


def _garage_steps(recs: List[dict], flat_setup: Dict[str, str], rear_min: Optional[float]) -> List[dict]:
    """
    Valores que el garage puede mostrar: respeta el mínimo trasero y, en imperial, lleva cada valor a la
    fracción del auto (1/32", 1/64"...) con al menos un paso de cambio; descarta lo que no cambia.
    """
    unit, denom = toe_display(flat_setup)
    step = MM_PER_IN / denom if unit == "in" and denom else None
    out = []
    for r in recs:
        a = r["a"]
        if step:
            d = a - r["de"]
            a = r["de"] + math.copysign(max(abs(d), step), d) if d else a
            a = round(a / step) * step
        if rear_min is not None and r["rueda"] in ("LR", "RR"):
            a = max(a, rear_min)
        if abs(a - r["de"]) > 1e-6:
            out.append(dict(r, a=a))
    return out


def plan(recs: List[dict]) -> Dict[str, float]:
    """Cambio de toe por rueda en mm (el total delantero se reparte mitad y mitad)"""
    out: Dict[str, float] = {}
    for r in recs:
        d = r["a"] - r["de"]
        for w in (("LF", "RF") if r["rueda"] == "F" else (r["rueda"],)):
            out[w] = out.get(w, 0.0) + (d / 2 if r["rueda"] == "F" else d)
    return out


def phrases(recs: List[dict], unit: str = "mm", denom: Optional[int] = None) -> List[str]:
    out = []
    for r in recs:
        txt = (f"Toe {NAMES[r['rueda']]}: de {fmt_length(r['de'], unit, denom)} a {fmt_length(r['a'], unit, denom)} "
               f"{LENGTH_WORDS[unit]}, {r['por']}.")
        if r.get("historial"):
            txt += f" Ojo: {r['historial']}."
        out.append(txt)
    return out
