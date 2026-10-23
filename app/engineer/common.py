"""Constantes, prioridades de mensajes y estado de cada auto, compartidos por el ingeniero."""

from typing import Dict, List, Optional

try:
    import irsdk
    IRSDK_AVAILABLE = True
except ImportError:
    irsdk = None
    IRSDK_AVAILABLE = False

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
