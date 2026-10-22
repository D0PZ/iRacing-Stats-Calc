"""
Análisis de inputs del piloto: pedales y volante (muestras a 60 Hz).

Se usan los canales CRUDOS del pedal (ThrottleRaw, BrakeRaw): el canal procesado incluye los golpes de
gas automáticos al bajar de marcha y la intervención de las ayudas, que no son tu pie.

Métricas (por vuelta y por curva):
- inercia_s: sin freno ni acelerador (< 5 %) a más de 54 km/h.
- ambos_pedales_s: freno y acelerador > 10 % a la vez.
- a_fondo_pct: tiempo con el acelerador sobre 98 %.
- ataque_freno_s: de 5 % al 90 % del pico de cada frenada fuerte (rápido = aprovecha el peso adelante).
- liberacion_s: del pico a soltar (trail braking).
- re_presiones: el pedal vuelve a subir mientras sueltas (liberación a escalones).
- levantadas: el acelerador cae > 20 % después de pasar 40 %, sin frenar (indecisión / corrección).
- correcciones: inversiones del volante dentro de una misma curva, > 90 °/s y > 10° de amplitud.
- acelera_agregando_volante_s: acelerador SUBIENDO por sobre 50 % mientras aún sumas volante (> 30 °/s) en
  curvas lentas y medias (< 160 km/h, > 0.6 g): lo típico de patinaje y cola suelta en la salida;
  primero se endereza, después se pisa.
"""

from typing import Dict, List, Optional

import numpy as np

G = 9.81
CORR_RATE = 90.0      # °/s
CORR_AMP = 10.0       # °


class InputTracker:
    """Procesa muestra a muestra (en vivo o recorriendo un .ibt) y entrega las métricas acumuladas"""

    def __init__(self):
        self.reset()

    def reset(self):
        self.t = 0.0
        self.coast = self.overlap = self.full = self.adding_lock = 0.0
        self.attacks: List[float] = []
        self.releases: List[float] = []
        self.restabs = 0
        self.lifts = 0
        self.corrections = 0
        # estado de frenada
        self._b_on, self._b_t0, self._b_peak, self._b_peak_t, self._b_reached = False, 0.0, 0.0, 0.0, None
        self._b_prev, self._b_falling = 0.0, False
        # estado de acelerador
        self._armed, self._t_peak = False, 0.0
        self._thr_prev = 0.0
        # estado de volante
        self._s_prev = None
        self._dir = 0
        self._ext = None
        self._ay_sign = 0
        self._ay_ok = True

    def add(self, dt: float, throttle: float, brake: float, steer_deg: float, ay_g: float, v: float):
        self.t += dt
        if v > 15 and throttle < 0.05 and brake < 0.05:
            self.coast += dt
        if throttle > 0.1 and brake > 0.1:
            self.overlap += dt
        if throttle > 0.98:
            self.full += dt
        self._brake(dt, brake)
        self._throttle(throttle, brake)
        self._steer(dt, steer_deg, ay_g, throttle, v)
        self._thr_prev = throttle

    # ------------------------------------------------------------------ freno
    def _brake(self, dt, b):
        if b > 0.05 and not self._b_on:
            self._b_on, self._b_t0, self._b_peak, self._b_reached = True, self.t, b, None
            self._b_falling = False
        if self._b_on:
            if b > self._b_peak:
                self._b_peak, self._b_peak_t = b, self.t
            if self._b_falling and b > self._b_prev + 0.02 and self._b_peak > 0.3:
                self.restabs += 1          # vuelve a subir mientras soltaba
                self._b_falling = False
            elif b < self._b_prev - 0.01:
                self._b_falling = True
            if b < 0.05:
                if self._b_peak >= 0.3 and self.t - self._b_t0 > 0.25:
                    # ataque: tiempo hasta el 90 % del pico (aprox. con el instante del pico)
                    self.attacks.append(max(self._b_peak_t - self._b_t0, dt))
                    self.releases.append(self.t - self._b_peak_t)
                self._b_on = False
        self._b_prev = b

    # ------------------------------------------------------------- acelerador
    def _throttle(self, thr, brk):
        if brk > 0.1 or thr > 0.98:
            self._armed, self._t_peak = False, 0.0
            return
        if thr > 0.4:
            self._armed = True
        if self._armed:
            self._t_peak = max(self._t_peak, thr)
            if thr < self._t_peak - 0.2:
                self.lifts += 1
                self._armed, self._t_peak = False, 0.0

    # ----------------------------------------------------------------- volante
    def _steer(self, dt, s, ay, thr, v):
        if self._s_prev is None:
            self._s_prev = s
            return
        rate = (s - self._s_prev) / dt
        self._s_prev = s
        # pisar el acelerador (subiendo) mientras se agrega volante en apoyo, en curvas lentas/medias
        if thr > 0.5 and thr > self._thr_prev and abs(ay) > 0.6 and abs(rate) > 30 and np.sign(rate) == np.sign(s) \
                and v < 160 / 3.6:
            self.adding_lock += dt
        # correcciones: inversión de la velocidad de giro dentro de una misma curva
        in_turn = abs(ay) > 0.4
        ay_sign = int(np.sign(ay)) if in_turn else 0
        if not in_turn or (self._ay_sign and ay_sign != self._ay_sign):
            self._dir, self._ext, self._ay_sign = 0, None, ay_sign
            return
        self._ay_sign = ay_sign
        if abs(rate) < CORR_RATE:
            return
        d = int(np.sign(rate))
        if self._dir and d != self._dir and self._ext is not None and abs(s - self._ext) > CORR_AMP \
                and np.sign(s) == np.sign(self._ext) and abs(s) > 10:
            self.corrections += 1
        if d != self._dir:
            self._ext = s
            self._dir = d

    # ---------------------------------------------------------------- resultado
    def result(self) -> Dict[str, float]:
        out = {"inercia_s": round(self.coast, 2), "ambos_pedales_s": round(self.overlap, 2),
               "a_fondo_pct": round(100 * self.full / self.t, 1) if self.t else 0.0,
               "re_presiones": self.restabs, "levantadas": self.lifts, "correcciones": self.corrections,
               "acelera_agregando_volante_s": round(self.adding_lock, 2), "duracion_s": round(self.t, 1)}
        if self.attacks:
            out["ataque_freno_s"] = round(float(np.median(self.attacks)), 2)
            out["liberacion_s"] = round(float(np.median(self.releases)), 2)
        return out


def run(throttle, brake, steer_rad, ay, v, dt: float = 1 / 60) -> Dict[str, float]:
    """Métricas de un tramo de muestras crudas (steer en radianes, ay en m/s²)"""
    tr = InputTracker()
    steer_deg = np.degrees(np.asarray(steer_rad, float))
    ay_g = np.asarray(ay, float) / G
    for k in range(len(steer_deg)):
        tr.add(dt, float(throttle[k]), float(brake[k]), float(steer_deg[k]), float(ay_g[k]), float(v[k]))
    return tr.result()


def compare(best: Dict[str, float], typical: Dict[str, float]) -> List[str]:
    """Qué hace distinto tu mejor paso por la curva (frases cortas, sin unidades raras)"""
    out = []
    if typical.get("ataque_freno_s") and best.get("ataque_freno_s") \
            and typical["ataque_freno_s"] - best["ataque_freno_s"] > 0.08:
        out.append(f"en tu mejor paso llegas a la presión máxima en {best['ataque_freno_s']:.2f} s y normalmente en "
                   f"{typical['ataque_freno_s']:.2f} s: pisa el freno más decidido al inicio")
    if typical.get("liberacion_s") and best.get("liberacion_s") \
            and best["liberacion_s"] - typical["liberacion_s"] > 0.15:
        out.append(f"en tu mejor paso sueltas el freno en {best['liberacion_s']:.2f} s y normalmente en "
                   f"{typical['liberacion_s']:.2f} s: suelta más progresivo hasta el vértice")
    if typical.get("inercia_s", 0) - best.get("inercia_s", 0) > 0.3:
        out.append(f"vas {typical['inercia_s']:.1f} s sin pedales contra {best.get('inercia_s', 0):.1f} s en tu mejor "
                   f"paso: une el final del freno con el inicio del acelerador")
    if typical.get("correcciones", 0) - best.get("correcciones", 0) >= 1:
        out.append(f"corriges el volante {typical['correcciones']:.0f} veces contra {best.get('correcciones', 0):.0f} "
                   f"en tu mejor paso: un solo movimiento de entrada y suelta")
    if typical.get("levantadas", 0) - best.get("levantadas", 0) >= 1:
        out.append("levantas el acelerador después de empezar a acelerar: aplica una vez y progresivo")
    if typical.get("acelera_agregando_volante_s", 0) - best.get("acelera_agregando_volante_s", 0) > 0.2:
        out.append("aceleras fuerte mientras todavía agregas volante: primero endereza, después pisa")
    return out
