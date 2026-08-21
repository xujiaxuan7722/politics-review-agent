from collections import Counter, defaultdict
from datetime import date, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Mistake, ReviewCard, ReviewLog, User
from app.routers.auth import get_current_user

router = APIRouter(prefix="/api/stats", tags=["stats"])

INTERVAL_BUCKETS = [
    ("1 天", 1, 1),
    ("2-3 天", 2, 3),
    ("4-7 天", 4, 7),
    ("8-15 天", 8, 15),
    ("16 天以上", 16, 10**6),
]


def _local_date(value) -> date:
    """review_logs 存的是 UTC 时间，按本机时区折算成日期。"""
    if value is None:
        return date.today()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone().date()


@router.get("/learning")
def learning(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """基于 review_logs 的学习数据看板：每日复习量与正确率、间隔保持率、最常遗忘的知识点、连续学习天数。"""
    logs = (
        db.query(ReviewLog)
        .filter(ReviewLog.user_id == current_user.id)
        .order_by(ReviewLog.reviewed_at.asc())
        .all()
    )
    cards = db.query(ReviewCard).filter(ReviewCard.user_id == current_user.id).all()
    today = date.today()

    # 每日复习量与正确率（最近 14 天）
    per_day_total = Counter()
    per_day_ok = Counter()
    for log in logs:
        day = _local_date(log.reviewed_at)
        per_day_total[day] += 1
        if log.quality >= 3:
            per_day_ok[day] += 1

    daily = []
    for offset in range(13, -1, -1):
        day = today - timedelta(days=offset)
        total = per_day_total.get(day, 0)
        daily.append({
            "date": day.isoformat(),
            "label": day.strftime("%m-%d"),
            "reviews": total,
            "accuracy": round(per_day_ok.get(day, 0) / total * 100) if total else None,
        })

    # 间隔保持率：上一次间隔越长，这次还记得的比例（近似遗忘曲线）
    bucket_total = defaultdict(int)
    bucket_ok = defaultdict(int)
    for log in logs:
        for name, low, high in INTERVAL_BUCKETS:
            if low <= max(log.interval_before, 1) <= high:
                bucket_total[name] += 1
                if log.quality >= 3:
                    bucket_ok[name] += 1
                break
    retention = [
        {
            "name": name,
            "reviews": bucket_total[name],
            "rate": round(bucket_ok[name] / bucket_total[name] * 100) if bucket_total[name] else None,
        }
        for name, _, _ in INTERVAL_BUCKETS
    ]

    # 最常遗忘的知识点（按卡片累计 lapses 聚合）
    lapses_by_point = Counter()
    cards_by_point = Counter()
    for card in cards:
        point = card.knowledge_point or "未标注"
        cards_by_point[point] += 1
        lapses_by_point[point] += card.lapses or 0
    weak_points = [
        {"name": point, "lapses": lapses, "cards": cards_by_point[point]}
        for point, lapses in lapses_by_point.most_common(6)
        if lapses > 0
    ]

    # 连续学习天数（截至今天或昨天）
    active_days = set(per_day_total)
    streak = 0
    cursor = today if today in active_days else today - timedelta(days=1)
    while cursor in active_days:
        streak += 1
        cursor -= timedelta(days=1)

    total_reviews = len(logs)
    remembered = sum(1 for log in logs if log.quality >= 3)
    mastered = sum(1 for card in cards if card.repetition >= 3 or card.interval_days >= 7)

    return {
        "summary": {
            "total_reviews": total_reviews,
            "accuracy": round(remembered / total_reviews * 100) if total_reviews else None,
            "streak_days": streak,
            "active_days": len(active_days),
            "mastered_cards": mastered,
            "total_cards": len(cards),
        },
        "daily": daily,
        "retention": retention,
        "weak_points": weak_points,
    }


@router.get("/overview")
def overview(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    mistake_count = db.query(Mistake).filter(Mistake.user_id == current_user.id).count()
    card_count = db.query(ReviewCard).filter(ReviewCard.user_id == current_user.id).count()
    today_count = (
        db.query(ReviewCard)
        .filter(
            ReviewCard.user_id == current_user.id,
            ReviewCard.next_review_date <= date.today(),
        )
        .count()
    )

    return {
        "mistake_count": mistake_count,
        "card_count": card_count,
        "today_review_count": today_count,
    }


@router.get("/charts")
def charts(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    mistakes = db.query(Mistake).filter(Mistake.user_id == current_user.id).all()
    cards = db.query(ReviewCard).filter(ReviewCard.user_id == current_user.id).all()

    module_counter = Counter()
    for mistake in mistakes:
        tags = split_tags(mistake.knowledge_points)
        if not tags:
            module_counter["未标注"] += 1
            continue
        for tag in tags[:3]:
            module_counter[tag] += 1

    today = date.today()
    review_buckets = {
        "已逾期": 0,
        "今天": 0,
        "未来7天": 0,
        "更久以后": 0,
    }
    mastery_buckets = {
        "未复习": 0,
        "复习1次": 0,
        "复习2次": 0,
        "复习3次及以上": 0,
    }

    for card in cards:
        if card.next_review_date < today:
            review_buckets["已逾期"] += 1
        elif card.next_review_date == today:
            review_buckets["今天"] += 1
        elif card.next_review_date <= today + timedelta(days=7):
            review_buckets["未来7天"] += 1
        else:
            review_buckets["更久以后"] += 1

        if card.repetition <= 0:
            mastery_buckets["未复习"] += 1
        elif card.repetition == 1:
            mastery_buckets["复习1次"] += 1
        elif card.repetition == 2:
            mastery_buckets["复习2次"] += 1
        else:
            mastery_buckets["复习3次及以上"] += 1

    return {
        "mistake_modules": to_chart_items(module_counter),
        "review_schedule": to_chart_items(review_buckets),
        "mastery": to_chart_items(mastery_buckets),
    }


def split_tags(value: str | None) -> list[str]:
    if not value:
        return []

    normalized = value.replace(",", "、").replace("，", "、")
    return [tag.strip() for tag in normalized.split("、") if tag.strip()]


def to_chart_items(counter) -> list[dict]:
    items = counter.items() if hasattr(counter, "items") else counter
    return [{"name": name, "value": value} for name, value in items]
