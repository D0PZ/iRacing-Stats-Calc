"""
Límites de pista: cuánto margen te queda antes de que iRacing te marque fuera.

iRacing no entrega la posición lateral del auto en vivo, así que se reconstruye:
- Rumbo de referencia de la pista por cada 5 m (aprendido de tus vueltas).
- Desplazamiento lateral = integral de v·sin(rumbo_velocidad - rumbo_referencia),
  anclado a que en los ~800 m previos a la zona vas, en promedio, por tu línea habitual.
- El límite se aprende de las salidas reales (PlayerTrackSurface = fuera de pista):
  la posición lateral en ese instante es el borde para esa zona.

Con GPS (archivos .ibt) se pasa el desplazamiento lateral exacto (±0.1 m).
En vivo iRacing no entrega GPS y la integración tiene ±0.5 m de error: no alcanza para
medir el margen en centímetros, así que en vivo solo se usan las zonas (dónde está el
límite), las salidas reales y la velocidad de salida.
"""

import json
import math
import os
import re
from collections import deque
from typing import Dict, List, Optional

import numpy as np

BIN = 5.0             # metros por celda del mapa de rumbo
ANCHOR_WINDOW = 800   # metros previos usados para anclar la posición lateral
ZONE_BEFORE = 100     # la zona empieza 100 m antes del punto de salida
ZONE_AFTER = 60       # y termina 60 m después
PEAK_WINDOW = 25      # el margen se mide a ±25 m del punto donde está el límite
MAX_LIMIT_OFFSET = 3.0  # más lejos que esto de tu línea no es un límite, es un trompo o error
MIN_BIN_SAMPLES = 12  # muestras por celda para confiar en el rumbo de referencia


def heading_from_irsdk(yaw_north: float, vel_x: float, vel_y: float) -> float:
    """Rumbo de la velocidad en coordenadas del mundo (radianes, antihorario desde el Este)"""
    return math.pi / 2 - yaw_north + math.atan2(vel_y, max(vel_x, 0.1))


class TrackLimitTracker:
    def __init__(self, track_len: float):
        self.track_len = track_len
        n = int(track_len / BIN) + 2
        self.sin_sum = np.zeros(n)
        self.cos_sum = np.zeros(n)
        self.count = np.zeros(n)
        self.zones: List[dict] = []
        self._buf: deque = deque()     # (distancia acumulada, e_raw)
        self._e_raw = 0.0
        self._zone: Optional[dict] = None
        self._anchor = 0.0
        self._peak = -1e9
        self._zone_off = False
        self._off_e: Optional[float] = None
        self._prev_off = False
        self._last_abs: Optional[float] = None

    # --------------------------------------------------------- referencia
    def _bin(self, s: float) -> int:
        return min(max(int(s / BIN), 0), len(self.count) - 1)

    def learn(self, s: float, psi: float):
        b = self._bin(s)
        self.sin_sum[b] += math.sin(psi)
        self.cos_sum[b] += math.cos(psi)
        self.count[b] += 1

    def ref_ready(self, s: float) -> bool:
        return self.count[self._bin(s)] >= MIN_BIN_SAMPLES

    def ref_heading(self, s: float) -> float:
        b = self._bin(s)
        return math.atan2(self.sin_sum[b], self.cos_sum[b])

    # -------------------------------------------------------------- zonas
    def zone_at(self, s: float) -> Optional[dict]:
        for z in self.zones:
            d = (s - z["s"] + self.track_len / 2) % self.track_len - self.track_len / 2
            if -ZONE_BEFORE <= d <= ZONE_AFTER:
                return z
        return None

    @staticmethod
    def limit_of(z: dict) -> float:
        return float(np.median(z["limits"]))

    def _register_off(self, s: float, e: float, side: int) -> dict:
        for z in self.zones:
            d = abs((s - z["s"] + self.track_len / 2) % self.track_len - self.track_len / 2)
            if d < 60 and z["side"] == side:
                z["limits"] = (z["limits"] + [e])[-20:]
                z["s_hist"] = (z.get("s_hist", [z["s"]]) + [s])[-20:]
                z["s"] = float(np.median(z["s_hist"]))
                z["offs"] = z.get("offs", 0) + 1
                return z
        z = {"s": s, "side": side, "limits": [e], "s_hist": [s], "offs": 1, "samples": []}
        self.zones.append(z)
        return z

    # ---------------------------------------------------------- tiempo real
    def step(self, lap: int, s: float, v: float, psi: float, offtrack: bool, dt: float,
             side_hint: int = 0, e_override: Optional[float] = None, stable: bool = True,
             learn_limits: bool = True) -> List[tuple]:
        """
        Procesa una muestra. Devuelve eventos:
          ("enter", zona)  ("off", zona, e)  ("exit", zona, margen_m, vel_salida_kmh, se_salio)
        side_hint: +1 si las ruedas izquierdas pisaron piano en el último segundo, -1 derechas, 0 ninguna.
        stable: False si el auto va cruzado (trompo): esa salida no sirve para aprender el límite.
        """
        events = []
        if v < 10:
            self._prev_off = offtrack
            return events
        dist = lap * self.track_len + s
        if self._last_abs is not None and not (0 <= dist - self._last_abs < 50):
            self._buf.clear()  # salto (reset, pits): reiniciar integración
            self._zone = None
        self._last_abs = dist

        if e_override is not None:
            self._e_raw = e_override
        elif self.ref_ready(s):
            self._e_raw += v * math.sin(psi - self.ref_heading(s)) * dt
        else:
            self._prev_off = offtrack
            return events  # todavía sin mapa de referencia en esta parte
        self._buf.append((dist, self._e_raw))
        while self._buf and self._buf[0][0] < dist - ANCHOR_WINDOW - 200:
            self._buf.popleft()

        z = self.zone_at(s)
        if z is not None and self._zone is None:
            prev = [e for d, e in self._buf if d < dist - 1]
            self._anchor = 0.0 if e_override is not None else (float(np.mean(prev)) if len(prev) > 20 else self._e_raw)
            self._zone, self._peak, self._zone_off, self._off_e = z, -1e9, False, None
            events.append(("enter", z))
        e = self._e_raw - self._anchor if self._zone is not None else None

        if offtrack and not self._prev_off:
            if self._zone is None:
                # Salida en un lugar nuevo: anclar con lo recorrido antes
                prev = [er for d, er in self._buf if dist - ANCHOR_WINDOW - 150 < d < dist - 150]
                anchor = 0.0 if e_override is not None else (float(np.mean(prev)) if len(prev) > 20 else None)
                if anchor is not None and stable and v > 15 and learn_limits:
                    e_new = self._e_raw - anchor
                    if abs(e_new) <= MAX_LIMIT_OFFSET:
                        side = (1 if e_new > 0 else -1) if abs(e_new) > 0.2 or not side_hint else side_hint
                        z = self._register_off(s, e_new, side)
                        events.append(("off", z, e_new))
            else:
                self._zone_off, self._off_e = True, e
                if stable and abs(e) <= MAX_LIMIT_OFFSET and learn_limits:
                    self._register_off(s, e, self._zone["side"])
                events.append(("off", self._zone, e))
        self._prev_off = offtrack

        if self._zone is not None:
            d = (s - self._zone["s"] + self.track_len / 2) % self.track_len - self.track_len / 2
            if abs(d) <= PEAK_WINDOW:
                self._peak = max(self._peak, self._zone["side"] * e)
            if self.zone_at(s) is not self._zone:
                zn = self._zone
                if self._peak < -1e8:  # no pasó por la ventana de medición
                    self._zone = None
                    return events
                margin = self.limit_of(zn) * zn["side"] - self._peak
                sample = {"margin": round(margin, 3), "exit_kmh": round(v * 3.6, 1), "off": self._zone_off}
                zn["samples"] = (zn["samples"] + [sample])[-40:]
                events.append(("exit", zn, margin, v * 3.6, self._zone_off))
                self._zone = None
        return events

    # ----------------------------------------------------- conclusiones
    @staticmethod
    def exit_gain(z: dict) -> Optional[tuple]:
        """(km/h ganados usando el límite, margen recomendado) con datos limpios suficientes"""
        clean = [x for x in z.get("samples", []) if not x["off"]]
        if len(clean) < 6:
            return None
        clean.sort(key=lambda x: x["margin"])
        half = len(clean) // 2
        tight, loose = clean[:half], clean[half:]
        gain = np.mean([x["exit_kmh"] for x in tight]) - np.mean([x["exit_kmh"] for x in loose])
        best = max(clean, key=lambda x: x["exit_kmh"])
        return float(gain), max(float(best["margin"]), 0.1)

    # ------------------------------------------------------- persistencia
    def to_dict(self) -> dict:
        return {"track_len": self.track_len, "bin": BIN,
                "sin": np.round(self.sin_sum, 3).tolist(), "cos": np.round(self.cos_sum, 3).tolist(),
                "count": self.count.astype(int).tolist(), "zones": self.zones}

    @classmethod
    def from_dict(cls, d: dict) -> "TrackLimitTracker":
        t = cls(d["track_len"])
        n = min(len(t.count), len(d["count"]))
        t.sin_sum[:n] = d["sin"][:n]
        t.cos_sum[:n] = d["cos"][:n]
        t.count[:n] = d["count"][:n]
        t.zones = d.get("zones", [])
        return t


def store_path(track_name: str) -> str:
    base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "track_limits")
    os.makedirs(base, exist_ok=True)
    safe = re.sub(r"[^a-z0-9]+", "_", track_name.lower()).strip("_") or "pista"
    return os.path.join(base, f"{safe}.json")


def load_tracker(track_name: str, track_len: float) -> TrackLimitTracker:
    path = store_path(track_name)
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            if abs(d.get("track_len", 0) - track_len) < 20:
                return TrackLimitTracker.from_dict(d)
        except Exception:
            pass
    return TrackLimitTracker(track_len)


def save_tracker(track_name: str, tracker: TrackLimitTracker):
    with open(store_path(track_name), "w", encoding="utf-8") as f:
        json.dump(tracker.to_dict(), f)


def gps_lateral_offsets(files, laps_ok) -> List[Optional[np.ndarray]]:
    """
    Desplazamiento lateral (m, izquierda +) respecto a tu línea media, usando el GPS del .ibt.
    laps_ok(file_idx) -> máscara de muestras válidas para construir la línea media.
    """
    xs, ys, ss, ps, frames = [], [], [], [], []
    lat0 = lon0 = None
    for fi, f in enumerate(files):
        if f.get("Lat") is None or f.get("Lon") is None:
            frames.append(None)
            continue
        lat = np.radians(f.get("Lat").astype(float))
        lon = np.radians(f.get("Lon").astype(float))
        if lat0 is None:
            lat0, lon0 = lat[0], lon[0]
        x = 6371000 * (lon - lon0) * math.cos(lat0)
        y = 6371000 * (lat - lat0)
        vx, vy = f.get("VelocityX").astype(float), f.get("VelocityY").astype(float)
        psi = math.pi / 2 - f.get("YawNorth").astype(float) + np.arctan2(vy, np.maximum(vx, 0.1))
        s = f.get("LapDist").astype(float)
        frames.append((x, y, s))
        ok = laps_ok(fi)
        xs.append(x[ok]); ys.append(y[ok]); ss.append(s[ok]); ps.append(psi[ok])
    if not xs:
        return [None] * len(files)
    X, Y, S, P = (np.concatenate(a) for a in (xs, ys, ss, ps))
    b = (S / BIN).astype(int)
    nb = int(max(fr[2].max() for fr in frames if fr is not None) / BIN) + 2
    cnt = np.bincount(b, minlength=nb)
    mx = np.bincount(b, X, nb) / np.maximum(cnt, 1)
    my = np.bincount(b, Y, nb) / np.maximum(cnt, 1)
    ref = np.arctan2(np.bincount(b, np.sin(P), nb), np.bincount(b, np.cos(P), nb))
    out = []
    for fr in frames:
        if fr is None:
            out.append(None)
            continue
        x, y, s = fr
        bi = np.clip((s / BIN).astype(int), 0, nb - 1)
        out.append(-(x - mx[bi]) * np.sin(ref[bi]) + (y - my[bi]) * np.cos(ref[bi]))
    return out


def replay_files(files, track_len: float, e_lateral=None, tracker: Optional[TrackLimitTracker] = None,
                 learn_limits: bool = True) -> tuple:
    """
    Pasa la telemetría de .ibt por el tracker (mismo código que en vivo).
    Devuelve (tracker, salidas) con salidas = [(file_idx, lap, zona_s, margen, vel_salida, se_salió)].
    """
    tr = tracker or TrackLimitTracker(track_len)
    exits = []
    lap_base = 0
    for fi, f in enumerate(files):
        v = f.get("Speed")
        vx, vy = f.get("VelocityX").astype(float), f.get("VelocityY").astype(float)
        psi = math.pi / 2 - f.get("YawNorth").astype(float) + np.arctan2(vy, np.maximum(vx, 0.1))
        slip = np.abs(np.arctan2(vy, np.maximum(np.abs(vx), 0.1)))
        s, lap = f.get("LapDist"), f.get("Lap")
        surf, pit = f.get("PlayerTrackSurface"), f.get("OnPitRoad")
        rum_l = (f.get("TireLF_RumblePitch", np.zeros(len(v))) > 0) | (f.get("TireLR_RumblePitch", np.zeros(len(v))) > 0)
        rum_r = (f.get("TireRF_RumblePitch", np.zeros(len(v))) > 0) | (f.get("TireRR_RumblePitch", np.zeros(len(v))) > 0)
        e = e_lateral[fi] if e_lateral is not None else None
        dt = 1.0 / f.tick_rate
        last_side, last_i = 0, -10 ** 9
        for i in range(len(v)):
            if pit[i]:
                continue
            off = surf[i] == 0
            if tracker is None and v[i] > 15 and not off:
                tr.learn(float(s[i]), float(psi[i]))
            if rum_l[i]:
                last_side, last_i = 1, i
            elif rum_r[i]:
                last_side, last_i = -1, i
            hint = last_side if i - last_i < f.tick_rate else 0
            for ev in tr.step(int(lap[i]) + lap_base, float(s[i]), float(v[i]), float(psi[i]), bool(off), dt,
                              side_hint=hint, e_override=None if e is None else float(e[i]),
                              stable=bool(slip[i] < 0.12), learn_limits=learn_limits):
                if ev[0] == "exit":
                    exits.append((fi, int(lap[i]), ev[1]["s"], ev[2], ev[3], ev[4]))
        lap_base += 1000
    return tr, exits
