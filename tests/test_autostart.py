import sys
from pathlib import Path

import pytest

from earnticker import autostart


def test_shortcut_path_in_user_startup_folder(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    expected = tmp_path / "Microsoft/Windows/Start Menu/Programs/Startup/EarnTicker.lnk"
    assert autostart.shortcut_path() == expected
    assert not autostart.is_enabled()


def test_disable_is_noop_when_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert autostart.disable()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows python layout")
def test_launch_target_from_source():
    target, args, workdir = autostart.launch_target(Path("C:/cfg dir"))
    assert target.lower().endswith(("pythonw.exe", "python.exe"))
    assert args.startswith("-m earnticker")
    assert '--config-dir "' in args
    assert Path(workdir, "earnticker").is_dir()


def test_ps_quote_escapes_single_quotes():
    assert autostart._ps_quote("it's") == "'it''s'"


def test_refresh_is_noop_when_shortcut_matches(monkeypatch):
    target, args, _ = autostart.launch_target()
    monkeypatch.setattr(autostart, "read_shortcut", lambda: (target, args))
    calls = []
    monkeypatch.setattr(autostart, "enable", lambda cfg=None: calls.append(cfg) or True)
    assert autostart.refresh()
    assert calls == []


def test_refresh_rewrites_stale_shortcut(monkeypatch):
    monkeypatch.setattr(autostart, "read_shortcut", lambda: ("C:/old/EarnTicker.exe", ""))
    calls = []
    monkeypatch.setattr(autostart, "enable", lambda cfg=None: calls.append(cfg) or True)
    assert autostart.refresh()
    assert calls == [None]


def test_enable_reports_failure_when_powershell_blocked(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))

    def blocked(*_a, **_k):
        raise OSError("powershell not allowed")

    monkeypatch.setattr(autostart.subprocess, "run", blocked)
    assert autostart.enable() is False
    assert autostart.read_shortcut() is None
