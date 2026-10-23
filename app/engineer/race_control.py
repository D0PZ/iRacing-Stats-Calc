"""Dirección de carrera: tráfico, autos detenidos, banderas, incidentes, pits, clima y vueltas restantes."""

import math
import time

from app.engineer.common import P_CAR, P_FLAG, P_INFO, P_SAFETY, irsdk


class RaceControlMixin:
    # ------------------------------------------------------------- tráfico
    def _check_traffic(self):
        if not self.is_race or self.ir["OnPitRoad"]:
            return
        me = self.cars.get(self.me)
        if not me or me.prog is None:
            return
        best = self.ir["CarIdxBestLapTime"] or []
        ref = self.ref_lap()
        for idx, d in self.drivers.items():
            c = self.cars[idx]
            if idx == self.me or c.prog is None or c.on_pit or c.surface != 3:
                continue
            ahead_m = ((c.prog - me.prog) % 1.0) * self.track_len
            behind_m = self.track_len - ahead_m
            other_class = d.get("CarClassID") != self.my_class
            their_best = best[idx] if idx < len(best) and best[idx] > 0 else None
            if ahead_m < 350:
                lapped = not other_class and math.floor(c.prog) < math.floor(me.prog)
                slower = other_class and their_best and their_best > ref + 2
                if lapped or slower:
                    what = "auto doblado" if lapped else "auto más lento de otra clase"
                    self.emit(f"Tráfico: {what} a {int(round(ahead_m, -1))} metros, "
                              f"elige bien dónde pasarlo.", P_CAR, key=f"traffic{idx}",
                              cooldown=ref * 0.8, ttl=5)
            elif behind_m < 250 and other_class and their_best and their_best < ref - 2:
                self.emit("Auto más rápido de otra clase llegando por detrás. Mantén tu línea.",
                          P_CAR, key=f"fastclass{idx}", cooldown=ref * 0.8, ttl=5)

    # ------------------------------------------------------------- hazards
    def _check_hazards(self):
        ir = self.ir
        if ir["OnPitRoad"] or (ir["SessionState"] or 0) >= irsdk.SessionState.checkered:
            return
        me = self.cars.get(self.me)
        if not me or me.prog is None:
            return
        surfaces = ir["CarIdxTrackSurface"] or []
        on_pit = ir["CarIdxOnPitRoad"] or []
        now = time.time()
        for idx, c in self.cars.items():
            if idx == self.me or c.prog is None or idx >= len(surfaces):
                continue
            if on_pit[idx] or surfaces[idx] in (-1, 1, 2):
                continue
            ahead_m = ((c.prog - me.prog) % 1.0) * self.track_len
            behind_m = self.track_len - ahead_m
            if c.hazard_cooldown > now:
                continue
            stopped = c.speed < 6 and c.max_recent_speed > 20
            off = surfaces[idx] == 0
            # Frenar para una horquilla no es accidente: exigir muy lento o fuera de pista
            spun = c.max_recent_speed > 25 and (c.speed < 10 or (off and c.speed < 0.4 * c.max_recent_speed))
            if ahead_m < 700 and (stopped or (off and spun)):
                where = f"a {max(100, int(round(ahead_m, -2)))}"
                if stopped:
                    self.emit(f"¡Auto parado {where}!", P_SAFETY, ttl=3)
                else:
                    self.emit(f"¡Trompo {where}!", P_SAFETY, ttl=3)
                c.hazard_cooldown = now + 15
            elif ahead_m < 300 and off and c.speed > 15:
                self.emit(f"Fuera de pista a {max(100, int(round(ahead_m, -2)))}.", P_SAFETY, ttl=3)
                c.hazard_cooldown = now + 12
            elif behind_m < 150 and (stopped or (off and spun)):
                self.emit("Accidente atrás.", P_CAR, ttl=3)
                c.hazard_cooldown = now + 15

    # --------------------------------------------------------------- flags
    def _check_flags_and_state(self):
        ir = self.ir
        flags = int(ir["SessionFlags"] or 0)
        if self.prev_flags is None:  # primer tick: no anunciar banderas viejas
            self.prev_flags = flags
        new = flags & ~self.prev_flags
        F = irsdk.Flags
        if new & (F.caution | F.caution_waving):
            self.emit("¡Full course yellow!", P_FLAG, key="caution", cooldown=10, ttl=4)
        elif new & (F.yellow | F.yellow_waving):
            self.emit("¡Amarilla, no adelantes!", P_FLAG, key="yellow", cooldown=6, ttl=3)
        if new & F.debris:
            self.emit("Restos en pista.", P_FLAG, key="debris", cooldown=15, ttl=4)
        if new & F.blue:
            self.emit("Azul, mantén la línea.", P_FLAG, key="blue", cooldown=20, ttl=4)
        if new & F.black:
            self.emit("¡Negra, a pits!", P_SAFETY, key="black", cooldown=30)
        if new & F.repair:
            self.emit("¡Reparación, a pits!", P_SAFETY, key="repair", cooldown=30)
        if new & F.white:
            self.emit("Última vuelta.", P_FLAG, key="white", cooldown=60)
        if new & F.checkered:
            self.emit(f"Bandera a cuadros. Buen trabajo, {self.name}.", P_FLAG, key="checkered", cooldown=120)
        if flags & (F.caution | F.caution_waving):
            self.lap_stats["yellow"] = True  # full course yellow: la vuelta no sirve para comparar ritmo
        self.prev_flags = flags

        state = ir["SessionState"] or 0
        if state != self.prev_state and state == irsdk.SessionState.racing:
            session_type = ""
            try:
                sessions = ir["SessionInfo"]["Sessions"]
                session_type = str(sessions[ir["SessionNum"]]["SessionType"]).lower()
            except Exception:
                pass
            if "race" in session_type:
                self.emit("¡Verde, verde, verde!", P_FLAG)
        self.prev_state = state

    def _laps_countdown(self):
        if not self.is_race:
            return
        left = self.laps_left()
        if left in (10, 5, 3, 2):
            self.emit(f"Quedan {left} vueltas.", P_INFO, key=f"left{left}", cooldown=300, ttl=10)

    # ---------------------------------------------- incidents & warnings
    def _check_incidents_and_warnings(self):
        ir = self.ir
        inc = ir["PlayerCarMyIncidentCount"]
        if inc is not None:
            if self.prev_incidents is not None and inc > self.prev_incidents \
                    and time.time() - self._last_off_t > 4:
                self.emit(f"Incidente de {inc - self.prev_incidents}x, llevas {inc}. Calma.",
                          P_CAR, key="inc", cooldown=5, ttl=6)
            self.prev_incidents = inc

        warn = int(ir["EngineWarnings"] or 0)
        # Motor apagado al subirse al auto no es alarma: solo avisar en movimiento
        if (ir["Speed"] or 0) < 10:
            self.prev_warnings = warn
            return
        new = warn & ~self.prev_warnings
        W = irsdk.EngineWarnings
        if new & W.water_temp_warning:
            self.emit("Temperatura de agua alta. Sal del rebufo para enfriar.", P_CAR, key="water", cooldown=60)
        if new & W.oil_pressure_warning:
            self.emit("Presión de aceite baja.", P_CAR, key="oilp", cooldown=60)
        if new & W.oil_temp_warning:
            self.emit("Temperatura de aceite alta.", P_CAR, key="oilt", cooldown=60)
        if new & W.engine_stalled:
            self.emit("¡Motor apagado!", P_SAFETY, key="stall", cooldown=10)
        self.prev_warnings = warn

    # ------------------------------------------------------------ pit lane
    def _check_pits(self):
        """Limitador y velocidad en pits: una penalización por exceso es evitable"""
        ir = self.ir
        limit = self.pit_limit_kmh
        if not limit:
            return
        v = (ir["Speed"] or 0.0) * 3.6
        limiter = bool(int(ir["EngineWarnings"] or 0) & irsdk.EngineWarnings.pit_speed_limiter)
        surface = ir["PlayerTrackSurface"]
        if ir["OnPitRoad"]:
            if not limiter and v > limit * 0.6:
                self.emit("¡Limitador!", P_SAFETY, key="limiter", cooldown=4, ttl=2)
            if v > limit + 0.5:
                self.emit("¡Velocidad en pits!", P_SAFETY,
                          key="pitspeed", cooldown=3, ttl=2)
        elif surface == 2 and not limiter and v > limit + 20:  # aproximándose a la entrada de pits
            self.emit("¡Limitador, entrada de pits!", P_SAFETY, key="pitentry",
                      cooldown=20, ttl=3)

    # --------------------------------------------------------------- clima
    def _check_weather(self):
        ir = self.ir
        wet = ir["TrackWetness"]
        precip = ir["Precipitation"] or 0.0
        if wet is not None:
            if self.prev_wetness is not None and wet != self.prev_wetness:
                if wet > self.prev_wetness and wet >= 3:
                    self.emit("La pista se está mojando, agarre cambiando. Frena antes y cuidado con los pianos.",
                              P_FLAG, key="wetter", cooldown=120, ttl=10)
                elif wet < self.prev_wetness and self.prev_wetness >= 3:
                    self.emit("La pista se está secando, vas a tener más agarre vuelta a vuelta.", P_CAR,
                              key="drier", cooldown=120, ttl=10)
            self.prev_wetness = wet
        if precip > 0.05 and self.prev_precip <= 0.05:
            self.emit("Empieza a llover.", P_FLAG, key="rain", cooldown=180, ttl=10)
        self.prev_precip = precip
