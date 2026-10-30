"""
Unidades del setup de iRacing.

El garage muestra los valores en las unidades que elegiste en iRacing (métricas o imperiales):
'183 kPa' o '26.5 psi', '66C' o '151F', '+1.3 mm' o '+1/16 in'. Internamente todo se calcula en
kPa, °C y mm, y al piloto se le habla en su unidad.

La presión en frío se ajusta en pasos de 0,5 psi (3,45 kPa); en métrico el garage la muestra
redondeada al kPa: 159, 162, 165, 169, 172, 176, 179, 183...
"""

import re
from typing import List, Optional

KPA_PER_PSI = 6.894757
KPA_PER_BAR = 100.0
ATM_KPA = 101.325            # presión absoluta = manométrica + atmosférica (ley de los gases)
MM_PER_IN = 25.4
PRESSURE_CLICK_KPA = 0.5 * KPA_PER_PSI
PRESSURE_WORDS = {"kPa": "kilopascales", "psi": "psi", "bar": "bar"}
LENGTH_WORDS = {"mm": "milímetros", "in": "pulgadas"}

_NUM = re.compile(r"[-+]?\d+(?:\.\d+)?")
_FRACTION = re.compile(r"^\s*([-+])?\s*(?:(\d+)\s+)?(\d+)\s*/\s*(\d+)")


def _num(text) -> Optional[float]:
    m = _NUM.search(str(text or ""))
    return float(m.group()) if m else None


# ------------------------------------------------------------- presión
def pressure_unit(text) -> str:
    t = str(text or "").lower()
    if "psi" in t:
        return "psi"
    if "bar" in t:
        return "bar"
    if "kpa" in t:
        return "kPa"
    v = _num(t)        # sin unidad: se deduce por el tamaño (frío/caliente de auto de calle o carrera)
    if v is None:
        return "kPa"
    return "bar" if v < 6 else "psi" if v < 60 else "kPa"


def pressure_kpa(text) -> Optional[float]:
    """'183 kPa' -> 183.0, '26.5 psi' -> 182.7, '1.83 bar' -> 183.0"""
    v = _num(text)
    if v is None:
        return None
    unit = pressure_unit(text)
    return v * KPA_PER_PSI if unit == "psi" else v * KPA_PER_BAR if unit == "bar" else v


def snap_pressure(kpa: float) -> float:
    """Al paso del garage (0,5 psi): solo se pueden recomendar presiones que existen"""
    return round(kpa / PRESSURE_CLICK_KPA) * PRESSURE_CLICK_KPA


def fmt_pressure(kpa: float, unit: str = "kPa") -> str:
    """Como lo muestra el garage en esa unidad: '176', '25.5', '1.76'"""
    if unit == "psi":
        return f"{kpa / KPA_PER_PSI:.1f}"
    if unit == "bar":
        return f"{kpa / KPA_PER_BAR:.2f}"
    return f"{kpa:.0f}"


def in_unit(kpa: Optional[float], unit: str = "kPa") -> Optional[float]:
    """Número en la unidad del piloto (para mostrar o pasarle a Claude)"""
    if kpa is None:
        return None
    return round(kpa / KPA_PER_PSI, 1) if unit == "psi" else round(kpa / KPA_PER_BAR, 2) if unit == "bar" \
        else round(kpa)


# -------------------------------------------------------- temperatura
def temp_unit(text) -> str:
    return "F" if re.search(r"\d\s*°?\s*F", str(text or "")) else "C"


def temps_c(text) -> List[float]:
    """'66C, 75C, 76C' -> [66, 75, 76]; '151F, 167F, 169F' -> en °C"""
    vals = [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", str(text or ""))]
    if temp_unit(text) == "F":
        return [(v - 32) / 1.8 for v in vals]
    return vals


def fmt_temp_delta(delta_c: float, unit: str = "C", sign: bool = True) -> str:
    """Diferencia de temperatura en la unidad del piloto ('+9' °C o '+16' °F)"""
    v = delta_c * 1.8 if unit == "F" else delta_c
    return f"{v:+.0f}" if sign else f"{v:.0f}"


# ------------------------------------------------------------ longitud
def length_unit(text) -> str:
    t = str(text or "").lower()
    return "in" if ("in" in t or '"' in t or "/" in t) else "mm"


def length_mm(text) -> Optional[float]:
    """'+1.3 mm' -> 1.3, '-0.05 in' -> -1.27, '+1/16"' -> 1.59, '+1 1/32 in' -> 26.19"""
    t = str(text or "")
    m = _FRACTION.match(t)
    if m:
        sign, whole, a, b = m.groups()
        if int(b) == 0:
            return None
        v = (int(whole) if whole else 0) + int(a) / int(b)
        v = -v if sign == "-" else v
    else:
        v = _num(t)
        if v is None:
            return None
    low = t.lower()
    if length_unit(t) == "in":
        return v * MM_PER_IN
    if "cm" in low:
        return v * 10.0
    return v


def length_denominator(text) -> Optional[int]:
    """En imperial el garage muestra el toe en fracciones sin simplificar ('+2/32"'): su denominador"""
    m = re.search(r"/\s*(\d+)", str(text or ""))
    return int(m.group(1)) if m and int(m.group(1)) > 0 else None


def fmt_length(mm: float, unit: str = "mm", denom: Optional[int] = None) -> str:
    """Toe en la unidad del piloto: '+1.3' mm, '+3/32' o '+0.051' pulgadas"""
    if unit == "in":
        if denom:
            n = round(mm / MM_PER_IN * denom)
            return f"{'-' if n < 0 else '+'}{abs(n)}/{denom}"
        return f"{mm / MM_PER_IN:+.3f}"
    return f"{mm:+.1f}"
