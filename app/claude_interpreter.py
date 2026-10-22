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
- Español de Chile, directo y breve: máximo 3 frases cortas (unas 40 palabras), se lee en voz alta.
- Sin símbolos ni markdown. Escribe las unidades con palabras (litros, segundos, grados, kilopascales).
- Las temperaturas de neumáticos son de la última entrada a pits (exterior, centro, interior); iRacing no las da en pista.
- Ajustes de neumáticos: el objetivo es interior sobre exterior según tire_targets y centro igual al promedio de los bordes."""


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
