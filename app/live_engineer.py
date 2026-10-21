"""
Ingeniero de carrera en tiempo real para iRacing
Lee la telemetría en vivo (memoria compartida de iRacing) y avisa por voz:
accidentes cerca, banderas, oportunidades de adelantamiento, presión desde atrás,
desgaste/deslizamiento de neumáticos, uso del ABS, combustible, etc.
"""

import math
import os
import queue
import re
import threading
import time
import unicodedata
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

try:
    import irsdk
    IRSDK_AVAILABLE = True
except ImportError:
    IRSDK_AVAILABLE = False

from app.track_limits import load_tracker, save_tracker


# Prioridades de mensajes (mayor = más urgente)
P_SAFETY = 100
P_FLAG = 80
P_BATTLE = 60
P_CAR = 40
P_INFO = 20

N_SEGMENTS = 20          # Minisectores por vuelta para comparar con rivales
PROFILE_BINS = 200       # Resolución del perfil de velocidad para detectar curvas
TICK_HZ = 60            # iRacing publica telemetría a 60 Hz; esperamos su evento de "dato nuevo"
SLOW_EVERY = 6          # tareas pesadas a 10 Hz
WHEELBASE = 2.46        # m (GT/Cup típico; solo escala el ángulo de subviraje)


@dataclass(order=True)
class Message:
    sort_key: float
    text: str = field(compare=False)
    priority: int = field(compare=False)
    expires: float = field(compare=False)


DEFAULT_NEURAL_VOICE = "es-CL-LorenzoNeural"


class NeuralTTS:
    """Voces neuronales de Microsoft Edge (gratis, requieren internet) con caché en disco"""

    def __init__(self, voice: str = DEFAULT_NEURAL_VOICE, rate: str = "+12%"):
        import edge_tts  # noqa: F401  (falla aquí si no está instalado)
        import hashlib
        import tempfile
        self.voice = voice
        self.rate = rate
        self._hash = hashlib.sha1
        self.cache_dir = os.path.join(tempfile.gettempdir(), "iracing_engineer_tts")
        os.makedirs(self.cache_dir, exist_ok=True)
        self.failed_until = 0.0
        self._lock = threading.Lock()

    def file_for(self, text: str) -> str:
        key = self._hash(f"{self.voice}|{self.rate}|{text}".encode("utf-8")).hexdigest()[:20]
        return os.path.join(self.cache_dir, f"{key}.mp3")

    def synth(self, text: str, timeout: float = 6.0) -> Optional[str]:
        path = self.file_for(text)
        if os.path.exists(path) and os.path.getsize(path) > 0:
            return path
        if time.time() < self.failed_until:
            return None
        import asyncio
        import edge_tts

        async def go():
            await edge_tts.Communicate(text, self.voice, rate=self.rate).save(path + ".part")
        try:
            with self._lock:
                asyncio.run(asyncio.wait_for(go(), timeout))
            os.replace(path + ".part", path)
            return path
        except Exception as e:
            print(f"[voz] Voz neuronal no disponible ({type(e).__name__}); uso la de Windows.", flush=True)
            self.failed_until = time.time() + 60
            return None

    @staticmethod
    def play(path: str):
        """Reproduce un mp3 con el MCI de Windows (sin abrir ventanas)"""
        import ctypes
        mci = ctypes.windll.winmm.mciSendStringW
        alias = f"radio{threading.get_ident()}"
        mci(f'open "{path}" type mpegvideo alias {alias}', None, 0, None)
        mci(f"play {alias} wait", None, 0, None)
        mci(f"close {alias}", None, 0, None)


# Frases urgentes que conviene tener sintetizadas de antemano (latencia cero)
PREWARM_PHRASES = (
    [f"¡Cuidado! Auto detenido a {d} metros." for d in range(100, 800, 100)]
    + [f"¡Cuidado! Auto trompeado a {d} metros." for d in range(100, 800, 100)]
    + [f"Auto fuera de pista a {d} metros." for d in range(100, 500, 100)]
    + ["Accidente justo detrás tuyo.", "Bandera amarilla en tu sector, cuidado, no adelantes.",
       "Caución, full course yellow. No adelantes.", "Bandera azul, te van a pasar, mantén tu línea.",
       "¡El de adelante se salió de pista! Aprovecha.", "El de atrás se salió, ganas aire.",
       "Última vuelta. Dalo todo.", "¡Verde, verde, verde! Vamos.", "Motor apagado, arranca."]
)


class Voice:
    """Cola de mensajes hablados: voz neuronal si hay internet, si no SAPI de Windows"""

    def __init__(self, enabled: bool = True, rate: int = 2, neural_voice: Optional[str] = DEFAULT_NEURAL_VOICE):
        self.enabled = enabled
        self.rate = rate
        self.neural: Optional[NeuralTTS] = None
        self.q: "queue.PriorityQueue[Message]" = queue.PriorityQueue()
        self._stop = threading.Event()
        if enabled and neural_voice:
            try:
                self.neural = NeuralTTS(neural_voice)
                threading.Thread(target=self._prewarm, daemon=True).start()
            except ImportError:
                print("[voz] Para voz humana instala edge-tts: pip install edge-tts", flush=True)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _prewarm(self):
        for phrase in PREWARM_PHRASES:
            if self._stop.is_set() or self.neural.synth(phrase) is None:
                break

    def say(self, text: str, priority: int, ttl: float = 8.0):
        now = time.time()
        # PriorityQueue saca el menor primero: prioridad negativa y luego orden de llegada
        self.q.put(Message(-priority * 1e6 + now, text, priority, now + ttl))

    def stop(self):
        self._stop.set()
        self.q.put(Message(-1e12, "", 0, 0))

    def _run(self):
        speaker = None
        if self.enabled:
            try:
                import pythoncom
                import win32com.client
                pythoncom.CoInitialize()
                speaker = win32com.client.Dispatch("SAPI.SpVoice")
                for token in speaker.GetVoices():
                    if "spanish" in token.GetDescription().lower():
                        speaker.Voice = token
                        break
                speaker.Rate = self.rate
            except Exception as e:
                print(f"[voz] No se pudo iniciar SAPI ({e}); solo texto.")
                speaker = None

        while not self._stop.is_set():
            msg = self.q.get()
            if self._stop.is_set():
                break
            if time.time() > msg.expires:
                continue  # Mensaje viejo, ya no sirve
            if self.neural is not None:
                cached = os.path.exists(self.neural.file_for(msg.text))
                # Una urgencia no espera 1.5 s a la voz neuronal: si no está en caché, voz de Windows ya
                urgent_uncached = msg.priority >= P_SAFETY and not cached and speaker is not None
                path = None if urgent_uncached else self.neural.synth(msg.text)
                if path and time.time() <= msg.expires:
                    try:
                        self.neural.play(path)
                        continue
                    except Exception:
                        pass
            if speaker is not None:
                try:
                    speaker.Speak(msg.text)
                except Exception:
                    pass


class CarState:
    """Estado estimado de cada auto a partir de su posición en pista"""

    def __init__(self):
        self.prog: Optional[float] = None      # vueltas + fracción
        self.prog_t: float = 0.0
        self.speed: float = 0.0                # m/s estimado
        self.max_recent_speed: float = 0.0
        self.laps: List[float] = []
        self.last_lap_seen: float = -1.0
        self.seg: Optional[int] = None
        self.seg_enter_t: float = 0.0
        self.seg_times: Dict[int, float] = {}
        self.hazard_cooldown: float = 0.0
        self.on_pit: bool = False
        self.prev_on_pit: bool = False
        self.surface: int = 3
        self.prev_surface: int = 3


class LiveEngineer:
    """Ingeniero de carrera en vivo"""

    def __init__(self, driver_name: str = "Diego", speak: bool = True,
                 neural_voice: Optional[str] = DEFAULT_NEURAL_VOICE,
                 on_message: Optional[Callable[[str, int], None]] = None,
                 log_path: Optional[str] = None):
        self.name = driver_name
        self.voice = Voice(enabled=speak, neural_voice=neural_voice)
        self.on_message = on_message
        self.log_file = open(log_path, "a", encoding="utf-8") if log_path else None
        self.ir = irsdk.IRSDK() if IRSDK_AVAILABLE else None
        self._stop = threading.Event()
        self.questions: "queue.Queue[str]" = queue.Queue()
        self.muted_until = 0.0
        self.last_spoken = ""
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

    @staticmethod
    def _new_lap_stats():
        return {"slides": 0, "understeer": 0, "wheelspin": 0, "brake_samples": 0,
                "abs_samples": 0, "yellow": False, "offtrack": False, "pit": False,
                "entry_slides": [], "exit_slides": [], "entry_under": [], "mid_under": [], "spin_pcts": []}

    def emit(self, text: str, priority: int = P_INFO, key: Optional[str] = None,
             cooldown: float = 0.0, ttl: float = 8.0):
        now = time.time()
        if key:
            if self.cooldowns.get(key, 0) > now:
                return
            self.cooldowns[key] = now + cooldown
        self.last_emit = now
        stamp = time.strftime("%H:%M:%S")
        line = f"[{stamp}] {text}"
        print(line, flush=True)
        if self.log_file:
            self.log_file.write(line + "\n")
            self.log_file.flush()
        if self.on_message:
            try:
                self.on_message(text, priority)
            except Exception:
                pass
        if now < self.muted_until and priority < P_FLAG:
            return  # modo silencio: solo peligros, banderas y respuestas
        self.last_spoken = text
        self.voice.say(text, priority, ttl)

    def ask(self, text: str):
        """Pregunta del piloto (desde el push-to-talk); se responde en el hilo de telemetría"""
        print(f"[pregunta] {text}", flush=True)
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
        if t < 1.0:
            return f"{int(round(t * 10))} décimas"
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

    # ------------------------------------------------------- mapa de pista
    def _record_profile(self, pct: float, speed: float):
        """Perfil de velocidad por posición, solo con tus vueltas limpias"""
        if pct is None or pct < 0 or self.lap_stats["offtrack"] or self.lap_stats["pit"]:
            return
        b = int(pct * PROFILE_BINS) % PROFILE_BINS
        old = self.speed_profile[b]
        self.speed_profile[b] = speed if old is None else 0.7 * old + 0.3 * speed

    def _detect_corners(self):
        prof = self.speed_profile
        if sum(p is not None for p in prof) < PROFILE_BINS * 0.95:
            return
        n = PROFILE_BINS
        filled = [p if p is not None else 0.0 for p in prof]
        for i, p in enumerate(prof):  # rellenar huecos con el vecino
            if p is None:
                filled[i] = filled[i - 1]
        smooth = [(filled[i - 1] + filled[i] + filled[(i + 1) % n]) / 3 for i in range(n)]
        corners = []
        w_min, w_max = 6, 14
        for i in range(n):
            window = [smooth[(i + k) % n] for k in range(-w_min, w_min + 1)]
            if smooth[i] > min(window) or smooth[i] == smooth[(i - 1) % n]:
                continue
            peak = max(smooth[(i + k) % n] for k in range(-w_max, w_max + 1))
            if peak - smooth[i] > 4.0:  # ~15 km/h de frenada = curva
                corners.append(i / n)
        if corners:
            self.corners = corners
            print(f"[mapa] {len(corners)} curvas detectadas en "
                  f"{', '.join(f'{c * 100:.0f}%' for c in corners)}", flush=True)

    def corner_at(self, pct: float) -> str:
        """Nombre de la zona más cercana a una posición de la vuelta"""
        if not self.corners:
            return f"a los {int(round(pct * self.track_len, -1))} metros"
        dists = [((pct - c + 0.5) % 1.0) - 0.5 for c in self.corners]  # + = después del vértice
        k = min(range(len(dists)), key=lambda i: abs(dists[i]))
        d = dists[k] * self.track_len
        if d < -60:
            return f"en la frenada de la curva {k + 1}"
        if d > 60:
            return f"en la salida de la curva {k + 1}"
        return f"en la curva {k + 1}"

    def zone_name(self, seg: int) -> str:
        return self.corner_at((seg + 0.5) / N_SEGMENTS)

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
                        self.emit("Todavía no estoy conectado a iRacing.", P_BATTLE + 5, ttl=10)
                    time.sleep(1.0)
            except Exception as e:
                print(f"[error] {e}", flush=True)
                time.sleep(0.05)
        if self.limits is not None and self.limits_dirty:
            save_tracker(self.track_name, self.limits)
        self.voice.stop()

    def stop(self):
        self._stop.set()

    def _check_connection(self):
        if self.connected and not (self.ir.is_initialized and self.ir.is_connected):
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
                self.track_name = name
                self.limits = load_tracker(name, self.track_len)
                self.zone_state = {}
                if self.limits.zones:
                    print(f"[límites] {len(self.limits.zones)} zonas de límite conocidas en {name}", flush=True)
        except Exception:
            pass
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

    # ---------------------------------------------------------------- tick
    def _tick(self):
        ir = self.ir
        self._refresh_session_info()
        if self.me < 0:
            return
        t = ir["SessionTime"] or 0.0
        if self._last_t is not None and 0 < t - self._last_t < 0.25:
            self.dt = t - self._last_t
        self._last_t = t
        self._tick_n += 1
        while not self.questions.empty():
            q = self.questions.get()
            try:
                answer = self._answer(q)
            except Exception as e:
                answer = "No tengo ese dato ahora."
                print(f"[pregunta] error: {e}", flush=True)
            self.emit(answer, P_BATTLE + 5, ttl=20)

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
        self._check_hazards()
        self._driver_dynamics()
        self._track_limits_live()
        self._rival_events()
        if self._tick_n % SLOW_EVERY == 0:
            self._check_traffic()
        self._check_lap_complete()
        self._check_incidents_and_warnings()

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
                    self.emit("¡El de adelante se salió de pista! Aprovecha.", P_BATTLE,
                              key=f"off{idx}", cooldown=20, ttl=4)
                else:
                    self.emit("El de atrás se salió, ganas aire.", P_BATTLE, key=f"off{idx}",
                              cooldown=20, ttl=5)

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
                where = f"a {max(100, int(round(ahead_m, -2)))} metros"
                if stopped:
                    self.emit(f"¡Cuidado! Auto detenido {where}.", P_SAFETY, ttl=4)
                else:
                    self.emit(f"¡Cuidado! Auto trompeado {where}.", P_SAFETY, ttl=4)
                c.hazard_cooldown = now + 15
            elif ahead_m < 300 and off and c.speed > 15:
                self.emit(f"Auto fuera de pista a {max(100, int(round(ahead_m, -2)))} metros.", P_SAFETY, ttl=4)
                c.hazard_cooldown = now + 12
            elif behind_m < 150 and (stopped or (off and spun)):
                self.emit("Accidente justo detrás tuyo.", P_CAR, ttl=4)
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
            self.emit("Caución, full course yellow. No adelantes.", P_FLAG, key="caution", cooldown=10)
        elif new & (F.yellow | F.yellow_waving):
            self.emit("Bandera amarilla en tu sector, cuidado, no adelantes.", P_FLAG, key="yellow", cooldown=6)
        if new & F.debris:
            self.emit("Restos en pista.", P_FLAG, key="debris", cooldown=15)
        if new & F.blue:
            self.emit("Bandera azul, te van a pasar, mantén tu línea.", P_FLAG, key="blue", cooldown=20)
        if new & F.black:
            self.emit("¡Bandera negra! Tienes penalización, entra a pits.", P_SAFETY, key="black", cooldown=30)
        if new & F.repair:
            self.emit("Bandera de reparación, daño obligatorio, entra a pits.", P_SAFETY, key="repair", cooldown=30)
        if new & F.white:
            self.emit("Última vuelta. Dalo todo.", P_FLAG, key="white", cooldown=60)
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
                self.emit("¡Verde, verde, verde! Vamos.", P_FLAG)
        self.prev_state = state

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
                if brk > 0.15:
                    st["entry_slides"].append(pct)
                elif thr > 0.4:
                    st["exit_slides"].append(pct)
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
                    (st["entry_under"] if brk > 0.1 else st["mid_under"]).append(pct)
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
        self.my_laps.append({"time": lap_time if clean else -1, "raw": lap_time,
                             "fuel": used if used > 0 and not st["pit"] else None, **st})
        self.fuel_at_lap_start = fuel
        self.lap_stats = self._new_lap_stats()
        lap_segs, self.my_lap_segs = self.my_lap_segs, {}
        for kind in ("entry_slides", "exit_slides", "entry_under", "mid_under"):
            self.session_handling[kind].extend(st[kind])
        self.session_handling["spin_pcts"].extend(st["spin_pcts"])
        self.session_handling["wheelspin_laps"].append(st["wheelspin"])
        self.laps_since_setup += 1

        if not self.corners or len(self.my_laps) % 5 == 0:
            self._detect_corners()

        self._lap_report(st, lap_time, clean, lap_segs)
        self._battle_report()
        self._fuel_report(fuel)
        self._laps_countdown()

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
                    self.emit(f"Esa vuelta perdiste {self.fmt_gap(losses[seg])} {self.zone_name(seg)} "
                              f"comparado con tu mejor.", P_INFO, key="coach",
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
                            msg += f" Lo alcanzas en unas {laps_to} vueltas."
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
                        msg += (f" Te alcanza en unas {laps_to} vueltas." if laps_to <= left
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

    def _laps_countdown(self):
        if not self.is_race:
            return
        left = self.laps_left()
        if left in (10, 5, 3, 2):
            self.emit(f"Quedan {left} vueltas.", P_INFO, key=f"left{left}", cooldown=300, ttl=10)

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
            self.emit("Motor apagado, arranca.", P_SAFETY, key="stall", cooldown=10)
        self.prev_warnings = warn


    # ------------------------------------------------- límites de pista (vivo)
    def _zone_key(self, z: dict) -> int:
        return int(round(z["s"] / 10))

    def _zone_name(self, z: dict) -> str:
        return self.corner_at((z["s"] % self.track_len) / self.track_len)

    def _track_limits_live(self):
        """
        Zonas de límite aprendidas de tus .ibt (con GPS) y de cada salida en vivo.
        En vivo no hay posición lateral fiable, así que se usan: aviso previo, salida real
        (PlayerTrackSurface) y velocidad de salida.
        """
        if self.limits is None:
            return
        ir = self.ir
        s = ir["LapDist"]
        v = ir["Speed"] or 0.0
        if s is None or s < 0 or ir["OnPitRoad"]:
            return
        now = time.time()
        L = self.track_len
        rumble_l = (ir["TireLF_RumblePitch"] or 0) > 0 or (ir["TireLR_RumblePitch"] or 0) > 0
        rumble_r = (ir["TireRF_RumblePitch"] or 0) > 0 or (ir["TireRR_RumblePitch"] or 0) > 0
        if rumble_l or rumble_r:
            self._rumble_side, self._rumble_t = (1 if rumble_l else -1), now

        def rel(z):
            return (s - z["s"] + L / 2) % L - L / 2

        # Aviso antes de llegar a una zona donde te pasas seguido
        for z in self.limits.zones:
            d = rel(z)
            st = self.zone_state.setdefault(self._zone_key(z), {"offs": 0, "passes": 0, "best_exit": 0.0,
                                                                "curb": 0})
            total = z.get("offs", 0)
            if -260 < d < -180 and v > 20 and (st["offs"] >= 1 or total >= 2):
                self.emit(f"Ojo límite {self._zone_name(z)}.", P_CAR, key=f"limw{self._zone_key(z)}",
                          cooldown=self.ref_lap() * 2.5, ttl=3)

        # Entrada / salida de la zona activa
        zone = next((z for z in self.limits.zones if -60 <= rel(z) <= 60), None)
        if zone is not None and self._cur_zone is None:
            self._cur_zone = {"z": zone, "off": False, "curb": False}
        if self._cur_zone is not None:
            cz = self._cur_zone
            if (rumble_l and cz["z"]["side"] > 0) or (rumble_r and cz["z"]["side"] < 0):
                cz["curb"] = True
            if zone is not cz["z"]:
                st = self.zone_state.setdefault(self._zone_key(cz["z"]), {"offs": 0, "passes": 0,
                                                                         "best_exit": 0.0, "curb": 0})
                st["passes"] += 1
                st["curb"] += int(cz["curb"])
                exit_kmh = v * 3.6
                if not cz["off"]:
                    if st["passes"] >= 3 and exit_kmh > st["best_exit"] + 0.5 and cz["curb"]:
                        self.emit(f"Salida perfecta {self._zone_name(cz['z'])}, usaste el piano sin pasarte.",
                                  P_INFO, key=f"limok{self._zone_key(cz['z'])}", cooldown=self.ref_lap() * 3, ttl=6)
                    st["best_exit"] = max(st["best_exit"], exit_kmh)
                self._cur_zone = None

        # Salida de pista real (iRacing marca fuera cuando pasas el límite)
        off = ir["PlayerTrackSurface"] == 0
        if off and not self._prev_offtrack and v > 12:
            self._last_off_t = now
            z = next((z for z in self.limits.zones if abs(rel(z)) < 60), None)
            vx, vy = ir["VelocityX"] or 0.0, ir["VelocityY"] or 0.0
            stable = abs(math.atan2(vy, max(abs(vx), 0.1))) < 0.12
            if z is None and stable:
                side = self._rumble_side if now - self._rumble_t < 1.0 else 0
                z = {"s": float(s), "side": side or 1, "limits": [], "s_hist": [float(s)], "offs": 0, "samples": []}
                self.limits.zones.append(z)
            if z is not None:
                z["offs"] = z.get("offs", 0) + 1
                self.limits_dirty = True
                st = self.zone_state.setdefault(self._zone_key(z), {"offs": 0, "passes": 0, "best_exit": 0.0,
                                                                    "curb": 0})
                st["offs"] += 1
                if self._cur_zone is not None:
                    self._cur_zone["off"] = True
                n = st["offs"]
                extra = f" Van {n} ahí esta sesión, deja un poco de margen." if n >= 2 else ""
                self.emit(f"Te pasaste del límite {self._zone_name(z)}.{extra}", P_CAR,
                          key="limoff", cooldown=6, ttl=5)
        self._prev_offtrack = off

    # ---------------------------------------------------------- preguntas
    @staticmethod
    def _norm(text: str) -> str:
        t = unicodedata.normalize("NFD", text.lower())
        return "".join(ch for ch in t if unicodedata.category(ch) != "Mn")

    def _fuel_numbers(self):
        ir = self.ir
        fuel = ir["FuelLevel"] or 0.0
        used = [l["fuel"] for l in self.my_laps[-5:] if l.get("fuel")]
        per_lap = sum(used) / len(used) if used else None
        left = self.laps_left()
        return fuel, per_lap, left

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

        if has("neumatico", "goma", "cubierta", "llanta", "rueda", "presion"):
            parts = []
            temps = {c: [ir[f"{c}temp{x}"] for x in ("L", "M", "R")] for c in ("LF", "RF", "LR", "RR")}
            if all(v is not None for t in temps.values() for v in t):
                avg = {c: sum(t) / 3 for c, t in temps.items()}
                parts.append(f"temperaturas: delanteras {avg['LF']:.0f} y {avg['RF']:.0f}, "
                             f"traseras {avg['LR']:.0f} y {avg['RR']:.0f} grados")
            press = [ir[f"{c}pressure"] for c in ("LF", "RF", "LR", "RR")]
            if all(p is not None for p in press) and has("presion"):
                parts.append("presiones " + ", ".join(f"{p:.0f}" for p in press) + " kilopascales")
            stint = len(self.my_laps) - self.stint_start_lap + 1
            if stint > 0:
                parts.append(f"llevan {stint} vueltas")
            recent = [l for l in self.my_laps[-3:]]
            slides = sum(l.get("slides", 0) for l in recent)
            spin = sum(l.get("wheelspin", 0) for l in recent)
            if recent:
                parts.append(f"en las últimas vueltas {slides} derrapes y {spin:.1f} segundos patinando"
                             + (", cuídalas en las salidas" if slides > 6 or spin > 4 else ", van bien"))
            return ("Neumáticos: " + "; ".join(parts) + ".") if parts else "Todavía no tengo datos de neumáticos."

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

    # ------------------------------------------------- ingeniero de setup
    def _check_setup_report(self):
        ir = self.ir
        in_box = bool(ir["PlayerCarInPitStall"]) or not ir["IsOnTrack"]
        if in_box and not self.prev_in_stall and self.laps_since_setup >= 2:
            try:
                self._setup_report()
            except Exception as e:
                print(f"[setup] error: {e}", flush=True)
        self.prev_in_stall = in_box

    @staticmethod
    def _walk(d, path=()):
        if isinstance(d, dict):
            for k, v in d.items():
                yield from LiveEngineer._walk(v, path + (str(k),))
        else:
            yield path, d

    @staticmethod
    def _nums(value) -> List[float]:
        return [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", str(value))]

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
        try:
            recs += self._tire_analysis(self.ir["CarSetup"] or {})
        except Exception as e:
            print(f"[setup] sin datos de neumáticos: {e}", flush=True)
        if not recs:
            recs.append("El auto se ve equilibrado en estas vueltas. Sigue así y enfócate en la consistencia.")

        # Reporte completo en archivo
        import os
        log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
        os.makedirs(log_dir, exist_ok=True)
        path = os.path.join(log_dir, time.strftime("setup_%Y%m%d_%H%M%S.txt"))
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"Reporte de setup ({laps} vueltas analizadas)\n")
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

        self.emit(f"Reporte de setup de {laps} vueltas. " + " ".join(recs[:3]), P_INFO, ttl=60)
        # Reiniciar para medir el efecto de los cambios en la próxima salida
        self.session_handling = defaultdict(list)
        self.laps_since_setup = 0


def main():
    import argparse
    import os
    import sys
    parser = argparse.ArgumentParser(description="Ingeniero de carrera en vivo para iRacing")
    parser.add_argument("--nombre", default="Diego", help="Tu nombre para la radio")
    parser.add_argument("--sin-voz", action="store_true", help="Solo texto en consola")
    parser.add_argument("--voz", default=None,
                        help="Voz neuronal (ej: es-AR-TomasNeural, es-MX-JorgeNeural, es-CL-CatalinaNeural)")
    parser.add_argument("--voz-windows", action="store_true", help="Usar la voz clásica de Windows (sin internet)")
    parser.add_argument("--asignar-boton", action="store_true",
                        help="Aprieta el botón del volante que quieres usar para hablarle al ingeniero")
    parser.add_argument("--tecla", default=None, help="Tecla push-to-talk alternativa (por defecto Insert)")
    parser.add_argument("--sin-ptt", action="store_true", help="Desactivar las preguntas por voz")
    args = parser.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, time.strftime("radio_%Y%m%d_%H%M%S.txt"))
    from app.voice_commands import PushToTalk, load_config, save_config, wait_for_button
    cfg = load_config()
    if args.asignar_boton:
        print("Aprieta el botón del volante que quieres usar para hablar (15 s)...", flush=True)
        found = wait_for_button()
        if not found:
            print("No detecté ningún botón.")
            return
        cfg["ptt_joystick"], cfg["ptt_button"] = found
        save_config(cfg)
        print(f"Listo: botón {found[1]} de {found[0]} guardado.")
        return
    if args.tecla:
        cfg["ptt_key"] = args.tecla
        save_config(cfg)
    if args.voz:
        cfg["voice"] = args.voz
        save_config(cfg)
    voice = cfg.get("voice") or DEFAULT_NEURAL_VOICE

    eng = LiveEngineer(driver_name=args.nombre, speak=not args.sin_voz,
                       neural_voice=None if args.voz_windows else voice, log_path=log_path)
    if not args.sin_ptt:
        ptt = PushToTalk(on_text=eng.ask, on_error=lambda m: eng.emit(m, P_BATTLE + 5, ttl=6), config=cfg)
        ptt.start()
    try:
        eng.run()
    except KeyboardInterrupt:
        eng.stop()


if __name__ == "__main__":
    main()
