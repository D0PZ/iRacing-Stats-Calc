import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import paths  # noqa: E402
from app.util import atomic_write_json, num, nums, slug  # noqa: E402


def test_paths_from_source_use_repo_root():
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    assert paths.data_dir("setups") == os.path.join(repo, "data", "setups")
    assert paths.logs_dir() == os.path.join(repo, "logs")


def test_paths_in_exe_live_next_to_executable(monkeypatch, tmp_path):
    """En el .exe (onefile) __file__ está en una carpeta temporal: los datos deben quedar junto al .exe"""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "iRacing Stats Calculator.exe"))
    assert paths.data_dir("ingeniero.json") == str(tmp_path / "data" / "ingeniero.json")
    assert paths.logs_dir() == str(tmp_path / "logs")


def test_slug_and_numbers():
    assert slug("Porsche 911 Cup (992)") == "porsche_911_cup_992"
    assert slug("") == "x" and slug(None, default="auto") == "auto"
    assert num("-2.5 deg") == -2.5 and num("+3 clicks") == 3.0 and num(None) is None
    assert nums("78C, 82C, 85C") == [78.0, 82.0, 85.0]


def test_atomic_write_json_replaces_without_leftovers(tmp_path):
    path = str(tmp_path / "sub" / "x.json")
    atomic_write_json(path, {"a": 1})
    atomic_write_json(path, {"a": 2})
    with open(path, encoding="utf-8") as f:
        assert json.load(f) == {"a": 2}
    assert os.listdir(tmp_path / "sub") == ["x.json"]
