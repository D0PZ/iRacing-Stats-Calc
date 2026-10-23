"""Combustible: consumo, plan de carrera, briefing antes de la largada y avisos de combustible."""

import math
from typing import Optional

from app.engineer.common import P_CAR, P_FLAG, P_INFO, irsdk


class FuelMixin:
    # ------------------------------------------- briefing y plan de carrera
    def fuel_per_lap_estimate(self) -> Optional[float]:
        """Consumo de este evento (mediana) o el guardado del análisis de .ibt de esta pista y auto"""
        if len(self.event_fuel) >= 2:
            vals = sorted(self.event_fuel)
            return vals[len(vals) // 2]
        if self.refs and self.refs.get("fuel_per_lap"):
            return float(self.refs["fuel_per_lap"])
        return None

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
        need = per_lap * (laps + 1) + 0.5 if per_lap else None   # +1 vuelta de formación, +0.5 L de margen
        return {"laps": laps, "per_lap": per_lap, "need": need}

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
        if key in self.briefed_sessions or not plan:
            return
        state = ir["SessionState"] or 0
        if self.is_race:
            if state > irsdk.SessionState.parade_laps:
                return
            self.briefed_sessions.add(key)
            self.emit(self._briefing_text(plan), P_FLAG, ttl=60)
        elif plan["need"]:
            # Práctica o clasificación: avisar si el setup no trae combustible para la carrera
            setup_fuel = self._setup_fuel()
            if setup_fuel is not None and setup_fuel + 0.1 < plan["need"]:
                self.briefed_sessions.add(key)
                self.emit(f"Ojo con la carrera: son {plan['laps']} vueltas, gastas {plan['per_lap']:.2f} litros "
                          f"por vuelta y tu setup tiene {setup_fuel:.0f} litros. Para no parar necesitas "
                          f"{plan['need']:.0f}. Cámbialo en el garage antes de la carrera.", P_FLAG, ttl=60)

    def _briefing_text(self, plan: dict) -> str:
        ir = self.ir
        parts = [f"Briefing: {plan['laps']} vueltas."]
        fuel = ir["FuelLevel"] or 0.0
        if plan["need"]:
            if fuel + 0.1 < plan["need"]:
                laps_fuel = fuel / plan["per_lap"]
                parts.append(f"Llevas {fuel:.0f} litros y necesitas {plan['need']:.0f}: alcanza para unas "
                             f"{laps_fuel:.0f} vueltas, vas a tener que parar o ahorrar.")
            else:
                parts.append(f"Combustible bien: {fuel:.0f} litros, te sobran {fuel - plan['need'] + 0.5:.1f}.")
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
