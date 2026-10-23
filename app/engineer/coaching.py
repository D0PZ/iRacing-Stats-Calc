"""Manejo: deslizamientos, bloqueos y ABS por curva, cierre de vuelta y consejos de pedales y volante."""

import math
from typing import Dict, List

from app.engineer.common import N_SEGMENTS, P_CAR, P_INFO
from app.vehicle_dynamics import WHEELBASE


class CoachingMixin:
    @staticmethod
    def _count(st: dict, kind: str, side: str, v: float):
        """Cuenta el evento por sentido de curva y por velocidad (lenta < 100, media < 160, rápida)"""
        kmh = v * 3.6
        bucket = "lenta" if kmh < 100 else "media" if kmh < 160 else "rapida"
        for key in (f"{kind}_{side}", f"{kind}_{bucket}"):
            st["by_side"][key] = st["by_side"].get(key, 0) + 1

    # ---------------------------------------------------- driver dynamics
    def _driver_dynamics(self):
        ir = self.ir
        dt = self.dt
        v = ir["Speed"] or 0.0
        steer = ir["SteeringWheelAngle"] or 0.0
        yaw = ir["YawRate"] or 0.0
        lat = abs(ir["LatAccel"] or 0.0) / 9.81
        thr = ir["Throttle"] or 0.0
        brk = ir["Brake"] or 0.0
        st = self.lap_stats

        if ir["OnPitRoad"]:
            st["pit"] = True
            self.stint_start_lap = len(self.my_laps) + 1
        surf = ir["PlayerTrackSurface"]
        if surf == 0:
            st["offtrack"] = True

        pct = ir["LapDistPct"]
        self._record_profile(pct, v)
        if not ir["OnPitRoad"] and ir["PlayerTrackSurface"] == 3:
            self.k_acc.add(v, steer, yaw, ir["LatAccel"] or 0.0, ir["LongAccel"] or 0.0)
        raw_t, raw_b = ir["ThrottleRaw"], ir["BrakeRaw"]
        self.inputs.add(dt, thr if raw_t is None else raw_t, brk if raw_b is None else raw_b,
                        math.degrees(steer), (ir["LatAccel"] or 0.0) / 9.81, v)
        if brk > 0.1 and not self._brake_on and v > 20:
            self._brake_on = True
            dist = ir["LapDist"]
            if dist is not None and dist >= 0:
                self.lap_brakes.append([float(dist), brk])   # [inicio, presión máxima]
        elif brk < 0.03:
            self._brake_on = False
        if self._brake_on and self.lap_brakes:
            self.lap_brakes[-1][1] = max(self.lap_brakes[-1][1], brk)

        if v < 15:
            self.slide_timer = self.under_timer = 0.0
            return

        # Aprender convención de signos volante/yaw en zonas tranquilas
        if 0.05 < abs(steer) and 0.1 < abs(yaw) and lat < 0.8:
            self.steer_yaw_sign.append(1 if steer * yaw > 0 else -1)
        sign = 1 if sum(self.steer_yaw_sign) >= 0 else -1

        # Sobreviraje: contravolante sostenido (volante opuesto al giro)
        if len(self.steer_yaw_sign) > 50 and abs(yaw) > 0.25 and abs(steer) > 0.08 \
                and steer * yaw * sign < 0:
            self.slide_timer += dt
            if self.slide_timer >= 0.25 and self.slide_timer - dt < 0.25:
                st["slides"] += 1
                side = "izq" if yaw * sign > 0 else "der"   # sentido de la curva (el auto gira hacia ahí)
                if brk > 0.15:
                    st["entry_slides"].append(pct)
                    self._count(st, "entry_slides", side, v)
                elif thr > 0.4:
                    st["exit_slides"].append(pct)
                    self._count(st, "exit_slides", side, v)
                if thr > 0.5:
                    self.emit("Cuidado con el acelerador en la salida, se te va la cola.",
                              P_CAR, key="exit_slide", cooldown=40, ttl=4)
        else:
            self.slide_timer = 0.0

        # Subviraje: ángulo de las ruedas delanteras menos el ángulo que el auto realmente gira
        # (Ackermann L·yaw/v). La relación de dirección se aprende en giros suaves a baja carga.
        if 10 < v < 35 and abs(steer) > 0.1 and lat < 0.35 and steer * yaw * sign > 0 and abs(yaw) > 0.02:
            self.yaw_ratio_ref.append(abs(steer) / (WHEELBASE * abs(yaw) / v))
            if len(self.yaw_ratio_ref) % 60 == 0:
                self._steer_ratio = sorted(self.yaw_ratio_ref)[len(self.yaw_ratio_ref) // 2]
        if self._steer_ratio and abs(steer) > 0.03 and lat > 0.9 and steer * yaw * sign > 0:
            under_deg = math.degrees(abs(steer) / self._steer_ratio - WHEELBASE * abs(yaw) / v)
            if under_deg > 3.0:  # en el límite normal hay 1-2°; más de 3° es irse de frente
                self.under_timer += dt
                if self.under_timer >= 0.5 and self.under_timer - dt < 0.5:
                    st["understeer"] += 1
                    kind = "entry_under" if brk > 0.1 else "mid_under"
                    st[kind].append(pct)
                    self._count(st, kind, "izq" if yaw * sign > 0 else "der", v)
            else:
                self.under_timer = 0.0

        # Patinaje de rueda: traseras más rápidas que delanteras (o RPM/velocidad anómalo)
        wheels = [ir[w] for w in ("LFspeed", "RFspeed", "LRspeed", "RRspeed")]
        gear, rpm = ir["Gear"] or 0, ir["RPM"] or 0.0
        if all(w is not None for w in wheels):
            front = (wheels[0] + wheels[1]) / 2
            if thr > 0.3 and front > 8 and (wheels[2] + wheels[3]) / 2 > front * 1.08:
                st["wheelspin"] += dt
                st["spin_pcts"].append(pct)
        elif gear > 0 and v > 15:
            r = rpm / v
            hist = self.rpm_ratio[gear]
            if thr < 0.3 or brk > 0:
                hist.append(r)
            elif thr > 0.6 and len(hist) > 60:
                base = sorted(hist)[len(hist) // 2]
                if r > base * 1.12:
                    st["wheelspin"] += dt
                    st["spin_pcts"].append(pct)

        # Uso del ABS
        if brk > 0.3:
            st["brake_samples"] += 1
            if ir["BrakeABSactive"]:
                st["abs_samples"] += 1

    # -------------------------------------------------------- lap complete
    def _check_lap_complete(self):
        ir = self.ir
        lc = ir["LapCompleted"]
        if lc is None:
            return
        if self.prev_lap_completed is None:
            self.prev_lap_completed = lc
            self.fuel_at_lap_start = ir["FuelLevel"]
            return
        if lc < self.prev_lap_completed:  # reinicio de vueltas (reset, nueva sesión)
            self.prev_lap_completed = lc
            self.fuel_at_lap_start = ir["FuelLevel"]
            return
        if lc == self.prev_lap_completed:
            return
        self.prev_lap_completed = lc

        lap_time = ir["LapLastLapTime"] or -1
        fuel = ir["FuelLevel"] or 0.0
        st = self.lap_stats
        used = (self.fuel_at_lap_start - fuel) if self.fuel_at_lap_start is not None else 0
        # Una salida leve (1x) sigue siendo vuelta de ritmo, igual que en el análisis de .ibt
        clean = not (st["yellow"] or st["pit"]) and lap_time > 0
        best_prev = min((l["time"] for l in self.my_laps if l["time"] > 0), default=None)
        if clean and best_prev and lap_time > best_prev * 1.04:
            clean = False
        if used > 0.2 and not st["pit"] and not st["yellow"]:
            self.event_fuel.append(used)
        self.my_laps.append({"time": lap_time if clean else -1, "raw": lap_time,
                             "fuel": used if used > 0 and not st["pit"] else None, **st})
        self.fuel_at_lap_start = fuel
        self.lap_stats = self._new_lap_stats()
        lap_segs, self.my_lap_segs = self.my_lap_segs, {}
        self._close_lap_trace(lap_time, clean)
        lap_inputs = self.inputs.result()
        self.inputs.reset()
        self.last_lap_inputs = lap_inputs if lap_inputs["duracion_s"] > 30 else None
        prev_clean = [l["time"] for l in self.my_laps if l["time"] > 0]
        if clean and self.last_lap_inputs and (not prev_clean or lap_time < min(prev_clean)):
            self.best_lap_inputs = dict(self.last_lap_inputs)
        elif self.last_lap_inputs and self.best_lap_inputs and clean:
            self._input_coaching(self.last_lap_inputs, self.best_lap_inputs)
        brakes, self.lap_brakes = self.lap_brakes, []
        self.last_lap_brakes = self._brakes_by_corner(brakes)
        prev_clean = [l["time"] for l in self.my_laps[:-1] if l["time"] > 0]
        if clean and (not prev_clean or lap_time < min(prev_clean)):
            self.best_lap_brakes = dict(self.last_lap_brakes)
        for kind in ("entry_slides", "exit_slides", "entry_under", "mid_under"):
            self.session_handling[kind].extend(st[kind])
        for k, n in st["by_side"].items():
            self.side_counts[k] = self.side_counts.get(k, 0) + n
        self.session_handling["spin_pcts"].extend(st["spin_pcts"])
        self.session_handling["wheelspin_laps"].append(st["wheelspin"])
        self.laps_since_setup += 1

        if not self.corners or len(self.my_laps) % 5 == 0:
            self._detect_corners()
        self._record_progress()

        self._lap_report(st, lap_time, clean, lap_segs)
        self._battle_report()
        self._fuel_report(fuel)
        self._laps_countdown()

    def _input_coaching(self, lap: dict, best: dict):
        """Una observación por vuelta (como mucho cada 3 vueltas) cuando tus inputs se alejan de tu mejor vuelta"""
        tips = []
        if lap["inercia_s"] - best["inercia_s"] > 1.5:
            tips.append(f"Esa vuelta fuiste {lap['inercia_s'] - best['inercia_s']:.1f} segundos más sin pedales que en tu "
                        f"mejor vuelta: une el freno con el acelerador.")
        if lap["correcciones"] - best["correcciones"] >= 8:
            tips.append(f"Corregiste el volante {lap['correcciones']} veces, {lap['correcciones'] - best['correcciones']} más "
                        f"que en tu mejor vuelta: entradas más limpias, un solo movimiento.")
        if lap["levantadas"] - best["levantadas"] >= 6:
            tips.append("Estás levantando mucho el acelerador en las salidas: aplica una vez y progresivo.")
        if lap["acelera_agregando_volante_s"] - best["acelera_agregando_volante_s"] > 0.8:
            tips.append("Pisas el acelerador mientras todavía agregas volante: primero endereza, después acelera.")
        if lap.get("ataque_freno_s") and best.get("ataque_freno_s") and lap["ataque_freno_s"] - best["ataque_freno_s"] > 0.12:
            tips.append("Estás atacando el freno más lento que en tu mejor vuelta: pisa más decidido al inicio.")
        if tips:
            self.emit(tips[0], P_INFO, key="inputs", cooldown=self.ref_lap() * 3, ttl=12)

    def _most_common_zone(self, pcts: List[float]) -> str:
        if not pcts:
            return ""
        names = [self.corner_at(p) for p in pcts]
        return max(set(names), key=names.count)

    def _lap_report(self, st: dict, lap_time: float, clean: bool, lap_segs: Dict[int, float]):
        ir = self.ir
        previous = [l["time"] for l in self.my_laps[:-1] if l["time"] > 0]
        if clean and previous and lap_time < min(previous):
            self.emit(f"Mejor vuelta personal, {self.fmt_lap(lap_time)}.", P_INFO, ttl=10)

        # Ajustes en el auto según el comportamiento de esta vuelta
        bias = ir["dcBrakeBias"]
        tc = ir["dcTractionControl"]
        if len(st["entry_slides"]) >= 2:
            now = f" Ahora está en {bias:.1f}." if bias else ""
            self.emit(f"Se te va la cola al frenar {self._most_common_zone(st['entry_slides'])}. "
                      f"Mueve el balance de frenos un clic hacia adelante.{now}", P_CAR,
                      key="bias_fwd", cooldown=150, ttl=15)
        elif len(st["entry_under"]) >= 3:
            now = f" Ahora está en {bias:.1f}." if bias else ""
            self.emit(f"Subviras entrando {self._most_common_zone(st['entry_under'])}. Suelta el freno "
                      f"más progresivo, o balance de frenos un clic atrás.{now}", P_CAR,
                      key="bias_rear", cooldown=150, ttl=15)
        if len(st["exit_slides"]) >= 2 or st["wheelspin"] >= 1.5:  # segundos patinando por vuelta
            zone = self._most_common_zone(st["exit_slides"] or st["spin_pcts"]) or "en las salidas"
            if tc is not None:
                self.emit(f"Pierdes tracción al acelerar {zone}. Sube el control de tracción un clic, "
                          f"ahora está en {tc:.0f}. Y acelerador más progresivo.", P_CAR,
                          key="tires", cooldown=self.ref_lap() * 4, ttl=15)
            else:
                self.emit(f"{self.name}, relaja los neumáticos: pierdes tracción al acelerar {zone}. "
                          f"Acelerador más progresivo.", P_CAR, key="tires", cooldown=120, ttl=15)
        elif st["slides"] >= 4:
            self.emit(f"{self.name}, relaja los neumáticos: {st['slides']} derrapes en la vuelta. "
                      f"Entra un poco más lento y suaviza.", P_CAR, key="tires", cooldown=120, ttl=15)
        elif len(st["mid_under"]) >= 4:
            self.emit(f"Subviras en el medio de curva, sobre todo {self._most_common_zone(st['mid_under'])}. "
                      f"El tren delantero se está gastando: entra más lento y espera para acelerar.",
                      P_CAR, key="under", cooldown=150, ttl=15)

        # ABS
        if st["brake_samples"] > 20 and st["abs_samples"] / st["brake_samples"] > 0.45:
            self.emit("Estás metido mucho en el ABS. Frena un poco menos fuerte, más progresivo.",
                      P_CAR, key="abs", cooldown=180, ttl=15)

        # Coaching: dónde perdiste tiempo vs tus mejores minisectores
        if clean and len(lap_segs) >= N_SEGMENTS - 2:
            losses = {}
            for seg, t in lap_segs.items():
                b = self.my_best_segs.get(seg)
                if b is not None:
                    losses[seg] = t - b
                    self.seg_loss_hist[seg].append(t - b)
                if b is None or t < b:
                    self.my_best_segs[seg] = t
            best_lap = min(previous) if previous else lap_time
            if losses and lap_time - best_lap > 0.3:
                seg = max(losses, key=losses.get)
                if losses[seg] > 0.15:
                    why = self._brake_cause((seg + 0.5) / N_SEGMENTS)
                    self.emit(f"Esa vuelta perdiste {self.fmt_gap(losses[seg])} {self.zone_name(seg)} "
                              f"comparado con tu mejor.{why}", P_INFO, key="coach",
                              cooldown=self.ref_lap() * 1.8, ttl=12)
            if len(self.my_best_segs) == N_SEGMENTS and len(previous) >= 3:
                ideal = sum(self.my_best_segs.values())
                best = min(previous + [lap_time])
                if best - ideal > 0.3 and self.seg_loss_hist:
                    worst = max(self.seg_loss_hist, key=lambda k: sum(self.seg_loss_hist[k]) /
                                max(len(self.seg_loss_hist[k]), 1))
                    self.emit(f"Tu vuelta ideal es {self.fmt_lap(ideal)}, tienes {self.fmt_gap(best - ideal)} "
                              f"por ganar. Donde más pierdes: {self.zone_name(worst)}.", P_INFO,
                              key="ideal", cooldown=self.ref_lap() * 6, ttl=15)

        # Degradación de ritmo (vueltas limpias recientes)
        recent = [l["time"] for l in self.my_laps[-6:] if l["time"] > 0]
        if len(recent) >= 5:
            n = len(recent)
            xm = (n - 1) / 2
            ym = sum(recent) / n
            slope = sum((i - xm) * (y - ym) for i, y in enumerate(recent)) / \
                sum((i - xm) ** 2 for i in range(n))
            if slope > 0.12:
                self.emit(f"Estás perdiendo {self.fmt_gap(slope)} por vuelta. Los neumáticos se están "
                          f"yendo, cuida la tracción en las salidas.", P_CAR, key="degrade",
                          cooldown=240, ttl=15)
