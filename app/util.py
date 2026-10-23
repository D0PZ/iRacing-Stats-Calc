"""Utilidades compartidas: nombres de archivo, lectura de números del setup y escritura segura de JSON."""

import json
import os
import re
from typing import List, Optional

G = 9.81
WHEELS = {"LF": "LeftFront", "RF": "RightFront", "LR": "LeftRear", "RR": "RightRear"}


def slug(text: str, default: str = "x") -> str:
    return re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_") or default


def num(text) -> Optional[float]:
    """Primer número de un valor del setup ('-2.5 deg' -> -2.5)"""
    m = re.search(r"[-+]?\d+(?:\.\d+)?", str(text or ""))
    return float(m.group()) if m else None


def nums(text) -> List[float]:
    return [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", str(text))]


def atomic_write_json(path: str, data, **kwargs):
    """Escribe en un temporal y lo reemplaza: un corte a mitad de escritura no deja el archivo roto"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, **kwargs)
    os.replace(tmp, path)
