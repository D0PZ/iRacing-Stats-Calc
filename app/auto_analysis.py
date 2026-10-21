"""
Análisis automático: cuando termina una sesión (archivo .ibt cerrado), lo analiza solo,
guarda el reporte en logs/ y actualiza las referencias y límites de la pista.
"""

import json
import os
import threading
import time
from typing import Callable, Optional

from app.ibt_reader import telemetry_dir

STATE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "analyzed_ibt.json")
LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
SETTLE_SECONDS = 20      # el archivo no cambia hace 20 s: iRacing terminó de escribirlo
LOOKBACK_SECONDS = 3600  # al arrancar, solo sesiones de la última hora


class AutoAnalyzer:
    def __init__(self, on_done: Callable[[object, str], None], folder: Optional[str] = None,
                 state_path: str = STATE_PATH, log_dir: str = LOG_DIR):
        self.on_done = on_done
        self.folder = folder or telemetry_dir()
        self.state_path = state_path
        self.log_dir = log_dir
        self.since = time.time() - LOOKBACK_SECONDS
        self.busy = False
        self._state = self._load()

    def _load(self) -> dict:
        try:
            with open(self.state_path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save(self):
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump(self._state, f)

    def pending(self) -> Optional[str]:
        """El .ibt más nuevo ya cerrado y sin analizar"""
        if not os.path.isdir(self.folder):
            return None
        now = time.time()
        best = None
        for name in os.listdir(self.folder):
            if not name.endswith(".ibt"):
                continue
            path = os.path.join(self.folder, name)
            try:
                st = os.stat(path)
            except OSError:
                continue
            if st.st_mtime < self.since or now - st.st_mtime < SETTLE_SECONDS:
                continue
            if self._state.get(path) == [st.st_size, int(st.st_mtime)]:
                continue
            if best is None or st.st_mtime > best[0]:
                best = (st.st_mtime, path)
        return best[1] if best else None

    def poll(self, can_run: bool, sync: bool = False) -> bool:
        """Lanza el análisis si hay una sesión nueva y no estás en pista. Devuelve True si arrancó."""
        if self.busy or not can_run:
            return False
        path = self.pending()
        if not path:
            return False
        self.busy = True
        if sync:
            self._run(path)
        else:
            threading.Thread(target=self._run, args=(path,), daemon=True).start()
        return True

    def _run(self, path: str):
        from app.telemetry_analyzer import TelemetryAnalyzer, format_report, group_session_files
        try:
            files = group_session_files(path)
            for p in files:  # marcar antes, para no reintentar en bucle si falla
                st = os.stat(p)
                self._state[p] = [st.st_size, int(st.st_mtime)]
            self._save()
            res = TelemetryAnalyzer(files).analyze()
            os.makedirs(self.log_dir, exist_ok=True)
            report = os.path.join(self.log_dir, time.strftime("analisis_%Y%m%d_%H%M%S.txt"))
            with open(report, "w", encoding="utf-8") as f:
                f.write("Archivos: " + ", ".join(os.path.basename(p) for p in files) + "\n\n")
                f.write(format_report(res))
            self.on_done(res, report)
        except Exception as e:
            print(f"[análisis] no se pudo analizar {os.path.basename(path)}: {e}", flush=True)
        finally:
            self.busy = False


def spoken_summary(res) -> str:
    """Resumen corto para la radio"""
    from app.telemetry_analyzer import fmt_lap
    if not res.best_lap:
        return "Analicé la sesión, pero no hubo vueltas completas válidas."
    parts = [f"Análisis de la sesión listo. Mejor vuelta {fmt_lap(res.best_lap.time)}"]
    gap = res.best_lap.time - res.theoretical_best
    if gap > 0.1:
        parts.append(f"tu vuelta ideal es {fmt_lap(res.theoretical_best)}, {gap:.1f} segundos por ganar")
    top = [r for r in res.recommendations if r.priority == 1][:2]
    msg = ", ".join(parts) + "."
    if top:
        titles = [(r.title[0].lower() + r.title[1:]).replace(" L ", " litros ") for r in top]
        msg += " Lo más importante: " + "; ".join(titles) + "."
    return msg
