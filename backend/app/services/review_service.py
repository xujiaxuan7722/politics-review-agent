from datetime import date, timedelta

def schedule_next_review(
    quality: int,
    repetition: int,
    interval_days: int,
    ease_factor: float,
):
    quality = max(0, min(5, quality))

    if quality < 3:
        repetition = 0
        interval_days = 1
        # 没记住的卡当天重新出现，直到打出及格分才进入间隔复习
        next_review_date = date.today()
    else:
        if repetition == 0:
            interval_days = 1
        elif repetition == 1:
            interval_days = 3
        else:
            interval_days = round(interval_days * ease_factor)

        repetition += 1
        next_review_date = date.today() + timedelta(days=interval_days)

    ease_factor = ease_factor + (
        0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02)
    )

    if ease_factor < 1.3:
        ease_factor = 1.3

    return {
        "repetition": repetition,
        "interval_days": interval_days,
        "ease_factor": ease_factor,
        "next_review_date": next_review_date,
    }
