"""
Ingeniero de carrera en tiempo real para iRacing
Lee la telemetría en vivo (memoria compartida de iRacing) y avisa por voz:
accidentes cerca, banderas, oportunidades de adelantamiento, presión desde atrás,
desgaste/deslizamiento de neumáticos, uso del ABS, combustible, etc.
"""

import math
import queue
import threading
import time
from collections import defaultdict, deque
from typing import Callable, Dict, List, Optional

from app.engineer.ai_bridge import ClaudeMixin
from app.engineer.audio import DEFAULT_NEURAL_VOICE, DEFAULT_OUTPUTS, Voice
from app.engineer.cli import main
from app.engineer.coaching import CoachingMixin
from app.engineer.common import (
    CarState, IRSDK_AVAILABLE, PROFILE_BINS, P_BATTLE, P_FLAG, P_INFO, SLOW_EVERY, TICK_HZ, irsdk)
from app.engineer.fuel import FuelMixin
from app.engineer.race_control import RaceControlMixin
from app.engineer.recording import RecordingMixin
from app.engineer.rivals import RivalsMixin
from app.engineer.setup_coach import SetupCoachMixin
from app.engineer.track_live import TrackMixin
from app.engineer.voice_qa import VoiceQAMixin
from app.references import load_references
from app.track_limits import load_tracker, save_tracker

# Lo que la GUI, voice_commands y los tests importan desde app.live_engineer
from app.engineer.audio import list_output_devices, pick_output_device  # noqa: F401
from app.engineer.common import P_SAFETY  # noqa: F401


class LiveEngineer(FuelMixin, ClaudeMixin, SetupCoachMixin, RecordingMixin, RivalsMixin,
                   RaceControlMixin, CoachingMixin, TrackMixin, VoiceQAMixin):
    """Ingeniero de carrera en vivo"""

    def __init__(self, driver_name: str = "Diego", speak: bool = True,
                 neural_voice: Optional[str] = DEFAULT_NEURAL_VOICE,
                 on_message: Optional[Callable[[str, int], None]] = None,
                 log_path: Optional[str] = None, auto_analysis: bool = True, auto_record: bool = True):
        self.name = driver_name
        from app.voice_commands import load_config
        self.voice = Voice(enabled=speak, neural_voice=neural_voice,
                           outputs=load_config().get("audio_output") or DEFAULT_OUTPUTS)
        self.on_message = on_message
        self.log_file = open(log_path, "a", encoding="utf-8") if log_path else None
        self.ir = irsdk.IRSDK() if IRSDK_AVAILABLE else None
        self._stop = threading.Event()
        self.questions: "queue.Queue[str]" = queue.Queue()
        self._emit_lock = threading.RLock()
        self.event_fuel: deque = deque(maxlen=10)   # litros por vuelta medidos en este evento
        self.briefed_sessions = set()
        self.muted_until = 0.0
        self.last_spoken = ""
        self.on_track = False
        self.last_ab_text = ""
        self.progress = None
        self.session_stamp = time.strftime("%Y-%m-%d %H:%M")
        self._rec_check_t = None
        self._rec_warned = False
        self.tire_model = None
        self.setup_db = None
        self._stint_offs = 0
        self.claude = self._init_claude()
        self.auto_record = auto_record
        self._we_record = False          # la grabación la inició el ingeniero
        self._off_track_since: Optional[float] = None
        self._reload_track_data = False
        self._last_poll = 0.0
        self.auto = None
        if auto_analysis:
            from app.auto_analysis import AutoAnalyzer
            self.auto = AutoAnalyzer(self._analysis_done)
        self.reset_session()

    # ------------------------------------------------------------------ util
    def reset_session(self):
        self.connected = False
        self.session_info_tick = -1
        self.track_len = 4000.0
        self.drivers: Dict[int, dict] = {}
        self.cars: Dict[int, CarState] = defaultdict(CarState)
        self.me = -1
        self.my_class = None
        self.prev_flags = None
        self.prev_state = 0
        self.prev_incidents = None
        self.prev_lap_completed = None
        self.prev_class_pos = None
        self.prev_warnings = 0
        self.cooldowns: Dict[str, float] = {}
        self.gap_history: Dict[tuple, deque] = defaultdict(lambda: deque(maxlen=6))
        # Estadísticas por vuelta del piloto
        self.lap_stats = self._new_lap_stats()
        self.my_laps: List[dict] = []
        self.fuel_at_lap_start = None
        self.steer_yaw_sign = deque(maxlen=400)
        self.rpm_ratio = defaultdict(lambda: deque(maxlen=600))
        self.slide_timer = 0.0
        self.under_timer = 0.0
        self.spin_timer = 0.0
        self.yaw_ratio_ref = deque(maxlen=1200)
        self._steer_ratio = None
        # Mapa de la pista aprendido de tu velocidad (para nombrar curvas)
        self.speed_profile: List[Optional[float]] = [None] * PROFILE_BINS
        self.corners: List[float] = []
        # Coaching por minisector
        self.my_lap_segs: Dict[int, float] = {}
        self.my_best_segs: Dict[int, float] = {}
        self.seg_loss_hist: Dict[int, deque] = defaultdict(lambda: deque(maxlen=5))
        self.session_num = None
        self.is_race = False
        self.last_emit = 0.0
        self.laps_since_summary = 0
        self.session_handling: Dict[str, List[float]] = defaultdict(list)
        self.side_counts: Dict[str, int] = {}
        from app.vehicle_dynamics import GradientAccumulator
        from app.driver_inputs import InputTracker
        self.k_acc = GradientAccumulator()
        self.inputs = InputTracker()            # pedales y volante de la vuelta en curso
        self.best_lap_inputs: Optional[dict] = None
        self.last_lap_inputs: Optional[dict] = None
        self.laps_since_setup = 0
        self.prev_in_stall = False
        # Tiempo real
        self.dt = 1.0 / TICK_HZ
        self._last_t = None
        self._tick_n = 0
        # Límites de pista
        self.track_name = ""
        self.limits = None
        self.limits_dirty = False
        self.zone_state: Dict[int, dict] = {}
        self._cur_zone = None
        self._prev_offtrack = False
        self._last_off_t = 0.0
        self._rumble_side = 0
        self._rumble_t = -99.0
        self.stint_start_lap = 0
        # Pits, clima y delta en vivo
        self.pit_limit_kmh: Optional[float] = None
        self.prev_wetness = None
        self.prev_precip = 0.0
        self.lap_trace: List[float] = [math.nan] * PROFILE_BINS     # tiempo de vuelta por posición
        self.best_trace: Optional[List[float]] = None
        self.best_trace_time = math.inf
        # Referencias de frenada por curva (del análisis de .ibt o de tu mejor vuelta en vivo)
        self.car_name = ""
        self.refs: Optional[dict] = None
        self.lap_brakes: List[float] = []
        self._brake_on = False
        self.best_lap_brakes: Dict[int, float] = {}
        self.last_lap_brakes: Dict[int, float] = {}

    @staticmethod
    def _new_lap_stats():
        return {"slides": 0, "understeer": 0, "wheelspin": 0, "brake_samples": 0,
                "abs_samples": 0, "yellow": False, "offtrack": False, "pit": False,
                "entry_slides": [], "exit_slides": [], "entry_under": [], "mid_under": [], "spin_pcts": [],
                "by_side": {}}

    def emit(self, text: str, priority: int = P_INFO, key: Optional[str] = None,
             cooldown: float = 0.0, ttl: float = 8.0, always: bool = False):
        """always=True: respuestas a tus preguntas, suenan aunque estés en modo silencio"""
        with self._emit_lock:
            now = time.time()
            if key:
                if self.cooldowns.get(key, 0) > now:
                    return
                self.cooldowns[key] = now + cooldown
            self.last_emit = now
            line = f"[{time.strftime('%H:%M:%S')}] {text}"
            print(line, flush=True)
            if self.log_file:
                self.log_file.write(line + "\n")
                self.log_file.flush()
            if self.on_message:
                try:
                    self.on_message(text, priority)
                except Exception:
                    pass
            if now < self.muted_until and priority < P_FLAG and not always:
                return  # modo silencio: solo peligros, banderas y respuestas
            self.last_spoken = text
            self.voice.say(text, priority, ttl)

    def ask(self, text: str):
        """Pregunta del piloto (desde el push-to-talk); se responde en el hilo de telemetría"""
        print(f"[pregunta] {text}", flush=True)
        with self._emit_lock:
            if self.log_file:
                self.log_file.write(f"[{time.strftime('%H:%M:%S')}] [pregunta] {text}\n")
        self.questions.put(text)

    def car_label(self, idx: int) -> str:
        d = self.drivers.get(idx)
        if not d:
            return "el auto"
        return f"el número {d.get('CarNumber', '?')}"

    @staticmethod
    def fmt_lap(t: float) -> str:
        m = int(t // 60)
        s = t - m * 60
        return f"{m}:{s:05.2f}" if m else f"{s:.2f}"

    @staticmethod
    def fmt_gap(t: float) -> str:
        t = abs(t)
        tenths = int(round(t * 10))
        if tenths < 10:
            return "una décima" if tenths <= 1 else f"{tenths} décimas"
        if round(t, 1) == 1.0:
            return "un segundo"
        return f"{t:.1f} segundos"

    def ref_lap(self) -> float:
        valid = [l["time"] for l in self.my_laps if l["time"] > 0]
        if valid:
            return min(valid)
        est = self.ir["DriverInfo"]["DriverCarEstLapTime"] if self.ir else None
        return float(est) if est else 100.0

    def laps_left(self) -> Optional[int]:
        ir = self.ir
        laps = ir["SessionLapsRemainEx"]
        if laps is not None and 0 <= laps < 32767:
            return int(laps)
        remain_t = ir["SessionTimeRemain"] or 0
        if 0 < remain_t < 604800:
            return math.ceil(remain_t / self.ref_lap())
        return None

    def _save_limits(self):
        if self.limits is not None and self.limits_dirty and self.track_name:
            try:
                save_tracker(self.track_name, self.limits)
                self.limits_dirty = False
            except OSError as e:
                print(f"[límites] no se pudieron guardar: {e}", flush=True)

    # ------------------------------------------------------------ main loop
    def run(self):
        if not IRSDK_AVAILABLE:
            print("Falta pyirsdk: pip install pyirsdk")
            return
        print("🎙️  Ingeniero en vivo esperando a iRacing...", flush=True)
        while not self._stop.is_set():
            try:
                self._check_connection()
                if self.connected:
                    # Bloquea hasta que iRacing publica la muestra nueva (cada 16 ms): latencia mínima
                    self.ir.freeze_var_buffer_latest()
                    self._tick()
                    self.ir.unfreeze_var_buffer_latest()
                else:
                    while not self.questions.empty():
                        self.questions.get()
                        self.emit("Todavía no estoy conectado a iRacing.", P_BATTLE + 5, ttl=10, always=True)
                    time.sleep(1.0)
                if self.auto is not None and time.time() - self._last_poll > 30:
                    self._last_poll = time.time()
                    self.auto.poll(can_run=not self.connected or not self.on_track)
            except Exception as e:
                print(f"[error] {e}", flush=True)
                time.sleep(0.05)
        self._save_limits()
        if self.claude is not None and hasattr(self.claude, "close"):
            self.claude.close()
        self.voice.stop()

    def stop(self):
        self._stop.set()

    def _check_connection(self):
        if self.connected and not (self.ir.is_initialized and self.ir.is_connected):
            self._we_record = False
            self._save_limits()
            self.ir.shutdown()
            self.reset_session()
            print("iRacing desconectado.", flush=True)
        elif not self.connected and self.ir.startup() and self.ir.is_initialized and self.ir.is_connected:
            self._last_t = None
            self.connected = True
            self.emit(f"Radio conectada. Listo cuando quieras, {self.name}.", P_INFO)

    def _refresh_session_info(self):
        tick = self.ir["SessionInfoUpdate"] if "SessionInfoUpdate" in self.ir.var_headers_names else None
        if tick == self.session_info_tick:
            return
        self.session_info_tick = tick
        try:
            tl = str(self.ir["WeekendInfo"]["TrackLength"])  # "3.70 km"
            value, unit = tl.split()[:2]
            self.track_len = float(value) * (1609.34 if unit.startswith("mi") else 1000.0)
        except Exception:
            pass
        try:
            name = str(self.ir["WeekendInfo"]["TrackDisplayName"])
            if name and name != self.track_name:
                if self.track_name:
                    self.event_fuel.clear()   # otra pista: el consumo medido ya no sirve
                self.track_name = name
                self.limits = load_tracker(name, self.track_len)
                self.zone_state = {}
                if self.limits.zones:
                    print(f"[límites] {len(self.limits.zones)} zonas de límite conocidas en {name}", flush=True)
        except Exception as e:
            print(f"[límites] no se pudo cargar la pista: {e}", flush=True)
        try:
            self.pit_limit_kmh = self._nums(self.ir["WeekendInfo"]["TrackPitSpeedLimit"])[0]
            if "mph" in str(self.ir["WeekendInfo"]["TrackPitSpeedLimit"]):
                self.pit_limit_kmh *= 1.609
        except Exception:
            pass
        self._record_cross_point()
        info = self.ir["DriverInfo"] or {}
        self.me = info.get("DriverCarIdx", -1)
        try:
            num = self.ir["SessionNum"]
            sessions = self.ir["SessionInfo"]["Sessions"]
            self.is_race = "race" in str(sessions[num]["SessionType"]).lower()
        except Exception:
            self.is_race = False
        self.drivers = {}
        for d in info.get("Drivers", []) or []:
            if d.get("CarIsPaceCar") or d.get("IsSpectator"):
                continue
            self.drivers[d["CarIdx"]] = d
        if self.me in self.drivers:
            self.my_class = self.drivers[self.me].get("CarClassID")
            car = str(self.drivers[self.me].get("CarScreenName", ""))
            if car and car != self.car_name and self.track_name:
                self.car_name = car
                from app.setup_db import SetupDB
                from app.progress import ProgressDB
                self.setup_db = SetupDB(self.track_name, car)
                self.progress = ProgressDB(self.track_name, car)
                print(f"[setups] {len(self.setup_db.stints)} salidas guardadas para {car} en {self.track_name}",
                      flush=True)
                self.refs = load_references(self.track_name, car, self.track_len)
                if self.refs:
                    # Misma numeración de curvas que el reporte del analizador
                    self.corners = [c["apex_m"] / self.track_len for c in self.refs["corners"]]
                    print(f"[referencias] {len(self.corners)} curvas de referencia para {car} en "
                          f"{self.track_name}", flush=True)

    # ---------------------------------------------------------------- tick
    def _tick(self):
        ir = self.ir
        self._refresh_session_info()
        if self.me < 0:
            # Sin auto propio (espectador, cargando): no dejar preguntas sin respuesta
            while not self.questions.empty():
                self.questions.get()
                self.emit("Todavía no estás en un auto, no tengo tus datos.", P_BATTLE + 5, ttl=10, always=True)
            return
        t = ir["SessionTime"] or 0.0
        if self._last_t is not None and 0 < t - self._last_t < 0.25:
            self.dt = t - self._last_t
        self._last_t = t
        self._tick_n += 1
        self.on_track = bool(ir["IsOnTrack"])
        self._auto_recording()
        if self._reload_track_data:
            # El análisis automático actualizó referencias y límites: recargarlos
            self._reload_track_data = False
            self.track_name = self.car_name = ""
            self.refs = None
            self.limits_dirty = False
            self.session_info_tick = -1
            self._refresh_session_info()
        while not self.questions.empty():
            q = self.questions.get()
            try:
                answer = self._answer(q)
            except Exception as e:
                answer = "No tengo ese dato ahora."
                print(f"[pregunta] error: {e}", flush=True)
            if self.claude is None or self._is_radio_command(q):
                self.emit(answer, P_BATTLE + 5, ttl=20, always=True)
            else:
                try:
                    snap = self._snapshot()
                except Exception as e:
                    print(f"[claude] no pude armar el resumen: {e}", flush=True)
                    self.emit(answer, P_BATTLE + 5, ttl=20, always=True)
                    continue
                self.voice.say("Copiado.", P_BATTLE + 5, ttl=3)   # pre-sintetizado: sale al instante
                threading.Thread(target=self._ask_claude, args=(q, snap, answer), daemon=True).start()

        num = ir["SessionNum"]
        if num != self.session_num:
            # Cambio de sesión (práctica -> clasificación -> carrera): limpiar historial de batalla
            self.session_num = num
            self.session_info_tick = -1
            self._refresh_session_info()
            self.gap_history.clear()
            self.prev_class_pos = None
            for c in self.cars.values():
                c.laps.clear()
            self.my_laps = []
            self.session_stamp = time.strftime("%Y-%m-%d %H:%M")
            self.prev_lap_completed = None
            self.fuel_at_lap_start = None
            self.lap_stats = self._new_lap_stats()
            self.my_lap_segs = {}
            self.my_best_segs = {}
            self.seg_loss_hist.clear()

        self._update_cars(t)
        self._check_flags_and_state()
        self._check_setup_report()
        if not ir["IsOnTrack"]:
            return
        self._check_pits()
        self._check_hazards()
        self._driver_dynamics()
        self._track_limits_live()
        self._record_lap_trace()
        self._rival_events()
        if self._tick_n % SLOW_EVERY == 0:
            self._check_traffic()
            self._check_weather()
            self._check_briefing()
        self._check_lap_complete()
        self._check_incidents_and_warnings()


if __name__ == "__main__":
    main()
