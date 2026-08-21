"""多智能体流水线：批改 → 错题归档 → 生成复习卡 → 进入复习队列，一次调用完成。"""

import re
from datetime import date

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Mistake, ReviewCard, User
from app.routers.auth import get_current_user
from app.routers.records import add_record
from app.routers.review import build_card_from_exam_question, stem_title
from app.services.agents import grader_agent
from app.services.mistake_agent import extract_politics_keywords

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])


class GradeArchiveReq(BaseModel):
    question_text: str = Field(min_length=1)
    student_answer: str = Field(min_length=1)
    title: str | None = None
    archive_correct: bool = False  # 答对的题默认不进错题本


def parse_judgement(grade_text: str) -> str:
    match = re.search(r"##\s*判断\s*\n+\s*(正确|基本正确|错误)", grade_text or "")
    return match.group(1) if match else "未知"


@router.post("/grade-archive")
async def grade_and_archive(
    req: GradeArchiveReq,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # 智能体 1：批改判题
    grade_text = await grader_agent(req.question_text, req.student_answer)
    judgement = parse_judgement(grade_text)

    result = {
        "judgement": judgement,
        "grade": grade_text,
        "archived": False,
        "mistake_id": None,
        "cards_count": 0,
    }

    record_title = (req.title or "").strip() or stem_title(req.question_text)
    add_record(
        db,
        current_user.id,
        source="ocr",
        title=f"{record_title}（判题：{judgement}）",
        question=f"{req.question_text}\n\n我的答案：{req.student_answer}",
        content=grade_text,
    )

    should_archive = judgement in ("错误", "基本正确", "未知") or req.archive_correct
    if not should_archive:
        return result

    # 智能体 2：错题归档（批改解析直接作为错题分析，不重复调用大模型）
    title = record_title
    keywords = "、".join(
        extract_politics_keywords(f"{req.question_text}\n{grade_text}")
    )

    mistake = Mistake(
        user_id=current_user.id,
        title=title,
        raw_text=f"{req.question_text}\n\n我的答案：{req.student_answer}",
        clean_text=req.question_text,
        analysis=grade_text,
        knowledge_points=keywords or None,
        source="pipeline",
        student_answer=req.student_answer,
        judgement=judgement,
    )
    db.add(mistake)
    db.commit()
    db.refresh(mistake)

    result["archived"] = True
    result["mistake_id"] = mistake.id

    # 智能体 3：生成复习卡并放入今日队列（拍题来源：卡面就是完整原题，背面是正确答案与解析）
    cards_data = [build_card_from_exam_question(mistake)]

    for item in cards_data:
        db.add(
            ReviewCard(
                user_id=current_user.id,
                mistake_id=mistake.id,
                question=item["question"],
                answer=item["answer"],
                knowledge_point=item.get("knowledge_point"),
                next_review_date=date.today(),
            )
        )
    db.commit()

    result["cards_count"] = len(cards_data)
    return result
