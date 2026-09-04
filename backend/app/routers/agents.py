import asyncio
import json
from collections import Counter
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
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
from app.services.conversation_service import (
    get_messages,
    get_or_create_conversation,
    list_conversations,
    load_history,
    save_turn,
)
from app.services.manager_agent import plan_with_manager, run_manager

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
    question_type: str = "unknown"  # single / multi / unknown


class PlannerReq(BaseModel):
    mistakes_summary: str = ""


class ManagerReq(BaseModel):
    instruction: str = Field(min_length=1)
    conversation_id: int | None = None  # 缺省新开一个对话；传了就接着聊


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
    answer = await grader_agent(req.question, req.student_answer, question_type=req.question_type)
    add_record(
        db,
        current_user.id,
        "agents",
        f"批改：{req.question.strip().splitlines()[0][:50]}",
        f"{req.question}\n\n学生答案：{req.student_answer}",
        answer,
    )
    return {"answer": answer}


def _open_conversation(db: Session, user_id: int, req: ManagerReq):
    try:
        return get_or_create_conversation(db, user_id, req.conversation_id, req.instruction)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _finish_turn(db: Session, user_id: int, conv, req: ManagerReq, result: dict) -> None:
    if result.get("answer"):
        add_record(
            db,
            user_id,
            "agents",
            f"学习管家：{req.instruction.strip()[:50]}",
            req.instruction,
            result["answer"],
        )
        save_turn(db, conv, user_id, req.instruction, result["answer"], result.get("steps"), result.get("model"))


@router.post("/manager")
async def manager(
    req: ManagerReq,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    conv = _open_conversation(db, current_user.id, req)
    history = load_history(db, conv.id)
    result = await run_manager(req.instruction, db, current_user.id, history=history)
    _finish_turn(db, current_user.id, conv, req, result)
    return {**result, "conversation_id": conv.id}


@router.get("/conversations")
def conversations(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return [
        {"id": c.id, "title": c.title, "updated_at": c.updated_at.isoformat()}
        for c in list_conversations(db, current_user.id)
    ]


@router.get("/conversations/{conversation_id}")
def conversation_detail(
    conversation_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        conv = get_or_create_conversation(db, current_user.id, conversation_id, "")
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"id": conv.id, "title": conv.title, "messages": get_messages(db, conv)}


@router.delete("/conversations/{conversation_id}")
def delete_conversation(
    conversation_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        conv = get_or_create_conversation(db, current_user.id, conversation_id, "")
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    db.delete(conv)
    db.commit()
    return {"ok": True}


@router.post("/manager-stream")
async def manager_stream(
    req: ManagerReq,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """SSE 版学习管家：每次模型思考、每个工具开始/结束都推一条事件，最后推完整结果。"""
    conv = _open_conversation(db, current_user.id, req)
    history = load_history(db, conv.id)
    queue: asyncio.Queue[dict | None] = asyncio.Queue()

    async def on_event(event: dict) -> None:
        await queue.put(event)

    async def worker() -> None:
        try:
            result = await run_manager(req.instruction, db, current_user.id, on_event=on_event, history=history)
            _finish_turn(db, current_user.id, conv, req, result)
            await queue.put({"type": "done", **result, "conversation_id": conv.id})
        except Exception as exc:
            await queue.put({"type": "error", "detail": f"学习管家执行失败：{type(exc).__name__}"})
        finally:
            await queue.put(None)

    def sse(payload: dict) -> str:
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    async def event_stream():
        yield sse({"type": "meta", "conversation_id": conv.id, "history_messages": len(history)})
        task = asyncio.create_task(worker())
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield sse(event)
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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

    # 主路：学习管家自己决定查学习状态/到期卡/错题原文，再产出计划。
    # 兜底：模型一条数据都没查、步数用尽或调用失败时，退回确定性的规划模块。
    steps: list[dict] = []
    answer = ""
    model = None
    try:
        result = await plan_with_manager(db, current_user.id, manual)
        steps = result.get("steps", [])
        model = result.get("model")
        if result.get("consulted"):
            answer = result["answer"]
    except Exception:
        answer = ""

    mode = "manager"
    if not answer:
        mode = "fallback"
        answer = await review_planner_agent(summary)

    add_record(db, current_user.id, "agents", "复习规划", summary, answer)
    return {
        "answer": answer,
        "based_on": db_summary or None,
        "steps": steps,
        "mode": mode,
        "model": model,
    }
