import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ibt_fixture import synthetic_session, write_ibt  # noqa: E402
from app.auto_analysis import AutoAnalyzer, spoken_summary  # noqa: E402


def test_new_session_is_analyzed_once(tmp_path, monkeypatch):
    monkeypatch.setattr("app.telemetry_analyzer.save_tracker", lambda *a, **k: None)
    monkeypatch.setattr("app.telemetry_analyzer.save_references", lambda *a, **k: None)
    monkeypatch.setattr("app.progress.BASE", str(tmp_path / "progress"))
    folder = tmp_path / "telemetry"
    folder.mkdir()
    channels, yaml_text = synthetic_session()
    ibt = folder / "testcar_testring 2026-01-01 10-00-00.ibt"
    write_ibt(str(ibt), channels, yaml_text)
    old = time.time() - 60                      # cerrado hace un minuto
    os.utime(ibt, (old, old))

    done = []
    auto = AutoAnalyzer(lambda res, path: done.append((res, path)), folder=str(folder),
                        state_path=str(tmp_path / "state.json"), log_dir=str(tmp_path / "logs"))
    assert not auto.poll(can_run=False, sync=True)        # en pista: no analiza
    assert auto.poll(can_run=True, sync=True)
    assert len(done) == 1
    res, report = done[0]
    assert res.best_lap is not None and os.path.exists(report)
    assert "Mejor vuelta" in spoken_summary(res)
    assert not auto.poll(can_run=True, sync=True)          # ya analizado


def test_file_still_being_written_is_skipped(tmp_path):
    folder = tmp_path / "telemetry"
    folder.mkdir()
    (folder / "car_track 2026-01-01 10-00-00.ibt").write_bytes(b"x" * 100)   # modificado ahora mismo
    auto = AutoAnalyzer(lambda *a: None, folder=str(folder), state_path=str(tmp_path / "s.json"),
                        log_dir=str(tmp_path / "logs"))
    assert auto.pending() is None
