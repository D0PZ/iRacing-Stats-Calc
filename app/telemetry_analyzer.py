"""
Análisis de telemetría (.ibt) con recomendaciones de setup y pilotaje
Divide la sesión en vueltas, detecta las curvas a partir de la velocidad y
compara cada vuelta contra tu mejor, curva por curva.
"""

import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np

from app.ibt_reader import IBTFile
from app.references import save_references
from app.track_limits import (TrackLimitTracker, gps_lateral_offsets, replay_files,
                              save_tracker)

G = 9.81
KMH = 3.6


@dataclass
class Lap:
    number: int
    time: float
    file_idx: int
    i0: int
    i1: int
    valid: bool
    fuel_used: float
    offtrack: bool
    pit: bool
    incidents: int = 0


@dataclass
class Recommendation:
    category: str      # Setup, Neumáticos, Frenos, Pilotaje, Combustible, Consistencia
    priority: int      # 1 = alta, 2 = media, 3 = baja
    title: str
    detail: str


@dataclass
class CornerResult:
    number: int
    apex_m: float
    best_time_lap: int = 0
    min_speed_best: float = 0.0
    min_speed_avg: float = 0.0
    brake_m_best: Optional[float] = None
    brake_m_avg: Optional[float] = None
    exit_speed_best: float = 0.0
    exit_speed_avg: float = 0.0
    time_loss_avg: float = 0.0       # vs mejor paso por esa curva
    understeer: float = 0.0          # 1 = neutro, < 1 subviraje
    oversteer_events: float = 0.0    # por vuelta
    wheelspin: float = 0.0           # % de muestras con patinaje en tracción
    lockups: float = 0.0             # % de muestras con bloqueo al frenar
    abs_pct: float = 0.0
    coast_s: float = 0.0
    understeer_deg: float = 0.0      # ángulo de subviraje (+) / sobreviraje (-) en grados
    offtracks: int = 0               # salidas de pista en esta curva (todas las vueltas)
    gear: int = 0
    kind: str = ""                   # lenta / media / rápida
    brake_at_m: Optional[float] = None       # metro de pista donde frenas en tu mejor paso
    brake_peak_best: float = 0.0             # presión máxima de freno (0-100%) en tu mejor paso
    brake_peak_avg: float = 0.0
    throttle_m_best: Optional[float] = None  # metros después del vértice hasta ir a fondo (mejor paso)
    throttle_m_avg: Optional[float] = None
    brake_efficiency: Optional[float] = None   # distancia mínima física / distancia real (mejor paso)
    brake_potential_m: float = 0.0            # metros que podrías frenar más tarde (al 90 % de eficiencia)
    brake_gain_s: float = 0.0
    grip_usage: Optional[float] = None         # uso medio del agarre demostrado a esa velocidad


@dataclass
class AnalysisResult:
    car: str = ""
    track: str = ""
    track_len: float = 0.0
    session_type: str = ""
    fixed_setup: bool = False
    laps: List[Lap] = field(default_factory=list)
    best_lap: Optional[Lap] = None
    theoretical_best: float = 0.0
    corners: List[CornerResult] = field(default_factory=list)
    recommendations: List[Recommendation] = field(default_factory=list)
    tires: Dict[str, Dict[str, float]] = field(default_factory=dict)
    fuel_per_lap: float = 0.0
    setup: Dict[str, str] = field(default_factory=dict)
    limit_zones: List[dict] = field(default_factory=list)
    gradients: Dict[str, dict] = field(default_factory=dict)   # K por banda de velocidad
    plan_lines: List[str] = field(default_factory=list)       # plan de qualy y carrera
    pad_plan: Optional[dict] = None
    brake_profile: Optional[dict] = None
    # Trazas para gráficos (eje distancia en metros)
    dist: Optional[np.ndarray] = None
    traces: Dict[str, Dict[str, np.ndarray]] = field(default_factory=dict)


class TelemetryAnalyzer:
    """Analiza uno o varios .ibt de la misma sesión/auto/pista"""

    def __init__(self, paths: List[str]):
        if isinstance(paths, str):
            paths = [paths]
        self.paths = paths
        self.files = [IBTFile(p) for p in paths]
        self.info = self.files[-1].session_info
        self.result = AnalysisResult()

    # ------------------------------------------------------------ helpers
    def _ch(self, f: IBTFile, name: str, i0: int, i1: int, default=0.0) -> np.ndarray:
        a = f.get(name)
        if a is None:
            return np.full(i1 - i0, default, dtype=float)
        return a[i0:i1].astype(float)

    @staticmethod
    def _num(value) -> Optional[float]:
        m = re.search(r"-?\d+(?:\.\d+)?", str(value))
        return float(m.group()) if m else None

    # ------------------------------------------------------------- vueltas
    def _split_laps(self):
        laps = []
        for fi, f in enumerate(self.files):
            lap = f.get("Lap")
            pct = f.get("LapDistPct")
            if lap is None or pct is None:
                continue
            cur_t = f.get("LapCurrentLapTime")
            on_pit = f.get("OnPitRoad")
            surface = f.get("PlayerTrackSurface")
            fuel = f.get("FuelLevel")
            inc = f.get("PlayerCarMyIncidentCount")
            on_track = f.get("IsOnTrack")
            change = np.flatnonzero(np.diff(lap)) + 1
            bounds = np.concatenate(([0], change, [len(lap)]))
            for a, b in zip(bounds[:-1], bounds[1:]):
                if b - a < f.tick_rate * 20:
                    continue
                if on_track is not None and not on_track[a:b].all():
                    continue
                if not (pct[a] < 0.03 and pct[b - 1] > 0.97):
                    continue  # vuelta incompleta (inicio/fin de grabación)
                t = float(cur_t[b - 1]) if cur_t is not None else (b - a) / f.tick_rate
                pit = bool(on_pit[a:b].any()) if on_pit is not None else False
                off = bool((surface[a:b] == 0).any()) if surface is not None else False
                fu = float(fuel[a] - fuel[b - 1]) if fuel is not None else 0.0
                incs = int(inc[b - 1] - inc[a]) if inc is not None else 0
                laps.append(Lap(int(lap[a]), t, fi, int(a), int(b), True, fu, off, pit, incs))
        # Eliminar vueltas duplicadas (archivos solapados): quedarse con la primera
        seen, unique = set(), []
        for l in laps:
            key = (l.number, round(l.time, 2))
            if key not in seen:
                seen.add(key)
                unique.append(l)
        clean = [l.time for l in unique if not l.pit]
        best = min(clean) if clean else min((l.time for l in unique), default=0)
        for l in unique:
            # Una salida leve (límites de pista, 1x) sigue siendo vuelta de ritmo; un trompo no
            l.valid = (not l.pit) and l.time < best * 1.04 and l.incidents <= 1
        # La primera vuelta de carrera (largada) no es representativa
        if self.result.session_type.lower().startswith("race") and unique:
            first = min(l.number for l in unique)
            for l in unique:
                if l.number == first and l.number <= 1:
                    l.valid = False
        self.result.laps = unique
        valid = [l for l in unique if l.valid]
        self.result.best_lap = min(valid, key=lambda l: l.time) if valid else None

    # ------------------------------------------------- remuestreo por metro
    def _resample(self, lap: Lap, grid: np.ndarray) -> Dict[str, np.ndarray]:
        f = self.files[lap.file_idx]
        i0, i1 = lap.i0, lap.i1
        dist = np.maximum.accumulate(self._ch(f, "LapDist", i0, i1))
        if dist.max() < grid[-1] * 0.9:  # LapDist poco fiable: usar porcentaje
            dist = np.maximum.accumulate(self._ch(f, "LapDistPct", i0, i1)) * self.result.track_len
        # eliminar distancias repetidas para interp
        keep = np.concatenate(([True], np.diff(dist) > 1e-4))
        d = dist[keep]

        def r(name, default=0.0):
            return np.interp(grid, d, self._ch(f, name, i0, i1, default)[keep])

        out = {name: r(name) for name in (
            "PlayerTrackSurface",
            "Speed", "Throttle", "Brake", "SteeringWheelAngle", "YawRate", "LatAccel",
            "LongAccel", "RPM", "Gear", "LFspeed", "RFspeed", "LRspeed", "RRspeed",
            "BrakeABSactive", "LFrideHeight", "RFrideHeight", "LRrideHeight", "RRrideHeight")}
        t = self._ch(f, "SessionTime", i0, i1)
        out["t"] = np.interp(grid, d, t[keep] - t[0])
        return out

    # ------------------------------------------------------------- curvas
    def _detect_corners(self, speed: np.ndarray, step: float) -> List[int]:
        win = max(int(25 / step), 1)
        smooth = np.convolve(np.pad(speed, win, mode="wrap"), np.ones(win) / win, mode="same")[win:-win]
        n = len(smooth)
        look_min = int(120 / step)
        look_max = int(500 / step)
        apexes = []
        for i in range(n):
            lo, hi = i - look_min, i + look_min + 1
            idx = np.arange(lo, hi) % n
            if smooth[i] > smooth[idx].min() + 1e-6:
                continue
            before = smooth[np.arange(i - look_max, i) % n].max()
            if before - smooth[i] > 15 / KMH:  # frenada de 15 km/h o más
                if not apexes or i - apexes[-1] > look_min:
                    apexes.append(i)
        return apexes

    # --------------------------------------------------------- análisis
    def analyze(self) -> AnalysisResult:
        res = self.result
        info = self.info
        wk = info.get("WeekendInfo", {}) or {}
        res.track = wk.get("TrackDisplayName", "")
        res.track_len = (self._num(wk.get("TrackLength", "4 km")) or 4.0) * 1000
        res.fixed_setup = bool((wk.get("WeekendOptions") or {}).get("IsFixedSetup", 0))
        drv = info.get("DriverInfo", {}) or {}
        me = drv.get("DriverCarIdx")
        for d in drv.get("Drivers", []) or []:
            if d.get("CarIdx") == me:
                res.car = d.get("CarScreenName", "")
        try:
            num = int(self.files[-1].get("SessionNum")[-1])
            res.session_type = info["SessionInfo"]["Sessions"][num]["SessionType"]
        except Exception:
            res.session_type = ""
        self._read_setup()
        self._split_laps()
        if not res.best_lap:
            res.recommendations.append(Recommendation(
                "General", 1, "No hay vueltas válidas",
                "Necesito al menos una vuelta completa, sin pits ni salidas de pista, para analizar."))
            return res

        step = 2.0
        grid = np.arange(0, res.track_len, step)
        res.dist = grid
        valid = [l for l in res.laps if l.valid]
        data = {l.number: self._resample(l, grid) for l in valid}
        best = data[res.best_lap.number]
        res.traces["Mejor vuelta"] = best
        if len(valid) > 1:
            median_lap = sorted(valid, key=lambda l: l.time)[len(valid) // 2]
            res.traces[f"Vuelta típica (V{median_lap.number})"] = data[median_lap.number]

        apexes = self._detect_corners(best["Speed"], step)
        self._analyze_corners(apexes, data, grid, step)
        try:
            self._physics(data, grid, step, apexes, valid)
        except Exception as e:
            print(f"[física] no se pudo calcular: {e}")
        self._tires(valid)
        try:
            self._track_limits(valid)
        except Exception as e:  # GPS ausente o datos raros: el resto del análisis sigue
            print(f"[límites] no se pudo analizar: {e}")
        self._fuel()
        try:
            self._session_plan([l for l in self.result.laps if l.valid])
        except Exception as e:
            print(f"[plan] no se pudo calcular: {e}")
        self._driving_recs(data, valid, step)
        self._setup_recs()
        self._consistency_recs(valid)
        self._limit_recs()
        res.recommendations.sort(key=lambda r: r.priority)
        try:
            save_references(res.track, res.car, res.track_len, res.corners, res.fuel_per_lap, res.plan_lines)
        except Exception:
            pass
        return res

    def _read_setup(self):
        setup = self.info.get("CarSetup", {}) or {}

        def walk(d, path=()):
            if isinstance(d, dict):
                for k, v in d.items():
                    yield from walk(v, path + (k,))
            else:
                yield path, d
        for path, v in walk(setup):
            if path and path[0] == "UpdateCount":
                continue
            self.result.setup[" > ".join(path)] = str(v)

    def _setup_value(self, *keys) -> Optional[str]:
        for k, v in self.result.setup.items():
            if all(x.lower() in k.lower() for x in keys):
                return v
        return None

    def _analyze_corners(self, apexes, data, grid, step):
        res = self.result
        n = len(grid)
        if not apexes:
            return
        # Ventana de cada curva: punto medio entre vértices
        bounds = []
        for k, a in enumerate(apexes):
            prev_a = apexes[k - 1] - (n if k == 0 else 0)
            next_a = apexes[(k + 1) % len(apexes)] + (n if k == len(apexes) - 1 else 0)
            bounds.append(((a + prev_a) // 2, (a + next_a) // 2))

        # Relación de dirección estimada en giros suaves a baja carga (casi sin deslizamiento):
        # ángulo de volante / ángulo de Ackermann (L * yaw / v)
        L = 2.46
        ratios = []
        for d in data.values():
            v, st, yr = d["Speed"], d["SteeringWheelAngle"], d["YawRate"]
            m = (v > 10) & (v < 35) & (np.abs(st) > 0.1) & (np.abs(d["LatAccel"]) < 0.35 * G) & (st * yr > 0)
            ratios.append(np.abs(st[m]) / (L * np.abs(yr[m]) / v[m]))
        ratios = np.concatenate(ratios) if ratios else np.array([])
        steer_ratio = float(np.median(ratios)) if len(ratios) > 50 else None
        neutral = steer_ratio

        for k, (apex, (s, e)) in enumerate(zip(apexes, bounds)):
            idx = np.arange(s, e) % n
            apex_rel = apex - s
            cr = CornerResult(number=k + 1, apex_m=float(grid[apex]))
            times, mins, exits, brakes, peaks, fulls = {}, {}, {}, {}, {}, {}
            under, over, spin, lock, absr, coast, gears = [], [], [], [], [], [], []
            for num, d in data.items():
                t = d["t"][idx]
                seg_t = t[-1] - t[0]
                if seg_t <= 0:  # cruza la meta: sumar el tramo de vuelta completa
                    seg_t += d["t"][-1]
                times[num] = seg_t
                v = d["Speed"][idx]
                mins[num] = v.min() * KMH
                exits[num] = v[-1] * KMH
                # Frenada de esta curva = última aplicación de freno antes del vértice (hasta 400 m atrás,
                # aunque empiece antes de la ventana de la curva, p. ej. en curvas enlazadas)
                prev_apex = apexes[k - 1] - (n if k == 0 else 0)
                back = min(int(400 / step), apex - prev_apex - 1)   # nunca antes del vértice anterior
                br = d["Brake"][np.arange(apex - back, apex + 1) % n]
                # Separar aplicaciones de freno y quedarse con la principal (mayor presión; empate: la última)
                pressed = br > 0.05
                edges = np.diff(np.concatenate(([0], pressed.astype(int), [0])))
                starts, ends = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)
                events = [(float(br[a:b].max()), a) for a, b in zip(starts, ends) if br[a:b].max() > 0.1]
                if events:
                    top = max(p for p, _ in events)
                    peak, start = max((e for e in events if e[0] >= top - 0.05), key=lambda e: e[1])
                    brakes[num] = (back - start) * step  # metros antes del vértice
                    peaks[num] = peak * 100
                # Vuelta al acelerador a fondo después del vértice (sostenido 20 m)
                thr_after = d["Throttle"][idx[apex_rel:]]
                hold = max(int(20 / step), 1)
                full = np.flatnonzero(np.convolve(thr_after > 0.95, np.ones(hold), "valid") >= hold)
                if len(full):
                    fulls[num] = full[0] * step
                gears.append(d["Gear"][apex])
                # Balance: subviraje con carga lateral alta
                st, yr, lat = d["SteeringWheelAngle"][idx], d["YawRate"][idx], d["LatAccel"][idx]
                m = (v > 12) & (np.abs(lat) > 0.9 * G) & (np.abs(st) > 0.03) & (st * yr > 0)
                if steer_ratio and m.any():
                    road = np.abs(st[m]) / steer_ratio
                    kin = L * np.abs(yr[m]) / v[m]
                    under.append(float(np.median(np.degrees(road - kin))))
                # Sobreviraje: contravolante con giro fuerte
                cs = (v > 15) & (np.abs(yr) > 0.2) & (np.abs(st) > 0.06) & (st * yr < 0)
                over.append(int(np.sum(np.diff(cs.astype(int)) == 1)))
                # Patinaje (tracción trasera) y bloqueos
                front = (d["LFspeed"][idx] + d["RFspeed"][idx]) / 2
                rear = (d["LRspeed"][idx] + d["RRspeed"][idx]) / 2
                thr, brk = d["Throttle"][idx], d["Brake"][idx]
                drive = (thr > 0.3) & (v > 8) & (front > 5)
                if drive.any():
                    spin.append(np.mean(rear[drive] / front[drive] > 1.06) * 100)
                braking = (brk > 0.2) & (v > 10)
                if braking.any():
                    wheels = np.vstack([d[w][idx][braking] for w in ("LFspeed", "RFspeed", "LRspeed", "RRspeed")])
                    lock.append(np.mean((wheels.min(axis=0) / v[braking]) < 0.88) * 100)
                    absr.append(np.mean(d["BrakeABSactive"][idx][braking] > 0.5) * 100)
                coast.append(np.sum((thr < 0.05) & (brk < 0.05) & (v > 15)) * step / max(v.mean(), 1))

            best_num = min(times, key=times.get)
            cr.best_time_lap = best_num
            best_t = times[best_num]
            cr.time_loss_avg = float(np.mean([t - best_t for t in times.values()]))
            cr.min_speed_best = float(mins[best_num])
            cr.min_speed_avg = float(np.mean(list(mins.values())))
            cr.exit_speed_best = float(exits[best_num])
            cr.exit_speed_avg = float(np.mean(list(exits.values())))
            cr.brake_m_best = brakes.get(best_num)
            cr.brake_m_avg = float(np.mean(list(brakes.values()))) if brakes else None
            if cr.brake_m_best is not None:
                cr.brake_at_m = float((grid[apex] - cr.brake_m_best) % res.track_len)
            cr.brake_peak_best = peaks.get(best_num, 0.0)
            cr.brake_peak_avg = float(np.mean(list(peaks.values()))) if peaks else 0.0
            cr.throttle_m_best = fulls.get(best_num)
            cr.throttle_m_avg = float(np.mean(list(fulls.values()))) if fulls else None
            cr.understeer_deg = float(np.median(under)) if under else 0.0
            cr.understeer = 1.0
            cr.offtracks = int(sum(np.sum(np.diff((d["PlayerTrackSurface"][idx] < 0.5).astype(int)) == 1)
                                   for d in data.values()))
            cr.oversteer_events = float(np.mean(over)) if over else 0.0
            cr.wheelspin = float(np.mean(spin)) if spin else 0.0
            cr.lockups = float(np.mean(lock)) if lock else 0.0
            cr.abs_pct = float(np.mean(absr)) if absr else 0.0
            cr.coast_s = float(np.mean(coast)) if coast else 0.0
            cr.gear = int(round(np.median(gears))) if gears else 0
            cr.kind = "lenta" if cr.min_speed_best < 100 else "media" if cr.min_speed_best < 160 else "rápida"
            res.corners.append(cr)
        # Vuelta teórica = suma de los mejores pasos por curva
        best_lap_t = data[res.best_lap.number]["t"]
        gain = 0.0
        for k, (apex, (s, e)) in enumerate(zip(apexes, bounds)):
            idx = np.arange(s, e) % n
            seg_best_lap = best_lap_t[idx][-1] - best_lap_t[idx][0]
            if seg_best_lap <= 0:
                seg_best_lap += best_lap_t[-1]
            seg_min = min(
                (d["t"][idx][-1] - d["t"][idx][0]) + (d["t"][-1] if d["t"][idx][-1] - d["t"][idx][0] <= 0 else 0)
                for d in data.values())
            gain += seg_best_lap - seg_min
        res.theoretical_best = res.best_lap.time - gain

    # ------------------------------------------------------------- física
    def _physics(self, data, grid, step, apexes, valid):
        from app import vehicle_dynamics as vd
        res = self.result
        cat = lambda k: np.concatenate([d[k] for d in data.values()])          # noqa: E731
        v, ax, ay, brk = cat("Speed"), cat("LongAccel"), cat("LatAccel"), cat("Brake")
        env_v, env_a = vd.braking_envelope(v, ax, brk)
        gg_v, gg_g = vd.gg_envelope(v, ax, ay)
        lat_env = vd.lateral_envelope(v, ay)
        # K por banda con las muestras crudas (más resolución que la grilla por metro)
        raw = {k: [] for k in ("Speed", "SteeringWheelAngle", "YawRate", "LatAccel", "LongAccel")}
        for l in valid:
            f = self.files[l.file_idx]
            for k in raw:
                a = f.get(k)
                if a is not None:
                    raw[k].append(a[l.i0:l.i1].astype(float))
        if all(raw.values()):
            res.gradients = vd.understeer_by_band(*(np.concatenate(raw[k]) for k in raw))
        n = len(grid)
        for c, apex in zip(res.corners, apexes):
            d = data[c.best_time_lap]
            if c.brake_m_best and c.brake_m_best > 10 and c.brake_peak_best >= 30:   # no en toques de freno
                # Ventana desde 300 m antes del vértice, sobre la trayectoria real de tu mejor paso
                back = int(min(300, c.brake_m_best + 150) / step)
                idx = np.arange(apex - back, apex + 1) % n
                b_idx = back - int(c.brake_m_best / step)
                pot = vd.braking_potential(d["Speed"][idx], d["YawRate"][idx], b_idx, back, step,
                                           (env_v, env_a), lat_env)
                if pot:
                    c.brake_potential_m = max(0.0, pot["meters_later"])
                    c.brake_gain_s = pot["gain_s"] if pot["meters_later"] > 0 else 0.0
                    real = c.brake_m_best
                    c.brake_efficiency = round(max(0.0, min(1.0, (real - c.brake_potential_m) / real)), 2)
            idx = np.arange(apex - int(80 / step), apex + int(80 / step)) % n
            c.grip_usage = round(vd.grip_usage(d["Speed"][idx], d["LongAccel"][idx], d["LatAccel"][idx],
                                               gg_v, gg_g), 2)
        self._physics_recs()

    def _physics_recs(self):
        from app.balance_model import TARGET_K, describe_k
        R, res = self.result.recommendations, self.result
        brakes = sorted([c for c in res.corners if c.brake_gain_s >= 0.05], key=lambda c: -c.brake_gain_s)[:3]
        if brakes:
            parts = [f"C{c.number}: {c.brake_potential_m:.0f} m más tarde, hasta {c.brake_gain_s:.2f} s" for c in brakes]
            R.append(Recommendation(
                "Pilotaje", 1, "Puedes frenar más tarde (cálculo físico)",
                "Modelo físico sobre tu trayectoria (elipse de fricción con el 90 % de tu agarre demostrado, "
                "hasta tu misma velocidad de vértice): " + "; ".join(parts) + ". La ganancia es frenar más tarde y, "
                "sobre todo, soltar el freno de forma progresiva llevando más velocidad durante la frenada."))
        low = sorted([c for c in res.corners if c.grip_usage and c.grip_usage < 0.8], key=lambda c: c.grip_usage)[:3]
        if low:
            R.append(Recommendation(
                "Pilotaje", 2, "Agarre sin usar en curva",
                "Uso del agarre que ya demostraste a esa velocidad: " + "; ".join(
                    f"C{c.number} {c.grip_usage * 100:.0f} %" for c in low)
                + ". Ahí puedes llevar más velocidad al vértice o acelerar antes."))
        for band, name, tool in (("lenta", "curvas lentas", "barras"), ("rapida", "curvas rápidas", "alerón")):
            g = res.gradients.get(band)
            if g and abs(g["K"] - TARGET_K[band]) > 0.3:
                R.append(Recommendation(
                    "Setup", 2, f"Balance en {name}: {describe_k(g['K'])}",
                    f"Gradiente de subviraje K = {g['K']:+.2f} grados por g (objetivo {TARGET_K[band]:+.1f}); se corrige "
                    f"con {tool}. Pregúntale a la ingeniera por los clics exactos: los calcula con lo que aprende de "
                    f"tus salidas."))

    # ---------------------------------------------------- límites de pista
    def _corner_name(self, s: float) -> str:
        if not self.result.corners:
            return f"a {s:.0f} m"
        L = self.result.track_len
        c = min(self.result.corners, key=lambda c: abs((s - c.apex_m + L / 2) % L - L / 2))
        d = (s - c.apex_m + L / 2) % L - L / 2
        where = "la entrada" if d < -40 else "la salida" if d > 40 else "el vértice"
        return f"{where} de la C{c.number}"

    def _track_limits(self, valid: List[Lap]):
        res = self.result
        L = res.track_len
        good = {(l.file_idx, l.number) for l in res.laps if not l.pit}

        def laps_ok(fi):
            f = self.files[fi]
            lap = f.get("Lap")
            mask = np.array([(fi, int(n)) in good for n in lap])
            return mask & (f.get("Speed") > 15) & (f.get("PlayerTrackSurface") != 0)

        e = gps_lateral_offsets(self.files, laps_ok)
        if all(x is None for x in e):
            return
        # Pasada 1: aprender dónde están los límites (de tus salidas reales)
        learned, _ = replay_files(self.files, L, e)
        if not learned.zones:
            return
        # Pasada 2: medir cada vuelta contra el límite ya conocido
        fixed = TrackLimitTracker(L)
        fixed.zones = [dict(z, samples=[]) for z in learned.zones]
        _, exits = replay_files(self.files, L, e, tracker=fixed, learn_limits=False)
        for z in fixed.zones:
            rows = [x for x in exits if x[2] is z["s"] or abs(x[2] - z["s"]) < 1 and (x[0], x[1]) in good]
            rows = [x for x in rows if (x[0], x[1]) in good]
            if not rows:
                continue
            seen, laps = set(), []
            for fi, lap, _, margin, exit_kmh, off in rows:
                if lap in seen:
                    continue
                seen.add(lap)
                laps.append({"lap": lap, "margin": margin, "exit_kmh": exit_kmh, "off": off})
            res.limit_zones.append({
                "s": z["s"], "side": z["side"], "name": self._corner_name(z["s"]),
                "laps": laps, "gain": TrackLimitTracker.exit_gain(z)})
        # Guardar para el ingeniero en vivo (zonas + mapa de rumbo de la pista)
        try:
            save_tracker(res.track, learned)
        except Exception:
            pass

    def _limit_recs(self):
        R = self.result.recommendations
        for z in self.result.limit_zones:
            offs = [l for l in z["laps"] if l["off"]]
            clean = [l for l in z["laps"] if not l["off"]]
            side = "izquierdo" if z["side"] > 0 else "derecho"
            parts = [f"Te pasaste en {len(offs)} de {len(z['laps'])} vueltas"
                     + (f" (hasta {abs(min(l['margin'] for l in offs)) * 100:.0f} cm afuera)" if offs else "") + "."]
            if clean:
                lo = min(l["margin"] for l in clean)
                hi = max(l["margin"] for l in clean)
                best = max(clean, key=lambda l: l["exit_kmh"])
                parts.append(f"En las vueltas limpias dejaste entre {max(lo, 0) * 100:.0f} y {hi * 100:.0f} cm de margen. "
                             f"Tu mejor salida limpia fue con {max(best['margin'], 0) * 100:.0f} cm "
                             f"({best['exit_kmh']:.0f} km/h).")
                if offs:
                    off_exit = np.mean([l["exit_kmh"] for l in offs])
                    parts.append(f"Saliéndote no ganas: salida media {off_exit:.0f} km/h fuera vs "
                                 f"{np.mean([l['exit_kmh'] for l in clean]):.0f} km/h limpia.")
            if z["gain"] and z["gain"][0] > 1:
                parts.append(f"Cuando apuras el límite sin pasarte sales {z['gain'][0]:.0f} km/h más rápido.")
            target = 30 if offs else 15
            parts.append(f"Objetivo: dejar unos {target} cm de margen del lado {side}.")
            R.append(Recommendation(
                "Límites de pista", 1 if offs else 3,
                f"Límite en {z['name']} ({z['s']:.0f} m, lado {side})", " ".join(parts)))

    # -------------------------------------------------------- neumáticos
    def _tires(self, valid: List[Lap]):
        res = self.result
        corners = {"LF": "Delantera izq.", "RF": "Delantera der.", "LR": "Trasera izq.", "RR": "Trasera der."}
        for c in corners:
            vals = {"L": [], "M": [], "R": [], "P": [], "W": []}
            for l in valid:
                f = self.files[l.file_idx]
                lat = f.get("LatAccel")
                load = np.abs(lat[l.i0:l.i1]) > 0.8 * G if lat is not None else slice(None)
                for side in ("L", "M", "R"):
                    a = f.get(f"{c}temp{side}")
                    if a is not None:
                        vals[side].append(a[l.i0:l.i1][load])
                p = f.get(f"{c}pressure")
                if p is not None:
                    vals["P"].append(p[l.i0:l.i1])
                w = f.get(f"{c}wearM")
                if w is not None:
                    vals["W"].append(w[l.i0:l.i1])
            if not vals["M"]:
                continue
            L, M, R = (float(np.mean(np.concatenate(vals[s]))) for s in ("L", "M", "R"))
            # Lado izquierdo del auto: L = exterior; lado derecho: L = interior
            outer, inner = (L, R) if c[0] == "L" else (R, L)
            res.tires[c] = {"name": corners[c], "outer": outer, "middle": M, "inner": inner,
                            "pressure": float(np.mean(np.concatenate(vals["P"]))) if vals["P"] else 0.0,
                            "wear": float(np.min(np.concatenate(vals["W"]))) * 100 if vals["W"] else 100.0}

    def _fuel(self):
        res = self.result
        used = [l.fuel_used for l in res.laps if not l.pit and l.fuel_used > 0.3]
        if not used:
            return
        res.fuel_per_lap = float(np.median(used))
        try:
            sessions = self.info["SessionInfo"]["Sessions"]
            race = next(s for s in sessions if str(s.get("SessionType", "")).lower().startswith("race"))
            race_laps = int(self._num(race.get("SessionLaps")) or 0)
        except Exception:
            race_laps = 0
        if race_laps:
            need = res.fuel_per_lap * (race_laps + 1) + 0.5
            res.recommendations.append(Recommendation(
                "Combustible", 1 if self._start_fuel() and self._start_fuel() < need else 3,
                f"Gastas {res.fuel_per_lap:.2f} L por vuelta",
                f"Para {race_laps} vueltas sin parar necesitas cargar al menos {need:.1f} L "
                f"(incluye la vuelta de formación y un margen de 0.5 L)."
                + (f" Saliste con {self._start_fuel():.1f} L, lo que obliga a parar en pits."
                   if self._start_fuel() and self._start_fuel() < need else "")))

    def _session_plan(self, valid: List[Lap]):
        from app import session_plan as sp
        res = self.result
        res.brake_profile = sp.brake_profile(self.files, valid)
        mu_f = self._num(self._setup_value("FrontBrakePadMu") or "")
        mu_r = self._num(self._setup_value("RearBrakePadMu") or "")
        bias = None
        biases = [self.files[l.file_idx].get("dcBrakeBias") for l in valid]
        vals = [b[l.i0:l.i1] for b, l in zip(biases, valid) if b is not None]
        if vals:
            bias = float(np.median(np.concatenate(vals))) / 100
        options = sp.pad_options_for(res.car)
        if options and mu_f and mu_f not in options:
            options = sorted(set(options) | {mu_f}, reverse=True)
        res.pad_plan = sp.pad_plan(res.brake_profile, mu_f, mu_r, bias, options) if options else None
        fuel = {}
        if res.fuel_per_lap:
            from app.vehicle_dynamics import fuel_seconds_per_liter
            if res.best_lap:
                fuel["s_per_liter"] = fuel_seconds_per_liter(res.best_lap.time)
            q_laps = sp.session_laps(self.info, "qual") or 2
            fuel.update(qualy=sp.qualy_fuel(res.fuel_per_lap, q_laps), qualy_laps=q_laps)
            r_laps = sp.session_laps(self.info, "race")
            if r_laps:
                fuel.update(race=sp.race_fuel(res.fuel_per_lap, r_laps), race_laps=r_laps)
        res.plan_lines = sp.describe(res.pad_plan, res.brake_profile, fuel) if (res.pad_plan or fuel) else []
        if res.plan_lines:
            res.recommendations.append(Recommendation("Plan", 1, "Plan para qualy y carrera",
                                                      " ".join(res.plan_lines)))

    def _start_fuel(self) -> Optional[float]:
        f = self.files[0].get("FuelLevel")
        return float(f[0]) if f is not None and len(f) else None

    # ------------------------------------------------- recomendaciones
    def _driving_recs(self, data, valid, step):
        res = self.result
        R = res.recommendations
        worst = sorted(res.corners, key=lambda c: c.time_loss_avg, reverse=True)[:4]
        for c in worst:
            if c.time_loss_avg < 0.08:
                continue
            reasons = []
            if c.brake_m_best is not None and c.brake_m_avg is not None and c.brake_m_avg - c.brake_m_best > 8:
                reasons.append(f"en promedio frenas {c.brake_m_avg - c.brake_m_best:.0f} m antes que en tu mejor paso "
                               f"({c.brake_m_best:.0f} m antes del vértice)")
            if c.min_speed_best - c.min_speed_avg > 2:
                reasons.append(f"llevas {c.min_speed_best - c.min_speed_avg:.0f} km/h menos al vértice "
                               f"({c.min_speed_avg:.0f} vs {c.min_speed_best:.0f} km/h)")
            if c.exit_speed_best - c.exit_speed_avg > 2:
                reasons.append(f"sales {c.exit_speed_best - c.exit_speed_avg:.0f} km/h más lento: acelera antes "
                               f"y más decidido")
            if c.throttle_m_best is not None and c.throttle_m_avg is not None and \
                    c.throttle_m_avg - c.throttle_m_best > 10:
                reasons.append(f"vuelves a fondo {c.throttle_m_avg - c.throttle_m_best:.0f} m más tarde que en tu mejor "
                               f"paso ({c.throttle_m_best:.0f} m después del vértice)")
            if c.brake_peak_best - c.brake_peak_avg > 8:
                reasons.append(f"frenas más suave: pico de {c.brake_peak_avg:.0f}% contra {c.brake_peak_best:.0f}% "
                               f"en tu mejor paso, ataca más fuerte el pedal al inicio")
            if c.coast_s > 0.4:
                reasons.append(f"pasas {c.coast_s:.1f} s sin freno ni acelerador (inercia)")
            detail = "; ".join(reasons) if reasons else "inconsistencia general en la línea"
            R.append(Recommendation(
                "Pilotaje", 1 if c.time_loss_avg > 0.2 else 2,
                f"Curva {c.number} (a {c.apex_m:.0f} m): pierdes {c.time_loss_avg:.2f} s por vuelta",
                detail[0].upper() + detail[1:] + "."))

        # Salidas de pista (cada una suele ser 1x)
        offs = [c for c in res.corners if c.offtracks > 0]
        if offs:
            total = sum(c.offtracks for c in offs)
            names = ", ".join(f"C{c.number} a {c.apex_m:.0f} m ({c.offtracks} veces)"
                              for c in sorted(offs, key=lambda c: -c.offtracks)[:4])
            R.append(Recommendation(
                "Pilotaje", 1, f"{total} salidas de pista en {len(valid)} vueltas",
                f"Dónde: {names}. Cada salida suma incidentes (1x) y arriesga penalización. "
                f"Deja medio metro de margen en la salida de esas curvas: el tiempo que ganas abriendo "
                f"no compensa los incidentes."))

        # Patinaje y bloqueos por curva
        spin = [c for c in res.corners if c.wheelspin > 12]
        if spin:
            names = ", ".join(f"C{c.number} ({c.wheelspin:.0f}%)" for c in sorted(spin, key=lambda c: -c.wheelspin)[:4])
            R.append(Recommendation(
                "Pilotaje", 1, "Patinaje de ruedas traseras al acelerar",
                f"Las traseras giran más de un 6% más rápido que las delanteras en: {names}. "
                f"Aplica el acelerador más progresivo, endereza el volante antes de pisar a fondo "
                f"y considera subir el TC un clic."))
        locks = [c for c in res.corners if c.lockups > 10]
        if locks:
            names = ", ".join(f"C{c.number} ({c.lockups:.0f}%)" for c in sorted(locks, key=lambda c: -c.lockups)[:4])
            R.append(Recommendation(
                "Frenos", 1, "Bloqueos de rueda en frenada",
                f"Alguna rueda baja más de un 12% bajo la velocidad del auto en: {names}. "
                f"Reduce la presión máxima del pedal o sube el ABS un nivel."))
        abs_heavy = [c for c in res.corners if c.abs_pct > 40]
        if abs_heavy:
            names = ", ".join(f"C{c.number}" for c in abs_heavy[:5])
            R.append(Recommendation(
                "Frenos", 2, "Frenas mucho dentro del ABS",
                f"Más del 40% de la frenada con el ABS activo en {names}. El ABS alarga la frenada: "
                f"busca el punto justo antes de que actúe, presión fuerte inicial y soltar progresivo."))

        # Cambios de marcha
        redline = (self.info.get("DriverInfo") or {}).get("DriverCarSLShiftRPM")
        if redline:
            ups = []
            for l in valid:
                f = self.files[l.file_idx]
                g, rpm = f.get("Gear")[l.i0:l.i1], f.get("RPM")[l.i0:l.i1]
                sh = np.flatnonzero(np.diff(g) == 1)
                ups += [float(rpm[i]) for i in sh if g[i] >= 1]
            if ups:
                avg = float(np.mean(ups))
                if avg < redline - 400:
                    R.append(Recommendation(
                        "Pilotaje", 2, "Cambias de marcha antes de tiempo",
                        f"Subes de marcha en promedio a {avg:.0f} rpm y el punto ideal (luces de cambio) es "
                        f"{redline:.0f} rpm. Estira cada marcha hasta que parpadeen las luces."))
                elif avg > redline + 250:
                    R.append(Recommendation(
                        "Pilotaje", 2, "Te pasas de vueltas al cambiar",
                        f"Subes a {avg:.0f} rpm, por encima de {redline:.0f}. Pegas en el limitador: cambia antes."))

        # Uso de inercia total
        total_coast = sum(c.coast_s for c in res.corners)
        if total_coast > 2.0:
            R.append(Recommendation(
                "Pilotaje", 2, f"{total_coast:.1f} s por vuelta sin freno ni acelerador",
                "Ir por inercia hace perder tiempo. Pasa directo del freno al acelerador: suelta el freno "
                "hasta el vértice (trail braking) y empieza a acelerar suave apenas el auto rote."))

    def _setup_recs(self):
        res = self.result
        R = res.recommendations
        cs = res.corners
        if not cs:
            return
        slow = [c for c in cs if c.kind == "lenta"]
        fast = [c for c in cs if c.kind == "rápida"] + [c for c in cs if c.kind == "media"]
        u_slow = np.mean([c.understeer_deg for c in slow]) if slow else 0.0
        u_fast = np.mean([c.understeer_deg for c in fast]) if fast else 0.0
        over = np.mean([c.oversteer_events for c in cs])
        spin = np.mean([c.wheelspin for c in cs])
        farb = self._setup_value("Front", "ArbSetting")
        rarb = self._setup_value("Rear", "ArbSetting")
        wing = self._setup_value("WingAngle")
        prefix = "En setup fijo no puedes cambiarlo, pero " if res.fixed_setup else ""

        if u_slow > 1.5:
            R.append(Recommendation(
                "Setup", 1, "Subviraje en curvas lentas",
                f"En curvas lentas las ruedas delanteras van {u_slow:.1f}° más giradas de lo que el auto realmente gira. "
                f"{prefix}ablanda la barra delantera (ahora {farb}) o endurece la trasera (ahora {rarb}). "
                f"También ayuda balance de frenos un poco atrás para rotar en la entrada."))
        if u_fast > 1.2 and fast:
            R.append(Recommendation(
                "Setup", 2, "Subviraje en curvas rápidas",
                f"En curvas medias/rápidas subviras {u_fast:.1f}°: falta agarre delantero. "
                f"{prefix}baja un punto el ala trasera (ahora {wing}) o baja 1-2 mm la altura delantera."))
        if u_slow < -0.5 or u_fast < -0.5:
            R.append(Recommendation(
                "Setup", 1, "Sobreviraje en apoyo",
                f"El auto rota más de lo que pides con el volante (lentas {u_slow:.1f}°, rápidas {u_fast:.1f}°). "
                f"{prefix}endurece la barra delantera (ahora {farb}) o ablanda la trasera (ahora {rarb})."))
        if over > 0.6 or spin > 15:
            R.append(Recommendation(
                "Setup", 1, "Sobreviraje / falta de tracción",
                f"{over:.1f} correcciones de contravolante por curva y {spin:.0f}% de patinaje medio. "
                f"{prefix}ablanda la barra trasera (ahora {rarb}) o sube un punto el ala trasera (ahora {wing}); "
                f"si persiste, TC un clic más."))

        # Neumáticos: camber y presión por temperaturas reales
        for axle, pair in (("delantero", ("LF", "RF")), ("trasero", ("LR", "RR"))):
            ts = [res.tires[c] for c in pair if c in res.tires]
            if not ts:
                continue
            spread = np.mean([t["inner"] - t["outer"] for t in ts])
            camber = self._setup_value("Left" + ("Front" if axle == "delantero" else "Rear"), "Camber")
            if spread > 10:
                R.append(Recommendation(
                    "Neumáticos", 2, f"Demasiado camber en el eje {axle}",
                    f"El interior va {spread:.0f}°C más caliente que el exterior (ideal 5-8°C). "
                    f"{prefix}reduce el camber negativo unos 0.3° (ahora {camber})."))
            elif spread < 3:
                R.append(Recommendation(
                    "Neumáticos", 2, f"Falta camber en el eje {axle}",
                    f"El interior solo va {spread:.0f}°C sobre el exterior (ideal 5-8°C): el exterior trabaja "
                    f"de más. {prefix}agrega unos 0.3° de camber negativo (ahora {camber})."))
        for c, t in res.tires.items():
            diff = t["middle"] - (t["inner"] + t["outer"]) / 2
            if diff > 3:
                R.append(Recommendation(
                    "Neumáticos", 2, f"{t['name']}: presión alta",
                    f"El centro va {diff:.0f}°C más caliente que los bordes ({t['pressure']:.0f} kPa en caliente). "
                    f"{prefix}baja unos 5 kPa en frío."))
            elif diff < -3:
                R.append(Recommendation(
                    "Neumáticos", 2, f"{t['name']}: presión baja",
                    f"El centro va {-diff:.0f}°C más frío que los bordes ({t['pressure']:.0f} kPa en caliente). "
                    f"{prefix}sube unos 5 kPa en frío."))

    def _consistency_recs(self, valid: List[Lap]):
        res = self.result
        if len(valid) < 3:
            return
        times = np.array([l.time for l in valid])
        std = float(np.std(times))
        gap = res.best_lap.time - res.theoretical_best
        res.recommendations.append(Recommendation(
            "Consistencia", 2 if std > 0.6 else 3,
            f"Variación entre vueltas: ±{std:.2f} s",
            f"Mejor vuelta {fmt_lap(res.best_lap.time)}; uniendo tus mejores pasos por cada curva saldría "
            f"{fmt_lap(res.theoretical_best)} ({gap:.2f} s por ganar sin cambiar nada del auto)."))


def fmt_lap(t: float) -> str:
    if not t or t <= 0:
        return "--"
    m = int(t // 60)
    return f"{m}:{t - m * 60:06.3f}"


def group_session_files(path: str) -> List[str]:
    """Archivos .ibt de la misma sesión (iRacing parte la grabación en varios)"""
    folder = os.path.dirname(path)
    base = os.path.basename(path)
    m = re.match(r"(.+?) \d{4}-\d{2}-\d{2} ", base)
    if not m:
        return [path]
    prefix = m.group(1)
    target = IBTFile(path)
    try:
        uid = target.session_info["WeekendInfo"]["SessionID"], target.session_info["WeekendInfo"]["SubSessionID"]
        num = int(target.get("SessionNum")[-1])
    except Exception:
        return [path]
    group = []
    for f in sorted(os.listdir(folder)):
        if not (f.startswith(prefix) and f.endswith(".ibt")):
            continue
        p = os.path.join(folder, f)
        try:
            o = IBTFile(p) if p != path else target
            ouid = o.session_info["WeekendInfo"]["SessionID"], o.session_info["WeekendInfo"]["SubSessionID"]
            if ouid == uid and int(o.get("SessionNum")[-1]) == num:
                group.append(p)
        except Exception:
            continue
    return group or [path]


def format_report(res: AnalysisResult) -> str:
    lines = [f"{res.car} — {res.track} ({res.session_type})",
             f"Setup {'FIJO' if res.fixed_setup else 'ABIERTO'}", ""]
    lines.append("VUELTAS")
    for l in res.laps:
        flag = "" if l.valid else " (" + ", ".join(
            x for x, c in (("pits", l.pit), ("fuera de pista", l.offtrack), (f"{l.incidents}x", l.incidents)) if c
        ) + ")" if (l.pit or l.offtrack or l.incidents) else " (no válida)"
        best = " ← mejor" if res.best_lap and l is res.best_lap else ""
        lines.append(f"  V{l.number:<3} {fmt_lap(l.time)}  comb. {l.fuel_used:5.2f} L{flag}{best}")
    if res.best_lap:
        lines.append(f"  Vuelta teórica: {fmt_lap(res.theoretical_best)}")
    lines.append("")
    if res.corners:
        lines.append("CURVAS  (pérdida = promedio vs tu mejor paso)")
        lines.append("   #   metro  tipo    marcha  vmin mejor/prom  salida mejor/prom  frenada m  pérdida  subvir.  patina  bloqueo  ABS  salidas")
        for c in res.corners:
            bm = f"{c.brake_m_best:.0f}/{c.brake_m_avg:.0f}" if c.brake_m_best is not None and c.brake_m_avg is not None else "-"
            lines.append(f"  C{c.number:<2} {c.apex_m:6.0f}  {c.kind:<7} {c.gear:^6}  "
                         f"{c.min_speed_best:5.0f}/{c.min_speed_avg:<5.0f}     {c.exit_speed_best:5.0f}/{c.exit_speed_avg:<5.0f}"
                         f"      {bm:>9}  {c.time_loss_avg:6.2f}s  {c.understeer_deg:+5.1f}°  {c.wheelspin:5.0f}%  "
                         f"{c.lockups:6.0f}%  {c.abs_pct:3.0f}%  {c.offtracks:4d}")
        lines.append("")
    if res.corners:
        lines.append("REFERENCIAS DE TU MEJOR PASO POR CADA CURVA")
        for c in res.corners:
            parts = [f"  C{c.number:<2} (V{c.best_time_lap})"]
            if c.brake_at_m is not None:
                parts.append(f"frena en el metro {c.brake_at_m:5.0f} ({c.brake_m_best:.0f} m antes del vértice, "
                             f"pico {c.brake_peak_best:.0f}%)")
            else:
                parts.append("sin frenar")
            parts.append(f"vértice a {c.min_speed_best:.0f} km/h en {c.gear}ª")
            if c.throttle_m_best is not None:
                parts.append(f"a fondo {c.throttle_m_best:.0f} m después")
            lines.append(" · ".join(parts))
        lines.append("")
    if res.gradients or any(c.brake_efficiency for c in res.corners):
        lines.append("FÍSICA")
        for band, g in res.gradients.items():
            lines.append(f"  Gradiente de subviraje curvas {band}: K {g['K']:+.2f} °/g (n={g['n']}, ruido {g['ruido']}°)")
        for c in res.corners:
            if c.brake_efficiency or c.grip_usage:
                be = f"frenada {c.brake_efficiency * 100:3.0f} % (+{c.brake_potential_m:.0f} m posible)" \
                    if c.brake_efficiency else "sin frenada"
                gu = f"agarre usado {c.grip_usage * 100:.0f} %" if c.grip_usage == c.grip_usage else ""
                lines.append(f"  C{c.number:<2} {be}  {gu}")
        lines.append("")
    if res.plan_lines:
        lines.append("PLAN QUALY / CARRERA")
        lines += [f"  {x}" for x in res.plan_lines]
        if res.pad_plan:
            lines.append("  Pastilla delantera: pedal con que entra el ABS (normal / frenadas fuertes) y balance "
                         "para el mismo reparto")
            for t in res.pad_plan["table"]:
                mark = " ← hoy" if t["mu"] == res.pad_plan["current"]["mu"] else ""
                mark += " ← carrera" if t["mu"] == res.pad_plan["race"]["mu"] else ""
                mark += " ← qualy" if t["mu"] == res.pad_plan["qualy"]["mu"] else ""
                over = "  (no llegas al ABS fuerte)" if t["hard"] > res.pad_plan["limit"] else ""
                lines.append(f"    {t['mu']:.2f}: {t['onset'] * 100:3.0f}% / {t['hard'] * 100:3.0f}%  "
                             f"balance {t['bias'] * 100:.1f}%{mark}{over}")
        lines.append("")
    if res.limit_zones:
        lines.append("LÍMITES DE PISTA (margen al límite por vuelta; negativo = te pasaste)")
        for z in res.limit_zones:
            laps = "  ".join(f"V{l['lap']}:{l['margin'] * 100:+.0f}cm/{l['exit_kmh']:.0f}" + ("✗" if l["off"] else "")
                             for l in z["laps"])
            lines.append(f"  {z['name']:<20} {z['s']:6.0f} m  lado {'izq' if z['side'] > 0 else 'der'}  {laps}")
        lines.append("")
    if res.tires:
        lines.append("NEUMÁTICOS (temperatura media en vueltas válidas, °C)")
        for c, t in res.tires.items():
            lines.append(f"  {t['name']:<16} ext {t['outer']:5.1f}  centro {t['middle']:5.1f}  int {t['inner']:5.1f}  "
                         f"presión {t['pressure']:5.1f} kPa  desgaste mín {t['wear']:.0f}%")
        lines.append("")
    lines.append("RECOMENDACIONES")
    prio = {1: "ALTA", 2: "MEDIA", 3: "INFO"}
    for r in res.recommendations:
        lines.append(f"  [{prio[r.priority]}] {r.category}: {r.title}")
        lines.append(f"      {r.detail}")
    return "\n".join(lines)


def main():
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if len(sys.argv) < 2:
        print("Uso: python -m app.telemetry_analyzer archivo.ibt [más archivos de la misma sesión]")
        return
    paths = sys.argv[1:]
    if len(paths) == 1:
        paths = group_session_files(paths[0])
    res = TelemetryAnalyzer(paths).analyze()
    print(format_report(res))


if __name__ == "__main__":
    main()
