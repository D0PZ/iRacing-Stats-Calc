"""Pista: perfil de velocidad y curvas, delta en vivo, límites de pista y referencias de frenada."""

import math
import time
from typing import Dict, List, Optional

from app.engineer.common import N_SEGMENTS, PROFILE_BINS, P_CAR, P_INFO


class TrackMixin:
    # ------------------------------------------------------- mapa de pista
    def _record_profile(self, pct: float, speed: float):
        """Perfil de velocidad por posición, solo con tus vueltas limpias"""
        if pct is None or pct < 0 or self.lap_stats["offtrack"] or self.lap_stats["pit"]:
            return
        b = int(pct * PROFILE_BINS) % PROFILE_BINS
        old = self.speed_profile[b]
        self.speed_profile[b] = speed if old is None else 0.7 * old + 0.3 * speed

    def _detect_corners(self):
        if self.refs:
            return  # las curvas vienen del análisis de .ibt
        prof = self.speed_profile
        if sum(p is not None for p in prof) < PROFILE_BINS * 0.95:
            return
        n = PROFILE_BINS
        filled = [p if p is not None else 0.0 for p in prof]
        for i, p in enumerate(prof):  # rellenar huecos con el vecino
            if p is None:
                filled[i] = filled[i - 1]
        smooth = [(filled[i - 1] + filled[i] + filled[(i + 1) % n]) / 3 for i in range(n)]
        corners = []
        w_min, w_max = 6, 14
        for i in range(n):
            window = [smooth[(i + k) % n] for k in range(-w_min, w_min + 1)]
            if smooth[i] > min(window) or smooth[i] == smooth[(i - 1) % n]:
                continue
            peak = max(smooth[(i + k) % n] for k in range(-w_max, w_max + 1))
            if peak - smooth[i] > 4.0:  # ~15 km/h de frenada = curva
                corners.append(i / n)
        if corners:
            self.corners = corners
            print(f"[mapa] {len(corners)} curvas detectadas en "
                  f"{', '.join(f'{c * 100:.0f}%' for c in corners)}", flush=True)

    def corner_at(self, pct: float) -> str:
        """Nombre de la zona más cercana a una posición de la vuelta"""
        if not self.corners:
            return f"a los {int(round(pct * self.track_len, -1))} metros"
        dists = [((pct - c + 0.5) % 1.0) - 0.5 for c in self.corners]  # + = después del vértice
        k = min(range(len(dists)), key=lambda i: abs(dists[i]))
        d = dists[k] * self.track_len
        if d < -60:
            return f"en la frenada de la curva {k + 1}"
        if d > 60:
            return f"en la salida de la curva {k + 1}"
        return f"en la curva {k + 1}"

    def zone_name(self, seg: int) -> str:
        return self.corner_at((seg + 0.5) / N_SEGMENTS)

    # ------------------------------------------------- delta contra tu mejor
    def _record_lap_trace(self):
        ir = self.ir
        pct, cur = ir["LapDistPct"], ir["LapCurrentLapTime"]
        if pct is None or cur is None or pct < 0 or cur <= 0:
            return
        b = int(pct * PROFILE_BINS) % PROFILE_BINS
        if math.isnan(self.lap_trace[b]):
            self.lap_trace[b] = cur

    def _close_lap_trace(self, lap_time: float, clean: bool):
        trace, self.lap_trace = self.lap_trace, [math.nan] * PROFILE_BINS
        filled = sum(not math.isnan(x) for x in trace)
        if clean and lap_time > 0 and filled > PROFILE_BINS * 0.9 and lap_time < self.best_trace_time:
            self.best_trace, self.best_trace_time = trace, lap_time

    def live_delta(self) -> Optional[float]:
        """Delta de la vuelta actual contra tu mejor (positivo = más lento)"""
        ir = self.ir
        if ir["LapDeltaToBestLap_OK"]:
            return ir["LapDeltaToBestLap"]
        pct, cur = ir["LapDistPct"], ir["LapCurrentLapTime"]
        if not self.best_trace or pct is None or cur is None or pct < 0.03:
            return None
        b = int(pct * PROFILE_BINS) % PROFILE_BINS
        for k in range(b, -1, -1):
            if not math.isnan(self.best_trace[k]):
                return cur - self.best_trace[k]
        return None

    # ------------------------------------------------- límites de pista (vivo)
    def _zone_key(self, z: dict) -> int:
        return int(round(z["s"] / 10))

    def _zone_name(self, z: dict) -> str:
        return self.corner_at((z["s"] % self.track_len) / self.track_len)

    def _track_limits_live(self):
        """
        Zonas de límite aprendidas de tus .ibt (con GPS) y de cada salida en vivo.
        En vivo no hay posición lateral fiable, así que se usan: aviso previo, salida real
        (PlayerTrackSurface) y velocidad de salida.
        """
        if self.limits is None:
            return
        ir = self.ir
        s = ir["LapDist"]
        v = ir["Speed"] or 0.0
        if s is None or s < 0 or ir["OnPitRoad"]:
            return
        now = time.time()
        L = self.track_len
        rumble_l = (ir["TireLF_RumblePitch"] or 0) > 0 or (ir["TireLR_RumblePitch"] or 0) > 0
        rumble_r = (ir["TireRF_RumblePitch"] or 0) > 0 or (ir["TireRR_RumblePitch"] or 0) > 0
        if rumble_l or rumble_r:
            self._rumble_side, self._rumble_t = (1 if rumble_l else -1), now

        def rel(z):
            return (s - z["s"] + L / 2) % L - L / 2

        # Aviso antes de llegar a una zona donde te pasas seguido
        for z in self.limits.zones:
            d = rel(z)
            st = self.zone_state.setdefault(self._zone_key(z), {"offs": 0, "passes": 0, "best_exit": 0.0,
                                                                "curb": 0})
            total = z.get("offs", 0)
            if -260 < d < -180 and v > 20 and (st["offs"] >= 1 or total >= 2):
                self.emit(f"Ojo límite {self._zone_name(z)}.", P_CAR, key=f"limw{self._zone_key(z)}",
                          cooldown=self.ref_lap() * 2.5, ttl=3)

        # Entrada / salida de la zona activa
        zone = next((z for z in self.limits.zones if -60 <= rel(z) <= 60), None)
        if zone is not None and self._cur_zone is None:
            self._cur_zone = {"z": zone, "off": False, "curb": False}
        if self._cur_zone is not None:
            cz = self._cur_zone
            if (rumble_l and cz["z"]["side"] > 0) or (rumble_r and cz["z"]["side"] < 0):
                cz["curb"] = True
            if zone is not cz["z"]:
                st = self.zone_state.setdefault(self._zone_key(cz["z"]), {"offs": 0, "passes": 0,
                                                                         "best_exit": 0.0, "curb": 0})
                st["passes"] += 1
                st["curb"] += int(cz["curb"])
                exit_kmh = v * 3.6
                if not cz["off"]:
                    if st["passes"] >= 3 and exit_kmh > st["best_exit"] + 0.5 and cz["curb"]:
                        self.emit(f"Salida perfecta {self._zone_name(cz['z'])}, usaste el piano sin pasarte.",
                                  P_INFO, key=f"limok{self._zone_key(cz['z'])}", cooldown=self.ref_lap() * 3, ttl=6)
                    st["best_exit"] = max(st["best_exit"], exit_kmh)
                self._cur_zone = None

        # Salida de pista real (iRacing marca fuera cuando pasas el límite)
        off = ir["PlayerTrackSurface"] == 0
        if off and not self._prev_offtrack and v > 12:
            self._last_off_t = now
            z = next((z for z in self.limits.zones if abs(rel(z)) < 60), None)
            vx, vy = ir["VelocityX"] or 0.0, ir["VelocityY"] or 0.0
            stable = abs(math.atan2(vy, max(abs(vx), 0.1))) < 0.12
            if z is None and stable:
                side = self._rumble_side if now - self._rumble_t < 1.0 else 0
                z = {"s": float(s), "side": side or 1, "limits": [], "s_hist": [float(s)], "offs": 0, "samples": []}
                self.limits.zones.append(z)
            if z is not None:
                z["offs"] = z.get("offs", 0) + 1
                self._stint_offs += 1
                self.limits_dirty = True
                st = self.zone_state.setdefault(self._zone_key(z), {"offs": 0, "passes": 0, "best_exit": 0.0,
                                                                    "curb": 0})
                st["offs"] += 1
                if self._cur_zone is not None:
                    self._cur_zone["off"] = True
                n = st["offs"]
                extra = f" Van {n} ahí esta sesión, deja un poco de margen." if n >= 2 else ""
                self.emit(f"Te pasaste del límite {self._zone_name(z)}.{extra}", P_CAR,
                          key="limoff", cooldown=6, ttl=5)
        self._prev_offtrack = off

    # ------------------------------------------------ referencias de frenada
    def _apexes_m(self) -> List[float]:
        return [c * self.track_len for c in self.corners]

    def _brakes_by_corner(self, brakes: List[list]) -> Dict[int, float]:
        """
        Frenada principal de cada curva: la de mayor presión en los 400 m previos al vértice
        (empate: la más cercana). Misma definición que el analizador de .ibt.
        """
        out: Dict[int, tuple] = {}
        apexes = self._apexes_m()
        L = self.track_len
        # Ventana de búsqueda de cada curva: hasta 400 m atrás, sin pasar del vértice anterior
        reach = [min(400.0, (a - apexes[k - 1]) % L or L) for k, a in enumerate(apexes)]
        for b, peak in brakes:
            for k, a in enumerate(apexes):
                d = (a - b) % L
                if d >= reach[k]:
                    continue
                if k not in out or peak > out[k][1] + 0.05 or (abs(peak - out[k][1]) <= 0.05 and d < out[k][2]):
                    out[k] = (b, peak, d)
        return {k: v[0] for k, v in out.items()}

    def ref_brake(self, k: int) -> Optional[float]:
        if self.refs and k < len(self.refs["corners"]):
            return self.refs["corners"][k].get("brake_at_m")
        return self.best_lap_brakes.get(k)

    def _brake_cause(self, pct: float) -> str:
        """Explica la pérdida en una zona si la frenada fue distinta a tu referencia"""
        apexes = self._apexes_m()
        if not apexes:
            return ""
        s = pct * self.track_len
        k = min(range(len(apexes)), key=lambda i: abs((s - apexes[i] + self.track_len / 2) % self.track_len
                                                      - self.track_len / 2))
        if abs((s - apexes[k] + self.track_len / 2) % self.track_len - self.track_len / 2) > self.track_len / N_SEGMENTS:
            return ""
        ref, mine = self.ref_brake(k), self.last_lap_brakes.get(k)
        if ref is None or mine is None:
            return ""
        diff = ((mine - ref + self.track_len / 2) % self.track_len) - self.track_len / 2
        if diff < -8:
            return f" Frenaste {-diff:.0f} metros antes que tu referencia."
        if diff > 8:
            return f" Frenaste {diff:.0f} metros más tarde que tu referencia, te pasaste de frenada."
        return ""
