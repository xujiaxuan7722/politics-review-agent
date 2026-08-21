import re
import asyncio
import json

import httpx

from app.config import settings
from app.services.prompt_rules import POLITICS_CONCISE_OUTPUT_RULES


DEFAULT_SYSTEM = """
你是一名考研政治复习助手。回答要短、准、能直接背。
不要输出乱码、HTML、JSON、代码块、LaTeX 公式、反斜杠转义符。
不要复述“user / assistant / system”等聊天角色标记。
如果题目要求固定格式，必须严格按固定格式输出，不要额外添加标题。

""" + POLITICS_CONCISE_OUTPUT_RULES


STRICT_REPAIR_SYSTEM = """
你是一名考研政治复习助手。上一次回答包含乱码或格式污染。
请重新回答：只输出简体中文、必要的 A/B/C/D 选项、必要标点和题目要求的固定标题。
禁止输出孤立的 D、DD、DDD、引用编号、残缺 Markdown、重复字、代码块、HTML、JSON、聊天角色标记。
"""


def clean_qwen_text(text: str) -> str:
    if not text:
        return ""

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u0000", "")
    text = text.replace("\ufffd", "")
    text = re.sub(r"\[ID:\s*\d+\]", "", text)
    text = re.sub(r"\[\d+\]", "", text)
    text = re.sub(r"[\u0590-\u05ff\u0250-\u02af]+", "", text)
    text = re.sub(r"```(?:markdown|md|text)?", "", text, flags=re.IGNORECASE)
    text = text.replace("```", "")
    text = re.sub(r"(?im)^\s*(user|assistant|system)\s*[:：]?\s*$", "", text)
    text = re.sub(r"([A-Za-z])\1{8,}", "", text)
    text = re.sub(r"\b([A-Za-z])\b(?:\s+\1\b){8,}", "", text)
    text = re.sub(r"([一-龥])\1{3,}", r"\1", text)
    for phrase in ("选项", "正确", "符合", "错误", "影响", "说明", "观点", "答案"):
        text = re.sub(f"(?:{phrase}){{2,}}", phrase, text)
    text = re.sub(r"\*{3,}", "", text)
    text = re.sub(r"(?<!\*)\*\*(?!\*)", "", text)
    text = re.sub(r"[，,]{2,}", "，", text)
    text = re.sub(r"[。\.]{2,}", "。", text)
    text = re.sub(r"，\s*。", "。", text)
    text = re.sub(r"：\s*。", "：", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = clean_ai_output_noise(text)

    return text.strip()


def clean_ai_output_noise(text: str) -> str:
    if not text:
        return ""

    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # Collapse repeated Chinese characters/phrases caused by model degeneration.
    text = re.sub(r"([一-龥])\1{3,}(?=[一-龥])", "", text)
    text = re.sub(r"([一-龥])\1{3,}", r"\1", text)
    text = re.sub(r"([一-龥]{2,8})(?:\1){2,}", r"\1", text)
    text = re.sub(r"([A-Za-z])\1{3,}", r"\1", text)

    for phrase in (
        "可以",
        "但是",
        "因此",
        "所以",
        "意识",
        "物质",
        "实践",
        "认识",
        "正确",
        "错误",
        "观点",
        "答案",
        "解析",
    ):
        text = re.sub(f"(?:{phrase}){{2,}}", phrase, text)

    # Drop exact repeated lines and repeated sentences while preserving Markdown headings.
    lines = []
    seen_lines = set()
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            lines.append("")
            continue
        if not line.startswith("#"):
            key = re.sub(r"\s+", "", line)
            if key in seen_lines:
                continue
            seen_lines.add(key)
        lines.append(raw_line)

    text = "\n".join(lines)
    text = dedupe_sentences(text)
    text = re.sub(r"[，,]{2,}", "，", text)
    text = re.sub(r"[。\.]{2,}", "。", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def dedupe_sentences(text: str) -> str:
    blocks = []
    for block in text.split("\n\n"):
        if block.lstrip().startswith("#"):
            blocks.append(block)
            continue

        pieces = re.split(r"([。！？!?])", block)
        sentences = []
        for index in range(0, len(pieces), 2):
            sentence = pieces[index].strip()
            punctuation = pieces[index + 1] if index + 1 < len(pieces) else ""
            if sentence:
                sentences.append(sentence + punctuation)

        if not sentences:
            blocks.append(block)
            continue

        seen = set()
        kept = []
        for sentence in sentences:
            key = re.sub(r"\s+", "", sentence)
            if key in seen:
                continue
            seen.add(key)
            kept.append(sentence)
        blocks.append("".join(kept))

    return "\n\n".join(blocks)


async def call_qwen(
    prompt: str,
    system: str = DEFAULT_SYSTEM,
    max_tokens: int = 1200,
    temperature: float = 0.2,
    thinking: bool = False,
) -> str:
    raw_text = await _post_qwen(prompt, system, max_tokens, temperature, thinking)
    cleaned = clean_qwen_text(raw_text)

    if is_dirty_qwen_output(cleaned):
        repair_prompt = f"""
请重新生成回答。要求：
1. 严格遵守原任务的格式。
2. 不要输出孤立的 D、DD、DDD、引用编号、乱码、残缺 Markdown 或重复字。
3. 如果是选择题，A/B/C/D 只能作为选项编号或答案字母出现。

原任务：
{prompt}
"""
        await asyncio.sleep(0.2)
        raw_text = await _post_qwen(
            repair_prompt,
            f"{system}\n\n{STRICT_REPAIR_SYSTEM}",
            max_tokens,
            min(temperature, 0.1),
            thinking,
        )
        cleaned = clean_qwen_text(raw_text)

    return cleaned


async def _post_qwen(
    prompt: str,
    system: str,
    max_tokens: int,
    temperature: float,
    thinking: bool = False,
) -> str:
    url = f"{settings.siliconflow_base_url}/chat/completions"

    headers = {
        "Authorization": f"Bearer {settings.siliconflow_api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": settings.siliconflow_chat_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
        # 思考模式下 max_tokens 同时覆盖思考与正文，需要预留额度
        "max_tokens": max_tokens + 2200 if thinking else max_tokens,
        "frequency_penalty": 0.4,
        "enable_thinking": thinking,
        "stream": False,
    }

    timeout = httpx.Timeout(180.0, connect=15.0, read=180.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()

    return data["choices"][0]["message"]["content"]


async def stream_qwen(
    prompt: str,
    system: str = DEFAULT_SYSTEM,
    max_tokens: int = 1800,
    temperature: float = 0.3,
):
    """流式产出模型回答，逐段 yield 文本增量。"""
    url = f"{settings.siliconflow_base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.siliconflow_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.siliconflow_chat_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "frequency_penalty": 0.4,
        "enable_thinking": False,
        "stream": True,
    }

    timeout = httpx.Timeout(180.0, connect=15.0, read=180.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("POST", url, headers=headers, json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    chunk = json.loads(data)
                    delta = chunk["choices"][0]["delta"].get("content") or ""
                except (KeyError, IndexError, json.JSONDecodeError):
                    continue
                if delta:
                    yield delta


def is_dirty_qwen_output(text: str) -> bool:
    if not text:
        return True

    if "\ufffd" in text:
        return True

    if re.search(r"\[ID:\s*\d+\]", text):
        return True

    if re.search(r"([A-Za-z])\1{8,}", text):
        return True

    if re.search(r"\b([A-Za-z])\b(?:\s+\1\b){8,}", text):
        return True

    if re.search(r"([一-龥])\1{3,}", text):
        return True

    if re.search(r"([一-龥]{2,8})(?:\1){2,}", text):
        return True

    return False
