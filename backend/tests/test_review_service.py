"""SM-2 调度算法的单元测试。"""

from datetime import date, timedelta

from app.services.review_service import schedule_next_review


def test_forgotten_card_resets_and_reappears_today():
    result = schedule_next_review(quality=1, repetition=4, interval_days=15, ease_factor=2.6)
    assert result["repetition"] == 0
    assert result["interval_days"] == 1
    assert result["next_review_date"] == date.today()


def test_first_success_schedules_one_day_later():
    result = schedule_next_review(quality=5, repetition=0, interval_days=1, ease_factor=2.5)
    assert result["repetition"] == 1
    assert result["interval_days"] == 1
    assert result["next_review_date"] == date.today() + timedelta(days=1)


def test_second_success_schedules_three_days_later():
    result = schedule_next_review(quality=4, repetition=1, interval_days=1, ease_factor=2.5)
    assert result["repetition"] == 2
    assert result["interval_days"] == 3


def test_later_successes_multiply_interval_by_ease_factor():
    result = schedule_next_review(quality=5, repetition=2, interval_days=3, ease_factor=2.5)
    assert result["interval_days"] == round(3 * 2.5)
    assert result["repetition"] == 3


def test_ease_factor_never_drops_below_floor_and_quality_is_clamped():
    result = schedule_next_review(quality=-10, repetition=0, interval_days=1, ease_factor=1.3)
    assert result["ease_factor"] == 1.3
    # 超出范围的 quality 被夹到 0-5，仍按失败处理
    assert result["repetition"] == 0
    assert result["next_review_date"] == date.today()


def test_good_answer_raises_ease_factor():
    before = 2.5
    result = schedule_next_review(quality=5, repetition=1, interval_days=1, ease_factor=before)
    assert result["ease_factor"] > before
