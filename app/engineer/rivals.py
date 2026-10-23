"""Rivales: posición estimada de cada auto, gaps, eventos y reporte de batalla."""

import math
from typing import Dict, Optional

from app.engineer.common import N_SEGMENTS, P_BATTLE, P_INFO


class RivalsMixin:
    # -------------------------------------------------------------- rivals
    def _update_cars(self, t: float):
        ir = self.ir
        laps = ir["CarIdxLap"] or []
        pcts = ir["CarIdxLapDistPct"] or []
        last = ir["CarIdxLastLapTime"] or []
        surfaces = ir["CarIdxTrackSurface"] or []
        on_pit = ir["CarIdxOnPitRoad"] or []
        for idx in self.drivers:
            if idx >= len(pcts):
                continue
            pct = pcts[idx]
            c = self.cars[idx]
            c.prev_on_pit, c.on_pit = c.on_pit, bool(on_pit[idx]) if idx < len(on_pit) else False
            c.prev_surface, c.surface = c.surface, surfaces[idx] if idx < len(surfaces) else 3
            if pct is None or pct < 0:
                c.prog = None
                c.seg = None
                continue
            prog = laps[idx] + pct
            if c.prog is not None:
                dt = t - c.prog_t
                dp = prog - c.prog
                if 0 < dt < 1.0 and -0.01 < dp < 0.1:
                    inst = dp * self.track_len / dt
                    c.speed = 0.6 * c.speed + 0.4 * max(inst, 0.0)
                    c.max_recent_speed = max(c.max_recent_speed * 0.995, c.speed)
                else:
                    c.speed = 0.0 if dp == 0 else c.speed
            # Minisectores con cruce interpolado
            seg = int(pct * N_SEGMENTS) % N_SEGMENTS
            if c.seg is not None and seg != c.seg:
                if seg == (c.seg + 1) % N_SEGMENTS and c.prog is not None:
                    boundary = (c.seg + 1) / N_SEGMENTS
                    p0 = c.prog % 1.0
                    p1 = pct if seg != 0 else pct + 1.0
                    frac = (boundary - p0) / (p1 - p0) if p1 > p0 else 1.0
                    cross_t = c.prog_t + frac * (t - c.prog_t)
                    if c.seg_enter_t > 0:
                        c.seg_times[c.seg] = cross_t - c.seg_enter_t
                        if idx == self.me:
                            self.my_lap_segs[c.seg] = c.seg_times[c.seg]
                    c.seg_enter_t = cross_t
                else:
                    c.seg_enter_t = 0.0  # salto raro (pits, reset), descartar
            c.seg = seg
            c.prog = prog
            c.prog_t = t
            if idx < len(last) and last[idx] and last[idx] > 0 and last[idx] != c.last_lap_seen:
                c.last_lap_seen = last[idx]
                c.laps.append(last[idx])

    def _gap_to(self, idx: int) -> Optional[float]:
        """Gap en segundos (positivo = el rival va adelante)"""
        me, other = self.cars.get(self.me), self.cars.get(idx)
        if not me or not other or me.prog is None or other.prog is None:
            return None
        ref = self.ref_lap()
        est = self.ir["CarIdxEstTime"]
        if est and max(idx, self.me) < len(est) and est[idx] > 0 and est[self.me] > 0:
            # Tiempo estimado por iRacing para llegar a cada posición: más preciso en curvas
            gap = (math.floor(other.prog) - math.floor(me.prog)) * ref + est[idx] - est[self.me]
            if abs(gap - (other.prog - me.prog) * ref) < ref * 0.25:
                return gap
        return (other.prog - me.prog) * ref

    def _class_neighbors(self):
        ir = self.ir
        cpos = ir["CarIdxClassPosition"] or []
        my_pos = cpos[self.me] if self.me < len(cpos) else 0
        ahead = behind = None
        if my_pos and my_pos > 0:
            for idx, d in self.drivers.items():
                if idx == self.me or idx >= len(cpos) or d.get("CarClassID") != self.my_class:
                    continue
                if cpos[idx] == my_pos - 1:
                    ahead = idx
                elif cpos[idx] == my_pos + 1:
                    behind = idx
        return my_pos, ahead, behind

    # ------------------------------------------------------ rivales directos
    def _rival_events(self):
        _, ahead, behind = self._class_neighbors()
        for idx, role in ((ahead, "adelante"), (behind, "atrás")):
            if idx is None:
                continue
            c = self.cars[idx]
            gap = self._gap_to(idx)
            if gap is None or abs(gap) > 8:
                continue
            if c.on_pit and not c.prev_on_pit:
                tip = " Empuja ahora, vuelta de ataque." if role == "adelante" else " Pista libre atrás."
                self.emit(f"{self.car_label(idx).capitalize()}, el de {role}, entró a pits.{tip}",
                          P_BATTLE, key=f"pit{idx}", cooldown=60)
            elif c.surface == 0 and c.prev_surface == 3:
                if role == "adelante":
                    self.emit("¡Se salió el de adelante, aprovecha!", P_BATTLE,
                              key=f"off{idx}", cooldown=20, ttl=4)
                else:
                    self.emit("Se salió el de atrás.", P_BATTLE, key=f"off{idx}",
                              cooldown=20, ttl=5)

    @staticmethod
    def _gap_rate(hist) -> float:
        """Cuánto se achica el gap por vuelta (positivo = se acercan)"""
        h = list(hist)[-4:]
        if len(h) < 2:
            return 0.0
        return (h[0] - h[-1]) / (len(h) - 1)

    def _battle_report(self):
        if not self.is_race:
            return
        my_pos, ahead, behind = self._class_neighbors()
        my_laps = self.cars[self.me].laps
        my_last = my_laps[-1] if my_laps else None
        me_segs = self.cars[self.me].seg_times
        ref = self.ref_lap()
        left = self.laps_left()
        spoke = False

        if self.prev_class_pos and my_pos and my_pos != self.prev_class_pos:
            if my_pos < self.prev_class_pos:
                self.emit(f"Bien ahí, ganaste posición. Vas P{my_pos}.", P_INFO, ttl=8)
            else:
                self.emit(f"Perdiste posición, vas P{my_pos}.", P_INFO, ttl=8)
        self.prev_class_pos = my_pos

        # Ataque al de adelante
        gap_a = None
        if ahead is not None:
            gap_a = self._gap_to(ahead)
            rival = self.cars[ahead]
            hist = self.gap_history[(ahead, "a")]
            if gap_a is not None and 0 < gap_a < ref * 0.5:
                hist.append(gap_a)
                rate = self._gap_rate(hist)
                msg = None
                if rival.laps and my_last and gap_a < 2.5:
                    delta = rival.laps[-1] - my_last
                    dropping = len(rival.laps) >= 3 and rival.laps[-1] - min(rival.laps[-3:]) > 0.6
                    if delta > 0.3 or dropping:
                        msg = (f"{self.name}, {self.car_label(ahead)} adelante a {self.fmt_gap(gap_a)}. "
                               f"Su rendimiento está bajando, {self.fmt_gap(max(delta, 0.1))} más lento que tú. "
                               f"¡Adelántalo!")
                if msg is None and rate > 0.1 and gap_a < 4:
                    laps_to = max(1, math.ceil((gap_a - 0.3) / rate))
                    msg = (f"Le descuentas {self.fmt_gap(rate)} por vuelta al de adelante, "
                           f"está a {self.fmt_gap(gap_a)}.")
                    if left is not None:
                        if laps_to <= left:
                            msg += (" Lo alcanzas en la próxima vuelta." if laps_to <= 1
                                    else f" Lo alcanzas en unas {laps_to} vueltas.")
                        else:
                            need = (gap_a - 0.3) / max(left, 1)
                            msg += f" A este ritmo no llegas, necesitas {self.fmt_gap(need)} por vuelta."
                elif msg is None and rate < -0.25 and gap_a < 3 and len(hist) >= 3:
                    msg = (f"El de adelante se escapa, {self.fmt_gap(-rate)} por vuelta. "
                           f"Cuida neumáticos y mira atrás.")
                if msg:
                    best_seg = self._segment_advantage(me_segs, rival.seg_times)
                    if best_seg and gap_a < 1.5:
                        seg, diff = best_seg
                        msg += f" Eres más rápido {self.zone_name(seg)}, prepara el ataque ahí."
                    self.emit(msg, P_BATTLE, key=f"attack{ahead}", cooldown=ref * 0.9, ttl=12)
                    spoke = True

        # Defensa contra el de atrás
        gap_b = None
        if behind is not None:
            gap = self._gap_to(behind)
            rival = self.cars[behind]
            hist = self.gap_history[(behind, "b")]
            if gap is not None and gap < 0:
                gap_b = g = -gap
                hist.append(g)
                rate = self._gap_rate(hist)
                if g < 1.0 or (rate > 0.15 and g < 3.0):
                    msg = f"Te presiona {self.car_label(behind)}, a {self.fmt_gap(g)}"
                    msg += f", te descuenta {self.fmt_gap(rate)} por vuelta." if rate > 0.1 else "."
                    if rate > 0.1 and left is not None and g > 0.8:
                        laps_to = math.ceil((g - 0.3) / rate)
                        msg += ((" Te alcanza en la próxima vuelta." if laps_to <= 1
                                 else f" Te alcanza en unas {laps_to} vueltas.") if laps_to <= left
                                else " A este ritmo no te alcanza antes del final.")
                    best_seg = self._segment_advantage(rival.seg_times, me_segs)
                    if best_seg:
                        seg, diff = best_seg
                        msg += (f" Te gana {self.fmt_gap(diff)} {self.zone_name(seg)}, "
                                f"seguro frena más tarde ahí. Cierra la puerta.")
                    self.emit(msg, P_BATTLE, key=f"defend{behind}", cooldown=ref * 0.9, ttl=12)
                    spoke = True

        # Resumen cuando hay calma
        self.laps_since_summary += 1
        if not spoke and self.laps_since_summary >= 3 and my_pos:
            parts = [f"Vas P{my_pos}"]
            if gap_a is not None and 0 < gap_a < 30:
                parts.append(f"a {self.fmt_gap(gap_a)} del de adelante")
            if gap_b is not None and gap_b < 30:
                parts.append(f"{self.fmt_gap(gap_b)} de ventaja atrás")
            self.emit(", ".join(parts) + ".", P_INFO, ttl=10)
            self.laps_since_summary = 0

    @staticmethod
    def _segment_advantage(fast: Dict[int, float], slow: Dict[int, float]):
        """Minisector donde 'fast' le gana más tiempo a 'slow'"""
        best = None
        for seg, t_fast in fast.items():
            t_slow = slow.get(seg)
            if not t_slow:
                continue
            diff = t_slow - t_fast
            if diff > 0.08 and (best is None or diff > best[1]):
                best = (seg, diff)
        return best
