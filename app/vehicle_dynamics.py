"""
Dinámica vehicular a partir de telemetría: magnitudes físicas continuas en vez de contar eventos.

1. Gradiente de subviraje K (°/g), modelo de régimen estable (bicicleta):
       δ_ruedas = L / R + K · a_y          con  L / R = L · ψ̇ / v
   δ_ruedas = ángulo de volante / relación de dirección (estimada en giros suaves a baja carga).
   K > 0 subviraje, K < 0 sobreviraje. Se mide por rango de velocidad: en curvas lentas manda el
   balance mecánico (barras, resortes, diferencial); en rápidas, el aerodinámico (alerón, rake).

2. Envolvente de frenada a_max(v) (percentil 95 de la desaceleración con freno, por velocidad) y
   distancia mínima de frenada entre v1 y v2:   d = ∫ v dv / a_max(v)   (integración numérica).

3. Envolvente g-g: agarre combinado √(a_x² + a_y²) demostrado a cada velocidad; el uso de agarre de
   una curva es su g combinado medio / envolvente.

4. Peso del combustible: Δt_vuelta ≈ s · t_vuelta · Δm / m   (s ≈ 0.4 para autos limitados por agarre).
"""

from typing import Dict, List, Optional, Tuple

import numpy as np

G = 9.81
WHEELBASE = 2.46                      # m (Porsche 992 Cup; solo escala K)
SPEED_BANDS = (("lenta", 0.0, 100 / 3.6), ("media", 100 / 3.6, 160 / 3.6), ("rapida", 160 / 3.6, 1e9))
FUEL_DENSITY = 0.75                   # kg/L
MASS_SENSITIVITY = 0.4                # fracción de tiempo de vuelta por fracción de masa


def steering_ratio(v, steer, yaw, ay) -> Optional[float]:
    """Relación volante/ruedas: giros suaves y lentos (casi sin deriva de neumático)"""
    v, steer, yaw, ay = map(np.asarray, (v, steer, yaw, ay))
    m = (v > 10) & (v < 35) & (np.abs(steer) > 0.1) & (np.abs(ay) < 0.35 * G) & (steer * yaw > 0) \
        & (np.abs(yaw) > 0.02)
    if m.sum() < 50:
        return None
    return float(np.median(np.abs(steer[m]) / (WHEELBASE * np.abs(yaw[m]) / v[m])))


def understeer_samples(v, steer, yaw, ay, ax, ratio):
    """(a_y en g, ángulo de subviraje en °, velocidad) de las muestras en giro casi estable"""
    v, steer, yaw, ay, ax = map(np.asarray, (v, steer, yaw, ay, ax))
    m = (steer * yaw > 0) & (np.abs(ay) > 0.3 * G) & (np.abs(ax) < 0.25 * G) & (np.abs(steer) > 0.02) & (v > 8)
    delta = np.degrees(np.abs(steer[m]) / ratio)
    kin = np.degrees(WHEELBASE * np.abs(yaw[m]) / v[m])
    return np.abs(ay[m]) / G, delta - kin, v[m]


def fit_gradient(a_g, under_deg) -> Optional[dict]:
    """K = pendiente de (ángulo de subviraje) vs (a_y en g)"""
    if len(a_g) < 50 or np.ptp(a_g) < 0.3:
        return None
    K, c0 = np.polyfit(a_g, under_deg, 1)
    noise = float(np.std(under_deg - (K * a_g + c0)))
    return {"K": round(float(K), 2), "offset": round(float(c0), 2), "n": int(len(a_g)), "ruido": round(noise, 2)}


def understeer_by_band(v, steer, yaw, ay, ax, ratio=None) -> Dict[str, dict]:
    ratio = ratio or steering_ratio(v, steer, yaw, ay)
    if not ratio:
        return {}
    a, u, vv = understeer_samples(v, steer, yaw, ay, ax, ratio)
    out = {}
    for name, lo, hi in SPEED_BANDS:
        k = (vv >= lo) & (vv < hi)
        fit = fit_gradient(a[k], u[k])
        if fit:
            out[name] = fit
    return out


class GradientAccumulator:
    """Regresión lineal incremental por banda de velocidad (para calcular K en vivo a 60 Hz sin guardar muestras)"""

    def __init__(self):
        self.s = {name: np.zeros(5) for name, _, _ in SPEED_BANDS}   # n, Σx, Σy, Σx², Σxy
        self.ratio_samples: List[float] = []
        self.ratio: Optional[float] = None

    def add(self, v: float, steer: float, yaw: float, ay: float, ax: float):
        if 10 < v < 35 and abs(steer) > 0.1 and abs(ay) < 0.35 * G and steer * yaw > 0 and abs(yaw) > 0.02:
            self.ratio_samples.append(abs(steer) / (WHEELBASE * abs(yaw) / v))
            if len(self.ratio_samples) % 120 == 0:
                self.ratio = float(np.median(self.ratio_samples[-3000:]))
        if not self.ratio or v < 8 or steer * yaw <= 0 or abs(ay) < 0.3 * G or abs(ax) > 0.25 * G \
                or abs(steer) < 0.02:
            return
        x = abs(ay) / G
        y = np.degrees(abs(steer) / self.ratio) - np.degrees(WHEELBASE * abs(yaw) / v)
        for name, lo, hi in SPEED_BANDS:
            if lo <= v < hi:
                self.s[name] += (1, x, y, x * x, x * y)
                break

    def result(self) -> Dict[str, dict]:
        out = {}
        for name, (n, sx, sy, sxx, sxy) in self.s.items():
            den = n * sxx - sx * sx
            if n < 50 or den <= 1e-9 or (sxx / n - (sx / n) ** 2) < 0.02:
                continue
            K = (n * sxy - sx * sy) / den
            out[name] = {"K": round(float(K), 2), "offset": round(float((sy - K * sx) / n), 2), "n": int(n)}
        return out

    def reset(self):
        for name in self.s:
            self.s[name][:] = 0


def braking_envelope(v, ax, brake, bins_ms=np.arange(15, 85, 5)) -> Tuple[np.ndarray, np.ndarray]:
    """Desaceleración máxima demostrada (m/s²) por velocidad (percentil 95)"""
    v, ax, brake = map(np.asarray, (v, ax, brake))
    dec = (brake > 0.3) & (ax < 0)
    centers, values = [], []
    for b in bins_ms:
        k = dec & (v >= b) & (v < b + 5)
        if k.sum() >= 20:
            centers.append(b + 2.5)
            values.append(float(np.percentile(-ax[k], 95)))
    return np.array(centers), np.array(values)


def min_braking_distance(v1: float, v2: float, env_v: np.ndarray, env_a: np.ndarray, steps: int = 200) -> float:
    """d = ∫ v dv / a_max(v) de v2 a v1 (m), con a_max interpolada de tu envolvente"""
    if v1 <= v2 or len(env_v) < 2:
        return 0.0
    vs = np.linspace(v2, v1, steps)
    a = np.interp(vs, env_v, env_a)
    return float(np.trapezoid(vs / np.maximum(a, 1.0), vs))


def braking_time_gain(meters_later: float, v_entry: float) -> float:
    """Frenar d metros más tarde = recorrerlos a velocidad de entrada en vez de frenando (aprox. lineal)"""
    return max(meters_later, 0.0) / max(v_entry, 1.0) * 0.5


def gg_envelope(v, ax, ay, bins_ms=np.arange(10, 85, 5)) -> Tuple[np.ndarray, np.ndarray]:
    """Agarre combinado demostrado (g) por velocidad (percentil 98)"""
    v, ax, ay = map(np.asarray, (v, ax, ay))
    g = np.hypot(ax, ay) / G
    centers, values = [], []
    for b in bins_ms:
        k = (v >= b) & (v < b + 5)
        if k.sum() >= 30:
            centers.append(b + 2.5)
            values.append(float(np.percentile(g[k], 98)))
    return np.array(centers), np.array(values)


def grip_usage(v, ax, ay, env_v, env_g) -> float:
    """Uso medio del agarre disponible (0-1) en un tramo con carga (curva)"""
    v, ax, ay = map(np.asarray, (v, ax, ay))
    g = np.hypot(ax, ay) / G
    k = g > 0.5
    if k.sum() < 5 or len(env_v) < 2:
        return float("nan")
    return float(np.mean(g[k] / np.interp(v[k], env_v, env_g)))


def fuel_seconds_per_liter(lap_time: float, car_mass_kg: float = 1350.0) -> float:
    """Δt por litro: s · t · (ρ / m)"""
    return MASS_SENSITIVITY * lap_time * FUEL_DENSITY / car_mass_kg


def lateral_envelope(v, ay, bins_ms=np.arange(10, 85, 5)) -> Tuple[np.ndarray, np.ndarray]:
    """Aceleración lateral máxima demostrada (m/s²) por velocidad (percentil 98)"""
    v, ay = np.asarray(v), np.abs(np.asarray(ay))
    centers, values = [], []
    for b in bins_ms:
        k = (v >= b) & (v < b + 5)
        if k.sum() >= 30:
            centers.append(b + 2.5)
            values.append(float(np.percentile(ay[k], 98)))
    return np.array(centers), np.array(values)


def braking_potential(v, yaw, brake_idx: int, apex_idx: int, step: float, brake_env, lat_env,
                      margin: float = 0.90) -> Optional[dict]:
    """
    Punto de frenada óptimo por modelo cuasi-estático con elipse de fricción sobre TU trayectoria:
      curvatura κ = |ψ̇| / v ; exigencia lateral a_lat = v² κ ;
      desaceleración disponible a_x = a_x,max(v) · √(1 − (a_lat / a_lat,max(v))²)
      integración hacia atrás desde el vértice: v² = v_vértice² + 2 a_x ds
    El punto óptimo es donde esa curva corta tu velocidad de llegada (tramo antes de frenar, y tu
    velocidad de inicio de frenada mantenida después). Devuelve metros que puedes frenar más tarde
    y el tiempo que vale (frenar más tarde + llevar más velocidad durante la frenada).
    margin = 0.90: un piloto rápido sostiene ~90 % de su pico de agarre demostrado, no el 100 %.
    """
    v, yaw = np.asarray(v, float), np.asarray(yaw, float)
    if not 0 < brake_idx < apex_idx or len(brake_env[0]) < 2 or len(lat_env[0]) < 2:
        return None
    kappa = np.abs(yaw) / np.maximum(v, 3.0)
    ideal = np.full(apex_idx + 1, np.inf)
    ideal[apex_idx] = v[apex_idx]
    for i in range(apex_idx, 0, -1):
        vi = ideal[i]
        a_lat = vi * vi * kappa[i - 1]
        a_lat_max = np.interp(vi, *lat_env) * margin
        a_x_max = np.interp(vi, *brake_env) * margin
        if a_lat >= a_lat_max:
            ideal[i - 1] = vi             # la curva ya pide todo el agarre lateral: no se puede frenar más ahí
            continue
        a_x = a_x_max * np.sqrt(1.0 - (a_lat / a_lat_max) ** 2)
        ideal[i - 1] = np.sqrt(vi * vi + 2.0 * a_x * step)
    # Velocidad de llegada si no frenaras todavía: la real antes de frenar y la de inicio de frenada después
    arrival = v[: apex_idx + 1].copy()
    arrival[brake_idx:] = v[brake_idx]
    crossing = np.flatnonzero(ideal[: apex_idx + 1] <= arrival)
    if not len(crossing):
        return None
    opt_idx = int(crossing[0])
    meters = (opt_idx - brake_idx) * step
    # Perfil óptimo: llegada hasta el punto óptimo y curva de frenada ideal después
    best = np.minimum(arrival, ideal[: apex_idx + 1])
    lo = min(opt_idx, brake_idx)
    seg = slice(lo, apex_idx + 1)
    gain = float(np.sum(step / np.maximum(v[seg], 1.0) - step / np.maximum(best[seg], 1.0)))
    return {"meters_later": round(meters, 1), "gain_s": round(max(gain, 0.0), 3),
            "v_brake": round(float(v[brake_idx]) * 3.6), "v_apex": round(float(v[apex_idx]) * 3.6)}
