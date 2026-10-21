"""
Push-to-talk para hablarle al ingeniero: mantienes un botón del volante (o una tecla),
hablas, sueltas, y la pregunta se transcribe con el reconocimiento de voz de Google (es-CL).
"""

import json
import os
import threading
import time
from typing import Callable, Optional

import numpy as np

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "ingeniero.json")
SAMPLE_RATE = 16000

DEFAULT_CONFIG = {
    "ptt_joystick": None,   # nombre del volante/botonera
    "ptt_button": None,     # índice del botón
    "ptt_key": "insert",    # tecla alternativa (iRacing no la usa por defecto)
    "language": "es-CL",
}


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg: dict):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def _init_pygame():
    # Leer el volante aunque la ventana activa sea iRacing, sin abrir ninguna ventana
    os.environ.setdefault("SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", "1")
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
    import pygame
    pygame.display.init()
    pygame.joystick.init()
    return pygame


def list_joysticks() -> list:
    pygame = _init_pygame()
    return [pygame.joystick.Joystick(i).get_name() for i in range(pygame.joystick.get_count())]


def wait_for_button(timeout: float = 15.0) -> Optional[tuple]:
    """Espera a que aprietes un botón en cualquier volante/botonera: (nombre, índice)"""
    pygame = _init_pygame()
    joys = [pygame.joystick.Joystick(i) for i in range(pygame.joystick.get_count())]
    for j in joys:
        j.init()
    pygame.event.clear()
    end = time.time() + timeout
    while time.time() < end:
        for ev in pygame.event.get():
            if ev.type == pygame.JOYBUTTONDOWN:
                j = joys[ev.joy] if ev.joy < len(joys) else pygame.joystick.Joystick(ev.joy)
                return j.get_name(), ev.button
        time.sleep(0.01)
    return None


class PushToTalk:
    """Graba mientras el botón/tecla está apretado y entrega el texto reconocido"""

    def __init__(self, on_text: Callable[[str], None], on_error: Optional[Callable[[str], None]] = None,
                 config: Optional[dict] = None):
        self.cfg = config or load_config()
        self.on_text = on_text
        self.on_error = on_error or (lambda msg: None)
        self._stop = threading.Event()
        self._pressed = False
        self._frames = []
        self._stream = None
        self._lock = threading.Lock()
        self.available = True
        try:
            import sounddevice  # noqa: F401
            import speech_recognition  # noqa: F401
        except ImportError:
            self.available = False
            print("[ptt] Faltan dependencias: pip install SpeechRecognition sounddevice", flush=True)

    # ---------------------------------------------------------- arranque
    def start(self):
        if not self.available:
            return
        if self.cfg.get("ptt_button") is not None:
            threading.Thread(target=self._joystick_loop, daemon=True).start()
        if self.cfg.get("ptt_key"):
            try:
                import keyboard
                keyboard.on_press_key(self.cfg["ptt_key"], lambda e: self._press(), suppress=False)
                keyboard.on_release_key(self.cfg["ptt_key"], lambda e: self._release(), suppress=False)
            except Exception as e:
                print(f"[ptt] Tecla no disponible ({e})", flush=True)
        btn = self.cfg.get("ptt_button")
        where = f"botón {btn} de {self.cfg.get('ptt_joystick')}" if btn is not None else "sin botón asignado"
        print(f"[ptt] Push-to-talk listo: {where}, tecla {self.cfg.get('ptt_key')}", flush=True)

    def stop(self):
        self._stop.set()

    def _joystick_loop(self):
        try:
            pygame = _init_pygame()
        except Exception as e:
            print(f"[ptt] No se pudo leer el volante ({e})", flush=True)
            return
        target = None
        while not self._stop.is_set():
            if target is None:
                for i in range(pygame.joystick.get_count()):
                    j = pygame.joystick.Joystick(i)
                    if self.cfg.get("ptt_joystick") in (None, j.get_name()):
                        j.init()
                        target = j
                        break
                if target is None:
                    time.sleep(2)
                    pygame.joystick.quit()
                    pygame.joystick.init()
                    continue
            pygame.event.pump()
            try:
                down = bool(target.get_button(self.cfg["ptt_button"]))
            except Exception:
                target = None
                continue
            if down and not self._pressed:
                self._press()
            elif not down and self._pressed:
                self._release()
            time.sleep(0.01)

    # ----------------------------------------------------------- grabación
    def _press(self):
        with self._lock:
            if self._pressed:
                return
            self._pressed = True
            self._frames = []
            try:
                import sounddevice as sd
                self._stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16",
                                              callback=lambda data, *_: self._frames.append(data.copy()))
                self._stream.start()
            except Exception as e:
                self._pressed = False
                self.on_error(f"No pude abrir el micrófono: {e}")
                return
        _beep(1200)

    def _release(self):
        with self._lock:
            if not self._pressed:
                return
            self._pressed = False
            stream, self._stream = self._stream, None
            frames, self._frames = self._frames, []
        if stream is not None:
            time.sleep(0.15)  # no cortar la última palabra
            stream.stop()
            stream.close()
        _beep(800)
        if not frames:
            return
        audio = np.concatenate(frames).tobytes()
        if len(audio) < SAMPLE_RATE * 2 * 0.4:  # menos de 0.4 s: toque accidental
            return
        threading.Thread(target=self._recognize, args=(audio,), daemon=True).start()

    def _recognize(self, audio: bytes):
        text = recognize(audio, self.cfg.get("language", "es-CL"))
        if text:
            self.on_text(text)
        else:
            self.on_error("No te entendí, repite.")


def recognize(audio: bytes, language: str = "es-CL", sample_rate: int = SAMPLE_RATE) -> Optional[str]:
    import speech_recognition as sr
    rec = sr.Recognizer()
    data = sr.AudioData(audio, sample_rate, 2)
    try:
        return rec.recognize_google(data, language=language)
    except sr.UnknownValueError:
        return None
    except sr.RequestError as e:
        print(f"[ptt] Sin conexión al reconocimiento de voz ({e})", flush=True)
        return None


def _beep(freq: int):
    def go():
        try:
            import winsound
            winsound.Beep(freq, 60)
        except Exception:
            pass
    threading.Thread(target=go, daemon=True).start()
