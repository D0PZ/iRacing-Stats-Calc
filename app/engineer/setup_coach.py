"""
Setup: salidas guardadas, progreso, pruebas A/B, consejos de chasis, toe y neumáticos,
y el reporte al volver a pits.
"""

import os
import time
from collections import defaultdict
from typing import Dict, List, Optional

from app.engineer.common import N_SEGMENTS, P_INFO
from app.paths import logs_dir
from app.util import nums


class SetupCoachMixin:
    # ------------------------------------------- base de setups por pista/auto
    def _tire_targets(self) -> Dict[str, float]:
        if self.tire_model is not None:
            return {"front": self.tire_model.target_spread("LF", self.track_name),
                    "rear": self.tire_model.target_spread("LR", self.track_name)}
        return {"front": 7.0, "rear": 5.0}

    def _store_stint(self, stint_laps: List[float], per_lap_events: dict, spin: float, recs: List[str]):
        if self.setup_db is None:
            return
        ir = self.ir
        fuel = [l["fuel"] for l in self.my_laps[-max(self.laps_since_setup, 1):] if l.get("fuel")]
        try:
            session = ""
            sessions = ir["SessionInfo"]["Sessions"]
            session = str(sessions[ir["SessionNum"]]["SessionType"])
        except Exception:
            pass
        handling = {k: round(v, 2) for k, v in per_lap_events.items()}
        handling["patinaje_s_por_vuelta"] = round(spin, 2)
        handling["por_sentido"] = dict(self.side_counts)
        handling["gradiente"] = self.k_acc.result()
        fuel_start = (ir["FuelLevel"] or 0.0) + sum(fuel) if fuel else None
        rec = self.setup_db.add_stint(ir["CarSetup"] or {}, stint_laps, session=session,
                                      track_temp=ir["TrackTempCrew"], air_temp=ir["AirTemp"], handling=handling,
                                      fuel_per_lap=round(sum(fuel) / len(fuel), 3) if fuel else None,
                                      offtracks=self._stint_offs, recommendations=recs, fuel_start=fuel_start)
        self._stint_offs = 0
        print(f"[setups] salida guardada ({rec['vueltas']} vueltas, "
              f"{'con' if rec['medicion'] else 'sin'} temperaturas) -> {len(self.setup_db.stints)} en total",
              flush=True)
        self._report_ab()

    def _record_progress(self):
        """Resumen de esta sesión para el seguimiento de progreso (se actualiza cada vuelta)"""
        if self.progress is None:
            return
        times = [l["time"] for l in self.my_laps if l["time"] > 0]
        if not times:
            return
        try:
            kind = str(self.ir["SessionInfo"]["Sessions"][self.ir["SessionNum"]]["SessionType"])
        except Exception:
            kind = ""
        ideal = sum(self.my_best_segs.values()) if len(self.my_best_segs) == N_SEGMENTS else None
        offs = sum(st.get("offs", 0) for st in self.zone_state.values())
        self.progress.add(f"vivo {self.session_stamp} {kind}", self.session_stamp, kind, times, ideal=ideal,
                          offtracks=offs, incidents=int(self.ir["PlayerCarMyIncidentCount"] or 0),
                          balance=self.k_acc.result(), source="vivo")

    def _report_ab(self) -> Optional[str]:
        """Prueba A/B: la salida que terminó contra la anterior con otro setup"""
        from app.ab_test import describe, last_ab
        from app.vehicle_dynamics import fuel_seconds_per_liter
        if self.setup_db is None:
            return None
        spl = fuel_seconds_per_liter(self.ref_lap())
        result = last_ab(self.setup_db.stints, spl)
        if not result:
            return None
        self.last_ab_text = describe(result)
        self.emit(self.last_ab_text, P_INFO, ttl=60)
        return self.last_ab_text

    def _record_cross_point(self):
        """Cada vez que cambias las perch en el garage, aprender cuánto se mueve el cross weight"""
        if self.setup_db is None:
            return
        try:
            from app.chassis_advisor import read_chassis
            from app.setup_db import flatten_setup
            c = read_chassis(flatten_setup(self.ir["CarSetup"] or {}))
            if c.get("cross") is not None and len(c.get("perch", {})) == 4:
                if self.setup_db.add_cross_point(c["perch"], c["cross"]):
                    print(f"[setups] perch {c['perch']} -> cross weight {c['cross']} %", flush=True)
        except Exception:
            pass

    def _chassis_recs(self, laps: Optional[int] = None) -> List[str]:
        """Barras, alerón y spring perch según dónde (velocidad y sentido de curva) aparece el problema"""
        from app.chassis_advisor import advise, learn_cross_sens, phrases
        from app.setup_db import flatten_setup
        laps = laps or max(self.laps_since_setup, 1)
        history = self.setup_db.stints if self.setup_db is not None else None
        sens = learn_cross_sens(self.setup_db.cross_points) if self.setup_db is not None else None
        flat = flatten_setup(self.ir["CarSetup"] or {})
        recs = advise(flat, laps, self.side_counts, history, cross_sens=sens)
        from app import balance_model
        K = self.k_acc.result()
        physics = balance_model.recommend(flat, K, history)
        # Donde hay K medido con suficientes muestras, la física manda sobre el conteo de eventos
        measured = {b for b, g in K.items() if g.get("n", 0) >= balance_model.MIN_SAMPLES}
        replaced = set()
        if "lenta" in measured:
            replaced |= {"Barra delantera", "Barra trasera"}
        if "rapida" in measured:
            replaced.add("Alerón")
        recs = [r for r in recs if r["param"] not in replaced]
        return balance_model.phrases(physics) + phrases(recs)

    def _toe_recs(self, laps: Optional[int] = None) -> List[str]:
        """Toe por rueda según cómo se comportó el auto (por sentido de curva) y tu historial"""
        from app.setup_db import flatten_setup
        from app.toe_advisor import advise, phrases
        laps = laps or max(self.laps_since_setup, 1)
        history = self.setup_db.stints if self.setup_db is not None else None
        recs = advise(flatten_setup(self.ir["CarSetup"] or {}), laps, self.side_counts, history)
        return phrases(recs)

    def _ideal_phrases(self) -> List[str]:
        """Valores ideales con todo tu historial en esta pista y auto (aunque sea otra sesión)"""
        if self.setup_db is None:
            return []
        sens = self.tire_model.state["sens"] if self.tire_model is not None else None
        ideals = self.setup_db.ideals(self._tire_targets(), sens)
        if not ideals:
            return []
        names = {"LF": "delantera izquierda", "RF": "delantera derecha",
                 "LR": "trasera izquierda", "RR": "trasera derecha"}
        parts = []
        for w, r in ideals.items():
            bits = []
            if r.get("presion_frio") is not None:
                bits.append(f"{r['presion_frio']} kilopascales en frío")
            if r.get("camber") is not None:
                bits.append(f"camber {r['camber']:.1f}")
            if bits:
                parts.append(f"{names[w]} {' y '.join(bits)}")
        n = max(r["salidas"] for r in ideals.values())
        method = "ajustado con tus salidas" if any(r["metodo"].startswith("ajuste") for r in ideals.values()) \
            else "estimado desde tu última salida medida"
        return [f"Ideales para {self.track_name} según {n} salida{'s' if n != 1 else ''} medida{'s' if n != 1 else ''} "
                f"({method}): " + "; ".join(parts) + "."]

    def _tire_recs(self, record_laps: Optional[List[float]] = None) -> List[str]:
        """
        Ajustes exactos de camber y presión por rueda hacia el contacto perfecto.
        record_laps: tiempos de la salida que acaba de terminar (para buscar tu óptimo).
        """
        from app.tire_model import TireModel, measured, read_setup
        from app.voice_commands import load_config
        data = read_setup(self.ir["CarSetup"] or {})
        if not measured(data):
            return self._ideal_phrases()   # sesión nueva: lo que sabemos de esta pista con este auto
        car = self.car_name or "auto"
        if self.tire_model is None or self.tire_model.car != car:
            self.tire_model = TireModel(car, load_config().get("tire_targets"))
        m = self.tire_model
        if record_laps is not None:
            m.learn(data)
            m.record_stint(data, record_laps, self.track_name)
        out = m.phrases(m.recommend(data, self.track_name))
        front, rear = m.target_spread("LF", self.track_name), m.target_spread("LR", self.track_name)
        out.append(f"Objetivo interior sobre exterior: {front:.1f} grados adelante y {rear:.1f} atrás, "
                   f"{m.target_source('front_spread', self.track_name)}.")
        return out

    # ------------------------------------------------- ingeniero de setup
    def _check_setup_report(self):
        ir = self.ir
        in_box = bool(ir["PlayerCarInPitStall"]) or not ir["IsOnTrack"]
        if in_box and not self.prev_in_stall and self.laps_since_setup >= 3:
            try:
                self._setup_report()
            except Exception as e:
                print(f"[setup] error: {e}", flush=True)
        self.prev_in_stall = in_box

    @staticmethod
    def _walk(d, path=()):
        if isinstance(d, dict):
            for k, v in d.items():
                yield from SetupCoachMixin._walk(v, path + (str(k),))
        else:
            yield path, d

    _nums = staticmethod(nums)

    def _tire_analysis(self, setup: dict) -> List[str]:
        """Temperaturas de la última salida (iRacing las actualiza al entrar a pits)"""
        temps, press = {}, {}
        for path, value in self._walk(setup):
            corner = next((p for p in path if p in ("LeftFront", "RightFront", "LeftRear", "RightRear")), None)
            if not corner:
                continue
            key = path[-1]
            if key.startswith("LastTemps"):
                vals = self._nums(value)
                if len(vals) == 3:
                    order = key[len("LastTemps"):]  # OMI o IMO
                    named = dict(zip(order, vals))
                    if all(k in named for k in "OMI"):
                        temps[corner] = named
            elif key == "LastHotPressure":
                v = self._nums(value)
                if v:
                    press[corner] = (v[0], str(value).split()[-1] if " " in str(value) else "")
        recs = []
        all_vals = [v for t in temps.values() for v in t.values()]
        if not temps or max(all_vals) - min(all_vals) < 2:
            return recs  # sin servicio en pits: temperaturas no actualizadas
        es = {"LeftFront": "delantera izquierda", "RightFront": "delantera derecha",
              "LeftRear": "trasera izquierda", "RightRear": "trasera derecha"}
        for axle, pair in (("delantero", ("LeftFront", "RightFront")), ("trasero", ("LeftRear", "RightRear"))):
            spreads = [temps[c]["I"] - temps[c]["O"] for c in pair if c in temps]
            if not spreads:
                continue
            spread = sum(spreads) / len(spreads)
            if spread > 12:
                recs.append(f"Camber {axle}: el interior está {spread:.0f} grados más caliente que el exterior. "
                            f"Quita camber negativo, unos 0.2 a 0.3 grados.")
            elif spread < 3:
                recs.append(f"Camber {axle}: el interior solo {spread:.0f} grados sobre el exterior. "
                            f"Agrega camber negativo, unos 0.2 a 0.3 grados.")
        for c, t in temps.items():
            edges = (t["I"] + t["O"]) / 2
            diff = t["M"] - edges
            if diff > 4:
                recs.append(f"Presión {es[c]}: el centro está {diff:.0f} grados sobre los bordes, "
                            f"está sobreinflada. Baja unos 3 a 5 kilopascales.")
            elif diff < -4:
                recs.append(f"Presión {es[c]}: el centro está {-diff:.0f} grados bajo los bordes, "
                            f"está baja. Sube unos 3 a 5 kilopascales.")
        front = [sum(temps[c].values()) / 3 for c in ("LeftFront", "RightFront") if c in temps]
        rear = [sum(temps[c].values()) / 3 for c in ("LeftRear", "RightRear") if c in temps]
        if front and rear:
            fa, ra = sum(front) / len(front), sum(rear) / len(rear)
            self._temps_line = (f"Temperatura media: delanteras {fa:.0f}, traseras {ra:.0f}. "
                                + ", ".join(f"{es[c]} O/M/I {t['O']:.0f}/{t['M']:.0f}/{t['I']:.0f}"
                                            for c, t in temps.items()))
            if fa - ra > 8:
                recs.append("Las delanteras trabajan mucho más que las traseras: el auto tiende a subvirar. "
                            "Ablanda la barra delantera o endurece la trasera.")
            elif ra - fa > 8:
                recs.append("Las traseras trabajan mucho más que las delanteras: el auto tiende a sobrevirar. "
                            "Ablanda la barra trasera o agrega ala trasera.")
        return recs

    def _setup_report(self):
        laps = max(self.laps_since_setup, 1)
        h = self.session_handling
        recs = []
        per = {k: len(h[k]) / laps for k in ("entry_slides", "exit_slides", "entry_under", "mid_under")}
        spin = sum(h["wheelspin_laps"]) / laps if h["wheelspin_laps"] else 0
        zone = self._most_common_zone
        if per["entry_slides"] >= 0.8:
            recs.append(f"Sobreviraje en la entrada ({zone(h['entry_slides'])}): balance de frenos hacia adelante. "
                        f"Si el setup es abierto: más bloqueo del diferencial en retención o barra trasera más blanda.")
        if per["entry_under"] >= 1.0:
            recs.append(f"Subviraje en la entrada ({zone(h['entry_under'])}): balance de frenos un clic atrás "
                        f"y barra delantera más blanda.")
        if per["mid_under"] >= 1.5:
            recs.append(f"Subviraje en el medio de curva ({zone(h['mid_under'])}): ablanda la barra delantera "
                        f"o endurece la trasera. En curvas rápidas, menos ala trasera.")
        if per["exit_slides"] >= 0.8 or spin >= 1.0:
            recs.append(f"Sobreviraje y patinaje en la salida ({zone(h['exit_slides'] or h['spin_pcts'])}): control de tracción "
                        f"más un clic, barra trasera más blanda, menos precarga del diferencial o más ala trasera.")
        self._temps_line = ""
        stint_laps = [l["time"] for l in self.my_laps[-self.laps_since_setup:] if l["time"] > 0]
        try:
            tire = self._tire_recs(record_laps=stint_laps)
        except Exception as e:
            tire = []
            print(f"[setup] sin datos de neumáticos: {e}", flush=True)
        if not recs:
            recs.append("El auto se ve equilibrado en estas vueltas.")
        toe = self._toe_recs(laps)
        chassis = self._chassis_recs(laps)
        recs = (chassis or recs[:2]) + tire + toe
        self._store_stint(stint_laps, per, spin, recs)

        # Reporte completo en archivo
        import os
        log_dir = logs_dir()
        os.makedirs(log_dir, exist_ok=True)
        path = os.path.join(log_dir, time.strftime("setup_%Y%m%d_%H%M%S.txt"))
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"Reporte de setup ({laps} vueltas analizadas)\n")
            f.write(f"Pista: {self.track_name} | Auto: {self.car_name}\n")
            f.write(f"Eventos por vuelta: {per}, patinaje medio {spin:.1f} s/vuelta\n")
            if self._temps_line:
                f.write(self._temps_line + "\n")
            f.write("\nRecomendaciones:\n")
            for r in recs:
                f.write(f"- {r}\n")
            f.write("\nSetup actual:\n")
            for p_, v in self._walk(self.ir["CarSetup"] or {}):
                f.write(f"  {' > '.join(p_)}: {v}\n")
        print(f"[setup] reporte guardado en {path}", flush=True)

        self.emit(f"Reporte de setup de {laps} vueltas. " + " ".join(recs), P_INFO, ttl=90)
        # Reiniciar para medir el efecto de los cambios en la próxima salida
        self.session_handling = defaultdict(list)
        self.side_counts = {}
        self.k_acc.reset()
        self.laps_since_setup = 0
