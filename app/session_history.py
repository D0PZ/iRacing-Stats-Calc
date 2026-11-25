"""
Historial de sesiones armado solo desde la telemetría (.ibt) que iRacing deja en Documentos.

Cada evento (SubSessionID) queda en el mismo formato de resultados de iRacing que usa StatsCalculator.
El YAML del .ibt es una foto del inicio de la grabación, así que el resultado de la carrera se toma de los
canales (posición, incidentes y vueltas en el último instante de la sesión de carrera).
Los resúmenes por archivo se guardan en data/session_history.json: el historial sigue aunque borres los .ibt.
"""

import json
import math
import os
import re
import threading
import time
from datetime import datetime
from typing import Dict, List, Optional

import numpy as np

from app.ibt_reader import IBTFile, telemetry_dir
from app.paths import data_dir
from app.util import atomic_write_json

CACHE_PATH = data_dir("session_history.json")
CACHE_VERSION = 1
SETTLE_SECONDS = 20       # archivo que iRacing todavía está escribiendo
MIN_ANALYSIS_TICKS = 60 * 60  # menos de un minuto grabado no sirve para analizar

_lock = threading.Lock()
_NAME_TIME = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}-\d{2}-\d{2})\.ibt$")


def strength_of_field(iratings: List[int]) -> Optional[int]:
    """SOF con la fórmula de iRacing: promedio exponencial de los iRating"""
    irs = [ir for ir in iratings if ir and ir > 0]
    if not irs:
        return None
    br = 1600 / math.log(2)
    return round(br * math.log(len(irs) / sum(math.exp(-ir / br) for ir in irs)))


def _start_time(path: str) -> str:
    m = _NAME_TIME.search(os.path.basename(path))
    if m:
        return datetime.strptime(m.group(1), "%Y-%m-%d %H-%M-%S").isoformat()
    return datetime.fromtimestamp(os.path.getmtime(path)).replace(microsecond=0).isoformat()


def _grid(si: dict, me) -> Optional[list]:
    """[posición, posición en clase] de largada (base 1), según la clasificación"""
    for r in (si.get("QualifyResultsInfo") or {}).get("Results") or []:
        if r.get("CarIdx") == me:
            return [r["Position"] + 1, r.get("ClassPosition", r["Position"]) + 1]
    for s in (si.get("SessionInfo") or {}).get("Sessions") or []:
        if "Qualify" not in str(s.get("SessionType")):
            continue
        for r in s.get("ResultsPositions") or []:
            if r.get("CarIdx") == me:
                return [r["Position"], r.get("ClassPosition", r["Position"] - 1) + 1]
    return None


def summarize_file(path: str) -> dict:
    """Lo que importa de un .ibt para el historial (sin guardar la telemetría)"""
    f = IBTFile(path)
    si = f.session_info
    w = si.get("WeekendInfo") or {}
    d = si.get("DriverInfo") or {}
    me = d.get("DriverCarIdx")
    drivers = d.get("Drivers") or []
    mine = next((x for x in drivers if x.get("CarIdx") == me), {})
    field = [x.get("IRating") for x in drivers
             if not x.get("CarIsPaceCar") and not x.get("IsSpectator") and not x.get("CarIsAI")]
    types = {s.get("SessionNum"): s.get("SessionType")
             for s in (si.get("SessionInfo") or {}).get("Sessions") or []}

    sessions = []
    num = f.get("SessionNum")
    if num is not None and len(num):
        zeros = np.zeros(len(num), dtype=np.int32)
        state = f.get("SessionState", zeros)
        pos = f.get("PlayerCarPosition", zeros)
        cpos = f.get("PlayerCarClassPosition", pos)
        inc = f.get("PlayerCarMyIncidentCount", zeros)
        laps = f.get("LapCompleted", zeros)
        for n in np.unique(num):
            idx = np.flatnonzero(num == n)
            last = idx[-1]
            sessions.append({
                "num": int(n), "type": types.get(int(n)), "ticks": int(len(idx)),
                "position": int(pos[last]), "class_position": int(cpos[last]),
                "incidents": int(inc[last]), "laps": max(0, int(laps[idx].max())),
                "finished": bool((state[idx] >= 5).any()),  # bandera a cuadros o vuelta de enfriamiento
            })

    return {
        "start": _start_time(path),
        "subsession": w.get("SubSessionID") or 0,
        "event": w.get("EventType") or "",
        "series_id": w.get("SeriesID") or 0,
        "official": bool(w.get("Official")),
        "track": w.get("TrackDisplayName") or w.get("TrackName") or "",
        "config": w.get("TrackConfigName") or "",
        "car": mine.get("CarScreenName") or "",
        "car_short": mine.get("CarScreenNameShort") or mine.get("CarScreenName") or "",
        "irating": mine.get("IRating"),
        "sof": strength_of_field(field),
        "grid": _grid(si, me),
        "sessions": sessions,
    }


def _series_name(s: dict) -> str:
    # El .ibt no trae el nombre de la serie, solo su ID
    return f"{s['car_short']} · serie {s['series_id']}" if s["series_id"] else f"{s['car_short']} · hosted"


def build_sessions(summaries: List[dict]) -> List[dict]:
    """Un resultado por evento, en el formato de la API de iRacing que lee StatsCalculator"""
    groups: Dict[object, List[dict]] = {}
    for s in sorted(summaries, key=lambda x: x["start"]):
        groups.setdefault(s["subsession"] or s["start"], []).append(s)

    out = []
    for files in groups.values():
        first = files[0]
        entry = {
            "subsession_id": first["subsession"],
            "event_type_name": first["event"],
            "start_time": first["start"],
            "series_name": _series_name(first),
            "official_session": first["official"],
            "track": {"track_name": first["track"], "config_name": first["config"]},
            "car_name": first["car"],
            "source": "ibt",
        }
        if first["event"] == "Race":
            races = [(f, s) for f in files for s in f["sessions"] if s["type"] == "Race"]
            if not races:
                continue  # hiciste práctica/clasificación pero no corriste la carrera
            race_file, final = races[-1]
            grid = next((f["grid"] for f in reversed(files) if f["grid"]), None)
            entry.update({
                "finish_position": final["position"],
                "finish_position_in_class": final["class_position"],
                "starting_position": grid[0] if grid else -1,
                "starting_position_in_class": grid[1] if grid else -1,
                "laps_complete": max(s["laps"] for _, s in races),
                "incidents": final["incidents"],
                "finished": any(s["finished"] for _, s in races),
                "event_strength_of_field": race_file["sof"] or -1,
                "oldi_rating": race_file["irating"],
            })
        else:
            last = {}
            for f in files:
                for s in f["sessions"]:
                    last[s["num"]] = s
            entry.update({
                "laps_complete": sum(s["laps"] for s in last.values()),
                "incidents": sum(s["incidents"] for s in last.values()),
                "event_strength_of_field": first["sof"] or -1,
                "oldi_rating": first["irating"],
            })
        out.append(entry)
    return out


def scan_files(folder: Optional[str] = None, cache_path: str = CACHE_PATH) -> Dict[str, dict]:
    """Resumen de cada .ibt (ruta -> resumen); solo se leen los archivos nuevos o que cambiaron"""
    folder = folder or telemetry_dir()
    with _lock:
        try:
            with open(cache_path, encoding="utf-8") as fh:
                cache = json.load(fh)
            if cache.get("version") != CACHE_VERSION:
                cache = {}
        except (OSError, ValueError):
            cache = {}
        files = cache.get("files", {})
        changed = False
        now = time.time()
        names = os.listdir(folder) if os.path.isdir(folder) else []
        for name in names:
            if not name.endswith(".ibt"):
                continue
            path = os.path.join(folder, name)
            try:
                st = os.stat(path)
            except OSError:
                continue
            key = [st.st_size, int(st.st_mtime)]
            if now - st.st_mtime < SETTLE_SECONDS or files.get(path, {}).get("key") == key:
                continue
            try:
                summary = summarize_file(path)
            except Exception as e:
                print(f"[historial] no se pudo leer {name}: {e}", flush=True)
                summary = None  # no reintentar en cada pasada mientras no cambie
            files[path] = {"key": key, "summary": summary}
            changed = True
        if changed:
            atomic_write_json(cache_path, {"version": CACHE_VERSION, "files": files})
        return {p: v["summary"] for p, v in files.items() if v.get("summary")}


def load_sessions(folder: Optional[str] = None, cache_path: str = CACHE_PATH) -> List[dict]:
    return build_sessions(list(scan_files(folder, cache_path).values()))


def telemetry_sessions(summaries: Dict[str, dict]) -> List[dict]:
    """Sesiones con telemetría para analizar, la más nueva primero: {label, path}"""
    groups: Dict[tuple, dict] = {}
    for path, s in summaries.items():
        if not s["sessions"] or not os.path.exists(path):
            continue
        last = s["sessions"][-1]
        key = (s["subsession"] or s["start"], last["num"])
        g = groups.setdefault(key, {"first": s["start"], "start": s["start"], "path": path, "ticks": 0,
                                    "summary": s, "last": last})
        g["ticks"] += sum(x["ticks"] for x in s["sessions"])
        g["first"] = min(g["first"], s["start"])
        if s["start"] > g["start"]:  # el archivo más nuevo de la sesión (group_session_files junta el resto)
            g.update(start=s["start"], path=path, summary=s, last=last)
    out = []
    for g in sorted(groups.values(), key=lambda x: x["start"], reverse=True):
        if g["ticks"] < MIN_ANALYSIS_TICKS:
            continue
        s, last = g["summary"], g["last"]
        when = datetime.fromisoformat(g["first"]).strftime("%d/%m %H:%M")
        kind = {"Race": "Carrera", "Practice": "Práctica", "Lone Qualify": "Clasificación",
                "Open Qualify": "Clasificación", "Offline Testing": "Test"}.get(last["type"], last["type"] or "Sesión")
        label = f"{when} · {s['track']} · {kind}"
        if last["type"] == "Race" and last["position"] > 0:
            label += f" (P{last['class_position'] or last['position']})"
        out.append({"label": label, "path": g["path"]})
    return out
