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
TUE = date(2026, 10, 20)

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


def test_auto_overtime_capped_at_end_plus_3h():
    # never stopped, at 23:00 only 17:15-20:15 counts
    e = compute_day(RULES, MON, None, at(MON, 23, 0))
    assert e.overtime == pytest.approx(3 * HOURLY * 1.25)
    assert e.overtime == pytest.approx(12032.09, abs=0.01)
    assert e.total == pytest.approx(39304.81, abs=0.01)
    assert not e.overtime_running
    assert not e.overtime_active
    assert e.phase == "after"
    assert e.current_coef == 0


def test_auto_overtime_cap_boundary_2015():
    before = compute_day(RULES, MON, None, at(MON, 20, 14, 59))
    assert before.overtime_running and before.overtime_active
    assert overtime_running(RULES, MON, None, at(MON, 20, 14, 59))
    assert not overtime_running(RULES, MON, None, at(MON, 20, 15))
    at_cap = compute_day(RULES, MON, None, at(MON, 20, 15))
    later = compute_day(RULES, MON, None, at(MON, 20, 16))
    assert at_cap.overtime == pytest.approx(3 * HOURLY * 1.25)
    assert later.overtime == at_cap.overtime
    # toggling after the cap starts a manual session (nothing to stop)
    rec = toggle_overtime(RULES, MON, None, at(MON, 20, 30))
    assert rec.has_open_session and rec.auto_stop is None


def test_auto_overtime_cap_is_configurable():
    rules = PayRules(auto_overtime_cap_hours=0.5)
    e = compute_day(rules, MON, None, at(MON, 19))
    assert e.overtime == pytest.approx(0.5 * HOURLY * 1.25)
    zero = PayRules(auto_overtime_cap_hours=0)
    e0 = compute_day(zero, MON, None, at(MON, 18))
    assert e0.overtime == 0 and not e0.overtime_running
    uncapped = PayRules(auto_overtime_cap_hours=12)
    e12 = compute_day(uncapped, MON, None, at(MON, 23))
    assert e12.overtime == pytest.approx(4.75 * HOURLY * 1.25 + HOURLY * 1.5)
    assert e12.current_coef == 1.5


def test_manual_session_past_cap_still_accrues():
    # weekday: auto 17:15-20:15 plus manual 21:00-23:00 (not capped)
    rec = DayRecord(sessions=((at(MON, 21), at(MON, 23)),))
    e = compute_day(RULES, MON, rec, at(MON, 23, 30))
    manual = HOURLY * 1.25 + HOURLY * 1.5
    assert manual == pytest.approx(8823.53, abs=0.01)
    assert e.overtime == pytest.approx(3 * HOURLY * 1.25 + manual)
    assert e.overtime == pytest.approx(20855.62, abs=0.01)
    # an open manual session keeps running after the cap
    rec = toggle_overtime(RULES, MON, None, at(MON, 21))
    e = compute_day(RULES, MON, rec, at(MON, 22, 30))
    assert e.overtime_running and e.current_coef == 1.5
    assert e.overtime == pytest.approx(3 * HOURLY * 1.25 + HOURLY * 1.25 + 0.5 * HOURLY * 1.5)


def test_manual_session_started_in_regular_hours_pending_until_end():
    rules = PayRules(auto_overtime=False)
    rec = toggle_overtime(rules, MON, None, at(MON, 15))
    e = compute_day(rules, MON, rec, at(MON, 16))
    assert e.overtime_active
    assert not e.overtime_running
    assert e.overtime_pending_from == at(MON, 17, 15)
    assert e.phase == "working"
    assert e.overtime == 0
    e2 = compute_day(rules, MON, rec, at(MON, 17, 15))
    assert e2.overtime_running and e2.overtime_pending_from is None
    assert e2.current_coef == 1.25
    # with auto overtime on the same session is also pending until 17:15
    e3 = compute_day(RULES, MON, toggle_overtime(RULES, MON, None, at(MON, 15)), at(MON, 16))
    assert not e3.overtime_running and e3.overtime_pending_from == at(MON, 17, 15)
    # toggling while pending closes the session
    closed = toggle_overtime(rules, MON, rec, at(MON, 16))
    assert not closed.has_open_session


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


def test_rest_day_late_night_uses_own_coefficient():
    rec = DayRecord(sessions=((at(SAT, 22), at(SAT, 23)),))
    e = compute_day(RULES, SAT, rec, at(SAT, 23, 30))
    assert e.overtime == pytest.approx(HOURLY * 1.6)
    assert e.overtime == pytest.approx(5133.69, abs=0.01)
    rec2 = DayRecord(sessions=((at(SAT, 21), at(SAT, 23)),))
    e2 = compute_day(RULES, SAT, rec2, at(SAT, 23, 30))
    assert e2.overtime == pytest.approx(HOURLY * 1.35 + HOURLY * 1.6)
    custom = PayRules(coef_rest_late_night=2.0)
    assert compute_day(custom, SAT, rec, at(SAT, 23, 30)).overtime == pytest.approx(HOURLY * 2)
    running = toggle_overtime(RULES, SAT, None, at(SAT, 22))
    assert compute_day(RULES, SAT, running, at(SAT, 22, 30)).current_coef == 1.6


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
    # app alive across midnight: last seen 23:59:30, rollover at 00:00:01
    rec = toggle_overtime(RULES, SAT, None, at(SAT, 23))
    rec = DayRecord(rec.sessions, rec.auto_stop, at(SAT, 23, 59, 30))
    closed, cont = close_day(RULES, SAT, rec, at(SUN, 0, 0, 1))
    assert cont
    assert closed.last_seen == at(SUN, 0)
    e = compute_day(RULES, SAT, closed, at(SUN, 1))
    assert e.overtime == pytest.approx(HOURLY * 1.6)
    # work day with app alive at midnight: auto overtime closed at midnight but capped
    closed_mon, _ = close_day(RULES, MON, DayRecord(last_seen=at(MON, 23, 59, 30)))
    assert closed_mon.auto_stop == at(TUE, 0)
    e2 = compute_day(RULES, MON, closed_mon, at(TUE, 1))
    assert e2.overtime == pytest.approx(3 * HOURLY * 1.25)
    uncapped = PayRules(auto_overtime_cap_hours=12)
    e3 = compute_day(uncapped, MON, closed_mon, at(TUE, 1))
    assert e3.overtime == pytest.approx(4.75 * HOURLY * 1.25 + 2 * HOURLY * 1.5)


def test_close_day_after_sleep_across_midnight_uses_last_seen():
    # M1 repro: Monday last seen 20:00, PC slept, woke Tuesday 08:00
    rec = DayRecord(last_seen=at(MON, 20))
    closed, cont = close_day(RULES, MON, rec, at(TUE, 8))
    assert not cont
    assert closed.auto_stop == at(MON, 20, 1)
    assert closed.last_seen == at(MON, 20, 1)
    e = compute_day(RULES, MON, closed, at(TUE, 8))
    expected = 2.75 * HOURLY * 1.25 + 60 * PER_SEC * 1.25
    assert e.overtime == pytest.approx(expected)
    assert e.overtime == pytest.approx(11029.41 + 66.84, abs=0.02)
    # month total is not inflated
    mtd = month_to_date(RULES, at(TUE, 8), {MON: closed})
    assert mtd == pytest.approx(13 * DAILY + expected)


def test_close_day_does_not_continue_open_session_after_long_sleep():
    rec = toggle_overtime(RULES, SAT, None, at(SAT, 21))
    rec = DayRecord(rec.sessions, rec.auto_stop, at(SAT, 21, 30))
    closed, cont = close_day(RULES, SAT, rec, at(SUN, 9))
    assert not cont
    assert closed.sessions == ((at(SAT, 21), at(SAT, 21, 31)),)
    # slept from 23:59:30 to 09:00: last_seen near midnight but now is not
    rec2 = DayRecord(rec.sessions, None, at(SAT, 23, 59, 30))
    _, cont2 = close_day(RULES, SAT, rec2, at(SUN, 9))
    assert not cont2


def test_close_day_without_last_seen_counts_nothing_open_ended():
    rec = toggle_overtime(RULES, SAT, None, at(SAT, 21))
    closed, cont = close_day(RULES, SAT, DayRecord(rec.sessions), at(SUN, 9))
    assert not cont
    assert compute_day(RULES, SAT, closed, at(SUN, 9)).overtime == 0
    closed_mon, _ = close_day(RULES, MON, DayRecord(), at(TUE, 9))
    assert compute_day(RULES, MON, closed_mon, at(TUE, 9)).overtime == 0


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
