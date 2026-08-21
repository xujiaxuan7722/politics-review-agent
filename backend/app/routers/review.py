import re
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Mistake, ReviewCard, ReviewLog, User
from app.routers.auth import get_current_user
from app.services.card_agent import generate_review_cards
from app.services.review_service import schedule_next_review

router = APIRouter(prefix="/api/review", tags=["review"])


class ReviewSubmit(BaseModel):
    card_id: int
    quality: int


def parse_cards_text(text: str) -> list[dict]:
    cards = []
    content = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    blocks = re.split(r"\n\s*---+\s*\n", content)

    for block in blocks:
        q = extract_any_card_field(block, ["Q", "问题", "复习问题"], ["A", "答案", "标准答案", "K", "知识点"])
        a = extract_any_card_field(block, ["A", "答案", "标准答案"], ["K", "知识点", "Q", "问题", "复习问题"])
        k = extract_any_card_field(block, ["K", "知识点"], ["Q", "问题", "复习问题", "A", "答案", "标准答案"])

        if q and a and not is_placeholder_card(q, a):
            cards.append({
                "question": q,
                "answer": a,
                "knowledge_point": k,
            })

    return cards


PLACEHOLDER_PHRASES = ("复习问题", "标准答案", "实际内容", "考点的名称", "针对这道题核心考点")


def is_placeholder_card(question: str, answer: str) -> bool:
    """模型把格式说明原样抄回来时（如 Q 就是“复习问题”），视为无效卡片。"""
    q = question.strip()
    a = answer.strip()
    if len(q) < 6 or len(a) < 6:
        return True
    return any(phrase in q for phrase in PLACEHOLDER_PHRASES) or any(
        phrase in a for phrase in ("标准答案：", "实际内容", "可背诵的关键结论，再用一句话")
    )


def extract_any_card_field(block: str, labels: list[str], next_labels: list[str]) -> str | None:
    for label in labels:
        value = extract_card_field(block, label, next_labels)
        if value:
            return value
    return None


def extract_card_field(block: str, label: str, next_labels: list[str]) -> str | None:
    next_part = "|".join(re.escape(item) for item in next_labels)
    pattern = (
        rf"(?:^|\n)\s*(?:[-*]\s*)?{re.escape(label)}\s*[:：]\s*"
        rf"([\s\S]*?)(?=\n\s*(?:[-*]\s*)?(?:{next_part})\s*[:：]|\Z)"
    )
    match = re.search(pattern, block, flags=re.IGNORECASE)
    if not match:
        return None

    value = re.sub(r"\n{3,}", "\n\n", match.group(1)).strip()
    return value or None


def extract_answer_section(grade_text: str) -> str:
    """从批改输出里取出“正确答案及解析”一节，作为原题卡片的背面。"""
    text = grade_text or ""
    match = re.search(r"##\s*正确答案及解析\s*\n([\s\S]*?)(?=\n##\s|\Z)", text)
    section = (match.group(1) if match else text).strip()
    return section or "（暂无解析）"


def build_card_from_exam_question(mistake: Mistake) -> dict:
    """拍题判卷来源的错题：卡面就是完整原题，背面是正确答案与解析。"""
    keywords = split_keywords(mistake.knowledge_points)
    question = (mistake.clean_text or mistake.raw_text or mistake.title or "").strip()
    return {
        "question": question,
        "answer": extract_answer_section(mistake.analysis or ""),
        "knowledge_point": keywords[0] if keywords else None,
    }


def stem_title(text: str, limit: int = 20) -> str:
    """从题干自动截取默认标题。"""
    compact = re.sub(r"\s+", " ", str(text or "")).strip()
    return compact[:limit] or "未命名题目"


def build_review_card_from_mistake(mistake: Mistake) -> dict:
    title = clean_plain_text(mistake.title) or "当前错题"
    keywords = split_keywords(mistake.knowledge_points)
    source = "\n\n".join(
        item for item in [mistake.analysis, mistake.clean_text, mistake.raw_text] if item
    )
    answer = build_review_answer(source, title, keywords)

    return {
        "question": f"请复述「{title}」的核心结论。",
        "answer": answer,
        "knowledge_point": keywords[0] if keywords else title[:40],
    }


def split_keywords(text: str | None) -> list[str]:
    return [
        item.strip()
        for item in re.split(r"[、,，/\s]+", str(text or ""))
        if item.strip()
    ][:6]


def clean_plain_text(text: str | None) -> str:
    value = str(text or "")
    value = re.sub(r"#{1,6}\s*", "", value)
    value = re.sub(r"\*\*|__|`", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def build_review_answer(source: str, title: str, keywords: list[str]) -> str:
    content = clean_plain_text(source)
    if not content:
        content = title

    sentences = re.findall(r"[^。！？!?]+[。！？!?]?", content)
    useful = []
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if sentence in useful:
            continue
        useful.append(sentence)
        if len("".join(useful)) >= 180:
            break

    summary = "".join(useful).strip() or content[:220]
    if len(summary) > 260:
        summary = summary[:260].rstrip("，,；;、 ") + "。"

    keyword_line = "、".join(keywords) if keywords else title
    return f"关键词：{keyword_line}\n\n背诵句：{summary}"


@router.post("/generate-from-mistake/{mistake_id}")
async def generate_from_mistake(
    mistake_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    mistake = (
        db.query(Mistake)
        .filter(Mistake.id == mistake_id, Mistake.user_id == current_user.id)
        .first()
    )

    if not mistake:
        raise HTTPException(status_code=404, detail="错题不存在")

    if mistake.source == "pipeline":
        # 拍题来源：卡面保留完整原题，不做 AI 总结
        cards_data = [build_card_from_exam_question(mistake)]
    else:
        try:
            cards_text = await generate_review_cards(
                mistake.clean_text or "",
                mistake.analysis or "",
            )
            cards_data = parse_cards_text(cards_text)
        except Exception:
            cards_data = []

        if not cards_data:
            cards_data = [build_review_card_from_mistake(mistake)]

    # 重复生成时替换旧卡，避免同一道错题堆积重复卡片
    db.query(ReviewCard).filter(
        ReviewCard.mistake_id == mistake.id,
        ReviewCard.user_id == current_user.id,
    ).delete(synchronize_session=False)

    saved = []
    for item in cards_data:
        card = ReviewCard(
            user_id=current_user.id,
            mistake_id=mistake.id,
            question=item["question"],
            answer=item["answer"],
            knowledge_point=item.get("knowledge_point"),
            next_review_date=date.today(),
        )
        db.add(card)
        saved.append(card)

    db.commit()

    return {
        "count": len(saved),
        "cards": cards_data,
    }


@router.get("/today")
def get_today_cards(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rows = (
        db.query(ReviewCard, Mistake.title)
        .outerjoin(Mistake, Mistake.id == ReviewCard.mistake_id)
        .filter(
            ReviewCard.user_id == current_user.id,
            ReviewCard.next_review_date <= date.today(),
        )
        .order_by(ReviewCard.next_review_date.asc(), ReviewCard.id.asc())
        .all()
    )
    return [
        {
            "id": card.id,
            "mistake_id": card.mistake_id,
            "mistake_title": mistake_title,
            "question": card.question,
            "answer": card.answer,
            "knowledge_point": card.knowledge_point,
            "repetition": card.repetition,
            "interval_days": card.interval_days,
            "lapses": card.lapses,
            "next_review_date": card.next_review_date,
        }
        for card, mistake_title in rows
    ]


@router.post("/submit")
def submit_review(
    req: ReviewSubmit,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    card = (
        db.query(ReviewCard)
        .filter(ReviewCard.id == req.card_id, ReviewCard.user_id == current_user.id)
        .first()
    )

    if not card:
        raise HTTPException(status_code=404, detail="复习卡不存在")

    interval_before = card.interval_days
    result = schedule_next_review(
        quality=req.quality,
        repetition=card.repetition,
        interval_days=card.interval_days,
        ease_factor=card.ease_factor,
    )

    card.repetition = result["repetition"]
    card.interval_days = result["interval_days"]
    card.ease_factor = result["ease_factor"]
    card.next_review_date = result["next_review_date"]
    card.last_reviewed_at = datetime.utcnow()
    if req.quality < 3:
        card.lapses = (card.lapses or 0) + 1

    db.add(
        ReviewLog(
            card_id=card.id,
            user_id=current_user.id,
            quality=req.quality,
            interval_before=interval_before,
            interval_after=card.interval_days,
            ease_after=card.ease_factor,
        )
    )
    db.commit()
    db.refresh(card)

    return card
