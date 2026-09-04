import json
import re

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.routers.auth import get_current_user
from app.routers.records import add_record
from app.services.llm_service import call_qwen, stream_qwen
from app.services.rag_service import build_context, retrieve

router = APIRouter(prefix="/api/chat", tags=["chat"])

CHAT_SYSTEM = "你是政治资深辅导老师。回答要准确、有深度、结构清晰，适合考生理解和背诵。"

ANSWER_FORMAT = """
回答分三个部分，深度逐层递进，严格按下面的骨架输出（「……」处填实际内容）：

## 结论
「3-5 句话，直接、完整地回答问题本身。」

## 要点
1. **「本条要点的考点短语，例如：领导力量与阶级基础」**：「先把这个知识点本身讲透彻，再自然衔接一两句它在考试中怎么考、答题时抓什么关键词。写成连贯的一段话，段内不加任何小标签。」
2. **「另一条要点的考点短语」**：「同上，共写 4-6 条，每条的加粗标题必须概括该条自己的内容，各条标题互不相同。」

## 深挖
1. **「点出本条针对的具体概念或题型，格式如“××与××的区分”“××类题的设错方式”，标题里必须出现具体概念名」**：「写一个具体的易混辨析、出题套路或考生高频错误理解，共 2-3 条，面向想拿高分的考生，允许写得更深。」

以上骨架中的说明文字只是要求，不要把任何说明文字原样抄进回答。
"""


class ChatRequest(BaseModel):
    question: str


def clean_chat_answer(text: str) -> str:
    if not text:
        return ""

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\[ID:\s*\d+\]", "", text)
    text = re.sub(r"\*{3,}", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def build_prompt(question: str, context: str = "") -> str:
    if context:
        return f"""
请基于下面的政治资料回答用户问题。

用户问题：
{question}

知识库资料：
{context}

要求：
1. 优先使用资料中的表述作答；资料没有覆盖的部分可以用你的学科知识补充，但不得与资料矛盾。
2. 回答要有深度和层次，目标是让考生"看完能答对这一类题"，不是给一句简单定义。
3. 不确定的时政内容提示"以最新官方材料为准"，不要编造。
{ANSWER_FORMAT}
"""
    return f"""
请回答下面的政治问题。

用户问题：
{question}

要求：
1. 回答要有深度和层次，目标是让考生"看完能答对这一类题"。
2. 不确定的时政内容提示"以最新官方材料为准"，不要编造。
{ANSWER_FORMAT}
"""


async def answer_with_context(question: str, context: str) -> str:
    answer = await call_qwen(
        build_prompt(question, context),
        system=CHAT_SYSTEM,
        max_tokens=1800,
        temperature=0.3,
    )
    return clean_chat_answer(answer)


async def answer_directly(question: str) -> str:
    answer = await call_qwen(
        build_prompt(question),
        system=CHAT_SYSTEM,
        max_tokens=1800,
        temperature=0.3,
    )
    return clean_chat_answer(answer)


@router.post("/ask-stream")
async def ask_stream(
    req: ChatRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """SSE 流式问答：先推送检索元信息，再逐段推送回答文本。"""
    try:
        passages = await retrieve(req.question)
    except Exception:
        passages = []

    if passages:
        meta = {
            "type": "meta",
            "mode": "rag",
            "reference": [
                {"heading": p["heading"], "score": p["score"]} for p in passages
            ],
        }
        prompt = build_prompt(req.question, build_context(passages))
    else:
        meta = {
            "type": "meta",
            "mode": "llm",
            "reference": [],
            "notice": "知识库中没有检索到相关资料，本回答由大模型直接生成。",
        }
        prompt = build_prompt(req.question)

    def sse(payload: dict) -> str:
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    async def event_stream():
        yield sse(meta)
        parts: list[str] = []
        try:
            async for delta in stream_qwen(prompt, system=CHAT_SYSTEM, max_tokens=1800):
                parts.append(delta)
                yield sse({"type": "delta", "text": delta})
        except Exception as error:
            yield sse({"type": "error", "detail": f"模型服务异常：{error}"})
            return

        answer = clean_chat_answer("".join(parts))
        add_record(
            db,
            current_user.id,
            source="chat",
            title=req.question.strip()[:60],
            question=req.question,
            content=answer,
        )
        yield sse({"type": "done"})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/ask")
async def ask(req: ChatRequest, current_user: User = Depends(get_current_user)):
    try:
        passages = await retrieve(req.question)
    except Exception:
        passages = []

    if passages:
        context = build_context(passages)
        answer = await answer_with_context(req.question, context)
        return {
            "mode": "rag",
            "answer": answer,
            "reference": [
                {
                    "heading": item["heading"],
                    "snippet": item["text"][:120],
                    "score": item["score"],
                }
                for item in passages
            ],
        }

    answer = await answer_directly(req.question)
    return {
        "mode": "llm",
        "answer": answer,
        "reference": [],
        "notice": "知识库中没有检索到相关资料，本回答由大模型直接生成。",
    }
