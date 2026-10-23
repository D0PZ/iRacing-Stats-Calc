"""Claude como intérprete de preguntas: configuración y foto compacta del estado actual."""

from app.engineer.common import P_BATTLE, irsdk


class ClaudeMixin:
    # ------------------------------------------- Claude como intérprete
    def _init_claude(self):
        """
        backend "claude_code" (por defecto): tu suscripción de Claude vía Claude Code, sin costo extra.
        backend "api": ANTHROPIC_API_KEY, se cobra aparte; solo si lo eliges explícitamente.
        """
        from app.claude_interpreter import (ANTHROPIC_AVAILABLE, ClaudeCodeSession, ClaudeInterpreter,
                                            credentials_available)
        from app.voice_commands import load_config
        cfg = load_config().get("claude", {}) or {}
        if not cfg.get("enabled", True):
            return None
        effort = cfg.get("effort", "low")
        if cfg.get("backend", "claude_code") == "api":
            if ANTHROPIC_AVAILABLE and credentials_available():
                model = cfg.get("model", "claude-opus-5-5")
                print(f"[claude] intérprete por API ({model}, esfuerzo {effort})", flush=True)
                return ClaudeInterpreter(model=model, effort=effort)
            print("[claude] backend 'api' sin ANTHROPIC_API_KEY: respondo con reglas locales", flush=True)
            return None
        if ClaudeCodeSession.available():
            model = cfg.get("model", "haiku")
            print(f"[claude] intérprete con tu suscripción vía Claude Code ({model}, sesión abierta)", flush=True)
            return ClaudeCodeSession(model=model, effort=effort)
        print("[claude] Claude Code no está instalado: respondo con reglas locales", flush=True)
        return None

    def _is_radio_command(self, q: str) -> bool:
        n = self._norm(q)
        return any(w in n for w in ("repite", "repeti", "que dijiste", "silencio", "callate", "calla",
                                    "no me hables", "habla", "vuelve", "activa la radio"))

    def _ask_claude(self, question: str, snapshot: dict, fallback: str):
        spoken = []

        def say_sentence(sentence: str):   # hablar cada frase apenas Claude la termina
            spoken.append(sentence)
            self.emit(sentence, P_BATTLE + 5, ttl=25, always=True)
        try:
            text = self.claude.answer(question, snapshot, on_sentence=say_sentence)
        except TypeError:   # intérpretes sin streaming
            text = self.claude.answer(question, snapshot)
            if text:
                say_sentence(text)
        if not spoken:
            self.emit(text or fallback, P_BATTLE + 5, ttl=25, always=True)

    def _snapshot(self) -> dict:
        """Resumen compacto del estado actual para que Claude lo interprete"""
        ir = self.ir
        r1 = lambda x, d=1: None if x is None else round(float(x), d)
        snap = {"pista": self.track_name, "auto": self.car_name, "es_carrera": self.is_race}
        my_pos, ahead, behind = self._class_neighbors()
        last, best = ir["LapLastLapTime"], ir["LapBestLapTime"]
        snap["piloto"] = {
            "posicion_clase": my_pos or None, "vueltas_completadas": ir["LapCompleted"],
            "vueltas_restantes": self.laps_left(), "ultima_vuelta_s": r1(last if last and last > 0 else None, 2),
            "mejor_vuelta_s": r1(best if best and best > 0 else None, 2), "delta_vs_mejor_s": r1(self.live_delta(), 2),
            "incidentes": ir["PlayerCarMyIncidentCount"], "en_pits": bool(ir["OnPitRoad"])}
        fuel, per_lap, left = self._fuel_numbers()
        per_lap = per_lap or self.fuel_per_lap_estimate()
        snap["combustible"] = {"litros": r1(fuel), "litros_por_vuelta": r1(per_lap, 2),
                               "alcanza_vueltas": r1(fuel / per_lap) if per_lap else None}
        for idx, key, tag in ((ahead, "rival_adelante", "a"), (behind, "rival_atras", "b")):
            if idx is None:
                continue
            gap = self._gap_to(idx)
            laps = self.cars[idx].laps
            snap[key] = {"numero": (self.drivers.get(idx) or {}).get("CarNumber"),
                         "gap_s": r1(abs(gap) if gap is not None else None, 2),
                         "se_acerca_s_por_vuelta": r1(self._gap_rate(self.gap_history.get((idx, tag), [])), 2),
                         "ultima_vuelta_s": r1(laps[-1], 2) if laps else None,
                         "en_pits": self.cars[idx].on_pit}
        snap["danos"] = {"reparacion_obligatoria_s": r1(ir["PitRepairLeft"], 0),
                         "reparacion_opcional_s": r1(ir["PitOptRepairLeft"], 0)}
        flags = int(ir["SessionFlags"] or 0)
        snap["banderas"] = [name for name, bit in vars(irsdk.Flags).items()
                            if not name.startswith("_") and isinstance(bit, int) and flags & bit
                            and name in ("yellow", "yellow_waving", "caution", "caution_waving", "blue",
                                         "black", "repair", "white", "checkered", "debris")]
        snap["ajustes_auto"] = {"balance_frenos": r1(ir["dcBrakeBias"]), "tc": ir["dcTractionControl"],
                                "abs": ir["dcABS"]}
        snap["motor"] = {"agua_c": r1(ir["WaterTemp"], 0), "aceite_c": r1(ir["OilTemp"], 0)}
        snap["clima"] = {"pista_c": r1(ir["TrackTempCrew"], 0), "aire_c": r1(ir["AirTemp"], 0),
                         "humedad_pista": ir["TrackWetness"]}
        try:
            from app.tire_model import measured, read_setup
            data = read_setup(ir["CarSetup"] or {})
            if measured(data):
                snap["neumaticos_ultima_entrada_pits"] = {
                    c: {"ext_centro_int_c": [round(x) for x in d["temps"]], "presion_caliente_kpa": d["hot"],
                        "presion_frio_kpa": d["cold"], "camber": d["camber"]} for c, d in data.items()}
                snap["ajustes_neumaticos_recomendados"] = self._tire_recs()
        except Exception:
            pass
        recent = self.my_laps[-3:]
        if recent:
            snap["ultimas_vueltas"] = {
                "derrapes": sum(l.get("slides", 0) for l in recent),
                "patinaje_s": r1(sum(l.get("wheelspin", 0) for l in recent)),
                "subviraje_eventos": sum(l.get("understeer", 0) for l in recent),
                "abs_pct_frenadas": r1(100 * sum(l.get("abs_samples", 0) for l in recent)
                                       / max(sum(l.get("brake_samples", 0) for l in recent), 1), 0)}
        if self.seg_loss_hist:
            worst = max(self.seg_loss_hist, key=lambda k: sum(self.seg_loss_hist[k]) /
                        max(len(self.seg_loss_hist[k]), 1))
            snap["donde_pierdes"] = {"zona": self.zone_name(worst).removeprefix("en "),
                                     "s_por_vuelta": r1(sum(self.seg_loss_hist[worst]) /
                                                        max(len(self.seg_loss_hist[worst]), 1), 2)}
        if self.limits and self.zone_state:
            snap["salidas_de_pista"] = {self._zone_name(z).removeprefix("en "): st["offs"]
                                        for z in self.limits.zones
                                        for k, st in [(self._zone_key(z), self.zone_state.get(self._zone_key(z), {}))]
                                        if st.get("offs")}
        if self.refs and self.refs.get("plan"):
            snap["plan_qualy_carrera"] = self.refs["plan"]
        if self.last_ab_text:
            snap["ultima_prueba_ab_setup"] = self.last_ab_text
        if self.progress is not None and self.progress.sessions:
            snap["progreso_en_esta_pista"] = self.progress.phrases()
        if self.last_lap_inputs:
            snap["inputs_ultima_vuelta"] = self.last_lap_inputs
        if self.best_lap_inputs:
            snap["inputs_mejor_vuelta"] = self.best_lap_inputs
        if self.refs and self.refs.get("corners"):
            tips = {f"curva {c['n']}": c["inputs"] for c in self.refs["corners"] if c.get("inputs")}
            if tips:
                snap["inputs_por_curva_vs_tu_mejor_paso"] = tips
            pot = [{"curva": c["n"], "frenar_mas_tarde_m": c["frenar_mas_tarde_m"], "ganancia_s": c["ganancia_s"],
                    "agarre_usado": c.get("agarre_usado")}
                   for c in self.refs["corners"] if (c.get("ganancia_s") or 0) >= 0.05]
            if pot:
                snap["potencial_por_curva_calculo_fisico"] = pot
        try:
            from app.setup_db import flatten_setup, key_setup
            snap["setup_actual"] = key_setup(flatten_setup(ir["CarSetup"] or {}))
        except Exception:
            pass
        try:
            chassis = self._chassis_recs()
            if chassis:
                snap["ajustes_chasis_recomendados"] = chassis
            toe = self._toe_recs()
            if toe:
                snap["ajustes_toe_recomendados"] = toe
            if self.side_counts:
                snap["eventos_por_sentido_y_velocidad"] = dict(self.side_counts)
            K = self.k_acc.result()
            if K:
                from app.balance_model import describe_k
                snap["balance_gradiente_subviraje"] = {b: {"K_grados_por_g": g["K"], "lectura": describe_k(g["K"]),
                                                           "muestras": g["n"]} for b, g in K.items()}
        except Exception:
            pass
        if self.setup_db is not None and self.setup_db.stints:
            sens = self.tire_model.state["sens"] if self.tire_model is not None else None
            snap["historial_esta_pista"] = self.setup_db.summary(self._tire_targets(), sens, last_n=2)
        if self.tire_model is not None:
            snap["tire_targets"] = {"adelante_c": self.tire_model.target_spread("LF", self.track_name),
                                    "atras_c": self.tire_model.target_spread("LR", self.track_name)}
        return _drop_empty(snap)


def _drop_empty(d):
    """Quita valores vacíos para no gastar tokens en datos que no hay"""
    if isinstance(d, dict):
        out = {k: _drop_empty(v) for k, v in d.items()}
        return {k: v for k, v in out.items() if v not in (None, "", [], {})}
    return d
