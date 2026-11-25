import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ibt_fixture import write_ibt  # noqa: E402
from app.session_history import load_sessions, strength_of_field  # noqa: E402

RACE_YAML = """---
WeekendInfo:
 TrackDisplayName: Test Ring
 TrackConfigName: GP
 SeriesID: 299
 SubSessionID: 5
 Official: 1
 EventType: Race

SessionInfo:
 Sessions:
 - SessionNum: 0
   SessionType: Lone Qualify
 - SessionNum: 1
   SessionType: Race

QualifyResultsInfo:
 Results:
 - Position: 0
   ClassPosition: 0
   CarIdx: 1
 - Position: 1
   ClassPosition: 1
   CarIdx: 0

DriverInfo:
 DriverCarIdx: 0
 Drivers:
 - CarIdx: 0
   CarScreenName: Test Car
   CarScreenNameShort: Test Car
   IRating: 1500
   CarIsPaceCar: 0
 - CarIdx: 1
   CarScreenName: Test Car
   IRating: 1500
   CarIsPaceCar: 0
 - CarIdx: 2
   CarScreenName: Safety Car
   IRating: 0
   CarIsPaceCar: 1
...
"""


def _race_file(folder):
    n = 600
    pos = np.r_[np.zeros(100), np.full(400, 2), np.ones(100)].astype(np.int32)   # largó 2º, terminó 1º
    channels = {
        "SessionNum": np.ones(n, dtype=np.int32),
        "SessionState": np.r_[np.full(500, 4), np.full(100, 5)].astype(np.int32),  # verde y bandera a cuadros
        "PlayerCarPosition": pos,
        "PlayerCarClassPosition": pos,
        "PlayerCarMyIncidentCount": np.r_[np.zeros(300), np.full(300, 4)].astype(np.int32),
        "LapCompleted": np.linspace(0, 10, n).astype(np.int32),
    }
    path = folder / "testcar_testring 2026-10-29 18-58-03.ibt"
    write_ibt(str(path), channels, RACE_YAML)
    old = time.time() - 60                      # cerrado hace un minuto
    os.utime(path, (old, old))
    return path


def test_race_result_comes_from_the_end_of_the_race(tmp_path):
    folder = tmp_path / "telemetry"
    folder.mkdir()
    _race_file(folder)
    sessions = load_sessions(str(folder), str(tmp_path / "cache.json"))
    assert len(sessions) == 1
    race = sessions[0]
    assert race["event_type_name"] == "Race"
    assert race["start_time"] == "2026-10-29T18:58:03"
    assert (race["starting_position"], race["finish_position"]) == (2, 1)
    assert (race["incidents"], race["laps_complete"], race["finished"]) == (4, 10, True)
    assert race["event_strength_of_field"] == 1500      # sin el auto de seguridad
    assert race["track"] == {"track_name": "Test Ring", "config_name": "GP"}


def test_history_survives_deleting_the_ibt(tmp_path):
    folder = tmp_path / "telemetry"
    folder.mkdir()
    path = _race_file(folder)
    cache = str(tmp_path / "cache.json")
    assert len(load_sessions(str(folder), cache)) == 1
    os.remove(path)
    assert len(load_sessions(str(folder), cache)) == 1


def test_strength_of_field():
    assert strength_of_field([1500, 1500]) == 1500
    assert strength_of_field([0, None]) is None
    assert 1500 < strength_of_field([1000, 3000]) < 2000   # queda bajo el promedio: pesa más el más lento
