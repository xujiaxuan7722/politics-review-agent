"""学习管家多轮对话记忆：读历史、截断、写回。

只存用户说的话和管家的最终答复；工具调用的中间消息不进历史，
否则几轮下来 token 会被工具结果撑爆。步骤轨迹以 JSON 挂在助手消息上供回看。
"""

import json
from datetime import datetime

from sqlalchemy.orm import Session

from app.models import Conversation, ConversationMessage

HISTORY_MAX_MESSAGES = 12   # 最多带最近几条（用户+助手合计）
HISTORY_MAX_CHARS = 6000    # 总字符上限，超了从最旧的开始丢
ASSISTANT_MAX_CHARS = 1500  # 单条助手答复进历史时截到这么长（保留开头，结论在前）


def get_or_create_conversation(db: Session, user_id: int, conversation_id: int | None, first_instruction: str) -> Conversation:
    if conversation_id is not None:
        conv = (
            db.query(Conversation)
            .filter(Conversation.id == conversation_id, Conversation.user_id == user_id)
            .first()
        )
        if conv is None:
            raise LookupError("对话不存在或不属于当前用户")
        return conv
    conv = Conversation(user_id=user_id, title=first_instruction.strip()[:60] or "新对话")
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return conv


def load_history(db: Session, conversation_id: int) -> list[dict]:
    """按时间顺序取最近的对话消息，转成模型 messages 格式并做截断。"""
    rows = (
        db.query(ConversationMessage)
        .filter(ConversationMessage.conversation_id == conversation_id)
        .order_by(ConversationMessage.id.desc())
        .limit(HISTORY_MAX_MESSAGES)
        .all()
    )
    rows.reverse()
    return truncate_history([{"role": r.role, "content": r.content} for r in rows])


def truncate_history(messages: list[dict]) -> list[dict]:
    trimmed = []
    for m in messages:
        content = m["content"] or ""
        if m["role"] == "assistant" and len(content) > ASSISTANT_MAX_CHARS:
            content = content[:ASSISTANT_MAX_CHARS] + "…（已截断）"
        trimmed.append({"role": m["role"], "content": content})

    total = sum(len(m["content"]) for m in trimmed)
    while trimmed and total > HISTORY_MAX_CHARS:
        dropped = trimmed.pop(0)
        total -= len(dropped["content"])
    # 历史必须以 user 开头，否则会出现两条 assistant 连着的情况
    while trimmed and trimmed[0]["role"] != "user":
        trimmed.pop(0)
    return trimmed


def save_turn(
    db: Session,
    conv: Conversation,
    user_id: int,
    instruction: str,
    answer: str,
    steps: list[dict] | None = None,
    model: str | None = None,
) -> None:
    db.add(ConversationMessage(conversation_id=conv.id, user_id=user_id, role="user", content=instruction))
    db.add(ConversationMessage(
        conversation_id=conv.id,
        user_id=user_id,
        role="assistant",
        content=answer,
        steps_json=json.dumps(steps or [], ensure_ascii=False),
        model=model,
    ))
    conv.updated_at = datetime.utcnow()
    db.commit()


def list_conversations(db: Session, user_id: int, limit: int = 30) -> list[Conversation]:
    return (
        db.query(Conversation)
        .filter(Conversation.user_id == user_id)
        .order_by(Conversation.updated_at.desc())
        .limit(limit)
        .all()
    )


def get_messages(db: Session, conv: Conversation) -> list[dict]:
    rows = (
        db.query(ConversationMessage)
        .filter(ConversationMessage.conversation_id == conv.id)
        .order_by(ConversationMessage.id.asc())
        .all()
    )
    out = []
    for r in rows:
        item = {"id": r.id, "role": r.role, "content": r.content, "created_at": r.created_at.isoformat()}
        if r.role == "assistant":
            try:
                item["steps"] = json.loads(r.steps_json or "[]")
            except json.JSONDecodeError:
                item["steps"] = []
            item["model"] = r.model
        out.append(item)
    return out
