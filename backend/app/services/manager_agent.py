"""学习管家：自主决策的调度智能体。

模型通过 function calling 自行决定调用哪些工具（搜知识库、查学习状态、
出题、建复习卡），循环执行直到任务完成——这是系统中真正意义上的
自主多智能体协作入口。
"""

import json
from collections import Counter
from datetime import date

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Mistake, ReviewCard
from app.services.agents import quiz_agent
from app.services.rag_service import retrieve

MAX_STEPS = 6

TOOL_LABELS = {
    "search_knowledge": "检索知识库",
    "get_study_status": "查看学习状态",
    "generate_quiz": "生成练习题",
    "create_review_card": "创建复习卡",
}

TOOL_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "search_knowledge",
            "description": "在考研政治教材知识库中检索资料，返回最相关的教材段落。需要讲解概念、核实知识点时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "要检索的知识点或问题"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_study_status",
            "description": "查询当前用户的真实学习状态：错题总数、错题集中的考点、最近错题、今日待复习卡片和薄弱知识点。制定计划或诊断薄弱点前必须调用。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_quiz",
            "description": "围绕指定主题生成一道练习题。",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "出题主题"},
                    "difficulty": {"type": "integer", "description": "难度 1-5，默认 3"},
                    "quiz_type": {"type": "string", "enum": ["single", "analysis"], "description": "single=单选题，analysis=分析题"},
                },
                "required": ["topic"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_review_card",
            "description": "为用户创建一张背诵复习卡，会进入今日复习队列。帮用户固化某个知识点时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "复习问题"},
                    "answer": {"type": "string", "description": "标准答案，含可背诵关键词"},
                    "knowledge_point": {"type": "string", "description": "知识点名称"},
                },
                "required": ["question", "answer"],
            },
        },
    },
]

SYSTEM_PROMPT = """你是考研政治复习系统的「学习管家」智能体。

用户会用自然语言提出学习任务（如"帮我准备明天的马原复习""检查我最薄弱的地方并出一道题"）。你要自主决定调用哪些工具、按什么顺序调用，获取真实数据后完成任务。

原则：
1. 涉及用户个人情况（薄弱点、错题、复习安排）时，必须先调用 get_study_status 拿真实数据，不要凭空假设。
2. 讲解知识点前先用 search_knowledge 检索教材。
3. 需要练习时用 generate_quiz；值得背诵的结论用 create_review_card 固化。
4. 工具调用完成后，用中文给出条理清晰的最终答复：先说结论/成果，再简述你做了哪些动作。
5. 不要重复调用同一工具做同样的事。"""


def _study_status(db: Session, user_id: int) -> str:
    mistakes = (
        db.query(Mistake)
        .filter(Mistake.user_id == user_id)
        .order_by(Mistake.id.desc())
        .all()
    )
    cards = db.query(ReviewCard).filter(ReviewCard.user_id == user_id).all()

    if not mistakes and not cards:
        return "该用户还没有任何错题和复习卡。"

    counter = Counter()
    for mistake in mistakes:
        for tag in (mistake.knowledge_points or "").replace(",", "、").replace("，", "、").split("、"):
            tag = tag.strip()
            if tag:
                counter[tag] += 1

    lines = [f"错题总数：{len(mistakes)}"]
    if counter:
        lines.append("错题集中的考点：" + "、".join(f"{k}（{v}题）" for k, v in counter.most_common(5)))
    titles = [m.title for m in mistakes[:6] if m.title]
    if titles:
        lines.append("最近错题：" + "；".join(titles))
    if cards:
        due = sum(1 for c in cards if c.next_review_date and c.next_review_date <= date.today())
        weak = sorted(cards, key=lambda c: (c.ease_factor or 2.5))[:5]
        weak_points = "、".join({c.knowledge_point for c in weak if c.knowledge_point})
        lines.append(f"复习卡 {len(cards)} 张，今日待复习 {due} 张。")
        if weak_points:
            lines.append("掌握最弱的知识点：" + weak_points)
    return "\n".join(lines)


async def _execute_tool(name: str, args: dict, db: Session, user_id: int) -> str:
    if name == "search_knowledge":
        passages = await retrieve(str(args.get("query", "")), top_k=4)
        if not passages:
            return "知识库中没有检索到相关资料。"
        return "\n\n".join(
            f"【{p['heading']}】{p['text'][:260]}" for p in passages
        )

    if name == "get_study_status":
        return _study_status(db, user_id)

    if name == "generate_quiz":
        return await quiz_agent(
            str(args.get("topic", "考研政治核心知识点")),
            int(args.get("difficulty", 3) or 3),
            str(args.get("quiz_type", "single")),
        )

    if name == "create_review_card":
        card = ReviewCard(
            user_id=user_id,
            question=str(args.get("question", "")).strip(),
            answer=str(args.get("answer", "")).strip(),
            knowledge_point=(str(args.get("knowledge_point", "")).strip() or None),
            next_review_date=date.today(),
        )
        if not card.question or not card.answer:
            return "创建失败：question 和 answer 都不能为空。"
        db.add(card)
        db.commit()
        return f"复习卡已创建（知识点：{card.knowledge_point or '未标注'}），已加入今日复习队列。"

    return f"未知工具：{name}"


async def _chat(messages: list[dict]) -> dict:
    payload = {
        "model": settings.siliconflow_chat_model,
        "messages": messages,
        "tools": TOOL_SPECS,
        "temperature": 0.2,
        "max_tokens": 1400,
        "enable_thinking": False,
        "stream": False,
    }
    timeout = httpx.Timeout(180.0, connect=15.0, read=180.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{settings.siliconflow_base_url}/chat/completions",
            headers={"Authorization": f"Bearer {settings.siliconflow_api_key}"},
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]


async def run_manager(instruction: str, db: Session, user_id: int) -> dict:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": instruction},
    ]
    steps: list[dict] = []

    for _ in range(MAX_STEPS):
        message = await _chat(messages)
        tool_calls = message.get("tool_calls")

        if not tool_calls:
            return {"answer": message.get("content") or "", "steps": steps}

        messages.append(message)
        for call in tool_calls:
            name = call["function"]["name"]
            try:
                args = json.loads(call["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}

            result = await _execute_tool(name, args, db, user_id)
            steps.append({
                "tool": name,
                "label": TOOL_LABELS.get(name, name),
                "args": args,
                "result_preview": result[:150],
            })
            messages.append({
                "role": "tool",
                "tool_call_id": call.get("id", ""),
                "content": result,
            })

    return {
        "answer": "任务包含的步骤较多，已执行的动作见步骤列表；可以把任务拆小一点再试。",
        "steps": steps,
    }
