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
import re as _re
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
from app.references import load_references


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


DEFAULT_NEURAL_VOICE = "es-CL-CatalinaNeural"


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
        self._slots = threading.Semaphore(2)   # no saturar el servicio de voz con muchas frases a la vez
        self._fails = 0

    def file_for(self, text: str, rate: Optional[str] = None) -> str:
        key = self._hash(f"{self.voice}|{rate or self.rate}|{text}".encode("utf-8")).hexdigest()[:20]
        return os.path.join(self.cache_dir, f"{key}.mp3")

    def synth(self, text: str, timeout: float = 6.0, rate: Optional[str] = None) -> Optional[str]:
        path = self.file_for(text, rate)
        if os.path.exists(path) and os.path.getsize(path) > 0:
            return path
        if time.time() < self.failed_until:
            return None
        import asyncio
        import edge_tts

        part = f"{path}.{threading.get_ident()}.part"

        async def go():
            await edge_tts.Communicate(text, self.voice, rate=rate or self.rate).save(part)
        with self._slots:
            if os.path.exists(path) and os.path.getsize(path) > 0:
                return path   # otra hebra ya la generó mientras esperábamos turno
            for attempt in (1, 2):
                try:
                    asyncio.run(asyncio.wait_for(go(), timeout))
                    os.replace(part, path)
                    self._fails = 0
                    return path
                except Exception as e:
                    err = type(e).__name__
            self._fails += 1
            if self._fails >= 3:   # varias frases seguidas fallando: de verdad no hay servicio de voz
                print(f"[voz] Voz neuronal no disponible ({err}); uso la de Windows por un minuto.", flush=True)
                self.failed_until = time.time() + 60
                self._fails = 0
            return None

    @staticmethod
    def play(path: str, interrupt: Optional[threading.Event] = None):
        """Reproduce un mp3 con el MCI de Windows (sin ventanas); se corta si llega una urgencia"""
        import ctypes
        mci = ctypes.windll.winmm.mciSendStringW
        alias = f"radio{threading.get_ident()}"
        mci(f'open "{path}" type mpegvideo alias {alias}', None, 0, None)
        if interrupt is None:
            mci(f"play {alias} wait", None, 0, None)
        else:
            mci(f"play {alias}", None, 0, None)
            buf = ctypes.create_unicode_buffer(32)
            while True:
                mci(f"status {alias} mode", buf, 32, None)
                if buf.value != "playing" or interrupt.is_set():
                    break
                time.sleep(0.03)
            mci(f"stop {alias}", None, 0, None)
        mci(f"close {alias}", None, 0, None)


# Frases urgentes que conviene tener sintetizadas de antemano (latencia cero)
URGENT_RATE = "+30%"     # llamadas urgentes: más rápidas
# Salida de audio de la radio: siempre los audífonos (configurable en data/ingeniero.json -> "audio_output")
DEFAULT_OUTPUTS = ["Auriculares", "Realtek(R) Audio"]


def _directsound_outputs() -> List[tuple]:
    """(índice, nombre) de las salidas de audio vía DirectSound (permite elegir dispositivo exacto)"""
    try:
        import sounddevice as sd
        apis = sd.query_hostapis()
        out = []
        for i, d in enumerate(sd.query_devices()):
            api = apis[d["hostapi"]]["name"]
            if d["max_output_channels"] > 0 and "DirectSound" in api and "primario" not in d["name"].lower() \
                    and "primary" not in d["name"].lower():
                out.append((i, d["name"]))
        return out
    except Exception:
        return []


def list_output_devices() -> List[str]:
    return [name for _, name in _directsound_outputs()]


def pick_output_device(preferences: Optional[List[str]] = None) -> Optional[tuple]:
    """(índice, nombre) del primer dispositivo que coincide con las preferencias; None = predeterminado"""
    devices = _directsound_outputs()
    for pref in preferences or DEFAULT_OUTPUTS:
        for idx, name in devices:
            if name == pref:
                return idx, name
        for idx, name in devices:
            if pref.lower() in name.lower() and "digital output" not in name.lower():
                return idx, name
    return None


# Frases urgentes pre-sintetizadas (latencia cero, misma voz). Se generan con URGENT_RATE.
PREWARM_URGENT = (
    [f"¡Auto parado a {d}!" for d in range(100, 800, 100)]
    + [f"¡Trompo a {d}!" for d in range(100, 800, 100)]
    + [f"Fuera de pista a {d}." for d in range(100, 400, 100)]
    + ["Accidente atrás.", "¡Amarilla, no adelantes!", "¡Full course yellow!", "Azul, mantén la línea.",
       "Restos en pista.", "¡Negra, a pits!", "¡Reparación, a pits!", "¡Motor apagado!", "¡Limitador!",
       "¡Velocidad en pits!", "¡Limitador, entrada de pits!", "Última vuelta.", "¡Verde, verde, verde!",
       "¡Se salió el de adelante, aprovecha!", "Se salió el de atrás.", "Copiado."]
)
PREWARM_NORMAL = ["Todavía no estoy conectado a iRacing.", "No te entendí, repite."]


class Voice:
    """Cola de mensajes hablados: voz neuronal si hay internet, si no SAPI de Windows"""

    def __init__(self, enabled: bool = True, rate: int = 2, neural_voice: Optional[str] = DEFAULT_NEURAL_VOICE,
                 outputs: Optional[List[str]] = None):
        self.enabled = enabled
        self.rate = rate
        self.outputs = outputs or DEFAULT_OUTPUTS
        self.device: Optional[str] = None
        self.device_index: Optional[int] = None
        self._mixer_ready = False
        self.neural: Optional[NeuralTTS] = None
        self.q: "queue.PriorityQueue[Message]" = queue.PriorityQueue()
        self._stop = threading.Event()
        self._urgent = threading.Event()   # corta lo que se esté diciendo si llega un peligro
        if enabled:
            # Elegir la salida aquí, antes de que el hilo de voz inicialice COM para la voz de Windows
            try:
                self._init_mixer()
            except Exception as e:
                print(f"[voz] no pude abrir la salida de audio ({e})", flush=True)
        if enabled and neural_voice:
            try:
                self.neural = NeuralTTS(neural_voice)
                threading.Thread(target=self._prewarm, daemon=True).start()
            except ImportError:
                print("[voz] Para voz humana instala edge-tts: pip install edge-tts", flush=True)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _init_mixer(self):
        """Elige la salida (la que configuraste); si no está conectada, la predeterminada de Windows"""
        found = pick_output_device(self.outputs)
        self.device_index, self.device = found if found else (None, None)
        self._mixer_ready = True
        print(f"[voz] salida de audio: {self.device or 'predeterminada de Windows'}", flush=True)

    def _play(self, path: str, interrupt: Optional[threading.Event] = None):
        """Reproduce el mp3 en el dispositivo elegido; se corta si llega una urgencia"""
        import sounddevice as sd
        import soundfile as sf
        if not self._mixer_ready:
            self._init_mixer()
        data, sr = sf.read(path, dtype="float32")
        try:
            sd.play(data, sr, device=self.device_index)
        except Exception:
            self._init_mixer()   # se desconectó: volver a buscarlo (o el predeterminado)
            sd.play(data, sr, device=self.device_index)
        end = time.time() + len(data) / sr + 0.3
        while time.time() < end:
            if interrupt is not None and interrupt.is_set():
                sd.stop()
                return
            time.sleep(0.02)
        sd.wait()

    @staticmethod
    def rate_for(priority: int) -> Optional[str]:
        return URGENT_RATE if priority >= P_FLAG else None

    def _prewarm(self):
        for phrase in PREWARM_URGENT:
            if self._stop.is_set() or self.neural.synth(phrase, rate=URGENT_RATE) is None:
                return
        for phrase in PREWARM_NORMAL:
            if self._stop.is_set() or self.neural.synth(phrase) is None:
                return

    def say(self, text: str, priority: int, ttl: float = 8.0):
        now = time.time()
        # Frases largas: cada oración por separado, para empezar a hablar con la primera
        parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", text) if p.strip()] if len(text) > 90 else [text]
        if priority >= P_SAFETY:
            self._urgent.set()
        for i, part in enumerate(parts):
            if self.neural is not None:   # sintetizar ya, en paralelo, mientras se habla lo anterior
                threading.Thread(target=self.neural.synth, args=(part,),
                                 kwargs={"rate": self.rate_for(priority)}, daemon=True).start()
            # PriorityQueue saca el menor primero: prioridad negativa y luego orden de llegada
            self.q.put(Message(-priority * 1e6 + now + i * 1e-3, part, priority, now + ttl))

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
                # Respaldo de Windows: voz femenina en español (misma voz de mujer que la neuronal)
                voices = list(speaker.GetVoices())
                female = [t for t in voices if "spanish" in t.GetDescription().lower()
                          and any(n in t.GetDescription().lower() for n in ("helena", "laura", "sabina", "elvira"))]
                spanish = [t for t in voices if "spanish" in t.GetDescription().lower()]
                if female or spanish:
                    speaker.Voice = (female or spanish)[0]
                for out in speaker.GetAudioOutputs():
                    desc = out.GetDescription()
                    if any(p.lower() in desc.lower() for p in self.outputs) and "digital" not in desc.lower():
                        speaker.AudioOutput = out
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
                # Si la síntesis en paralelo ya está en curso, esperarla un momento en vez de repetirla.
                # Misma voz siempre: la de Windows solo si el servicio de voz neuronal no responde.
                rate = self.rate_for(msg.priority)
                for _ in range(40):
                    if os.path.exists(self.neural.file_for(msg.text, rate)):
                        break
                    time.sleep(0.05)
                path = self.neural.synth(msg.text, rate=rate)
                if path and time.time() <= msg.expires:
                    try:
                        if msg.priority >= P_SAFETY:
                            self._urgent.clear()
                            self._play(path)                  # una urgencia no se interrumpe
                        else:
                            self._play(path, self._urgent)    # se corta si llega una urgencia
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

    # ------------------------------------------------------- mapa de pista
    def _record_profile(self, pct: float, speed: float):
        """Perfil de velocidad por posición, solo con tus vueltas limpias"""
        if pct is None or pct < 0 or self.lap_stats["offtrack"] or self.lap_stats["pit"]:
            return
        b = int(pct * PROFILE_BINS) % PROFILE_BINS
        old = self.speed_profile[b]
        self.speed_profile[b] = speed if old is None else 0.7 * old + 0.3 * speed

    def _detect_corners(self):
        if self.refs:
            return  # las curvas vienen del análisis de .ibt
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
        except Exception:
            pass
        if self.setup_db is not None and self.setup_db.stints:
            sens = self.tire_model.state["sens"] if self.tire_model is not None else None
            snap["historial_esta_pista"] = self.setup_db.summary(self._tire_targets(), sens, last_n=2)
        if self.tire_model is not None:
            snap["tire_targets"] = {"adelante_c": self.tire_model.target_spread("LF", self.track_name),
                                    "atras_c": self.tire_model.target_spread("LR", self.track_name)}
        return _drop_empty(snap)

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
        rec = self.setup_db.add_stint(ir["CarSetup"] or {}, stint_laps, session=session,
                                      track_temp=ir["TrackTempCrew"], air_temp=ir["AirTemp"], handling=handling,
                                      fuel_per_lap=round(sum(fuel) / len(fuel), 3) if fuel else None,
                                      offtracks=self._stint_offs, recommendations=recs)
        self._stint_offs = 0
        print(f"[setups] salida guardada ({rec['vueltas']} vueltas, "
              f"{'con' if rec['medicion'] else 'sin'} temperaturas) -> {len(self.setup_db.stints)} en total",
              flush=True)

    def _chassis_recs(self, laps: Optional[int] = None) -> List[str]:
        """Barras, alerón y spring perch según dónde (velocidad y sentido de curva) aparece el problema"""
        from app.chassis_advisor import advise, phrases
        from app.setup_db import flatten_setup
        laps = laps or max(self.laps_since_setup, 1)
        history = self.setup_db.stints if self.setup_db is not None else None
        return phrases(advise(flatten_setup(self.ir["CarSetup"] or {}), laps, self.side_counts, history))

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

    # ------------------------------------------------ grabación automática
    def _auto_recording(self):
        """Graba telemetría a disco mientras estás en el auto (sin Alt+L) y la cierra al bajarte"""
        if not self.auto_record:
            return
        ir = self.ir
        active = ir["IsDiskLoggingActive"]
        if active is None or not ir["IsDiskLoggingEnabled"]:
            return  # iRacing con la telemetría de disco desactivada (irsdkEnableDisk=0)
        if self.on_track:
            self._off_track_since = None
            if not active and self._tick_n % 60 == 0:   # reintentar como mucho una vez por segundo
                ir.telem_command(irsdk.TelemCommandMode.start)
                if not self._we_record:
                    self._we_record = True
                    print("[telemetría] grabación iniciada", flush=True)
        else:
            now = time.time()
            if self._off_track_since is None:
                self._off_track_since = now
            elif now - self._off_track_since > 5 and self._we_record and active:
                ir.telem_command(irsdk.TelemCommandMode.stop)
                self._we_record = False
                print("[telemetría] grabación cerrada, se analizará automáticamente", flush=True)

    def _save_limits(self):
        if self.limits is not None and self.limits_dirty and self.track_name:
            try:
                save_tracker(self.track_name, self.limits)
                self.limits_dirty = False
            except OSError as e:
                print(f"[límites] no se pudieron guardar: {e}", flush=True)

    # ------------------------------------------------ análisis automático
    def _analysis_done(self, res, report_path: str):
        from app.auto_analysis import spoken_summary
        print(f"[análisis] reporte guardado en {report_path}", flush=True)
        self.emit(spoken_summary(res), P_INFO, ttl=120)
        self._reload_track_data = True

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
        except Exception:
            pass
        try:
            self.pit_limit_kmh = self._nums(self.ir["WeekendInfo"]["TrackPitSpeedLimit"])[0]
            if "mph" in str(self.ir["WeekendInfo"]["TrackPitSpeedLimit"]):
                self.pit_limit_kmh *= 1.609
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
            car = str(self.drivers[self.me].get("CarScreenName", ""))
            if car and car != self.car_name and self.track_name:
                self.car_name = car
                from app.setup_db import SetupDB
                self.setup_db = SetupDB(self.track_name, car)
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
                    self.emit("¡Se salió el de adelante, aprovecha!", P_BATTLE,
                              key=f"off{idx}", cooldown=20, ttl=4)
                else:
                    self.emit("Se salió el de atrás.", P_BATTLE, key=f"off{idx}",
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
                            msg += (" Lo alcanzas en la próxima vuelta." if laps_to <= 1
                                    else f" Lo alcanzas en unas {laps_to} vueltas.")
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
                        msg += ((" Te alcanza en la próxima vuelta." if laps_to <= 1
                                 else f" Te alcanza en unas {laps_to} vueltas.") if laps_to <= left
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

    # ------------------------------------------------- delta contra tu mejor
    def _record_lap_trace(self):
        ir = self.ir
        pct, cur = ir["LapDistPct"], ir["LapCurrentLapTime"]
        if pct is None or cur is None or pct < 0 or cur <= 0:
            return
        b = int(pct * PROFILE_BINS) % PROFILE_BINS
        if math.isnan(self.lap_trace[b]):
            self.lap_trace[b] = cur

    def _close_lap_trace(self, lap_time: float, clean: bool):
        trace, self.lap_trace = self.lap_trace, [math.nan] * PROFILE_BINS
        filled = sum(not math.isnan(x) for x in trace)
        if clean and lap_time > 0 and filled > PROFILE_BINS * 0.9 and lap_time < self.best_trace_time:
            self.best_trace, self.best_trace_time = trace, lap_time

    def live_delta(self) -> Optional[float]:
        """Delta de la vuelta actual contra tu mejor (positivo = más lento)"""
        ir = self.ir
        if ir["LapDeltaToBestLap_OK"]:
            return ir["LapDeltaToBestLap"]
        pct, cur = ir["LapDistPct"], ir["LapCurrentLapTime"]
        if not self.best_trace or pct is None or cur is None or pct < 0.03:
            return None
        b = int(pct * PROFILE_BINS) % PROFILE_BINS
        for k in range(b, -1, -1):
            if not math.isnan(self.best_trace[k]):
                return cur - self.best_trace[k]
        return None

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
                self._stint_offs += 1
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

    # ------------------------------------------------ referencias de frenada
    def _apexes_m(self) -> List[float]:
        return [c * self.track_len for c in self.corners]

    def _brakes_by_corner(self, brakes: List[list]) -> Dict[int, float]:
        """
        Frenada principal de cada curva: la de mayor presión en los 400 m previos al vértice
        (empate: la más cercana). Misma definición que el analizador de .ibt.
        """
        out: Dict[int, tuple] = {}
        apexes = self._apexes_m()
        L = self.track_len
        # Ventana de búsqueda de cada curva: hasta 400 m atrás, sin pasar del vértice anterior
        reach = [min(400.0, (a - apexes[k - 1]) % L or L) for k, a in enumerate(apexes)]
        for b, peak in brakes:
            for k, a in enumerate(apexes):
                d = (a - b) % L
                if d >= reach[k]:
                    continue
                if k not in out or peak > out[k][1] + 0.05 or (abs(peak - out[k][1]) <= 0.05 and d < out[k][2]):
                    out[k] = (b, peak, d)
        return {k: v[0] for k, v in out.items()}

    def ref_brake(self, k: int) -> Optional[float]:
        if self.refs and k < len(self.refs["corners"]):
            return self.refs["corners"][k].get("brake_at_m")
        return self.best_lap_brakes.get(k)

    def _brake_cause(self, pct: float) -> str:
        """Explica la pérdida en una zona si la frenada fue distinta a tu referencia"""
        apexes = self._apexes_m()
        if not apexes:
            return ""
        s = pct * self.track_len
        k = min(range(len(apexes)), key=lambda i: abs((s - apexes[i] + self.track_len / 2) % self.track_len
                                                      - self.track_len / 2))
        if abs((s - apexes[k] + self.track_len / 2) % self.track_len - self.track_len / 2) > self.track_len / N_SEGMENTS:
            return ""
        ref, mine = self.ref_brake(k), self.last_lap_brakes.get(k)
        if ref is None or mine is None:
            return ""
        diff = ((mine - ref + self.track_len / 2) % self.track_len) - self.track_len / 2
        if diff < -8:
            return f" Frenaste {-diff:.0f} metros antes que tu referencia."
        if diff > 8:
            return f" Frenaste {diff:.0f} metros más tarde que tu referencia, te pasaste de frenada."
        return ""

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
            return ", ".join(parts) + "."
        ref = self.best_lap_brakes.get(k)
        if ref is not None:
            return f"En tu mejor vuelta frenaste para la curva {n} en el metro {ref:.0f}."
        return f"Todavía no tengo referencia para la curva {n}. Analiza un archivo de telemetría de esta pista."

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
                    f"carga {plan['need']:.0f} litros para hacerla sin parar.")

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
            t = read_toe(flatten_setup(ir["CarSetup"] or {}))
            now = ", ".join(f"{k} {v:+.1f}" for k, v in t.items())
            return (f"Con lo que veo, no cambiaría el toe ({now} milímetros): el auto no muestra sobreviraje ni "
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

        m_curve = _re.search(r"(?:curva|la)\s+(\d{1,2})", n)
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
        temps = {c: [ir[f"{c}tempC{x}"] for x in ("L", "M", "R")] for c in names}
        parts = []
        if all(v is not None for t in temps.values() for v in t) and \
                max(max(t) for t in temps.values()) - min(min(t) for t in temps.values()) >= 2:
            def omi(c):  # exterior, centro, interior (en las izquierdas L es el exterior)
                l, m, r = temps[c]
                return (l, m, r) if c[0] == "L" else (r, m, l)
            parts.append("Medidas en tu última entrada a pits, de exterior a interior: " + "; ".join(
                f"{names[c]} {omi(c)[0]:.0f}, {omi(c)[1]:.0f} y {omi(c)[2]:.0f}" for c in names) + " grados")
        hot = {}
        try:
            for path, value in self._walk(ir["CarSetup"] or {}):
                corner = next((k for k, v in {"LF": "LeftFront", "RF": "RightFront",
                                              "LR": "LeftRear", "RR": "RightRear"}.items() if v in path), None)
                if corner and path[-1] == "LastHotPressure":
                    hot[corner] = self._nums(value)[0]
        except Exception:
            pass
        cold = [ir[f"{c}coldPressure"] for c in names]
        if len(hot) == 4:
            parts.append(f"presión en caliente {hot['LF']:.0f} y {hot['RF']:.0f} adelante, "
                         f"{hot['LR']:.0f} y {hot['RR']:.0f} atrás")
        if all(c is not None for c in cold):
            parts.append(f"en frío {cold[0]:.0f} adelante y {cold[2]:.0f} atrás")
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
        log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
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
        self.laps_since_setup = 0


def _drop_empty(d):
    """Quita valores vacíos para no gastar tokens en datos que no hay"""
    if isinstance(d, dict):
        out = {k: _drop_empty(v) for k, v in d.items()}
        return {k: v for k, v in out.items() if v not in (None, "", [], {})}
    return d


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
    parser.add_argument("--elegir-salida", action="store_true",
                        help="Elegir el dispositivo de audio por donde habla la ingeniera (audífonos)")
    parser.add_argument("--asignar-boton", action="store_true",
                        help="Aprieta el botón del volante que quieres usar para hablarle al ingeniero")
    parser.add_argument("--tecla", default=None, help="Tecla push-to-talk alternativa (por defecto Insert)")
    parser.add_argument("--sin-ptt", action="store_true", help="Desactivar las preguntas por voz")
    parser.add_argument("--sin-grabar", action="store_true",
                        help="No iniciar la grabación de telemetría automáticamente (usar Alt+L)")
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
    if args.elegir_salida:
        devices = list_output_devices()
        if not devices:
            print("No encontré salidas de audio.")
            return
        print("Salidas de audio:")
        for i, name in enumerate(devices, 1):
            mark = "  <- actual" if name in (cfg.get("audio_output") or []) else ""
            print(f"  {i}. {name}{mark}")
        choice = input("Escribe el número de la salida para la radio y presiona Enter: ").strip()
        if not choice.isdigit() or not 1 <= int(choice) <= len(devices):
            print("Número inválido, no cambié nada.")
            return
        name = devices[int(choice) - 1]
        cfg["audio_output"] = [name]
        save_config(cfg)
        print(f"Listo: la radio va a salir por '{name}'. Escucha la prueba...")
        test = Voice(neural_voice=cfg.get("voice") or DEFAULT_NEURAL_VOICE, outputs=[name])
        test.say("Prueba de radio. Si me escuchas aquí, la salida quedó bien.", P_INFO, ttl=20)
        time.sleep(6)
        test.stop()
        return
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
                       neural_voice=None if args.voz_windows else voice, log_path=log_path,
                       auto_record=not args.sin_grabar)
    if not args.sin_ptt:
        ptt = PushToTalk(on_text=eng.ask, on_error=lambda m: eng.emit(m, P_BATTLE + 5, ttl=6, always=True),
                         config=cfg)
        ptt.start()
    try:
        eng.run()
    except KeyboardInterrupt:
        eng.stop()


if __name__ == "__main__":
    main()
