"""
Generador de archivos .ibt sintéticos (mismo formato binario que iRacing) para las pruebas.
"""

import math
import struct

import numpy as np

_TYPES = {np.dtype("bool"): (1, "?"), np.dtype("int32"): (2, "<i4"), np.dtype("float32"): (4, "<f4"),
          np.dtype("float64"): (5, "<f8")}


def write_ibt(path: str, channels: dict, session_yaml: str, tick_rate: int = 60):
    """channels: nombre -> np.ndarray 1D (bool, int32, float32 o float64), todos del mismo largo"""
    names = list(channels)
    n = len(next(iter(channels.values())))
    layout, offset = [], 0
    for name in names:
        arr = np.asarray(channels[name])
        vtype, dt = _TYPES[arr.dtype]
        layout.append((name, vtype, offset, np.dtype(dt)))
        offset += np.dtype(dt).itemsize
    buf_len = offset

    var_header_offset = 144
    yaml_bytes = session_yaml.encode("latin-1") + b"\0"
    si_offset = var_header_offset + 144 * len(names)
    buf_offset = si_offset + len(yaml_bytes)

    header = struct.pack("<10i", 2, 1, tick_rate, 1, len(yaml_bytes), si_offset, len(names),
                         var_header_offset, 1, buf_len)
    header += struct.pack("<2i", 0, 0)
    header += struct.pack("<4i", n, buf_offset, 0, 0) + b"\0" * 48  # varBuf[0..3]
    disk = struct.pack("<3d2i", 0.0, 0.0, n / tick_rate, 0, n)
    out = bytearray(header + disk)
    out += b"\0" * (var_header_offset - len(out))
    for name, vtype, off, dt in layout:
        vh = struct.pack("<3i", vtype, off, 1) + b"\0" * 4
        vh += name.encode()[:31].ljust(32, b"\0") + b"".ljust(64, b"\0") + b"".ljust(32, b"\0")
        out += vh
    out += yaml_bytes
    rec = np.zeros((n, buf_len), dtype=np.uint8)
    for name, vtype, off, dt in layout:
        col = np.asarray(channels[name]).astype(dt)
        rec[:, off:off + dt.itemsize] = col.view(np.uint8).reshape(n, dt.itemsize)
    out += rec.tobytes()
    with open(path, "wb") as f:
        f.write(out)


SESSION_YAML = """---
WeekendInfo:
 TrackName: testring
 TrackDisplayName: Test Ring
 TrackLength: {length_km:.4f} km
 TrackPitSpeedLimit: 60.00 kph
 SessionID: 1
 SubSessionID: 2
 WeekendOptions:
  IsFixedSetup: 0
  IncidentLimit: 17

SessionInfo:
 Sessions:
 - SessionNum: 0
   SessionType: Race
   SessionLaps: 10

DriverInfo:
 DriverCarIdx: 0
 DriverCarSLShiftRPM: 8000.000
 DriverCarEstLapTime: 60.0000
 Drivers:
 - CarIdx: 0
   UserName: Test Driver
   CarNumber: "7"
   CarScreenName: Test Car
   CarClassID: 1

CarSetup:
 Chassis:
  Front:
   ArbSetting: 3
  Rear:
   ArbSetting: 4
   WingAngle: 8
...
"""


def synthetic_session(laps: int = 4, offtrack_lap: int = 3, length: float = 2000.0, tick_rate: int = 60):
    """
    Circuito circular de `length` m con dos frenadas (curvas) por vuelta.
    La vuelta `offtrack_lap` tiene una salida de pista en la segunda curva.
    """
    R = length / (2 * math.pi)
    cols = {k: [] for k in (
        "SessionTime", "SessionNum", "Lap", "LapDist", "LapDistPct", "LapCurrentLapTime", "Speed", "Throttle",
        "Brake", "SteeringWheelAngle", "YawRate", "LatAccel", "LongAccel", "RPM", "Gear", "LFspeed", "RFspeed",
        "LRspeed", "RRspeed", "BrakeABSactive", "OnPitRoad", "PlayerTrackSurface", "FuelLevel", "IsOnTrack",
        "PlayerCarMyIncidentCount", "Lat", "Lon", "VelocityX", "VelocityY", "YawNorth")}
    t, fuel, inc = 0.0, 40.0, 0
    lat0, lon0 = math.radians(30.0), math.radians(-97.0)
    dt = 1.0 / tick_rate
    for lap in range(1, laps + 1):
        s, lap_t = 0.0, 0.0
        while s < length:
            pct = s / length
            # Dos curvas: velocidad mínima en 25% y 75% de la vuelta
            dip = min(abs(pct - 0.25), abs(pct - 0.75))
            v = 30.0 + 25.0 * min(dip / 0.12, 1.0)
            if lap == 2:
                v *= 1.02  # vuelta más rápida
            braking = 0.1 < (0.25 - pct) % 0.5 < 0.18 or 0.1 < (0.75 - pct) % 1.0 < 0.18
            off = lap == offtrack_lap and abs(pct - 0.75) < 0.01
            theta = s / R
            x, y = R * math.cos(theta), R * math.sin(theta)
            heading = theta + math.pi / 2          # antihorario desde el Este
            yaw = v / R
            vals = {
                "SessionTime": t, "SessionNum": 0, "Lap": lap, "LapDist": s, "LapDistPct": pct,
                "LapCurrentLapTime": lap_t, "Speed": v, "Throttle": 0.0 if braking else 1.0,
                "Brake": 0.8 if braking else 0.0, "SteeringWheelAngle": 2.46 * 14 / R, "YawRate": yaw,
                "LatAccel": v * v / R, "LongAccel": 0.0, "RPM": 6000.0, "Gear": 3,
                "LFspeed": v, "RFspeed": v, "LRspeed": v, "RRspeed": v, "BrakeABSactive": False,
                "OnPitRoad": False, "PlayerTrackSurface": 0 if off else 3, "FuelLevel": fuel, "IsOnTrack": True,
                "PlayerCarMyIncidentCount": inc, "Lat": math.degrees(lat0 + y / 6371000.0),
                "Lon": math.degrees(lon0 + x / (6371000.0 * math.cos(lat0))),
                "VelocityX": v, "VelocityY": 0.0, "YawNorth": (math.pi / 2 - heading) % (2 * math.pi),
            }
            for k, val in vals.items():
                cols[k].append(val)
            s += v * dt
            t += dt
            lap_t += dt
            fuel -= 2.5 * v * dt / length  # 2.5 L por vuelta
    types = {"SessionNum": np.int32, "Lap": np.int32, "Gear": np.int32, "PlayerTrackSurface": np.int32,
             "PlayerCarMyIncidentCount": np.int32, "OnPitRoad": np.bool_, "IsOnTrack": np.bool_,
             "BrakeABSactive": np.bool_, "SessionTime": np.float64, "Lat": np.float64, "Lon": np.float64}
    channels = {k: np.array(v, dtype=types.get(k, np.float32)) for k, v in cols.items()}
    return channels, SESSION_YAML.format(length_km=length / 1000)
