from collections import Counter
from datetime import date

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Mistake, ReviewCard, User
from app.routers.auth import get_current_user
from app.routers.records import add_record
from app.services.agents import (
    explainer_agent,
    grader_agent,
    quiz_agent,
    review_planner_agent,
)
from app.services.manager_agent import run_manager

router = APIRouter(prefix="/api/agents", tags=["agents"])


class ExplainReq(BaseModel):
    topic: str = Field(min_length=1)


class QuizReq(BaseModel):
    topic: str = Field(min_length=1)
    difficulty: int = Field(default=3, ge=1, le=5)
    quiz_type: str = Field(default="single")


class GradeReq(BaseModel):
    question: str = Field(min_length=1)
    student_answer: str = Field(min_length=1)


class PlannerReq(BaseModel):
    mistakes_summary: str = ""


class ManagerReq(BaseModel):
    instruction: str = Field(min_length=1)


def split_tags(value: str | None) -> list[str]:
    if not value:
        return []
    normalized = value.replace(",", "、").replace("，", "、")
    return [tag.strip() for tag in normalized.split("、") if tag.strip()]


def build_db_summary(db: Session, user_id: int) -> str:
    mistakes = (
        db.query(Mistake)
        .filter(Mistake.user_id == user_id)
        .order_by(Mistake.id.desc())
        .all()
    )
    cards = db.query(ReviewCard).filter(ReviewCard.user_id == user_id).all()

    if not mistakes and not cards:
        return ""

    module_counter = Counter()
    for mistake in mistakes:
        for tag in split_tags(mistake.knowledge_points)[:3]:
            module_counter[tag] += 1

    lines = [f"错题总数：{len(mistakes)}"]
    if module_counter:
        top_modules = "、".join(f"{name}（{count}题）" for name, count in module_counter.most_common(5))
        lines.append(f"错题集中的考点：{top_modules}")

    recent_titles = [m.title for m in mistakes[:6] if m.title]
    if recent_titles:
        lines.append("最近的错题：" + "；".join(recent_titles))

    if cards:
        due = sum(1 for c in cards if c.next_review_date and c.next_review_date <= date.today())
        weak = sorted(cards, key=lambda c: (c.ease_factor or 2.5))[:5]
        weak_points = "、".join({c.knowledge_point for c in weak if c.knowledge_point})
        lines.append(f"复习卡共 {len(cards)} 张，今日待复习 {due} 张。")
        if weak_points:
            lines.append(f"掌握最弱的知识点：{weak_points}")

    return "\n".join(lines)


@router.post("/explain")
async def explain(req: ExplainReq, current_user: User = Depends(get_current_user)):
    return {"answer": await explainer_agent(req.topic)}


@router.post("/quiz")
async def quiz(
    req: QuizReq,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    answer = await quiz_agent(req.topic, req.difficulty, req.quiz_type)
    add_record(db, current_user.id, "agents", f"出题：{req.topic[:50]}", req.topic, answer)
    return {"answer": answer}


@router.post("/grade")
async def grade(
    req: GradeReq,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    answer = await grader_agent(req.question, req.student_answer)
    add_record(
        db,
        current_user.id,
        "agents",
        f"批改：{req.question.strip().splitlines()[0][:50]}",
        f"{req.question}\n\n学生答案：{req.student_answer}",
        answer,
    )
    return {"answer": answer}


@router.post("/manager")
async def manager(
    req: ManagerReq,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await run_manager(req.instruction, db, current_user.id)
    if result.get("answer"):
        add_record(
            db,
            current_user.id,
            "agents",
            f"学习管家：{req.instruction.strip()[:50]}",
            req.instruction,
            result["answer"],
        )
    return result


@router.post("/plan")
async def plan(
    req: PlannerReq,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    db_summary = build_db_summary(db, current_user.id)
    manual = req.mistakes_summary.strip()

    parts = [p for p in (db_summary, f"用户补充说明：{manual}" if manual else "") if p]
    if not parts:
        return {
            "answer": "",
            "notice": "错题本还是空的，先录入几道错题或生成复习卡，规划才有依据。",
        }

    summary = "\n".join(parts)
    answer = await review_planner_agent(summary)
    add_record(db, current_user.id, "agents", "复习规划", summary, answer)
    return {
        "answer": answer,
        "based_on": db_summary or None,
    }
