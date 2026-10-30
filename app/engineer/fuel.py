"""Combustible: consumo, plan de carrera, briefing antes de la largada y avisos de combustible."""

import math
from typing import Optional

from app.engineer.common import P_CAR, P_FLAG, P_INFO, irsdk
from app.session_plan import race_fuel, race_pace_fuel, race_use


class FuelMixin:
    # ------------------------------------------- briefing y plan de carrera
    def fuel_per_lap_estimate(self) -> Optional[float]:
        """Consumo a ritmo de carrera de este evento o el del análisis de .ibt de esta pista y auto,
        el mayor de los dos: una práctica tranquila no puede bajar el consumo de la carrera"""
        measured = race_pace_fuel(self.event_fuel) if len(self.event_fuel) >= 2 else None
        saved = float(self.refs["fuel_per_lap"]) if self.refs and self.refs.get("fuel_per_lap") else None
        return max((v for v in (measured, saved) if v), default=None)

    def race_plan(self) -> Optional[dict]:
        """Vueltas de la carrera del evento y combustible necesario para hacerla sin parar"""
        try:
            sessions = self.ir["SessionInfo"]["Sessions"]
            race = next(x for x in sessions if "race" in str(x.get("SessionType", "")).lower())
        except Exception:
            return None
        laps = None
        raw_laps = str(race.get("SessionLaps", ""))
        if raw_laps.isdigit():
            laps = int(raw_laps)
        else:
            secs = self._nums(race.get("SessionTime", ""))
            if secs:
                laps = math.ceil(secs[0] / self.ref_lap()) + 1
        per_lap = self.fuel_per_lap_estimate()
        if not laps:
            return None
        if not per_lap:
            return {"laps": laps, "per_lap": None, "need": None, "use": None}
        standing = self._standing_start()
        return {"laps": laps, "per_lap": per_lap, "need": race_fuel(per_lap, laps, standing),
                "use": race_use(per_lap, laps, standing)}

    def _standing_start(self) -> Optional[bool]:
        try:
            return bool(int(self.ir["WeekendInfo"]["WeekendOptions"]["StandingStart"]))
        except Exception:
            return None

    def _setup_fuel(self) -> Optional[float]:
        try:
            for path, value in self._walk(self.ir["CarSetup"] or {}):
                if path and path[-1] == "FuelLevel":
                    v = self._nums(value)
                    return v[0] if v else None
        except Exception:
            pass
        return None

    def _check_briefing(self):
        if not self.on_track:
            return
        ir = self.ir
        plan = self.race_plan()
        key = (self.track_name, self.session_num)
        if not plan:
            return
        state = ir["SessionState"] or 0
        if self.is_race:
            if key in self.briefed_sessions or state > irsdk.SessionState.parade_laps:
                return
            self.briefed_sessions.add(key)
            self.emit(self._briefing_text(plan), P_FLAG, ttl=60)
        elif plan["need"]:
            # Práctica o clasificación: avisar si el setup no trae combustible para la carrera,
            # y de nuevo si el consumo medido sube (las vueltas más rápidas gastan más)
            setup_fuel = self._setup_fuel()
            warned = self.fuel_warned.get(key)
            if (setup_fuel is not None and setup_fuel + 0.1 < plan["need"]
                    and (warned is None or plan["need"] >= warned + 1.0)):
                self.fuel_warned[key] = plan["need"]
                self.emit(f"Ojo con la carrera: son {plan['laps']} vueltas, gastas {plan['per_lap']:.2f} litros "
                          f"por vuelta a ritmo de carrera y tu setup tiene {setup_fuel:.1f} litros. Carga "
                          f"{plan['need']:.1f}, con un litro de margen. Cámbialo en el garage antes de la carrera.",
                          P_FLAG, ttl=60)

    def _briefing_text(self, plan: dict) -> str:
        ir = self.ir
        parts = [f"Briefing: {plan['laps']} vueltas."]
        fuel = ir["FuelLevel"] or 0.0
        if plan["need"]:
            spare = fuel - plan["use"]
            if spare < 0:
                parts.append(f"Llevas {fuel:.1f} litros y la carrera gasta {plan['use']:.1f}: te faltan "
                             f"{-spare:.1f}, vas a tener que parar o ahorrar.")
            else:
                parts.append(f"Combustible bien: {fuel:.1f} litros, te sobran {spare:.1f}.")
        if self.limits and self.limits.zones:
            hot = sorted(self.limits.zones, key=lambda z: -z.get("offs", 0))[:2]
            hot = [z for z in hot if z.get("offs", 0) >= 2]
            if hot:
                parts.append("Cuidado con los límites " + " y ".join(self._zone_name(z) for z in hot) + ".")
        temp = ir["TrackTempCrew"]
        if temp is not None:
            parts.append(f"Pista a {temp:.0f} grados.")
        parts.append(f"Paciencia en la primera curva. Vamos, {self.name}.")
        return " ".join(parts)

    def _fuel_report(self, fuel: float):
        ir = self.ir
        used = [l["fuel"] for l in self.my_laps[-5:] if l["fuel"]]
        if len(used) < 2:
            return
        per_lap = sum(used) / len(used)
        if per_lap <= 0:
            return
        laps_fuel = fuel / per_lap
        laps_left = ir["SessionLapsRemainEx"]
        if laps_left is None or laps_left >= 32767:
            remain_t = ir["SessionTimeRemain"] or 0
            laps_left = math.ceil(remain_t / self.ref_lap()) if remain_t > 0 else None
        if laps_left is None:
            return
        if laps_fuel < laps_left + 0.3:
            short = (laps_left + 0.3 - laps_fuel) * per_lap
            self.emit(f"Ojo combustible: te alcanza para {laps_fuel:.1f} vueltas y faltan {laps_left}. "
                      f"Te faltan {short:.1f} litros, ahorra o para en pits.", P_CAR,
                      key="fuel_short", cooldown=self.ref_lap() * 2.5, ttl=15)
        elif len(self.my_laps) % 5 == 0:
            self.emit(f"Combustible para {laps_fuel:.0f} vueltas, faltan {laps_left}.", P_INFO, ttl=10)

    def _fuel_numbers(self):
        ir = self.ir
        fuel = ir["FuelLevel"] or 0.0
        used = [l["fuel"] for l in self.my_laps[-5:] if l.get("fuel")]
        per_lap = sum(used) / len(used) if used else None
        left = self.laps_left()
        return fuel, per_lap, left
