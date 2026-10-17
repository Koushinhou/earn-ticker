"""Pure pay calculation logic for EarnTicker.

This module must not import Qt. Every function that depends on the current
moment takes an explicit ``now`` argument so results are deterministic and
easy to test.

Rules (see README / docs/HAND_CALC.md):

* working_days = days_in_year - rest_days - annual_leave_days
* daily = annual_income / working_days
* hourly = daily / daily_hours  (daily_hours = end - start, no lunch deduction)
* per_second = hourly / 3600
* Regular pay accrues linearly between start and end on work days.
* Overtime is paid at hourly x coefficient. Late-night seconds (22:00-05:00)
  use the late-night coefficient (on rest days: max(rest, late-night)).
* Overtime accrual for a day stops at local midnight.
* For past days, anything still "running" (auto overtime that was never
  stopped, an open manual session) is counted until ``last_seen``, the last
  moment EarnTicker was observed running that day.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

LATE_NIGHT_START = time(22, 0)
LATE_NIGHT_END = time(5, 0)

Interval = tuple[datetime, datetime]


@dataclass(frozen=True)
class PayRules:
    """All parameters needed by the pay calculation."""

    annual_income: float = 6_000_000.0
    days_in_year: int = 365
    rest_days: int = 125
    annual_leave_days: int = 20
    start: time = time(8, 45)
    end: time = time(17, 15)
    work_weekdays: frozenset[int] = frozenset({0, 1, 2, 3, 4})  # Mon=0
    holidays: frozenset[date] = frozenset()
    auto_overtime: bool = True
    coef_weekday: float = 1.25
    coef_late_night: float = 1.5
    coef_rest_day: float = 1.35
    take_home_enabled: bool = False
    take_home_ratio: float = 0.8

    @property
    def working_days(self) -> int:
        return max(self.days_in_year - self.rest_days - self.annual_leave_days, 0)

    @property
    def daily_seconds(self) -> float:
        """Length of the regular work day in seconds (0 if end <= start)."""
        start = _seconds_of(self.start)
        end = _seconds_of(self.end)
        return float(max(end - start, 0))

    @property
    def daily_hours(self) -> float:
        return self.daily_seconds / 3600.0

    @property
    def daily(self) -> float:
        """Gross pay for one full regular work day."""
        if self.working_days <= 0:
            return 0.0
        return self.annual_income / self.working_days

    @property
    def hourly(self) -> float:
        if self.daily_hours <= 0:
            return 0.0
        return self.daily / self.daily_hours

    @property
    def per_second(self) -> float:
        return self.hourly / 3600.0

    @property
    def ratio(self) -> float:
        """Multiplier applied to every displayed amount."""
        return self.take_home_ratio if self.take_home_enabled else 1.0


@dataclass(frozen=True)
class DayRecord:
    """Overtime-related runtime state of a single calendar day."""

    # Manual overtime sessions: (start, end) where end is None while running.
    sessions: tuple[tuple[datetime, datetime | None], ...] = ()
    # Moment the user pressed "end overtime" while auto overtime was running.
    auto_stop: datetime | None = None
    # Last moment the app was seen running on this day (used for past days).
    last_seen: datetime | None = None

    @property
    def has_open_session(self) -> bool:
        return any(end is None for _, end in self.sessions)


@dataclass(frozen=True)
class DayEarnings:
    """Earnings of one day. All amounts already include the take-home ratio."""

    day: date
    is_work_day: bool
    regular: float
    overtime: float
    phase: str  # "rest", "before", "working", "after", "overtime", "future"
    overtime_running: bool = False
    current_coef: float = 0.0
    overtime_intervals: tuple[Interval, ...] = field(default=())

    @property
    def total(self) -> float:
        return self.regular + self.overtime


# ---------------------------------------------------------------------------
# helpers


def _seconds_of(t: time) -> int:
    return t.hour * 3600 + t.minute * 60 + t.second


def day_bounds(d: date) -> Interval:
    start = datetime.combine(d, time())
    return start, start + timedelta(days=1)


def is_work_day(rules: PayRules, d: date) -> bool:
    return d.weekday() in rules.work_weekdays and d not in rules.holidays


def merge_intervals(intervals: Iterable[Interval]) -> list[Interval]:
    items = sorted((a, b) for a, b in intervals if b > a)
    merged: list[Interval] = []
    for a, b in items:
        if merged and a <= merged[-1][1]:
            if b > merged[-1][1]:
                merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    return merged


def subtract_interval(intervals: Iterable[Interval], cut: Interval) -> list[Interval]:
    c0, c1 = cut
    out: list[Interval] = []
    for a, b in intervals:
        if b <= c0 or a >= c1:
            out.append((a, b))
            continue
        if a < c0:
            out.append((a, c0))
        if b > c1:
            out.append((c1, b))
    return out


def is_late_night(moment: datetime) -> bool:
    t = moment.time()
    return t >= LATE_NIGHT_START or t < LATE_NIGHT_END


def overtime_coef(rules: PayRules, work_day: bool, late_night: bool) -> float:
    if work_day:
        return rules.coef_late_night if late_night else rules.coef_weekday
    if late_night:
        return max(rules.coef_rest_day, rules.coef_late_night)
    return rules.coef_rest_day


def _split_late_night(a: datetime, b: datetime) -> list[tuple[datetime, datetime, bool]]:
    """Split [a, b) at 05:00 and 22:00 boundaries; flag late-night pieces."""
    cuts: list[datetime] = []
    d = a.date()
    while datetime.combine(d, time()) < b:
        for t in (LATE_NIGHT_END, LATE_NIGHT_START):
            point = datetime.combine(d, t)
            if a < point < b:
                cuts.append(point)
        d += timedelta(days=1)
    points = [a, *sorted(cuts), b]
    return [(p, q, is_late_night(p)) for p, q in zip(points, points[1:], strict=False) if q > p]


# ---------------------------------------------------------------------------
# core calculation


def regular_seconds(rules: PayRules, d: date, now: datetime) -> float:
    """Seconds of regular work accrued on day ``d`` as of ``now``."""
    if not is_work_day(rules, d) or rules.daily_seconds <= 0:
        return 0.0
    start = datetime.combine(d, rules.start)
    end = datetime.combine(d, rules.end)
    if now <= start:
        return 0.0
    return (min(now, end) - start).total_seconds()


def _effective_cut(d: date, record: DayRecord, now: datetime) -> datetime:
    """Moment until which open-ended overtime runs for day ``d``."""
    day_start, day_end = day_bounds(d)
    if now < day_end:
        return max(now, day_start)
    if record.last_seen is None:
        return day_start
    return min(max(record.last_seen, day_start), day_end)


def overtime_intervals(
    rules: PayRules, d: date, record: DayRecord | None, now: datetime
) -> list[Interval]:
    """Merged overtime intervals of day ``d`` as of ``now``.

    Auto overtime runs from ``end`` until ``auto_stop`` (or until now / the
    last-seen moment). Manual sessions are added; overlaps are merged and the
    regular work window is removed so no second is paid twice.
    """
    record = record or DayRecord()
    day_start, day_end = day_bounds(d)
    if now <= day_start:
        return []
    cut = _effective_cut(d, record, now)
    limit = min(now, day_end)
    work_day = is_work_day(rules, d)

    raw: list[Interval] = []
    if work_day and rules.auto_overtime and rules.daily_seconds > 0:
        a0 = datetime.combine(d, rules.end)
        a1 = record.auto_stop if record.auto_stop is not None else cut
        raw.append((a0, min(a1, limit)))
    for s, e in record.sessions:
        end = e if e is not None else cut
        raw.append((max(s, day_start), min(end, limit)))

    merged = merge_intervals(raw)
    if work_day and rules.daily_seconds > 0:
        merged = subtract_interval(
            merged, (datetime.combine(d, rules.start), datetime.combine(d, rules.end))
        )
    return merged


def overtime_amount(rules: PayRules, d: date, intervals: Iterable[Interval]) -> float:
    """Gross overtime pay (without take-home ratio) for the given intervals."""
    work_day = is_work_day(rules, d)
    total = 0.0
    for a, b in intervals:
        for p, q, late in _split_late_night(a, b):
            coef = overtime_coef(rules, work_day, late)
            total += rules.hourly * coef * (q - p).total_seconds() / 3600.0
    return total


def overtime_running(rules: PayRules, d: date, record: DayRecord | None, now: datetime) -> bool:
    """True if overtime is accruing at ``now`` (only meaningful for today)."""
    record = record or DayRecord()
    if now.date() != d:
        return False
    if record.has_open_session:
        return True
    if is_work_day(rules, d) and rules.auto_overtime and rules.daily_seconds > 0:
        return now >= datetime.combine(d, rules.end) and record.auto_stop is None
    return False


def compute_day(
    rules: PayRules, d: date, record: DayRecord | None, now: datetime
) -> DayEarnings:
    """Full earnings breakdown for day ``d`` as of ``now``."""
    record = record or DayRecord()
    work_day = is_work_day(rules, d)
    day_start, _ = day_bounds(d)
    if now < day_start:
        return DayEarnings(d, work_day, 0.0, 0.0, "future")

    ratio = rules.ratio
    regular = rules.per_second * regular_seconds(rules, d, now) * ratio
    intervals = overtime_intervals(rules, d, record, now)
    overtime = overtime_amount(rules, d, intervals) * ratio
    running = overtime_running(rules, d, record, now)
    coef = overtime_coef(rules, work_day, is_late_night(now)) if running else 0.0

    if running:
        phase = "overtime"
    elif not work_day:
        phase = "rest"
    elif now < datetime.combine(d, rules.start):
        phase = "before"
    elif now < datetime.combine(d, rules.end):
        phase = "working"
    else:
        phase = "after"
    return DayEarnings(d, work_day, regular, overtime, phase, running, coef, tuple(intervals))


def period_total(
    rules: PayRules,
    first_day: date,
    now: datetime,
    records: Mapping[date, DayRecord],
) -> float:
    """Earnings from ``first_day`` up to and including today (as of ``now``).

    Completed work days count a full ``daily``; recorded overtime of past days
    comes from ``records``; today's live amount is added on top.
    """
    today = now.date()
    total = 0.0
    d = first_day
    while d < today:
        if is_work_day(rules, d):
            total += rules.daily * rules.ratio
        rec = records.get(d)
        if rec is not None:
            total += overtime_amount(rules, d, overtime_intervals(rules, d, rec, now)) * rules.ratio
        d += timedelta(days=1)
    if first_day <= today:
        total += compute_day(rules, today, records.get(today), now).total
    return total


def month_to_date(rules: PayRules, now: datetime, records: Mapping[date, DayRecord]) -> float:
    return period_total(rules, now.date().replace(day=1), now, records)


def year_to_date(rules: PayRules, now: datetime, records: Mapping[date, DayRecord]) -> float:
    return period_total(rules, date(now.year, 1, 1), now, records)


# ---------------------------------------------------------------------------
# overtime toggle (pure state transitions)


def toggle_overtime(
    rules: PayRules, d: date, record: DayRecord | None, now: datetime
) -> DayRecord:
    """Return the new record after pressing the start/end overtime toggle.

    If overtime is running (auto or manual) it is stopped: open manual sessions
    are closed at ``now`` and, if auto overtime was running, ``auto_stop`` is
    recorded. Otherwise a new manual session starts at ``now``.
    """
    record = record or DayRecord()
    if overtime_running(rules, d, record, now):
        sessions = tuple((s, e if e is not None else now) for s, e in record.sessions)
        auto_stop = record.auto_stop
        auto_active = (
            is_work_day(rules, d)
            and rules.auto_overtime
            and rules.daily_seconds > 0
            and now >= datetime.combine(d, rules.end)
            and auto_stop is None
        )
        if auto_active:
            auto_stop = now
        return DayRecord(sessions, auto_stop, record.last_seen)
    return DayRecord((*record.sessions, (now, None)), record.auto_stop, record.last_seen)


def close_day(rules: PayRules, d: date, record: DayRecord | None) -> tuple[DayRecord, bool]:
    """Close day ``d`` at midnight (called on date rollover while running).

    Returns the closed record and whether a manual session was open (so the
    caller can continue it on the next day).
    """
    record = record or DayRecord()
    _, midnight = day_bounds(d)
    was_open = record.has_open_session
    sessions = tuple((s, e if e is not None else midnight) for s, e in record.sessions)
    auto_stop = record.auto_stop
    if (
        auto_stop is None
        and is_work_day(rules, d)
        and rules.auto_overtime
        and rules.daily_seconds > 0
    ):
        auto_stop = midnight
    return DayRecord(sessions, auto_stop, midnight), was_open
