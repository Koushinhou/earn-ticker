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

## 3. Weekday overtime 17:15 → 23:00 (auto overtime, never stopped)

| Part | Duration | Coefficient | Amount |
|---|---|---|---|
| 17:15 – 22:00 weekday | 4.75 h | × 1.25 | 4.75 × 3,208.5561 × 1.25 = **19,050.80** |
| 22:00 – 23:00 late night | 1.00 h | × 1.5 | 1 × 3,208.5561 × 1.5 = **4,812.83** |
| **Overtime total** | | | **23,863.64** |

Day total at 23:00 = 27,272.73 (regular, capped at daily) + 23,863.64 = **¥51,136.36**.
Rates do not stack: late-night seconds use 1.5 *instead of* 1.25.
(Test: `test_auto_overtime_until_2300_spans_late_night`.)

At 18:30 (screenshot `docs/panel_overtime.png`):
1.25 h × 3,208.5561 × 1.25 = 5,013.37 → day total 27,272.73 + 5,013.37 = **¥32,286.10**,
ticking by **+¥1.11** per second (0.891266 × 1.25).

## 4. Rest day, 2 h manual overtime

Saturday, 「开始加班」 at 10:00, 「结束加班」 at 12:00:

2 h × 3,208.5561 × 1.35 = **¥8,663.10**

(Test: `test_rest_day_manual_overtime_135`.) On a rest day late-night seconds use
max(1.35, 1.5) = 1.5 (`test_rest_day_late_night_uses_max_rule`).

## 5. Take-home ratio

With 「到手比例」 enabled at 80 %, every displayed amount is multiplied by 0.8, e.g.
14:30 → 18,449.20 × 0.8 = **¥14,759.36** (`test_take_home_ratio_on_off`).

## 6. Month-to-date (example: Mon 2026-10-19 14:30, holiday list = Japan example)

Completed work days in October before the 19th: Oct 1, 2, 5–9, 13–16 = 11 days
(Oct 12 スポーツの日 is a holiday).

11 × 27,272.7273 + 18,449.20 = 300,000.00 + 18,449.20 = **¥318,449**
(screenshot `docs/panel_custom_color.png`).
