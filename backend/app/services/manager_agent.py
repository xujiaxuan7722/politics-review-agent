"""学习管家：自主决策的调度智能体。

模型通过 function calling 自行决定调用哪些工具（搜知识库、查学习状态、
查到期卡、按考点翻错题、讲解、出题、判卷、建复习卡），循环执行直到任务
完成——这是系统中真正意义上的自主编排入口。讲解/出题/判卷等确定性模块
（agents.py）在这里只是被编排的工具。
"""

import asyncio
import json
import logging
import re
from collections import Counter
from collections.abc import Awaitable, Callable
from datetime import date, timedelta

import httpx
from sqlalchemy.orm import Session

from app.config import LLMEndpoint, settings
from app.models import Mistake, ReviewCard
from app.services.agents import explainer_agent, grader_agent, quiz_agent
from app.services.llm_service import auth_headers, llm_client, strip_latex, thinking_fields
from app.services.rag_service import retrieve

logger = logging.getLogger(__name__)

MAX_STEPS = 6
MAX_REPEATS = 2  # 同一工具同参数重复调用超过此数，强制收尾

DUPLICATE_NOTICE = (
    "（此工具已用相同参数调用过，结果见前文，不要重复调用。"
    "请基于已有信息继续下一步，或直接给出最终答复。）"
)
WRAP_UP_NOTICE = "工具调用已达上限。请只根据上面已经拿到的工具结果，直接给出完整的最终答复，不要再调用工具。"

TOOL_LABELS = {
    "search_knowledge": "检索知识库",
    "get_study_status": "查看学习状态",
    "get_due_cards": "查看到期复习卡",
    "get_mistakes_by_topic": "翻阅相关错题",
    "explain_topic": "讲解知识点",
    "generate_quiz": "生成练习题",
    "grade_answer": "批改答案",
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
            "name": "get_due_cards",
            "description": "查询用户今日及未来三天内到期的复习卡，附掌握度（熟练度越低越薄弱）和累计遗忘次数。安排复习时段、决定先背什么时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "description": "向后查看的天数，默认 3"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_mistakes_by_topic",
            "description": "按考点或关键词翻阅用户的错题原文和已有分析，看清具体错在哪里。诊断某个考点为什么薄弱、针对性出题前调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "考点或关键词，如“剩余价值”“矛盾的同一性”"},
                    "limit": {"type": "integer", "description": "最多返回几条，默认 3"},
                },
                "required": ["topic"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "explain_topic",
            "description": "结合教材知识库，系统讲解一个考研政治知识点（是什么/怎么理解/易混与考法）。用户要求讲解、或错题分析后需要补课时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "要讲解的知识点"},
                },
                "required": ["topic"],
            },
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
            "name": "grade_answer",
            "description": "批改用户对某道题的作答，给出对错判断、正确答案和解析。用户给出题目和自己的答案让你判断时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "题目原文（含选项）"},
                    "student_answer": {"type": "string", "description": "用户的作答"},
                    "question_type": {"type": "string", "enum": ["single", "multi", "unknown"], "description": "题型：用户明确说了单选/多选就传 single/multi，否则 unknown"},
                },
                "required": ["question", "student_answer"],
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
2. 要弄清某个考点具体错在哪，用 get_mistakes_by_topic 看错题原文；安排背诵时用 get_due_cards 看到期卡。
3. 用户要求讲解、或需要补课时用 explain_topic；只需核实一句表述时用 search_knowledge。
4. 需要练习时用 generate_quiz；用户给出题目和作答让你判断时用 grade_answer；值得背诵的结论用 create_review_card 固化。
5. 工具调用完成后，用中文给出条理清晰的最终答复：先说结论/成果，再简述你做了哪些动作。工具返回的讲解、题目、批改内容要完整保留在答复里，不要只写一句“已完成”。
6. 不要重复调用同一工具做同样的事。
7. 不要只描述你打算做什么（“我先看看…再出题”）；要做就直接调用工具，只有任务全部完成后才输出最终答复。"""

# 护栏四：模型没调工具、只说了句"接下来我会…"就结束时，推它一把继续执行（最多一次）
INTENT_PATTERN = re.compile(r"(我先|接下来|让我|马上|然后再|我来|稍等|请稍候|我将|我会)[^。！!\n]{0,40}(看|查|出|做|讲|批|生成|检索|创建|分析|安排)")
INTENT_NUDGE = "你刚才只描述了计划，没有执行。请直接调用工具完成剩余步骤，全部完成后再给最终答复。"


def looks_like_intent_only(content: str) -> bool:
    text = (content or "").strip()
    return 0 < len(text) <= 120 and bool(INTENT_PATTERN.search(text))

PLANNER_RULES = """本次任务是制定复习计划。额外要求：
- 必须先调用 get_study_status 和 get_due_cards，再决定计划内容；有明显薄弱考点时用 get_mistakes_by_topic 看一眼错在哪。
- 计划按“通读→深挖→巩固”三段组织，每段写明知识点、做什么、大约用时；到期复习卡必须安排进“巩固”段。
- 用户补充说明中的时间限制、主攻方向优先级最高。
- 不要在计划里调用 explain_topic 或 generate_quiz 展开讲解和出题，计划只列安排。"""


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


def _due_cards(db: Session, user_id: int, days: int) -> str:
    days = max(0, min(int(days or 3), 14))
    horizon = date.today() + timedelta(days=days)
    cards = (
        db.query(ReviewCard)
        .filter(ReviewCard.user_id == user_id, ReviewCard.next_review_date <= horizon)
        .order_by(ReviewCard.next_review_date.asc(), ReviewCard.ease_factor.asc())
        .limit(20)
        .all()
    )
    if not cards:
        return f"未来 {days} 天内没有到期的复习卡。"

    lines = [f"未来 {days} 天内到期复习卡 {len(cards)} 张（按到期日、掌握度排序）："]
    for card in cards:
        due_label = "今日" if card.next_review_date <= date.today() else card.next_review_date.isoformat()
        lines.append(
            f"- [{due_label}] {card.knowledge_point or '未标注'}｜{card.question[:40]}"
            f"｜熟练度 {card.ease_factor:.2f}｜遗忘 {card.lapses or 0} 次"
        )
    return "\n".join(lines)


def _mistakes_by_topic(db: Session, user_id: int, topic: str, limit: int) -> str:
    topic = (topic or "").strip()
    limit = max(1, min(int(limit or 3), 6))
    query = db.query(Mistake).filter(Mistake.user_id == user_id)
    if topic:
        pattern = f"%{topic}%"
        query = query.filter(
            (Mistake.knowledge_points.ilike(pattern))
            | (Mistake.title.ilike(pattern))
            | (Mistake.clean_text.ilike(pattern))
        )
    mistakes = query.order_by(Mistake.id.desc()).limit(limit).all()
    if not mistakes:
        return f"没有找到与“{topic}”相关的错题。" if topic else "错题本还是空的。"

    blocks = []
    for mistake in mistakes:
        body = (mistake.clean_text or mistake.raw_text or "").strip()[:300]
        analysis = (mistake.analysis or "").strip()[:200]
        block = f"【{mistake.title}】考点：{mistake.knowledge_points or '未标注'}\n题目：{body or '（无原文）'}"
        if mistake.student_answer:
            block += f"\n当时作答：{mistake.student_answer[:80]}"
        if analysis:
            block += f"\n已有分析：{analysis}"
        blocks.append(block)
    return "\n\n".join(blocks)


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

    if name == "get_due_cards":
        return _due_cards(db, user_id, args.get("days", 3))

    if name == "get_mistakes_by_topic":
        return _mistakes_by_topic(db, user_id, str(args.get("topic", "")), args.get("limit", 3))

    if name == "explain_topic":
        return await explainer_agent(str(args.get("topic", "")) or "考研政治核心知识点")

    if name == "generate_quiz":
        return await quiz_agent(
            str(args.get("topic", "考研政治核心知识点")),
            int(args.get("difficulty", 3) or 3),
            str(args.get("quiz_type", "single")),
        )

    if name == "grade_answer":
        question = str(args.get("question", "")).strip()
        student_answer = str(args.get("student_answer", "")).strip()
        if not question or not student_answer:
            return "批改失败：question 和 student_answer 都不能为空。"
        return await grader_agent(question, student_answer, question_type=str(args.get("question_type", "unknown")))

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


async def _chat_via(endpoint: LLMEndpoint, messages: list[dict], use_tools: bool) -> dict:
    payload = {
        "model": endpoint.model,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": 1400,
        "stream": False,
        **thinking_fields(endpoint.provider, False),  # 管家只做调度，不开思考
    }
    if use_tools:
        payload["tools"] = TOOL_SPECS
    timeout = httpx.Timeout(120.0, connect=15.0, read=120.0)
    async with llm_client(timeout) as client:
        resp = await client.post(
            f"{endpoint.base_url}/chat/completions",
            headers=auth_headers(endpoint),
            json=payload,
        )
        resp.raise_for_status()
        message = resp.json()["choices"][0]["message"]
    if not isinstance(message, dict):
        raise ValueError("模型返回缺少 message")
    return message


RETRY_DELAY = 2.0  # 同一端点瞬时错误（429/5xx/超时）的重试间隔


def _transient(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 502, 503, 504)
    return isinstance(exc, httpx.TransportError)


def _endpoint_chain() -> list[LLMEndpoint]:
    """管家降级链：专用组 → 通用对话组 → 基础组（去重，保持顺序）。"""
    chain: list[LLMEndpoint] = []
    for ep in (settings.manager_endpoint, settings.chat_endpoint, settings.base_chat_endpoint):
        if ep not in chain:
            chain.append(ep)
    return chain


async def _chat(messages: list[dict], use_tools: bool = True) -> dict:
    """沿降级链逐个端点尝试；瞬时错误在同一端点先重试一次，再换下一个端点。

    返回的 message 带一个私有键 _endpoint（本次实际使用的端点标签），
    调用方在把 message 回灌进对话历史前要 pop 掉。
    """
    last: Exception | None = None
    for ep in _endpoint_chain():
        for attempt in (1, 2):
            try:
                message = await _chat_via(ep, messages, use_tools)
                message["_endpoint"] = ep.label
                return message
            except (httpx.HTTPError, KeyError, ValueError) as exc:
                last = exc
                brief = f"HTTP {exc.response.status_code}" if isinstance(exc, httpx.HTTPStatusError) else type(exc).__name__
                if attempt == 1 and _transient(exc):
                    logger.warning("学习管家端点 %s 失败（%s），%.0fs 后重试", ep.label, brief, RETRY_DELAY)
                    await asyncio.sleep(RETRY_DELAY)
                    continue
                logger.warning("学习管家端点 %s 失败（%s），换下一个端点", ep.label, brief)
                break
    assert last is not None
    raise last


EventHook = Callable[[dict], Awaitable[None]]


async def run_manager(
    instruction: str,
    db: Session,
    user_id: int,
    extra_rules: str = "",
    max_steps: int = MAX_STEPS,
    on_event: EventHook | None = None,
    history: list[dict] | None = None,
) -> dict:
    """on_event 可选：每次模型思考、每个工具开始/结束时回调一个事件字典，供 SSE 推进度。
    history 可选：之前几轮的 user/assistant 消息（已截断），让"再出一道类似的"能被理解。"""
    system = SYSTEM_PROMPT if not extra_rules else f"{SYSTEM_PROMPT}\n\n{extra_rules}"
    if history:
        system += "\n\n下面先给出你和用户之前几轮的对话，最后一条才是本次任务；“再来一道”“类似的”等指代请结合上文理解。"

    async def emit(event: dict) -> None:
        if on_event is not None:
            await on_event(event)
    messages = [
        {"role": "system", "content": system},
        *(history or []),
        {"role": "user", "content": instruction},
    ]
    steps: list[dict] = []
    executed: set[tuple[str, str]] = set()
    repeats = 0
    endpoints: list[str] = []

    def _note(message: dict) -> dict:
        label = message.pop("_endpoint", None)
        if label:
            endpoints.append(label)
        return message

    def _result(answer: str, exhausted: bool) -> dict:
        primary = settings.manager_endpoint.label
        return {
            "answer": strip_latex(answer),
            "steps": steps,
            "exhausted": exhausted,
            "model": endpoints[-1] if endpoints else primary,
            "degraded": any(label != primary for label in endpoints),
        }

    nudged = False
    for round_no in range(1, max_steps + 1):
        await emit({"type": "thinking", "round": round_no})
        message = _note(await _chat(messages))
        tool_calls = message.get("tool_calls")

        if not tool_calls:
            content = message.get("content") or ""
            if not nudged and looks_like_intent_only(content):
                nudged = True
                messages.append({"role": "assistant", "content": content})
                messages.append({"role": "user", "content": INTENT_NUDGE})
                await emit({"type": "nudge", "reason": "intent_only"})
                continue
            return _result(content, exhausted=False)

        messages.append(message)
        for call in tool_calls:
            name = call["function"]["name"]
            try:
                args = json.loads(call["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            label = TOOL_LABELS.get(name, name)

            # 护栏一：同一工具同参数重复调用不再执行，提醒模型往前走。
            key = (name, json.dumps(args, ensure_ascii=False, sort_keys=True))
            if key in executed:
                repeats += 1
                result = DUPLICATE_NOTICE
            else:
                executed.add(key)
                await emit({"type": "step", "status": "running", "tool": name, "label": label, "args": args})
                failed = False
                try:
                    result = await _execute_tool(name, args, db, user_id)
                except Exception as exc:  # 单个工具失败不拖垮整条链，让模型知情后继续
                    db.rollback()
                    failed = True
                    result = f"工具 {name} 执行失败：{type(exc).__name__}。请不要重试该工具，基于已有信息继续或如实告知用户。"
                step = {
                    "tool": name,
                    "label": label,
                    "args": args,
                    "result_preview": result[:150],
                }
                steps.append(step)
                await emit({"type": "step", "status": "failed" if failed else "done", **step})
            messages.append({
                "role": "tool",
                "tool_call_id": call.get("id", ""),
                "content": result,
            })

        if repeats >= MAX_REPEATS:
            break

    # 护栏二：步数用尽或反复打转时，不丢已取到的数据，强制模型只凭现有结果收尾。
    messages.append({"role": "user", "content": WRAP_UP_NOTICE})
    await emit({"type": "thinking", "round": max_steps + 1, "wrap_up": True})
    final = _note(await _chat(messages, use_tools=False))
    answer = (final.get("content") or "").strip()
    if not answer:
        answer = "任务包含的步骤较多，已执行的动作见步骤列表；可以把任务拆小一点再试。"
    return _result(answer, exhausted=True)


async def plan_with_manager(db: Session, user_id: int, manual_note: str = "") -> dict:
    """复习规划入口：让学习管家自己决定查哪些数据，再产出计划。

    返回 {"answer", "steps", "consulted"}；consulted=False 表示模型一条数据都没查
    就直接作答（或收尾失败），调用方应当回退到确定性的 review_planner_agent。
    步数用尽但已取到数据并成功收尾的情况视为 consulted=True。
    """
    instruction = "根据我的真实学习状态，制定明天的复习计划。"
    manual_note = (manual_note or "").strip()
    if manual_note:
        instruction += f"\n\n我的补充说明：{manual_note}"

    result = await run_manager(instruction, db, user_id, extra_rules=PLANNER_RULES, max_steps=8)
    consulted = any(
        step["tool"] in ("get_study_status", "get_due_cards", "get_mistakes_by_topic")
        for step in result["steps"]
    )
    result["consulted"] = consulted and bool(result.get("answer"))
    return result
