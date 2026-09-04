import re
import asyncio
import json

import httpx

import logging

from app.config import LLMEndpoint, settings

logger = logging.getLogger(__name__)
from app.services.prompt_rules import POLITICS_CONCISE_OUTPUT_RULES


FORMAT_RULES = (
    "格式硬规则：公式一律用纯文本写（如 m′=m/v、p′=m/(c+v)），"
    "不要用 LaTeX、$ 符号、\\frac、\\( \\) 等标记；不要输出代码块、HTML、聊天角色标记。"
)

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


def strip_latex(text: str) -> str:
    """把模型偶尔冒出的 LaTeX 标记降成纯文本（前端不渲染公式）。"""
    if "\\" not in text and "$" not in text:
        return text
    text = re.sub(r"\\frac\{([^{}]*)\}\{([^{}]*)\}", r"\1/\2", text)
    text = re.sub(r"\\(?:text|mathrm|mathbf|operatorname)\{([^{}]*)\}", r"\1", text)
    text = re.sub(r"\\[\[\]()]", "", text)          # \[ \] \( \)
    text = re.sub(r"\$\$?", "", text)                # $ 与 $$
    text = re.sub(r"\\(?:times|cdot)\b", "×", text)
    text = re.sub(r"\\(?:quad|,|;|!)", " ", text)
    text = re.sub(r"\\(?:left|right)\b", "", text)
    return text


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
    text = strip_latex(text)
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


def with_format_rules(system: str) -> str:
    system = (system or "").rstrip()
    return system if FORMAT_RULES in system else f"{system}\n\n{FORMAT_RULES}"


def thinking_fields(provider: str, thinking: bool) -> dict:
    """把"是否开思考"翻译成各平台自己的字段。

    siliconflow（Qwen3）：enable_thinking=true/false
    sensenova（DeepSeek/Kimi 等）：默认开思考，必须显式传 reasoning_effort="none" 才关；
        否则 max_tokens 会被推理烧光、正文为空。开思考时不传即可。
    其他平台：不传任何私有字段。
    """
    provider = (provider or "").lower()
    if provider == "siliconflow":
        return {"enable_thinking": thinking}
    if provider == "sensenova":
        return {} if thinking else {"reasoning_effort": "none"}
    return {}


def llm_client(timeout: httpx.Timeout | float) -> httpx.AsyncClient:
    """模型请求一律直连，不读环境变量里的代理（国内站点走境外代理会随机断连）。"""
    return httpx.AsyncClient(timeout=timeout, trust_env=False)


def auth_headers(endpoint: LLMEndpoint) -> dict:
    return {"Authorization": f"Bearer {endpoint.api_key}", "Content-Type": "application/json"}


async def call_qwen(
    prompt: str,
    system: str = DEFAULT_SYSTEM,
    max_tokens: int = 1200,
    temperature: float = 0.2,
    thinking: bool = False,
    endpoint: LLMEndpoint | None = None,
    thinking_gate: bool | None = None,
    allow_fallback: bool = True,
) -> str:
    """endpoint 缺省为通用对话组；thinking_gate 缺省为 CHAT_THINKING 总闸；
    allow_fallback=False 时只重试不降级（评测要保证答的是指定模型）。"""
    gate = settings.chat_thinking if thinking_gate is None else thinking_gate
    thinking = thinking and gate
    system = with_format_rules(system)
    raw_text = await _post_qwen(prompt, system, max_tokens, temperature, thinking, endpoint, allow_fallback)
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
            endpoint,
            allow_fallback,
        )
        cleaned = clean_qwen_text(raw_text)

    return cleaned


async def _post_qwen(
    prompt: str,
    system: str,
    max_tokens: int,
    temperature: float,
    thinking: bool = False,
    endpoint: LLMEndpoint | None = None,
    allow_fallback: bool = True,
) -> str:
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": prompt},
    ]

    async def attempt(ep: LLMEndpoint) -> str:
        return await _post_chat(ep, messages, max_tokens, temperature, thinking)

    return await with_fallback(attempt, primary=endpoint, allow_fallback=allow_fallback)


RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in RETRYABLE_STATUS
    return isinstance(exc, (httpx.TransportError, KeyError, ValueError))


async def with_fallback(
    attempt,
    retry_delays: tuple[float, ...] = (2.0, 4.0),
    primary: LLMEndpoint | None = None,
    allow_fallback: bool = True,
):
    """对话降级链：主端点（缺省通用对话组）失败（限流/网关/超时）按退避间隔重试，
    仍失败且基础组是另一个端点时，切到基础组（SiliconFlow）再试。"""
    primary = primary or settings.chat_endpoint
    base = settings.base_chat_endpoint
    last: Exception | None = None
    for i, delay in enumerate((0.0, *retry_delays)):
        if delay:
            await asyncio.sleep(delay)
        try:
            return await attempt(primary)
        except Exception as exc:
            if not _is_retryable(exc):
                raise
            last = exc
            logger.warning("对话端点 %s 失败（%s），第 %d/%d 次", primary.label, _brief(exc), i + 1, 1 + len(retry_delays))
    if base == primary or not allow_fallback:
        raise last  # type: ignore[misc]
    logger.warning("对话端点 %s 连续失败，降级到 %s", primary.label, base.label)
    return await attempt(base)


def _brief(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    return type(exc).__name__


async def _post_chat(
    endpoint: LLMEndpoint,
    messages: list[dict],
    max_tokens: int,
    temperature: float,
    thinking: bool,
) -> str:
    payload = {
        "model": endpoint.model,
        "messages": messages,
        "temperature": temperature,
        # 思考模式下 max_tokens 同时覆盖思考与正文，需要预留额度
        "max_tokens": max_tokens + 2200 if thinking else max_tokens,
        "frequency_penalty": 0.4,
        "stream": False,
        **thinking_fields(endpoint.provider, thinking),
    }
    timeout = httpx.Timeout(180.0, connect=15.0, read=180.0)
    async with llm_client(timeout) as client:
        resp = await client.post(f"{endpoint.base_url}/chat/completions", headers=auth_headers(endpoint), json=payload)
        resp.raise_for_status()
        data = resp.json()
    content = data["choices"][0]["message"].get("content")
    if content is None:
        raise ValueError("模型返回空正文")
    return content


async def stream_qwen(
    prompt: str,
    system: str = DEFAULT_SYSTEM,
    max_tokens: int = 1800,
    temperature: float = 0.3,
):
    """流式产出模型回答，逐段 yield 文本增量。

    降级只在拿到首个字节之前发生（连接/限流/网关错误）；一旦开始输出就不再切换端点。
    """
    messages = [
        {"role": "system", "content": with_format_rules(system)},
        {"role": "user", "content": prompt},
    ]

    async def open_stream(endpoint: LLMEndpoint):
        payload = {
            "model": endpoint.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "frequency_penalty": 0.4,
            "stream": True,
            **thinking_fields(endpoint.provider, False),
        }
        timeout = httpx.Timeout(180.0, connect=15.0, read=180.0)
        client = llm_client(timeout)
        try:
            cm = client.stream("POST", f"{endpoint.base_url}/chat/completions", headers=auth_headers(endpoint), json=payload)
            resp = await cm.__aenter__()
            try:
                resp.raise_for_status()
            except Exception:
                await cm.__aexit__(None, None, None)
                raise
        except Exception:
            await client.aclose()
            raise
        return client, cm, resp

    client, cm, resp = await with_fallback(open_stream)
    try:
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
    finally:
        await cm.__aexit__(None, None, None)
        await client.aclose()


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
