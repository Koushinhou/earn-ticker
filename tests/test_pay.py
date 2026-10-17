from datetime import date, datetime, time

import pytest

from earnticker.pay import (
    DayRecord,
    PayRules,
    close_day,
    compute_day,
    is_work_day,
    month_to_date,
    overtime_running,
    period_total,
    toggle_overtime,
    year_to_date,
)

RULES = PayRules()  # annual 6,000,000, 220 days, 08:45-17:15
MON = date(2026, 10, 19)  # Monday
SAT = date(2026, 10, 17)  # Saturday
SUN = date(2026, 10, 18)

DAILY = 6_000_000 / 220
HOURLY = DAILY / 8.5
PER_SEC = HOURLY / 3600


def at(d: date, hh: int, mm: int = 0, ss: int = 0) -> datetime:
    return datetime.combine(d, time(hh, mm, ss))


# -- rates -------------------------------------------------------------------


def test_rate_math_example_numbers():
    assert RULES.working_days == 220
    assert RULES.daily_hours == 8.5
    assert RULES.daily == pytest.approx(27272.7273, abs=1e-4)
    assert RULES.hourly == pytest.approx(3208.5561, abs=1e-4)
    assert RULES.per_second == pytest.approx(0.891266, abs=1e-6)


def test_editable_days_change_rate():
    rules = PayRules(days_in_year=366, rest_days=120, annual_leave_days=10)
    assert rules.working_days == 236
    assert rules.daily == pytest.approx(6_000_000 / 236)
    assert rules.hourly == pytest.approx(6_000_000 / 236 / 8.5)
    fewer = PayRules(annual_leave_days=0)
    assert fewer.working_days == 240
    assert fewer.daily < RULES.daily


def test_editable_hours_change_rate():
    rules = PayRules(start=time(9, 0), end=time(17, 0))
    assert rules.daily_hours == 8
    assert rules.hourly == pytest.approx(DAILY / 8)


def test_degenerate_settings_do_not_divide_by_zero():
    assert PayRules(rest_days=400).daily == 0
    assert PayRules(start=time(18, 0), end=time(9, 0)).hourly == 0


# -- regular pay -------------------------------------------------------------


def test_before_start_is_zero():
    e = compute_day(RULES, MON, None, at(MON, 8, 0))
    assert e.total == 0
    assert e.phase == "before"
    assert compute_day(RULES, MON, None, at(MON, 8, 45)).total == 0


def test_mid_day_value_1430():
    e = compute_day(RULES, MON, None, at(MON, 14, 30))
    assert e.regular == pytest.approx(20700 * PER_SEC)
    assert e.regular == pytest.approx(18449.20, abs=0.01)
    assert e.overtime == 0
    assert e.phase == "working"


def test_no_lunch_pause_linear_at_1230():
    e1200 = compute_day(RULES, MON, None, at(MON, 12, 0)).total
    e1230 = compute_day(RULES, MON, None, at(MON, 12, 30)).total
    e1300 = compute_day(RULES, MON, None, at(MON, 13, 0)).total
    assert e1230 - e1200 == pytest.approx(1800 * PER_SEC)
    assert e1300 - e1230 == pytest.approx(1800 * PER_SEC)
    assert e1230 == pytest.approx((3 * 3600 + 45 * 60) * PER_SEC)


def test_at_and_after_end_capped_at_daily_without_overtime():
    rules = PayRules(auto_overtime=False)
    assert compute_day(rules, MON, None, at(MON, 17, 15)).total == pytest.approx(DAILY)
    e = compute_day(rules, MON, None, at(MON, 20, 0))
    assert e.total == pytest.approx(DAILY)
    assert e.phase == "after"


def test_one_second_tick_equals_per_second():
    a = compute_day(RULES, MON, None, at(MON, 10, 0, 0)).total
    b = compute_day(RULES, MON, None, at(MON, 10, 0, 1)).total
    assert b - a == pytest.approx(0.891266, abs=1e-6)


# -- overtime ----------------------------------------------------------------


def test_auto_overtime_weekday_125_after_end():
    e = compute_day(RULES, MON, None, at(MON, 18, 30))
    assert e.regular == pytest.approx(DAILY)
    assert e.overtime == pytest.approx(1.25 * HOURLY * 1.25)
    assert e.overtime == pytest.approx(5013.37, abs=0.01)
    assert e.overtime_running
    assert e.current_coef == 1.25
    assert e.phase == "overtime"


def test_auto_overtime_until_2300_spans_late_night():
    e = compute_day(RULES, MON, None, at(MON, 23, 0))
    weekday_part = 4.75 * HOURLY * 1.25
    late_part = 1.0 * HOURLY * 1.5
    assert weekday_part == pytest.approx(19050.80, abs=0.01)
    assert late_part == pytest.approx(4812.83, abs=0.01)
    assert e.overtime == pytest.approx(weekday_part + late_part)
    assert e.overtime == pytest.approx(23863.64, abs=0.01)
    assert e.current_coef == 1.5


def test_manual_session_2100_to_2300_spans_both_rates():
    rules = PayRules(auto_overtime=False)
    rec = DayRecord(sessions=((at(MON, 21), at(MON, 23)),))
    e = compute_day(rules, MON, rec, at(MON, 23, 30))
    assert e.overtime == pytest.approx(HOURLY * 1.25 + HOURLY * 1.5)
    assert not e.overtime_running


def test_end_overtime_stops_auto_overtime():
    rec = toggle_overtime(RULES, MON, None, at(MON, 19, 15))
    assert rec.auto_stop == at(MON, 19, 15)
    e = compute_day(RULES, MON, rec, at(MON, 21, 0))
    assert e.overtime == pytest.approx(2 * HOURLY * 1.25)
    assert not e.overtime_running
    assert e.phase == "after"


def test_toggle_before_end_starts_manual_and_restart_after_stop():
    # pressing the toggle while not running opens a manual session
    rec = toggle_overtime(RULES, MON, None, at(MON, 19, 0))  # stops auto
    rec = toggle_overtime(RULES, MON, rec, at(MON, 20, 0))  # starts manual
    assert rec.has_open_session
    e = compute_day(RULES, MON, rec, at(MON, 20, 30))
    assert e.overtime == pytest.approx(1.75 * HOURLY * 1.25 + 0.5 * HOURLY * 1.25)
    assert e.overtime_running
    rec = toggle_overtime(RULES, MON, rec, at(MON, 21, 0))
    assert not rec.has_open_session
    assert compute_day(RULES, MON, rec, at(MON, 23, 0)).overtime == pytest.approx(
        2.75 * HOURLY * 1.25
    )


def test_manual_sessions_on_weekday_exclude_regular_hours_and_overlap():
    rules = PayRules(auto_overtime=False)
    rec = DayRecord(
        sessions=(
            (at(MON, 7, 45), at(MON, 9, 45)),  # only 07:45-08:45 is overtime
            (at(MON, 18, 0), at(MON, 19, 0)),
            (at(MON, 18, 30), at(MON, 19, 30)),  # overlaps previous -> 18:00-19:30
        )
    )
    e = compute_day(rules, MON, rec, at(MON, 20, 0))
    assert e.overtime == pytest.approx(2.5 * HOURLY * 1.25)
    assert e.regular == pytest.approx(DAILY)


def test_manual_session_overlapping_auto_not_double_counted():
    rec = DayRecord(sessions=((at(MON, 18), at(MON, 19)),))
    e = compute_day(RULES, MON, rec, at(MON, 19, 15))
    assert e.overtime == pytest.approx(2 * HOURLY * 1.25)


def test_early_morning_late_night_portion():
    rules = PayRules(auto_overtime=False)
    rec = DayRecord(sessions=((at(MON, 4), at(MON, 6)),))
    e = compute_day(rules, MON, rec, at(MON, 7))
    assert e.overtime == pytest.approx(HOURLY * 1.5 + HOURLY * 1.25)


def test_rest_day_manual_overtime_135():
    rec = DayRecord(sessions=((at(SAT, 10), at(SAT, 12)),))
    e = compute_day(RULES, SAT, rec, at(SAT, 15))
    assert e.regular == 0
    assert e.overtime == pytest.approx(2 * HOURLY * 1.35)
    assert e.overtime == pytest.approx(8663.10, abs=0.01)
    assert e.phase == "rest"


def test_rest_day_running_session_and_coef():
    rec = toggle_overtime(RULES, SAT, None, at(SAT, 10))
    e = compute_day(RULES, SAT, rec, at(SAT, 11))
    assert e.overtime_running
    assert e.current_coef == 1.35
    assert e.overtime == pytest.approx(HOURLY * 1.35)


def test_rest_day_late_night_uses_max_rule():
    rec = DayRecord(sessions=((at(SAT, 21), at(SAT, 23)),))
    e = compute_day(RULES, SAT, rec, at(SAT, 23, 30))
    assert e.overtime == pytest.approx(HOURLY * 1.35 + HOURLY * 1.5)
    # if the rest-day coefficient is higher it wins at night too
    high = PayRules(coef_rest_day=1.6)
    e2 = compute_day(high, SAT, rec, at(SAT, 23, 30))
    assert e2.overtime == pytest.approx(2 * HOURLY * 1.6)


def test_rest_day_no_auto_overtime():
    e = compute_day(RULES, SAT, None, at(SAT, 20))
    assert e.total == 0
    assert not e.overtime_running


def test_overtime_stops_at_midnight_and_past_day_uses_last_seen():
    # yesterday (Monday) auto overtime never stopped; app last seen 20:00
    rec = DayRecord(last_seen=at(MON, 20))
    tue_morning = at(date(2026, 10, 20), 9)
    e = compute_day(RULES, MON, rec, tue_morning)
    assert e.regular == pytest.approx(DAILY)
    assert e.overtime == pytest.approx(2.75 * HOURLY * 1.25)
    # without last_seen nothing open-ended is counted for a past day
    assert compute_day(RULES, MON, DayRecord(), tue_morning).overtime == 0


def test_close_day_caps_at_midnight_and_continues_session():
    rec = toggle_overtime(RULES, SAT, None, at(SAT, 23))
    closed, was_open = close_day(RULES, SAT, rec)
    assert was_open
    e = compute_day(RULES, SAT, closed, at(SUN, 1))
    assert e.overtime == pytest.approx(HOURLY * 1.5)
    # work day: auto overtime closed at midnight
    closed_mon, _ = close_day(RULES, MON, DayRecord())
    e2 = compute_day(RULES, MON, closed_mon, at(date(2026, 10, 20), 1))
    assert e2.overtime == pytest.approx(4.75 * HOURLY * 1.25 + 2 * HOURLY * 1.5)


def test_overtime_running_only_for_today():
    assert overtime_running(RULES, MON, None, at(MON, 18))
    assert not overtime_running(RULES, MON, None, at(MON, 16))
    assert not overtime_running(RULES, MON, None, at(date(2026, 10, 20), 18))


# -- take-home ---------------------------------------------------------------


def test_take_home_ratio_on_off():
    off = compute_day(PayRules(take_home_ratio=0.8), MON, None, at(MON, 14, 30))
    on = compute_day(
        PayRules(take_home_enabled=True, take_home_ratio=0.8), MON, None, at(MON, 14, 30)
    )
    assert off.total == pytest.approx(20700 * PER_SEC)
    assert on.total == pytest.approx(20700 * PER_SEC * 0.8)
    ot = compute_day(PayRules(take_home_enabled=True), MON, None, at(MON, 18, 15))
    assert ot.overtime == pytest.approx(HOURLY * 1.25 * 0.8)
    assert ot.regular == pytest.approx(DAILY * 0.8)


# -- calendar ----------------------------------------------------------------


def test_rest_day_detection_weekend_and_holidays():
    rules = PayRules(holidays=frozenset({date(2026, 11, 3)}))
    assert is_work_day(rules, MON)
    assert not is_work_day(rules, SAT)
    assert not is_work_day(rules, SUN)
    assert not is_work_day(rules, date(2026, 11, 3))  # Tuesday holiday
    assert is_work_day(rules, date(2026, 11, 4))
    e = compute_day(rules, date(2026, 11, 3), None, datetime(2026, 11, 3, 14, 30))
    assert e.total == 0 and e.phase == "rest"


def test_custom_work_weekdays():
    rules = PayRules(work_weekdays=frozenset({1, 2, 3, 4, 5}))  # Tue-Sat
    assert not is_work_day(rules, MON)
    assert is_work_day(rules, SAT)


# -- totals ------------------------------------------------------------------


def test_month_to_date_counts_completed_work_days_plus_today():
    # October 2026: Oct 1 is Thursday. Before Mon Oct 19 there are work days
    # 1,2,5-9,12-16 = 12 (no holidays configured).
    now = at(MON, 14, 30)
    assert month_to_date(RULES, now, {}) == pytest.approx(12 * DAILY + 20700 * PER_SEC)


def test_month_to_date_with_holiday_and_recorded_overtime():
    rules = PayRules(holidays=frozenset({date(2026, 10, 12)}))
    records = {
        date(2026, 10, 14): DayRecord(auto_stop=datetime(2026, 10, 14, 19, 15)),
        SAT: DayRecord(sessions=((at(SAT, 10), at(SAT, 12)),)),
    }
    now = at(MON, 14, 30)
    expected = 11 * DAILY + 2 * HOURLY * 1.25 + 2 * HOURLY * 1.35 + 20700 * PER_SEC
    assert month_to_date(rules, now, records) == pytest.approx(expected)


def test_month_to_date_on_rest_day():
    now = at(SAT, 12)
    # Oct 1..16 work days: 1,2,5-9,12-16 = 12
    assert month_to_date(RULES, now, {}) == pytest.approx(12 * DAILY)


def test_year_to_date():
    # 2026-01-01 is Thursday. Jan 1..Jan 9 completed work days: 1,2,5,6,7,8 = 6
    now = datetime(2026, 1, 9, 9, 45)
    assert year_to_date(RULES, now, {}) == pytest.approx(6 * DAILY + 3600 * PER_SEC)


def test_period_total_with_take_home():
    rules = PayRules(take_home_enabled=True, take_home_ratio=0.5)
    now = at(MON, 8)
    assert period_total(rules, date(2026, 10, 12), now, {}) == pytest.approx(5 * DAILY * 0.5)
