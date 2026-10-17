# Hand calculation (example data)

All numbers use the **example** settings shipped in `config/settings.example.json`
(annual income ¥6,000,000 pre-tax, defaults for everything else). They match the
assertions in `tests/test_pay.py`.

## 1. Rates

| Step | Formula | Result |
|---|---|---|
| Working days | 365 − 125 − 20 | **220** days |
| Daily hours | 17:15 − 08:45 (no lunch deduction) | **8.5** h |
| Daily pay | 6,000,000 ÷ 220 | **27,272.7273** |
| Hourly pay | 27,272.7273 ÷ 8.5 | **3,208.5561** |
| Per second | 3,208.5561 ÷ 3,600 | **0.891266** |

The panel therefore ticks by **+¥0.89** every second during regular hours.

## 2. Weekday at 14:30:00

* Elapsed since 08:45:00 = 5 h 45 min = **20,700 s**
* 20,700 × 0.8912656 = **¥18,449.20**

(Test: `test_mid_day_value_1430`; screenshot `docs/panel.png`.)

No lunch pause: 12:00 → 12:30 → 13:00 each add exactly 1,800 s × 0.891266 = ¥1,604.28
(`test_no_lunch_pause_linear_at_1230`).

## 3. Weekday overtime (auto overtime, capped at end + 3 h)

Auto overtime starts at the end time (17:15) and counts for at most
「自动加班上限」 = **3 h** (default), i.e. until **20:15**, unless 「结束加班」 is pressed
earlier. After the cap the panel shows 「已下班 · 其中加班 …」, not 「加班中」.

At 18:30 (screenshot `docs/panel_overtime.png`):
1.25 h × 3,208.5561 × 1.25 = 5,013.37 → day total 27,272.73 + 5,013.37 = **¥32,286.10**,
ticking by **+¥1.11** per second (0.891266 × 1.25).
(Test: `test_auto_overtime_weekday_125_after_end`.)

### 3a. Never stopped, looked at 23:00

Only 17:15 – 20:15 counts:

3 h × 3,208.5561 × 1.25 = **12,032.09** → day total 27,272.73 + 12,032.09 = **¥39,304.81**
(Tests: `test_auto_overtime_capped_at_end_plus_3h`, `test_auto_overtime_cap_boundary_2015`.)

### 3b. Plus a manual session 21:00 – 23:00

Manual 「开始加班 / 结束加班」 sessions are **not** capped:

| Part | Duration | Coefficient | Amount |
|---|---|---|---|
| 17:15 – 20:15 auto | 3 h | × 1.25 | **12,032.09** |
| 21:00 – 22:00 manual, weekday | 1 h | × 1.25 | 3,208.5561 × 1.25 = **4,010.70** |
| 22:00 – 23:00 manual, weekday late night | 1 h | × 1.5 | 3,208.5561 × 1.5 = **4,812.83** |
| **Overtime total** | | | **20,855.62** |

Manual part alone: 4,010.70 + 4,812.83 = **8,823.53**. (Sum of rounded parts; the exact
value is 20,855.6149.) Rates do not stack: late-night seconds use 1.5 *instead of* 1.25.
(Test: `test_manual_session_past_cap_still_accrues`.)

A manual session started *during* regular hours (e.g. 15:00) only starts to count at
17:15; until then the panel shows 「已开始加班（17:15 起计）」
(`test_manual_session_started_in_regular_hours_pending_until_end`).

## 4. Rest day manual overtime

Saturday, 「开始加班」 at 10:00, 「结束加班」 at 12:00:

2 h × 3,208.5561 × 1.35 = **¥8,663.10** (`test_rest_day_manual_overtime_135`)

Rest-day late night (22:00 – 05:00) has its own coefficient 「休息日深夜系数」, default
**1.6**. Saturday 22:00 – 23:00:

1 h × 3,208.5561 × 1.6 = **¥5,133.69** (`test_rest_day_late_night_uses_own_coefficient`)

| Day type | Daytime | Late night 22:00 – 05:00 |
|---|---|---|
| Work day | 1.25 | 1.5 |
| Rest day / holiday | 1.35 | 1.6 |

## 4a. PC asleep across midnight

If the app was last seen running Monday 20:00 (it records this once a minute) and the PC
wakes on Tuesday 08:00, Monday's open-ended overtime is closed at
min(midnight, last seen + 60 s) = 20:01, not at midnight:

(2.75 h + 60 s) × 3,208.5561 × 1.25 = 11,029.41 + 66.84 = **¥11,096.25**
(`test_close_day_after_sleep_across_midnight_uses_last_seen`). An open manual session
is only continued on the new day when the app was actually running at midnight.

## 5. Take-home ratio

With 「到手比例」 enabled at 80 %, every displayed amount is multiplied by 0.8, e.g.
14:30 → 18,449.20 × 0.8 = **¥14,759.36** (`test_take_home_ratio_on_off`).

## 6. Month-to-date (example: Mon 2026-10-19 14:30, holiday list = Japan example)

Completed work days in October before the 19th: Oct 1, 2, 5–9, 13–16 = 11 days
(Oct 12 スポーツの日 is a holiday).

11 × 27,272.7273 + 18,449.20 = 300,000.00 + 18,449.20 = **¥318,449**
(screenshot `docs/panel_custom_color.png`).
