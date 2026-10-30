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
            m = self.tire_model
            return {"front": m.target_spread("LF", self.track_name), "rear": m.target_spread("LR", self.track_name),
                    "learned": m.target_source("front_spread", self.track_name) != "punto de partida"}
        return {"front": 7.0, "rear": 5.0}

    def _tire_limits(self) -> dict:
        """Límites del garage y perfil del auto (mínima más rápida, máximo camber, solo toe-in atrás)"""
        if self.tire_model is not None:
            return self.tire_model.limits
        from app.tire_model import limits_for
        from app.voice_commands import load_config
        return limits_for(self.car_name, load_config().get("tire_limits"))

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

    def _tire_data(self):
        """Medición de la última entrada a pits, temperatura de pista y si las gomas trabajaron lo suficiente"""
        from app.tire_model import measured, read_setup, worked
        data = read_setup(self.ir["CarSetup"] or {})
        track_temp = self.ir["TrackTempCrew"]
        return data, track_temp, measured(data) and worked(data, track_temp)

    def _toe_plan(self, laps: Optional[int] = None) -> List[dict]:
        """Toe por rueda según cómo se comportó el auto (por sentido de curva), tus temperaturas y tu historial"""
        from app.setup_db import flatten_setup
        from app.toe_advisor import advise
        laps = laps or max(self.laps_since_setup, 1)
        history = self.setup_db.stints if self.setup_db is not None else None
        data, _, ok = self._tire_data()
        temps = {c: d["temps"] for c, d in data.items()} if ok else None
        return advise(flatten_setup(self.ir["CarSetup"] or {}), laps, self.side_counts, history, temps,
                      rear_min=self._tire_limits().get("rear_toe_min_mm"))

    def _toe_recs(self, laps: Optional[int] = None) -> List[str]:
        from app.setup_db import flatten_setup
        from app.toe_advisor import phrases, toe_display
        return phrases(self._toe_plan(laps), *toe_display(flatten_setup(self.ir["CarSetup"] or {})))

    def _ideal_phrases(self) -> List[str]:
        """Valores ideales con todo tu historial en esta pista y auto (aunque sea otra sesión)"""
        if self.setup_db is None:
            return []
        sens = self.tire_model.state["sens"] if self.tire_model is not None else None
        ideals = self.setup_db.ideals(self._tire_targets(), sens, limits=self._tire_limits())
        if not ideals:
            return []
        from app.units import PRESSURE_WORDS
        names = {"LF": "delantera izquierda", "RF": "delantera derecha",
                 "LR": "trasera izquierda", "RR": "trasera derecha"}
        parts = []
        for w, r in ideals.items():
            bits = []
            if r.get("presion_frio") is not None:
                bits.append(f"{r['presion_frio']} {PRESSURE_WORDS[r.get('unidad', 'kPa')]} en frío")
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
        Ajustes exactos de camber y presión por rueda hacia el contacto perfecto, coordinados con el toe:
        si también se recomienda cambiar el toe, el camber compensa lo que ese toe moverá las temperaturas.
        record_laps: tiempos de la salida que acaba de terminar (para aprender y buscar tu óptimo).
        """
        from app.tire_model import TireModel, limits_for, measured
        from app.toe_advisor import plan
        from app.voice_commands import load_config
        data, track_temp, ok = self._tire_data()
        if not measured(data):
            return self._ideal_phrases()   # sesión nueva: lo que sabemos de esta pista con este auto
        car = self.car_name or "auto"
        if self.tire_model is None or self.tire_model.car != car:
            cfg = load_config()
            self.tire_model = TireModel(car, cfg.get("tire_targets"), limits_for(car, cfg.get("tire_limits")))
        m = self.tire_model
        self._temps_line = self._tire_line(data)
        if not ok:
            return ["Las gomas no alcanzaron temperatura en esa salida: la medición no sirve para camber ni "
                    "presiones. Dame al menos tres vueltas a ritmo antes de entrar."] + self._ideal_phrases()
        if record_laps is not None:
            m.learn(data, track_temp)
            m.record_stint(data, record_laps, self.track_name, track_temp)
        out = m.phrases(m.recommend(data, self.track_name, toe_plan=plan(self._toe_plan())))
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

    @staticmethod
    def _tire_line(data) -> str:
        """Para el reporte: O/M/I, presiones, camber y toe de cada rueda"""
        from app.tire_model import NAMES
        from app.units import fmt_length, fmt_pressure
        parts = []
        for c, d in data.items():
            if not d["temps"]:
                continue
            u = d.get("unit", "kPa")
            o, mid, i = d["temps"]
            txt = f"{NAMES[c]} O/M/I {o:.0f}/{mid:.0f}/{i:.0f} C"
            if d["cold"] is not None:
                txt += f", frío {fmt_pressure(d['cold'], u)} {u}"
            if d.get("hot") is not None:
                txt += f", caliente {fmt_pressure(d['hot'], u)} {u}"
            if d["camber"] is not None:
                txt += f", camber {d['camber']:.1f}"
            if d.get("toe") is not None:
                txt += f", toe {fmt_length(d['toe'])} mm"
            parts.append(txt)
        return ("Neumáticos: " + "; ".join(parts)) if parts else ""

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
