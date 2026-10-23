"""Radio: voces neuronales (edge-tts) o de Windows, salida de audio elegida y cola de mensajes."""

import os
import queue
import re
import threading
import time
from dataclasses import dataclass, field
from typing import List, Optional

from app.engineer.common import P_FLAG, P_SAFETY


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
