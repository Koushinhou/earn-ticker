from pathlib import Path

from earnticker import autostart


def test_shortcut_path_in_user_startup_folder(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    expected = tmp_path / "Microsoft/Windows/Start Menu/Programs/Startup/EarnTicker.lnk"
    assert autostart.shortcut_path() == expected
    assert not autostart.is_enabled()


def test_disable_is_noop_when_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert autostart.disable()


def test_launch_target_from_source():
    target, args, workdir = autostart.launch_target(Path("C:/cfg dir"))
    assert target.lower().endswith(("pythonw.exe", "python.exe"))
    assert args.startswith("-m earnticker")
    assert '--config-dir "' in args
    assert Path(workdir, "earnticker").is_dir()


def test_ps_quote_escapes_single_quotes():
    assert autostart._ps_quote("it's") == "'it''s'"
