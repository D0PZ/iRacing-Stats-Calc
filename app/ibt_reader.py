"""
Lector de archivos de telemetría de iRacing (.ibt)
Formato: cabecera irsdk + sub-cabecera de disco + definiciones de variables
+ YAML de sesión + registros de datos a 60 Hz.
"""

import os
import struct
from typing import Dict, Optional

import numpy as np

try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False


# Tipos de variables irsdk -> dtype de numpy
_VAR_TYPES = {
    0: np.dtype("S1"),      # char
    1: np.dtype("?"),       # bool
    2: np.dtype("<i4"),     # int
    3: np.dtype("<u4"),     # bitfield
    4: np.dtype("<f4"),     # float
    5: np.dtype("<f8"),     # double
}


class IBTFile:
    """Telemetría completa de un archivo .ibt en memoria"""

    def __init__(self, path: str):
        self.path = path
        self.channels: Dict[str, np.ndarray] = {}
        self.units: Dict[str, str] = {}
        self.descriptions: Dict[str, str] = {}
        self.session_info: dict = {}
        self.tick_rate = 60
        self._load()

    def _load(self):
        with open(self.path, "rb") as f:
            raw = f.read()

        (ver, status, tick_rate, _si_update, si_len, si_offset,
         num_vars, var_header_offset, _num_buf, buf_len) = struct.unpack_from("<10i", raw, 0)
        self.tick_rate = tick_rate or 60
        # varBuf[0]: tickCount, bufOffset
        _tick_count, buf_offset = struct.unpack_from("<2i", raw, 48)
        # Sub-cabecera de disco (después de los 112 bytes de cabecera)
        _start_date, _start_time, _end_time = struct.unpack_from("<3d", raw, 112)
        _lap_count, record_count = struct.unpack_from("<2i", raw, 136)

        available = (len(raw) - buf_offset) // buf_len if buf_len else 0
        if record_count <= 0 or record_count > available:
            record_count = available  # archivo cortado o sin cerrar bien

        # Definiciones de variables (144 bytes cada una)
        fields = []
        for i in range(num_vars):
            base = var_header_offset + i * 144
            vtype, offset, count = struct.unpack_from("<3i", raw, base)
            name = raw[base + 16:base + 48].split(b"\0", 1)[0].decode("latin-1")
            desc = raw[base + 48:base + 112].split(b"\0", 1)[0].decode("latin-1")
            unit = raw[base + 112:base + 144].split(b"\0", 1)[0].decode("latin-1")
            fields.append((name, vtype, offset, count))
            self.units[name] = unit
            self.descriptions[name] = desc

        records = np.frombuffer(raw, dtype=np.uint8, count=record_count * buf_len,
                                offset=buf_offset).reshape(record_count, buf_len)
        for name, vtype, offset, count in fields:
            dt = _VAR_TYPES.get(vtype)
            if dt is None:
                continue
            width = dt.itemsize * count
            block = np.ascontiguousarray(records[:, offset:offset + width])
            data = block.view(dt)
            self.channels[name] = data[:, 0] if count == 1 else data.reshape(record_count, count)

        text = raw[si_offset:si_offset + si_len].split(b"\0", 1)[0].decode("latin-1")
        self.session_info = parse_session_yaml(text)

    def __contains__(self, name: str) -> bool:
        return name in self.channels

    def get(self, name: str, default=None) -> Optional[np.ndarray]:
        return self.channels.get(name, default)

    def __len__(self):
        speed = self.channels.get("Speed")
        return len(speed) if speed is not None else 0


def parse_session_yaml(text: str) -> dict:
    """iRacing genera YAML con algunos valores sin comillas que rompen el parser"""
    if not YAML_AVAILABLE or not text:
        return {}
    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    try:
        return yaml.load(text, Loader=loader) or {}
    except yaml.YAMLError:
        # Nombres de equipo/usuario con caracteres especiales: citar los valores problemáticos
        fixed = []
        for line in text.splitlines():
            key, sep, value = line.partition(": ")
            if sep and any(k in key for k in ("UserName", "TeamName", "AbbrevName", "Initials", "CarScreenName")):
                value = value.replace("'", "''")
                line = f"{key}: '{value}'"
            fixed.append(line)
        try:
            return yaml.load("\n".join(fixed), Loader=loader) or {}
        except yaml.YAMLError:
            return {}


def telemetry_dir() -> str:
    """Carpeta donde iRacing guarda los .ibt (Documentos normal o redirigido a OneDrive)"""
    for base in (os.path.expanduser("~/Documents"), os.path.expanduser("~/OneDrive/Documents"),
                 os.path.expanduser("~/OneDrive/Documentos")):
        path = os.path.join(base, "iRacing", "telemetry")
        if os.path.isdir(path):
            return path
    return os.path.join(os.path.expanduser("~/Documents"), "iRacing", "telemetry")
