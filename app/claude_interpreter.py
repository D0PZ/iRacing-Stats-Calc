"""
Claude como intérprete de los datos: solo cuando le preguntas por voz.

Una llamada por pregunta, sin historial: se envía un resumen compacto del estado actual
(unos cientos de tokens) y Claude responde en 1-3 frases para la radio. Si falla,
el ingeniero responde con sus reglas locales.

Dos formas de llamar a Claude:
- ClaudeCodeInterpreter: usa Claude Code (`claude -p`) con tu suscripción, sin costo extra.
- ClaudeInterpreter: usa la API con ANTHROPIC_API_KEY (se cobra aparte).
"""

import json
import re
import os
import shutil
import subprocess
import tempfile
import time
from typing import Optional

try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False

DEFAULT_MODEL = "claude-opus-5-5"

SYSTEM = """Eres el ingeniero de carrera de un piloto de iRacing. Te hablan por radio en plena sesión.
Recibes un resumen de los datos actuales en JSON y una pregunta del piloto.

Reglas:
- Responde solo con lo que muestran los datos. Si un dato no está, dilo en pocas palabras; no inventes valores.
- Interpreta: no recites el JSON. Di qué significa y, si aplica, qué hacer.
- Español de Chile, directo: máximo 2 frases cortas (25 palabras en total), se lee en voz alta en plena carrera.
- Empieza por la respuesta, sin saludos ni repetir la pregunta.
- Sin símbolos ni markdown. Escribe las unidades con palabras (litros, segundos, grados, kilopascales).
- Las temperaturas de neumáticos son de la última entrada a pits (exterior, centro, interior); iRacing no las da en pista.
- Ajustes de neumáticos: el objetivo es interior sobre exterior según tire_targets y centro igual al promedio de los bordes.
- historial_esta_pista trae tus salidas ANTERIORES en esta misma pista con este auto, de cualquier sesión o día,
  con su setup, lo medido en pits y el ritmo. ideales_por_rueda son los valores ideales estimados con todo ese
  historial: úsalos cuando pregunten por presiones, camber o setup ideal aunque en esta sesión no haya mediciones.
- setup_actual es el setup cargado ahora mismo (toe, barras, ala, presiones, camber, pastillas, etc.).
- Toe: usa ajustes_toe_recomendados si vienen. Toe-in trasero = estabilidad, menos rotación y velocidad punta;
  la trasera exterior estabiliza cada curva (a la derecha trabaja la izquierda).
- Spring perch: en este auto subir la perch de una rueda la BAJA y le QUITA peso. Cross weight =
  (delantera derecha + trasera izquierda) / total: subir la perch delantera derecha o trasera izquierda BAJA el
  cross weight. Nunca corrijas el cross weight con una sola rueda: se hace en diagonal con las 4 ruedas, la misma
  cantidad (delantera izquierda y trasera derecha en un sentido, delantera derecha y trasera izquierda en el otro)
  para no cambiar la altura de cada eje. Da los valores exactos de ajustes_chasis_recomendados.
- balance_gradiente_subviraje: K en grados por g medido en esta salida por velocidad de curva (lenta < 100,
  media, rapida > 160 km/h). K > 0 subviraje, K < 0 sobreviraje. Objetivo +0.2 en lentas (barras) y +0.4 en
  rápidas (alerón). Barra delantera más dura sube K, trasera más dura lo baja, más alerón sube K en rápidas.
- Inputs (pedal crudo, sin ayudas): inercia_s = sin pedales, ambos_pedales_s, a_fondo_pct, levantadas de
  acelerador, correcciones de volante dentro de una curva, ataque_freno_s (hasta la presión máxima), liberacion_s
  (trail braking), acelera_agregando_volante_s. Compara la última vuelta con la mejor; por curva, usa
  inputs_por_curva_vs_tu_mejor_paso.
- Chasis: usa ajustes_chasis_recomendados si vienen. Barras = balance en curvas lentas/medias; alerón = balance
  en curvas rápidas y velocidad punta; spring perch = cross weight (simetría izquierda/derecha) y rake.
- eventos_por_sentido_y_velocidad cuenta derrapes y subviraje por sentido (izq/der) y por velocidad de curva
  (lenta < 100, media < 160, rapida). Entry = frenando, mid = en apoyo, exit = acelerando."""


def credentials_available() -> bool:
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return True
    return os.path.isdir(os.path.join(os.path.expanduser("~"), ".config", "anthropic"))


class ClaudeInterpreter:
    def __init__(self, model: str = DEFAULT_MODEL, effort: str = "low"):
        self.model = model
        self.effort = effort
        self.client = anthropic.Anthropic(max_retries=1, timeout=20.0)
        self.tokens_in = 0
        self.tokens_out = 0

    def answer(self, question: str, snapshot: dict) -> Optional[str]:
        """Respuesta para la radio, o None si no se pudo (el ingeniero usa sus reglas)"""
        data = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"), default=str)
        try:
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=2048,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",                  # si se rechaza, otro modelo responde en la misma llamada
                output_config={"effort": self.effort},
                system=SYSTEM,
                messages=[{"role": "user", "content": f"Datos:\n{data}\n\nPregunta del piloto: {question}"}],
            )
        except anthropic.AuthenticationError:
            print("[claude] credencial inválida: revisa ANTHROPIC_API_KEY", flush=True)
            return None
        except anthropic.RateLimitError:
            print("[claude] límite de uso alcanzado, respondo con reglas locales", flush=True)
            return None
        except anthropic.APIStatusError as e:
            print(f"[claude] error de la API ({e.status_code}), respondo con reglas locales", flush=True)
            return None
        except anthropic.APIConnectionError:
            print("[claude] sin conexión, respondo con reglas locales", flush=True)
            return None
        self.tokens_in += response.usage.input_tokens
        self.tokens_out += response.usage.output_tokens
        print(f"[claude] {response.usage.input_tokens} tokens de entrada, {response.usage.output_tokens} de salida "
              f"(sesión: {self.tokens_in} / {self.tokens_out})", flush=True)
        if response.stop_reason == "refusal":
            return None
        text = " ".join(b.text for b in response.content if b.type == "text").strip()
        return text or None



class ClaudeCodeInterpreter:
    """Pregunta a Claude Code en modo no interactivo (`claude -p`) con tu suscripción de Claude"""

    def __init__(self, model: Optional[str] = None, effort: str = "low", timeout: float = 30.0):
        self.exe = shutil.which("claude")
        self.model = model
        self.effort = effort
        self.timeout = timeout
        # Carpeta vacía: que no cargue CLAUDE.md ni archivos de ningún proyecto
        self.cwd = os.path.join(tempfile.gettempdir(), "iracing_engineer_claude")
        os.makedirs(self.cwd, exist_ok=True)

    @staticmethod
    def available() -> bool:
        return shutil.which("claude") is not None

    def answer(self, question: str, snapshot: dict) -> Optional[str]:
        data = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"), default=str)
        cmd = [self.exe, "-p", "--tools", "", "--no-session-persistence", "--output-format", "text",
               "--strict-mcp-config", "--effort", self.effort, "--system-prompt", SYSTEM]
        if self.model:
            cmd += ["--model", self.model]
        env = dict(os.environ)
        env.pop("ANTHROPIC_API_KEY", None)        # usar la suscripción, nunca una clave de API con cobro
        t0 = time.time()
        try:
            out = subprocess.run(cmd, input=f"Datos:\n{data}\n\nPregunta del piloto: {question}",
                                 capture_output=True, text=True, encoding="utf-8", timeout=self.timeout,
                                 cwd=self.cwd, env=env,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))  # sin ventanas
        except subprocess.TimeoutExpired:
            print(f"[claude] no respondió en {self.timeout:.0f} s, respondo con reglas locales", flush=True)
            return None
        except OSError as e:
            print(f"[claude] no pude ejecutar Claude Code ({e})", flush=True)
            return None
        text = (out.stdout or "").strip()
        if out.returncode != 0 or not text:
            err = (out.stderr or out.stdout or "").strip().splitlines()[-1:] or [""]
            print(f"[claude] Claude Code falló: {err[0][:150]}", flush=True)
            return None
        print(f"[claude] respuesta en {time.time() - t0:.1f} s (suscripción)", flush=True)
        return text


class ClaudeCodeSession:
    """
    Sesión de Claude Code abierta todo el rato (stream-json): sin el arranque de ~2 s por pregunta
    y entregando cada frase apenas Claude la escribe. Usa tu suscripción, sin costo extra.
    Se reinicia cada `max_turns` preguntas para que el historial no crezca.
    """

    def __init__(self, model: Optional[str] = "haiku", effort: str = "low", timeout: float = 20.0,
                 max_turns: int = 15):
        import queue as _queue
        import threading as _threading
        self._queue_mod, self._threading = _queue, _threading
        self.exe = shutil.which("claude")
        self.model, self.effort, self.timeout, self.max_turns = model, effort, timeout, max_turns
        self.cwd = os.path.join(tempfile.gettempdir(), "iracing_engineer_claude")
        os.makedirs(self.cwd, exist_ok=True)
        self.proc = None
        self.events = None
        self.turns = 0
        self.lock = _threading.Lock()
        self._start()

    @staticmethod
    def available() -> bool:
        return shutil.which("claude") is not None

    def _start(self):
        cmd = [self.exe, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
               "--include-partial-messages", "--tools", "", "--strict-mcp-config", "--no-session-persistence",
               "--effort", self.effort,
               "--system-prompt", SYSTEM + "\n- Cada mensaje trae los datos actuales: usa solo los del último mensaje."]
        if self.model:
            cmd += ["--model", self.model]
        env = dict(os.environ)
        env.pop("ANTHROPIC_API_KEY", None)        # siempre la suscripción, nunca una clave con cobro
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, text=True, encoding="utf-8", cwd=self.cwd,
                                     env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.events = self._queue_mod.Queue()
        proc, events = self.proc, self.events

        def reader():
            for line in proc.stdout:
                try:
                    events.put(json.loads(line))
                except ValueError:
                    continue
            events.put(None)   # proceso terminado
        self._threading.Thread(target=reader, daemon=True).start()
        self.turns = 0

    def close(self):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.stdin.close()
                self.proc.wait(timeout=3)
            except Exception:
                self.proc.kill()

    def answer(self, question: str, snapshot: dict, on_sentence=None) -> Optional[str]:
        """Devuelve la respuesta completa; on_sentence(frase) se llama con cada frase terminada"""
        with self.lock:
            if self.proc is None or self.proc.poll() is not None or self.turns >= self.max_turns:
                self.close()
                self._start()
            data = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"), default=str)
            msg = {"type": "user", "message": {"role": "user",
                                               "content": f"Datos:\n{data}\n\nPregunta del piloto: {question}"}}
            t0 = time.time()
            try:
                self.proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
                self.proc.stdin.flush()
            except OSError:
                self.proc = None
                return None
            self.turns += 1
            text, pending = "", ""
            while True:
                try:
                    ev = self.events.get(timeout=max(0.1, self.timeout - (time.time() - t0)))
                except self._queue_mod.Empty:
                    print(f"[claude] sin respuesta en {self.timeout:.0f} s, reinicio la sesión", flush=True)
                    self.close()
                    self.proc = None
                    return None
                if ev is None:
                    print("[claude] la sesión de Claude Code se cerró, la reabro en la próxima pregunta", flush=True)
                    self.proc = None
                    return text.strip() or None
                kind = ev.get("type")
                if kind == "stream_event":
                    delta = ev.get("event", {}).get("delta", {})
                    if delta.get("type") == "text_delta":
                        text += delta["text"]
                        pending += delta["text"]
                        # Entregar cada frase completa apenas termina
                        while on_sentence:
                            m = re.search(r"[.!?](\s|$)", pending)
                            if not m or m.end() == len(pending) and not pending.endswith((" ", "\n")):
                                break
                            sentence, pending = pending[:m.end()].strip(), pending[m.end():]
                            if sentence:
                                on_sentence(sentence)
                elif kind == "result":
                    if on_sentence and pending.strip():
                        on_sentence(pending.strip())
                    print(f"[claude] respuesta en {time.time() - t0:.1f} s (suscripción, sesión abierta)", flush=True)
                    return text.strip() or None
