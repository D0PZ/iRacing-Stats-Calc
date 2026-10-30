"""Respuestas a las preguntas por voz (combustible, daño, neumáticos, rivales, curvas...)."""

import re
import time
import unicodedata

from app.engineer.common import N_SEGMENTS, irsdk


class VoiceQAMixin:
    def _corner_ref_answer(self, k: int) -> str:
        n = k + 1
        if self.refs and k < len(self.refs["corners"]):
            c = self.refs["corners"][k]
            if c.get("brake_at_m") is None:
                return f"La curva {n} es sin frenar, a {c['min_kmh']:.0f} kilómetros por hora en {c['gear']}a."
            parts = [f"En la curva {n} frena en el metro {c['brake_at_m']:.0f}, "
                     f"{c['brake_m_before']:.0f} metros antes del vértice, con {c['brake_peak']:.0f} por ciento de pedal",
                     f"pasa a {c['min_kmh']:.0f} kilómetros por hora en {c['gear']}a"]
            if c.get("throttle_m") is not None:
                parts.append(f"y a fondo {c['throttle_m']:.0f} metros después del vértice")
            msg = ", ".join(parts) + "."
            if c.get("inputs"):
                msg += " En tu mejor paso: " + c["inputs"][0] + "."
            if (c.get("frenar_mas_tarde_m") or 0) >= 4 and (c.get("ganancia_s") or 0) >= 0.03:
                msg += (f" Según el cálculo físico puedes frenar unos {c['frenar_mas_tarde_m']:.0f} metros más tarde y "
                        f"soltar progresivo: hasta {c['ganancia_s']:.2f} segundos.")
            return msg
        ref = self.best_lap_brakes.get(k)
        if ref is not None:
            return f"En tu mejor vuelta frenaste para la curva {n} en el metro {ref:.0f}."
        return f"Todavía no tengo referencia para la curva {n}. Analiza un archivo de telemetría de esta pista."

    # ---------------------------------------------------------- preguntas
    @staticmethod
    def _norm(text: str) -> str:
        t = unicodedata.normalize("NFD", text.lower())
        return "".join(ch for ch in t if unicodedata.category(ch) != "Mn")

    def _answer(self, question: str) -> str:
        ir = self.ir
        n = self._norm(question)

        def has(*words):
            return any(w in n for w in words)

        if has("repite", "repeti", "que dijiste", "no te escuche"):
            return self.last_spoken or "Todavía no te he dicho nada."
        if has("silencio", "callate", "calla", "no me hables"):
            self.muted_until = time.time() + 300
            return "Entendido, silencio cinco minutos. Solo te aviso de peligros y banderas."
        if has("habla", "vuelve", "activa la radio", "quita el silencio"):
            self.muted_until = 0
            return "Radio activa de nuevo."

        if has("dano", "reparacion", "roto", "golpe", "averia"):
            rep_t = ir["PitRepairLeft"] or 0.0
            opt_t = ir["PitOptRepairLeft"] or 0.0
            parts = []
            if rep_t > 0:
                parts.append(f"tienes reparación obligatoria de {rep_t:.0f} segundos")
            if opt_t > 0:
                parts.append(f"reparación opcional de {opt_t:.0f} segundos")
            warn = int(ir["EngineWarnings"] or 0)
            W = irsdk.EngineWarnings
            if warn & W.water_temp_warning:
                parts.append("temperatura de agua alta")
            if warn & W.oil_pressure_warning:
                parts.append("presión de aceite baja")
            if not parts:
                return "Sin daño registrado. El auto está entero."
            return "Daño: " + ", ".join(parts) + ". " + (
                "La obligatoria hay que hacerla en pits." if rep_t > 0 else "Puedes seguir, la opcional es para recuperar rendimiento.")

        if has("pastilla", "plan de qualy", "plan de carrera", "plan para", "qualy", "clasificacion"):
            from app.session_plan import qualy_fuel
            lines = list((self.refs or {}).get("plan") or [])
            per_lap = self.fuel_per_lap_estimate()
            if per_lap and has("qualy", "clasificacion", "plan"):
                lines = [l for l in lines if not l.startswith("Qualy:")]
                lines.insert(0, f"Qualy: carga {qualy_fuel(per_lap, 2):.1f} litros (salida y 2 vueltas lanzadas).")
            if has("pastilla"):
                lines = [l for l in lines if l.startswith(("Frenos", "Pastilla"))]
            if not lines:
                return "Todavía no tengo el plan: analiza un archivo de telemetría de esta pista con este auto."
            return " ".join(lines)

        if has("para la carrera", "cargar para", "cuanto cargo", "cuanto combustible cargo"):
            plan = self.race_plan()
            if not plan:
                return "No veo una carrera en este evento."
            if not plan["need"]:
                return f"La carrera es de {plan['laps']} vueltas. Haz un par de vueltas para medir el consumo."
            return (f"La carrera es de {plan['laps']} vueltas. Gastas {plan['per_lap']:.2f} litros por vuelta: "
                    f"carga {plan['need']:.1f} litros, con un litro de margen.")

        if has("combustible", "gasolina", "bencina", "nafta", "fuel", "litros"):
            fuel, per_lap, left = self._fuel_numbers()
            if not per_lap:
                return f"Tienes {fuel:.1f} litros. Necesito una vuelta completa para calcular el consumo."
            laps_fuel = fuel / per_lap
            msg = f"Tienes {fuel:.1f} litros, gastas {per_lap:.2f} por vuelta: te alcanza para {laps_fuel:.1f} vueltas."
            if left is not None:
                diff = fuel - (left + 0.2) * per_lap
                msg += (f" Faltan {left}. Te sobran {diff:.1f} litros." if diff >= 0
                        else f" Faltan {left}. Te faltan {-diff:.1f} litros, tienes que parar o ahorrar.")
            return msg

        if has("pits", "parar", "box", "estrategia", "entrar"):
            fuel, per_lap, left = self._fuel_numbers()
            rep_t = ir["PitRepairLeft"] or 0.0
            parts = []
            if per_lap and left is not None:
                need = (left + 0.3) * per_lap - fuel
                parts.append(f"carga {need + 0.5:.1f} litros para terminar" if need > 0
                             else "no necesitas combustible")
            if rep_t > 0:
                parts.append(f"hay {rep_t:.0f} segundos de reparación obligatoria")
            if not parts:
                return "Todavía no tengo datos de consumo para la estrategia."
            return "Para pits: " + ", ".join(parts) + "."

        if has("limite", "piano", "me sali", "track limit"):
            if not self.limits or not self.limits.zones:
                return "No conozco zonas de límite en esta pista todavía."
            hot = []
            for z in self.limits.zones:
                st = self.zone_state.get(self._zone_key(z), {})
                if st.get("offs"):
                    hot.append(f"{st['offs']} {self._zone_name(z)}")
            if hot:
                return "Esta sesión te pasaste: " + ", ".join(hot) + ". Deja medio metro de margen ahí."
            names = ", ".join(self._zone_name(z).removeprefix("en ") for z in self.limits.zones[:4])
            return f"Ninguna salida esta sesión. Zonas con límite estricto: {names}."

        if has("sirvio", "funciono", "prueba", "a b", "comparacion", "el cambio", "mejoro"):
            return self.last_ab_text or self._report_ab() or (
                "Todavía no hay una prueba: necesito una salida con un setup y otra con un cambio, de 2 vueltas o más.")

        if has("progreso", "progresando", "mejorando", "historial", "evolucion", "record"):
            phrases = self.progress.phrases() if self.progress is not None else []
            return " ".join(phrases) if phrases else "Todavía no tengo sesiones guardadas en esta pista con este auto."

        if has("pedal", "pedales", "volante", "input", "como freno", "como acelero", "manejo"):
            lap, best = self.last_lap_inputs, self.best_lap_inputs
            if not lap:
                return "Necesito una vuelta completa para analizar tus pedales y volante."
            msg = (f"Última vuelta: {lap['inercia_s']:.1f} segundos sin pedales, {lap['a_fondo_pct']:.0f} por ciento a "
                   f"fondo, {lap['levantadas']} levantadas de acelerador y {lap['correcciones']} correcciones de volante.")
            if best and best is not lap:
                diffs = []
                if lap["inercia_s"] - best["inercia_s"] > 0.8:
                    diffs.append(f"{lap['inercia_s'] - best['inercia_s']:.1f} segundos más de inercia")
                if lap["correcciones"] - best["correcciones"] >= 4:
                    diffs.append(f"{lap['correcciones'] - best['correcciones']} correcciones más")
                msg += (" Contra tu mejor vuelta: " + " y ".join(diffs) + ".") if diffs else " Igual que en tu mejor vuelta."
            return msg

        if has("barra", "arb", "estabilizadora", "aleron", "wing", "perch", "cross", "altura", "rake"):
            recs = self._chassis_recs()
            if recs:
                return " ".join(recs)
            from app.chassis_advisor import read_chassis
            from app.setup_db import flatten_setup
            c = read_chassis(flatten_setup(ir["CarSetup"] or {}))
            return (f"No cambiaría barras ni alerón todavía (barra delantera {c.get('arb_front')}, trasera "
                    f"{c.get('arb_rear')}, alerón {c.get('wing')}, cross weight {c.get('cross')}): no veo una tendencia "
                    f"clara. Dame unas vueltas más al límite.")

        if has("toe", "convergencia", "divergencia"):
            recs = self._toe_recs()
            if recs:
                return " ".join(recs)
            from app.setup_db import flatten_setup
            from app.toe_advisor import read_toe
            from app.toe_advisor import toe_display
            from app.units import LENGTH_WORDS, fmt_length
            flat = flatten_setup(ir["CarSetup"] or {})
            t, (u, denom) = read_toe(flat), toe_display(flat)
            now = ", ".join(f"{k} {fmt_length(v, u, denom)}" for k, v in t.items())
            return (f"Con lo que veo, no cambiaría el toe ({now} {LENGTH_WORDS[u]}): el auto no muestra sobreviraje ni "
                    f"subviraje de entrada marcado. Dame un par de vueltas más al límite y te digo.")

        if has("ideal", "ideales") and has("presion", "camber", "neumatico", "setup"):
            phrases = self._ideal_phrases()
            return phrases[0] if phrases else "Todavía no tengo salidas medidas en esta pista con este auto."

        if has("neumatico", "goma", "cubierta", "llanta", "rueda", "presion", "camber"):
            return self._tire_answer(n)

        if has("incidente"):
            inc = ir["PlayerCarMyIncidentCount"] or 0
            limit = None
            try:
                limit = ir["WeekendInfo"]["WeekendOptions"]["IncidentLimit"]
            except Exception:
                pass
            return f"Llevas {inc} incidentes" + (f" de un límite de {limit}." if limit and str(limit).isdigit() else ".")

        if has("motor", "agua", "aceite"):
            w, o, op = ir["WaterTemp"], ir["OilTemp"], ir["OilPress"]
            return (f"Motor: agua {w:.0f} grados, aceite {o:.0f} grados"
                    + (f", presión de aceite {op:.1f} bar." if op is not None else ".")) if w is not None else                 "No tengo datos del motor."

        if has("clima", "lluvia", "mojad", "temperatura de pista", "pista"):
            tt, at = ir["TrackTempCrew"], ir["AirTemp"]
            wet = ir["TrackWetness"]
            msg = f"Pista a {tt:.0f} grados, aire a {at:.0f}." if tt is not None and at is not None else ""
            if wet is not None and wet > 1:
                msg += " La pista tiene humedad."
            return msg or "No tengo datos del clima."

        m_curve = re.search(r"(?:curva|la)\s+(\d{1,2})", n)
        if m_curve and has("freno", "frenada", "frenar", "referencia", "como hago", "como tomo"):
            k = int(m_curve.group(1)) - 1
            if 0 <= k < max(len(self.corners), 1):
                return self._corner_ref_answer(k)
            return f"No tengo la curva {k + 1}, en esta pista conozco {len(self.corners)} curvas."

        if has("freno", "balance", "traccion", "tc", "abs"):
            bias, tc, abs_ = ir["dcBrakeBias"], ir["dcTractionControl"], ir["dcABS"]
            parts = []
            if bias is not None:
                parts.append(f"balance de frenos {bias:.1f}")
            if tc is not None:
                parts.append(f"control de tracción {tc:.0f}")
            if abs_ is not None:
                parts.append(f"ABS {abs_:.0f}")
            return ("Tienes " + ", ".join(parts) + ".") if parts else "No tengo los ajustes del auto."

        my_pos, ahead, behind = self._class_neighbors()
        if has("adelante", "delante", "el de enfrente", "puntero"):
            if ahead is None:
                return "No tienes a nadie adelante, vas primero."
            return self._rival_answer(ahead, "adelante")
        if has("atras", "detras"):
            if behind is None:
                return "No tienes a nadie atrás."
            return self._rival_answer(behind, "atrás")

        if has("falta", "quedan", "queda", "cuantas vueltas"):
            left = self.laps_left()
            remain = ir["SessionTimeRemain"]
            if left is not None:
                return f"Quedan {left} vueltas" + (f", unos {remain / 60:.0f} minutos." if remain and 0 < remain < 86400 else ".")
            return "No sé cuánto falta."

        if has("pierdo", "perdiendo", "mejorar", "lento", "ideal"):
            if not self.seg_loss_hist:
                return "Necesito un par de vueltas limpias para decirte dónde pierdes."
            worst = max(self.seg_loss_hist, key=lambda k: sum(self.seg_loss_hist[k]) / max(len(self.seg_loss_hist[k]), 1))
            loss = sum(self.seg_loss_hist[worst]) / max(len(self.seg_loss_hist[worst]), 1)
            msg = f"Donde más pierdes es {self.zone_name(worst)}, unas {self.fmt_gap(loss)} por vuelta contra tu mejor."
            if len(self.my_best_segs) == N_SEGMENTS:
                msg += f" Tu vuelta ideal es {self.fmt_lap(sum(self.my_best_segs.values()))}."
            return msg

        if has("esta vuelta", "delta", "como voy en la vuelta", "voy arriba", "voy abajo"):
            d = self.live_delta()
            if d is None:
                if self.best_trace and (ir["LapDistPct"] or 0) < 0.03:
                    return "Recién cruzas la meta, pregúntame en un momento."
                return "Necesito una vuelta limpia de referencia para darte el delta."
            return (f"Vas {self.fmt_gap(d)} más lento que tu mejor vuelta." if d > 0.05 else
                    f"Vas {self.fmt_gap(-d)} más rápido que tu mejor vuelta, sigue así." if d < -0.05 else
                    "Vas clavado en tu mejor vuelta.")

        if has("vuelta", "tiempo", "record"):
            last = ir["LapLastLapTime"]
            mine = [l["raw"] for l in self.my_laps if l.get("raw", 0) > 0] + \
                [x for x in [ir["LapBestLapTime"]] if x and x > 0]
            best = min(mine) if mine else None
            if not last or last <= 0:
                return "Todavía no tienes vueltas completas."
            msg = f"Última vuelta {self.fmt_lap(last)}"
            if best and best > 0:
                msg += f", mejor {self.fmt_lap(best)}, a {self.fmt_gap(last - best)} de tu mejor." if last > best + 0.001 \
                    else ", y es tu mejor vuelta."
            return msg

        if has("setup", "auto", "subvir", "sobrevir", "comportamiento"):
            h = self.session_handling
            laps = max(self.laps_since_setup, 1)
            counts = {k: len(h[k]) / laps for k in ("entry_slides", "exit_slides", "entry_under", "mid_under")}
            top = max(counts, key=counts.get)
            if counts[top] < 0.5:
                return "El auto se ve equilibrado, sin tendencias marcadas."
            names = {"entry_slides": "sobreviraje al frenar", "exit_slides": "sobreviraje al acelerar",
                     "entry_under": "subviraje en la entrada", "mid_under": "subviraje en el medio de curva"}
            return f"Lo que más se repite es {names[top]}, {counts[top]:.1f} veces por vuelta, sobre todo {self._most_common_zone(h[top])}."

        if has("posicion", "lugar", "puesto", "como voy", "resumen", "situacion"):
            parts = [f"Vas P{my_pos}" if my_pos else "Sin posición todavía"]
            if ahead is not None:
                g = self._gap_to(ahead)
                if g is not None and 0 < g < 60:
                    parts.append(f"a {self.fmt_gap(g)} del de adelante")
            if behind is not None:
                g = self._gap_to(behind)
                if g is not None and -60 < g < 0:
                    parts.append(f"{self.fmt_gap(-g)} sobre el de atrás")
            fuel, per_lap, left = self._fuel_numbers()
            if per_lap:
                laps_f = int(fuel / per_lap)
                parts.append(f"combustible para {laps_f} vuelta" + ("" if laps_f == 1 else "s"))
            return ", ".join(parts) + "."

        return ("No entendí. Pregúntame por daños, combustible, pits, neumáticos, límites, "
                "el de adelante o atrás, vueltas, tiempos o dónde pierdes.")

    def _tire_answer(self, n: str) -> str:
        """
        iRacing no entrega la temperatura de los neumáticos mientras corres: la de carcasa
        (tempCL/CM/CR), el desgaste y la presión en caliente se actualizan al entrar a pits.
        """
        ir = self.ir
        names = {"LF": "delantera izquierda", "RF": "delantera derecha",
                 "LR": "trasera izquierda", "RR": "trasera derecha"}
        from app.tire_model import read_setup
        from app.units import PRESSURE_WORDS, fmt_pressure
        try:
            setup = read_setup(ir["CarSetup"] or {})
        except Exception:
            setup = {}
        # Unidades del garage; la telemetría siempre viene en °C y kPa
        unit = (setup.get("LF") or {}).get("unit", "kPa")
        to_unit = (lambda c: c * 1.8 + 32) if (setup.get("LF") or {}).get("temp_unit") == "F" else (lambda c: c)
        temps = {c: [ir[f"{c}tempC{x}"] for x in ("L", "M", "R")] for c in names}
        parts = []
        if all(v is not None for t in temps.values() for v in t) and \
                max(max(t) for t in temps.values()) - min(min(t) for t in temps.values()) >= 2:
            def omi(c):  # exterior, centro, interior (en las izquierdas L es el exterior)
                l, m, r = (to_unit(x) for x in temps[c])
                return (l, m, r) if c[0] == "L" else (r, m, l)
            parts.append("Medidas en tu última entrada a pits, de exterior a interior: " + "; ".join(
                f"{names[c]} {omi(c)[0]:.0f}, {omi(c)[1]:.0f} y {omi(c)[2]:.0f}" for c in names) + " grados")
        hot = {c: d["hot"] for c, d in setup.items() if d.get("hot") is not None}
        p = lambda kpa: fmt_pressure(kpa, unit)                # noqa: E731
        cold = [ir[f"{c}coldPressure"] for c in names]
        if len(hot) == 4:
            parts.append(f"presión en caliente {p(hot['LF'])} y {p(hot['RF'])} adelante, "
                         f"{p(hot['LR'])} y {p(hot['RR'])} atrás" + ("" if unit == "kPa" else f" {PRESSURE_WORDS[unit]}"))
        if all(c is not None for c in cold):
            parts.append(f"en frío {p(cold[0])} adelante y {p(cold[2])} atrás")
        wear = [ir[f"{c}wear{x}"] for c in names for x in ("L", "M", "R")]
        if all(w is not None for w in wear):
            parts.append(f"desgaste mínimo {min(wear) * 100:.0f} por ciento")
        if not parts:
            return ("iRacing no entrega la temperatura de los neumáticos mientras corres. "
                    "Entra a pits y te digo temperaturas, presiones y qué ajustar.")
        msg = ". ".join(p[0].upper() + p[1:] for p in parts) + "."
        try:
            recs = self._tire_recs()
        except Exception:
            recs = []
        if recs:
            msg += " " + " ".join(recs)
        elif temps and parts:
            msg += " Camber y presiones se ven bien."
        recent = self.my_laps[-3:]
        slides = sum(l.get("slides", 0) for l in recent)
        spin = sum(l.get("wheelspin", 0) for l in recent)
        if recent and (slides > 6 or spin > 4):
            msg += f" Ojo: {slides} derrapes y {spin:.1f} segundos patinando en las últimas vueltas, cuídalas."
        return msg

    def _rival_answer(self, idx: int, where: str) -> str:
        gap = self._gap_to(idx)
        if gap is None:
            return f"No tengo el gap del de {where}."
        g = abs(gap)
        rival = self.cars[idx]
        key = (idx, "a" if where == "adelante" else "b")
        rate = self._gap_rate(self.gap_history.get(key, []))
        msg = f"{self.car_label(idx).capitalize()} {where} a {self.fmt_gap(g)}"
        if abs(rate) > 0.05:
            if where == "adelante":
                msg += f", le descuentas {self.fmt_gap(rate)} por vuelta" if rate > 0 else f", se escapa {self.fmt_gap(-rate)} por vuelta"
            else:
                msg += f", te descuenta {self.fmt_gap(rate)} por vuelta" if rate > 0 else f", lo alejas {self.fmt_gap(-rate)} por vuelta"
        my = self.cars[self.me].laps
        if rival.laps and my:
            d = rival.laps[-1] - my[-1]
            msg += f". Su última vuelta fue {self.fmt_lap(rival.laps[-1])}, " + (
                f"{self.fmt_gap(d)} más lento que tú" if d > 0 else f"{self.fmt_gap(-d)} más rápido que tú")
        c = self.cars[idx]
        if c.on_pit:
            msg += ". Está en pits"
        return msg + "."
