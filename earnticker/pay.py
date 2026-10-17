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
* Overtime is paid at hourly x coefficient: weekday, weekday late night
  (22:00-05:00), rest day and rest-day late night each have their own
  coefficient (they never stack).
* Auto overtime on work days runs from ``end`` for at most
  ``auto_overtime_cap_hours``; manual sessions are not capped.
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

# The app records ``last_seen`` about once a minute, so up to this much time
# after it is assumed to have been running as well.
LAST_SEEN_GRACE = timedelta(seconds=60)
# Max distance from midnight for the app to count as "alive across midnight".
ALIVE_GAP = timedelta(minutes=2)

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
    coef_rest_late_night: float = 1.6
    auto_overtime_cap_hours: float = 3.0
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
    def has_auto_overtime(self) -> bool:
        return self.auto_overtime and self.daily_seconds > 0 and self.auto_overtime_cap_hours > 0

    def auto_window(self, d: date) -> Interval:
        """Window in which auto overtime may accrue on work day ``d``."""
        a0 = datetime.combine(d, self.end)
        return a0, a0 + timedelta(hours=max(self.auto_overtime_cap_hours, 0.0))

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
    overtime_running: bool = False  # overtime is accruing at ``now``
    current_coef: float = 0.0
    overtime_intervals: tuple[Interval, ...] = field(default=())
    overtime_active: bool = False  # toggle state: open session or auto overtime
    # set when a manual session is open but not accruing yet (inside regular hours)
    overtime_pending_from: datetime | None = None

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
    return rules.coef_rest_late_night if late_night else rules.coef_rest_day


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
    if work_day and rules.has_auto_overtime:
        a0, cap_end = rules.auto_window(d)
        a1 = record.auto_stop if record.auto_stop is not None else cut
        raw.append((a0, min(a1, cap_end, limit)))
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


def auto_overtime_active(
    rules: PayRules, d: date, record: DayRecord | None, now: datetime
) -> bool:
    """True if auto overtime is accruing at ``now`` (inside the capped window)."""
    record = record or DayRecord()
    if now.date() != d or record.auto_stop is not None:
        return False
    if not (is_work_day(rules, d) and rules.has_auto_overtime):
        return False
    a0, cap_end = rules.auto_window(d)
    return a0 <= now < cap_end


def overtime_active(rules: PayRules, d: date, record: DayRecord | None, now: datetime) -> bool:
    """Toggle state: a manual session is open or auto overtime is active.

    This decides whether 「开始加班」 or 「结束加班」 is offered.
    """
    record = record or DayRecord()
    if now.date() != d:
        return False
    return record.has_open_session or auto_overtime_active(rules, d, record, now)


def _in_regular_hours(rules: PayRules, d: date, moment: datetime) -> bool:
    if not is_work_day(rules, d) or rules.daily_seconds <= 0:
        return False
    return datetime.combine(d, rules.start) <= moment < datetime.combine(d, rules.end)


def overtime_running(rules: PayRules, d: date, record: DayRecord | None, now: datetime) -> bool:
    """True if overtime is actually accruing at ``now`` (only meaningful for today).

    A manual session opened during regular hours is active but does not
    accrue until the end time.
    """
    record = record or DayRecord()
    if now.date() != d:
        return False
    if auto_overtime_active(rules, d, record, now):
        return True
    return record.has_open_session and not _in_regular_hours(rules, d, now)


def overtime_pending_from(
    rules: PayRules, d: date, record: DayRecord | None, now: datetime
) -> datetime | None:
    """When an open but not yet accruing manual session starts to count."""
    record = record or DayRecord()
    if now.date() != d or not record.has_open_session:
        return None
    if overtime_running(rules, d, record, now):
        return None
    return datetime.combine(d, rules.end)


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
    active = overtime_active(rules, d, record, now)
    pending = overtime_pending_from(rules, d, record, now)
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
    return DayEarnings(
        d, work_day, regular, overtime, phase, running, coef, tuple(intervals), active, pending
    )


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

    If overtime is active (auto within its cap, or an open manual session) it
    is stopped: open manual sessions are closed at ``now`` and, if auto
    overtime was active, ``auto_stop`` is recorded. Otherwise a new manual
    session starts at ``now``.
    """
    record = record or DayRecord()
    if overtime_active(rules, d, record, now):
        sessions = tuple((s, e if e is not None else now) for s, e in record.sessions)
        auto_stop = record.auto_stop
        if auto_overtime_active(rules, d, record, now):
            auto_stop = now
        return DayRecord(sessions, auto_stop, record.last_seen)
    return DayRecord((*record.sessions, (now, None)), record.auto_stop, record.last_seen)


def close_day(
    rules: PayRules, d: date, record: DayRecord | None, now: datetime | None = None
) -> tuple[DayRecord, bool]:
    """Close day ``d`` on date rollover.

    Open-ended overtime (open manual sessions, auto overtime never stopped) is
    closed at ``cut = min(midnight, last_seen + LAST_SEEN_GRACE)``: if the PC
    slept across midnight only the time the app was actually seen running
    counts. Without ``last_seen`` nothing open-ended is counted.

    Returns the closed record and whether an open manual session should be
    continued on the next day. That only happens when the app was alive across
    midnight (``last_seen`` and ``now`` both within ``ALIVE_GAP`` of it).
    """
    record = record or DayRecord()
    day_start, midnight = day_bounds(d)
    if record.last_seen is None:
        cut = day_start
    else:
        cut = min(midnight, max(record.last_seen + LAST_SEEN_GRACE, day_start))
    was_open = record.has_open_session
    sessions = tuple((s, e if e is not None else max(s, cut)) for s, e in record.sessions)
    auto_stop = record.auto_stop
    if auto_stop is None and is_work_day(rules, d) and rules.has_auto_overtime:
        auto_stop = cut
    alive = (
        record.last_seen is not None
        and record.last_seen >= midnight - ALIVE_GAP
        and (now is None or now <= midnight + ALIVE_GAP)
    )
    last_seen = cut if record.last_seen is not None else None
    return DayRecord(sessions, auto_stop, last_seen), was_open and alive
