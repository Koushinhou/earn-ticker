import json
from datetime import date, datetime, time
from pathlib import Path

import pytest

from earnticker import settings as settings_mod
from earnticker.pay import DayRecord, PayRules, compute_day
from earnticker.settings import (
    Settings,
    load_settings,
    parse_holiday_file,
    read_settings,
    resolve_config_dir,
    save_settings,
)
from earnticker.state import StateStore

ROOT = Path(__file__).resolve().parent.parent


def test_settings_round_trip(tmp_path):
    s = Settings(
        annual_income=1_234_567,
        currency_symbol="$",
        rest_days=120,
        start_time="09:00",
        end_time="18:00",
        work_weekdays=[0, 1, 2, 3],
        holidays=["2026-11-03", "2026-01-01"],
        amount_color="#00FF00",
        take_home_enabled=True,
        take_home_ratio=0.75,
        panel_x=100,
        panel_y=-20,
        show_month_total=True,
        coef_rest_late_night=1.75,
        auto_overtime_cap_hours=4.5,
    )
    path = tmp_path / "earnticker_settings.json"
    save_settings(path, s)
    loaded, existed = load_settings(path)
    assert existed
    assert loaded.annual_income == 1_234_567
    assert loaded.currency_symbol == "$"
    assert loaded.work_weekdays == [0, 1, 2, 3]
    assert loaded.holidays == ["2026-01-01", "2026-11-03"]  # normalized/sorted
    assert loaded.panel_x == 100 and loaded.panel_y == -20
    assert loaded.coef_rest_late_night == 1.75
    assert loaded.auto_overtime_cap_hours == 4.5
    rules = loaded.pay_rules()
    assert rules.coef_rest_late_night == 1.75 and rules.auto_overtime_cap_hours == 4.5
    assert loaded.to_dict() == s.copy().to_dict()
    # file is human readable UTF-8 with the currency symbol intact
    assert "¥" in json.dumps(Settings().to_dict(), ensure_ascii=False)


def test_settings_to_pay_rules():
    rules = Settings(start_time="09:00", end_time="18:30", holidays=["2026-11-03"]).pay_rules()
    assert rules.start == time(9, 0) and rules.end == time(18, 30)
    assert date(2026, 11, 3) in rules.holidays
    assert rules.working_days == 220
    default = Settings().pay_rules()
    assert default == PayRules()


def test_missing_settings_file_gives_defaults(tmp_path):
    s, existed = load_settings(tmp_path / "nope.json")
    assert not existed
    assert s.annual_income == 6_000_000


def test_corrupt_json_falls_back_and_backs_up(tmp_path):
    path = tmp_path / "earnticker_settings.json"
    path.write_text("{ this is not json", encoding="utf-8")
    s, existed = load_settings(path)
    assert existed
    assert s == Settings()
    baks = list(tmp_path.glob("earnticker_settings.json.*.bak"))
    assert len(baks) == 1
    assert baks[0].read_text(encoding="utf-8") == "{ this is not json"
    # a second corruption never overwrites the first backup
    result = read_settings(path)
    assert result.corrupt and result.backup is not None
    assert len(list(tmp_path.glob("earnticker_settings.json.*.bak"))) == 2


def test_backup_name_has_timestamp(tmp_path):
    path = tmp_path / "earnticker_settings.json"
    path.write_text("x", encoding="utf-8")
    bak = settings_mod.backup_corrupt(path, datetime(2026, 10, 18, 1, 45, 0))
    assert bak == tmp_path / "earnticker_settings.json.20261018-014500.bak"
    again = settings_mod.backup_corrupt(path, datetime(2026, 10, 18, 1, 45, 0))
    assert again == tmp_path / "earnticker_settings.json.20261018-014500-1.bak"
    assert bak.exists() and again.exists()


def test_settings_with_bom_load(tmp_path):
    path = tmp_path / "earnticker_settings.json"
    path.write_bytes(
        json.dumps({"annual_income": 1_234_567, "currency_symbol": "¥"}, ensure_ascii=False).encode(
            "utf-8-sig"
        )
    )
    result = read_settings(path)
    assert not result.corrupt
    assert result.settings.annual_income == 1_234_567
    assert result.settings.currency_symbol == "¥"
    save_settings(path, result.settings)
    assert not path.read_bytes().startswith(b"\xef\xbb\xbf")  # written without BOM


def test_state_with_bom_loads(tmp_path):
    path = tmp_path / "earnticker_state.json"
    data = {
        "version": 1,
        "days": {"2026-10-17": {"sessions": [["2026-10-17T10:00:00", "2026-10-17T12:00:00"]]}},
    }
    path.write_bytes(json.dumps(data).encode("utf-8-sig"))
    store = StateStore(path)
    store.load()
    assert not store.corrupt
    assert len(store.get(date(2026, 10, 17)).sessions) == 1


def test_old_settings_without_new_fields_load_defaults(tmp_path):
    path = tmp_path / "s.json"
    old = Settings().to_dict()
    del old["coef_rest_late_night"]
    del old["auto_overtime_cap_hours"]
    path.write_text(json.dumps(old), encoding="utf-8")
    s, _ = load_settings(path)
    assert s.coef_rest_late_night == 1.6
    assert s.auto_overtime_cap_hours == 3.0


def test_normalized_clamps_and_rejects_non_finite():
    s = Settings(
        annual_income=float("nan"),
        coef_weekday=float("inf"),
        coef_late_night=0.5,
        coef_rest_day=99,
        coef_rest_late_night=float("-inf"),
        auto_overtime_cap_hours=50,
        take_home_ratio=float("nan"),
        opacity=-1,
    ).normalized()
    assert s.annual_income == 6_000_000
    assert s.coef_weekday == 1.25
    assert s.coef_late_night == 1.0
    assert s.coef_rest_day == 10.0
    assert s.coef_rest_late_night == 1.6
    assert s.auto_overtime_cap_hours == 12.0
    assert s.take_home_ratio == 0.8
    assert s.opacity == 0.0
    assert Settings(annual_income=1e20).normalized().annual_income == 1e10
    assert Settings(annual_income=-5).normalized().annual_income == 0
    assert Settings(auto_overtime_cap_hours=-1).normalized().auto_overtime_cap_hours == 0


def test_normalized_keeps_at_least_one_working_day():
    s = Settings(days_in_year=365, rest_days=400, annual_leave_days=30).normalized()
    assert s.rest_days == 364 and s.annual_leave_days == 0
    assert s.pay_rules().working_days == 1
    s2 = Settings(days_in_year=365, rest_days=300, annual_leave_days=100).normalized()
    assert (s2.rest_days, s2.annual_leave_days) == (300, 64)
    assert s2.pay_rules().working_days == 1
    assert Settings(days_in_year=0).normalized().days_in_year == 1
    assert Settings().normalized().pay_rules().working_days == 220


def test_json_nan_and_infinity_fall_back(tmp_path):
    path = tmp_path / "s.json"
    path.write_text(
        '{"annual_income": NaN, "coef_weekday": Infinity, "rest_days": 1e400,'
        ' "auto_overtime_cap_hours": "x"}',
        encoding="utf-8",
    )
    s, _ = load_settings(path)
    assert s.annual_income == 6_000_000
    assert s.coef_weekday == 1.25
    assert s.rest_days == 125
    assert s.auto_overtime_cap_hours == 3.0


def test_wrong_types_fall_back_per_field(tmp_path):
    path = tmp_path / "s.json"
    path.write_text(
        json.dumps(
            {
                "annual_income": "abc",
                "rest_days": True,
                "start_time": "nonsense",
                "opacity": 5,
                "work_weekdays": [0, 9, 2],
                "holidays": ["2026-02-30", "2026-02-11"],
                "unknown_key": 1,
            }
        ),
        encoding="utf-8",
    )
    s, _ = load_settings(path)
    assert s.annual_income == 6_000_000
    assert s.rest_days == 125
    assert s.start_time == "08:45"
    assert s.opacity == 1.0
    assert s.work_weekdays == [0, 2]
    assert s.holidays == ["2026-02-11"]


def test_json_array_root_is_treated_as_corrupt(tmp_path):
    path = tmp_path / "s.json"
    path.write_text("[1, 2]", encoding="utf-8")
    s, _ = load_settings(path)
    assert s == Settings()
    assert list(tmp_path.glob("s.json.*.bak"))


def test_path_override_wins(tmp_path):
    target = tmp_path / "custom"
    assert resolve_config_dir(target) == target
    assert target.is_dir()


def test_path_frozen_uses_exe_dir_when_writable(tmp_path):
    exe = tmp_path / "app" / "EarnTicker.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"")
    got = resolve_config_dir(frozen=True, executable=exe, fallback=tmp_path / "fb")
    assert got == exe.parent.resolve()


def test_path_fallback_when_exe_dir_not_writable(tmp_path, monkeypatch):
    exe = tmp_path / "ro" / "EarnTicker.exe"
    exe.parent.mkdir()
    real = settings_mod.is_writable_dir

    def fake_writable(path):
        if Path(path).resolve() == exe.parent.resolve():
            return False
        return real(path)

    monkeypatch.setattr(settings_mod, "is_writable_dir", fake_writable)
    fb = tmp_path / "appdata" / "EarnTicker"
    assert resolve_config_dir(frozen=True, executable=exe, fallback=fb) == fb
    assert fb.is_dir()


def test_path_from_source_never_repo_root(tmp_path):
    fb = tmp_path / "appdata" / "EarnTicker"
    got = resolve_config_dir(frozen=False, fallback=fb)
    assert got == fb
    assert got.resolve() != ROOT


def test_is_writable_dir_real_check(tmp_path):
    assert settings_mod.is_writable_dir(tmp_path)
    assert not list(tmp_path.iterdir())  # temp probe removed


def test_example_settings_file_loads():
    s, existed = load_settings(ROOT / "config" / "settings.example.json")
    assert existed
    assert s.annual_income == 6_000_000
    assert s.pay_rules().working_days == 220


def test_holiday_example_and_import_formats():
    data = json.loads((ROOT / "config" / "holidays.example.json").read_text(encoding="utf-8"))
    days = parse_holiday_file(data)
    assert "2026-01-01" in days and "2027-11-23" in days
    assert "2026-09-22" in days  # citizens' holiday between two holidays
    assert all(d[:4] in ("2026", "2027") for d in days)
    assert parse_holiday_file(["2026-01-01", "bad"]) == ["2026-01-01"]
    assert parse_holiday_file({"2026-05-05": "Children's Day"}) == ["2026-05-05"]
    assert parse_holiday_file([{"date": "2026-05-04"}]) == ["2026-05-04"]
    assert parse_holiday_file(42) == []


# -- state -------------------------------------------------------------------


def test_state_round_trip_and_toggle(tmp_path):
    path = tmp_path / "earnticker_state.json"
    store = StateStore(path)
    rules = PayRules()
    sat = date(2026, 10, 17)
    store.toggle(rules, datetime(2026, 10, 17, 10, 0))
    store.toggle(rules, datetime(2026, 10, 17, 12, 0))
    store.toggle(rules, datetime(2026, 10, 17, 13, 0))  # still open

    again = StateStore(path)
    again.load()
    rec = again.get(sat)
    assert len(rec.sessions) == 2
    assert rec.sessions[1][1] is None
    e = compute_day(rules, sat, rec, datetime(2026, 10, 17, 14, 0))
    assert e.overtime == pytest.approx(3 * rules.hourly * 1.35)
    assert e.overtime_running


def test_state_corrupt_file(tmp_path):
    path = tmp_path / "earnticker_state.json"
    path.write_text("garbage", encoding="utf-8")
    store = StateStore(path)
    store.load()
    assert store.records == {}
    assert store.corrupt
    assert store.backup is not None and store.backup.exists()
    assert store.backup.name.startswith("earnticker_state.json.")


def test_state_rollover_continues_open_session(tmp_path):
    store = StateStore(tmp_path / "st.json")
    rules = PayRules()
    sat, sun = date(2026, 10, 17), date(2026, 10, 18)
    store.toggle(rules, datetime(2026, 10, 17, 23, 0))
    store.touch(datetime(2026, 10, 17, 23, 59, 30))
    store.rollover(rules, sat, sun, datetime(2026, 10, 18, 0, 0, 1))
    assert not store.get(sat).has_open_session
    assert store.get(sun).sessions == ((datetime(2026, 10, 18, 0, 0), None),)
    e = compute_day(rules, sun, store.get(sun), datetime(2026, 10, 18, 1, 0))
    assert e.overtime == pytest.approx(rules.hourly * 1.6)


def test_state_rollover_after_sleep_does_not_continue(tmp_path):
    store = StateStore(tmp_path / "st.json")
    rules = PayRules()
    sat, sun = date(2026, 10, 17), date(2026, 10, 18)
    store.toggle(rules, datetime(2026, 10, 17, 21, 0))
    store.touch(datetime(2026, 10, 17, 21, 30))
    store.rollover(rules, sat, sun, datetime(2026, 10, 18, 9, 0))
    closed = ((datetime(2026, 10, 17, 21), datetime(2026, 10, 17, 21, 31)),)
    assert store.get(sat).sessions == closed
    assert store.get(sun).sessions == ()


def test_state_touch_and_prune(tmp_path):
    store = StateStore(tmp_path / "st.json")
    store.records[date(2024, 1, 1)] = DayRecord()
    store.touch(datetime(2026, 10, 19, 18, 0))
    store.prune(date(2026, 10, 19))
    assert date(2024, 1, 1) not in store.records
    assert store.get(date(2026, 10, 19)).last_seen == datetime(2026, 10, 19, 18, 0)
