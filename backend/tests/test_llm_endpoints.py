"""模型端点分组：配置回落、思考开关按平台翻译、学习管家降级链。"""

from types import SimpleNamespace

import httpx
import pytest

from app.config import LLMEndpoint, Settings
from app.services import manager_agent
from app.services import llm_service
from app.services.llm_service import thinking_fields

BASE = dict(
    siliconflow_api_key="sf-key",
    siliconflow_base_url="https://api.siliconflow.cn/v1/",
    siliconflow_chat_model="Qwen/Qwen3-8B",
    siliconflow_embed_model="BAAI/bge-m3",
    baidu_ocr_api_key="x",
    baidu_ocr_secret_key="y",
    database_url="sqlite://",
)


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **BASE, **overrides)


def test_groups_fall_back_to_siliconflow_when_blank():
    s = _settings()
    assert s.embed_endpoint == LLMEndpoint("siliconflow", "https://api.siliconflow.cn/v1", "sf-key", "BAAI/bge-m3")
    assert s.chat_endpoint == LLMEndpoint("siliconflow", "https://api.siliconflow.cn/v1", "sf-key", "Qwen/Qwen3-8B")
    assert s.manager_endpoint == s.chat_endpoint


def test_manager_group_overrides_only_what_is_set():
    s = _settings(manager_provider="SenseNova", manager_base_url="https://token.sensenova.cn/v1", manager_api_key="sn-key", manager_model="deepseek-v4-pro")
    assert s.manager_endpoint == LLMEndpoint("sensenova", "https://token.sensenova.cn/v1", "sn-key", "deepseek-v4-pro")
    assert s.manager_endpoint.label == "sensenova/deepseek-v4-pro"
    # 只填模型名：沿用通用对话组的平台/地址/密钥
    s = _settings(manager_model="Qwen/Qwen3-32B")
    assert s.manager_endpoint == LLMEndpoint("siliconflow", "https://api.siliconflow.cn/v1", "sf-key", "Qwen/Qwen3-32B")
    # 其他模块不受管家组影响
    assert s.chat_endpoint.model == "Qwen/Qwen3-8B"


def test_thinking_switch_is_translated_per_provider():
    assert thinking_fields("siliconflow", True) == {"enable_thinking": True}
    assert thinking_fields("siliconflow", False) == {"enable_thinking": False}
    assert thinking_fields("sensenova", False) == {"reasoning_effort": "none"}
    assert thinking_fields("sensenova", True) == {}
    assert thinking_fields("openai", True) == {} and thinking_fields("", False) == {}


def _fake_settings(primary: LLMEndpoint, fallback: LLMEndpoint, base: LLMEndpoint | None = None):
    return SimpleNamespace(manager_endpoint=primary, chat_endpoint=fallback, base_chat_endpoint=base or fallback)


MIDDLE = LLMEndpoint("sensenova", "https://token.sensenova.cn/v1", "sn", "deepseek-v4-flash")


PRIMARY = LLMEndpoint("sensenova", "https://token.sensenova.cn/v1", "sn", "deepseek-v4-pro")
FALLBACK = LLMEndpoint("siliconflow", "https://api.siliconflow.cn/v1", "sf", "Qwen/Qwen3-8B")


@pytest.mark.anyio
async def test_manager_chat_retries_transient_error_once_then_walks_the_chain(monkeypatch):
    monkeypatch.setattr(manager_agent, "settings", _fake_settings(PRIMARY, MIDDLE, FALLBACK))
    monkeypatch.setattr(manager_agent, "RETRY_DELAY", 0)
    calls: list[str] = []

    async def fake_via(endpoint, messages, use_tools):
        calls.append(endpoint.label)
        if endpoint is PRIMARY:
            raise httpx.ReadTimeout("slow")          # 瞬时：重试一次
        if endpoint is MIDDLE:
            raise _resp_error(429)                    # 同池限流：重试一次后换端点
        return {"role": "assistant", "content": "来自基础组"}

    monkeypatch.setattr(manager_agent, "_chat_via", fake_via)
    msg = await manager_agent._chat([{"role": "user", "content": "hi"}])
    assert msg["content"] == "来自基础组" and msg["_endpoint"] == FALLBACK.label
    assert calls == [PRIMARY.label, PRIMARY.label, MIDDLE.label, MIDDLE.label, FALLBACK.label]


@pytest.mark.anyio
async def test_manager_chat_skips_retry_on_non_transient_error(monkeypatch):
    monkeypatch.setattr(manager_agent, "settings", _fake_settings(PRIMARY, FALLBACK))
    monkeypatch.setattr(manager_agent, "RETRY_DELAY", 0)
    calls: list[str] = []

    async def fake_via(endpoint, messages, use_tools):
        calls.append(endpoint.label)
        if endpoint is PRIMARY:
            raise _resp_error(400)                    # 参数错：不重试，直接换端点
        return {"role": "assistant", "content": "备用"}

    monkeypatch.setattr(manager_agent, "_chat_via", fake_via)
    msg = await manager_agent._chat([{"role": "user", "content": "hi"}])
    assert msg["_endpoint"] == FALLBACK.label and calls == [PRIMARY.label, FALLBACK.label]


@pytest.mark.anyio
async def test_manager_chat_raises_after_chain_is_exhausted(monkeypatch):
    monkeypatch.setattr(manager_agent, "settings", _fake_settings(FALLBACK, FALLBACK))
    monkeypatch.setattr(manager_agent, "RETRY_DELAY", 0)
    calls: list[str] = []

    async def fake_via(endpoint, messages, use_tools):
        calls.append(endpoint.label)
        raise httpx.ConnectError("down")

    monkeypatch.setattr(manager_agent, "_chat_via", fake_via)
    with pytest.raises(httpx.ConnectError):
        await manager_agent._chat([{"role": "user", "content": "hi"}])
    assert calls == [FALLBACK.label, FALLBACK.label]  # 链上只有一个端点：重试一次即放弃


@pytest.mark.anyio
async def test_run_manager_reports_model_and_degraded_flag(db, user, monkeypatch):
    monkeypatch.setattr(manager_agent, "settings", _fake_settings(PRIMARY, FALLBACK))
    replies = [
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "get_study_status", "arguments": "{}"}}], "_endpoint": PRIMARY.label},
        {"role": "assistant", "content": "答复", "_endpoint": FALLBACK.label},
    ]
    seen: list[list[dict]] = []

    async def fake_chat(messages, use_tools=True):
        seen.append([dict(m) for m in messages])
        return replies.pop(0)

    monkeypatch.setattr(manager_agent, "_chat", fake_chat)
    result = await manager_agent.run_manager("看看状态", db, user["id"])
    assert result["answer"] == "答复"
    assert result["model"] == FALLBACK.label and result["degraded"] is True
    # 私有键 _endpoint 不能混进回灌给模型的对话历史
    assert all("_endpoint" not in m for m in seen[-1])


@pytest.mark.anyio
async def test_global_thinking_switch_caps_per_call_thinking(monkeypatch):
    captured: list[bool] = []

    async def fake_post(prompt, system, max_tokens, temperature, thinking, endpoint=None, allow_fallback=True):
        captured.append(thinking)
        return "答案"

    monkeypatch.setattr(llm_service, "_post_qwen", fake_post)
    monkeypatch.setattr(llm_service, "is_dirty_qwen_output", lambda text: False)

    monkeypatch.setattr(llm_service, "settings", SimpleNamespace(chat_thinking=False))
    await llm_service.call_qwen("q", thinking=True)
    monkeypatch.setattr(llm_service, "settings", SimpleNamespace(chat_thinking=True))
    await llm_service.call_qwen("q", thinking=True)
    await llm_service.call_qwen("q", thinking=False)
    assert captured == [False, True, False]


def _resp_error(status: int) -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "https://x/v1/chat/completions")
    return httpx.HTTPStatusError("err", request=req, response=httpx.Response(status, request=req))


@pytest.mark.anyio
async def test_chat_group_retries_once_then_degrades_to_base_group(monkeypatch):
    monkeypatch.setattr(llm_service, "settings", SimpleNamespace(chat_endpoint=PRIMARY, base_chat_endpoint=FALLBACK))
    calls: list[str] = []

    async def attempt(endpoint):
        calls.append(endpoint.label)
        if endpoint is PRIMARY:
            raise _resp_error(429)
        return "来自基础组"

    assert await llm_service.with_fallback(attempt, retry_delays=(0.0, 0.0)) == "来自基础组"
    assert calls == [PRIMARY.label, PRIMARY.label, PRIMARY.label, FALLBACK.label]


@pytest.mark.anyio
async def test_chat_group_retry_succeeds_without_degrading(monkeypatch):
    monkeypatch.setattr(llm_service, "settings", SimpleNamespace(chat_endpoint=PRIMARY, base_chat_endpoint=FALLBACK))
    calls: list[str] = []

    async def attempt(endpoint):
        calls.append(endpoint.label)
        if len(calls) == 1:
            raise httpx.ReadTimeout("slow")
        return "重试成功"

    assert await llm_service.with_fallback(attempt, retry_delays=(0.0,)) == "重试成功"
    assert calls == [PRIMARY.label, PRIMARY.label]


@pytest.mark.anyio
async def test_chat_group_does_not_retry_client_errors(monkeypatch):
    monkeypatch.setattr(llm_service, "settings", SimpleNamespace(chat_endpoint=PRIMARY, base_chat_endpoint=FALLBACK))
    calls: list[str] = []

    async def attempt(endpoint):
        calls.append(endpoint.label)
        raise _resp_error(400)

    with pytest.raises(httpx.HTTPStatusError):
        await llm_service.with_fallback(attempt, retry_delays=(0.0,))
    assert calls == [PRIMARY.label]


@pytest.mark.anyio
async def test_chat_group_without_separate_base_gives_up_after_retry(monkeypatch):
    monkeypatch.setattr(llm_service, "settings", SimpleNamespace(chat_endpoint=FALLBACK, base_chat_endpoint=FALLBACK))
    calls: list[str] = []

    async def attempt(endpoint):
        calls.append(endpoint.label)
        raise _resp_error(503)

    with pytest.raises(httpx.HTTPStatusError):
        await llm_service.with_fallback(attempt, retry_delays=(0.0,))
    assert calls == [FALLBACK.label, FALLBACK.label]


def test_strip_latex_turns_formula_markup_into_plain_text():
    src = "剩余价值率 \\( m' = \\frac{m}{v} \\)，利润率 $$p' = \\frac{m}{c+v}$$，即 \\[ m' \\times \\text{剩余劳动} \\]"
    out = llm_service.strip_latex(src)
    assert "\\frac" not in out and "$" not in out and "\\(" not in out and "\\text" not in out
    assert "m' = m/v" in out and "p' = m/c+v" in out and "× 剩余劳动" in out
    # 普通文本原样返回
    assert llm_service.strip_latex("m′=m/v，无标记") == "m′=m/v，无标记"


def test_format_rules_are_appended_once():
    once = llm_service.with_format_rules("你是老师。")
    assert once.endswith(llm_service.FORMAT_RULES) and once.startswith("你是老师。")
    assert llm_service.with_format_rules(once) == once


def test_grader_group_defaults_to_chat_group_and_inherits_platform():
    s = _settings()
    assert s.grader_endpoint == s.chat_endpoint and s.grader_thinking_enabled is True
    s = _settings(chat_provider="sensenova", chat_base_url="https://token.sensenova.cn/v1", chat_api_key="sn",
                  chat_model="deepseek-v4-flash", chat_thinking=False, grader_model="deepseek-v4-pro")
    assert s.grader_endpoint == LLMEndpoint("sensenova", "https://token.sensenova.cn/v1", "sn", "deepseek-v4-pro")
    assert s.chat_endpoint.model == "deepseek-v4-flash"
    assert s.grader_thinking_enabled is False
    assert _settings(chat_thinking=False, grader_thinking=True).grader_thinking_enabled is True


@pytest.mark.anyio
async def test_call_qwen_routes_to_given_endpoint_with_its_own_thinking_gate(monkeypatch):
    seen: list[tuple[str, bool]] = []

    async def fake_post_chat(endpoint, messages, max_tokens, temperature, thinking):
        seen.append((endpoint.label, thinking))
        return "ok"

    monkeypatch.setattr(llm_service, "_post_chat", fake_post_chat)
    monkeypatch.setattr(llm_service, "is_dirty_qwen_output", lambda text: False)
    monkeypatch.setattr(llm_service, "settings", SimpleNamespace(chat_endpoint=FALLBACK, base_chat_endpoint=FALLBACK, chat_thinking=False))

    await llm_service.call_qwen("q", thinking=True)                                        # 通用组，总闸关
    await llm_service.call_qwen("q", thinking=True, endpoint=PRIMARY, thinking_gate=True)  # 判卷组，自己的闸开
    assert seen == [(FALLBACK.label, False), (PRIMARY.label, True)]


@pytest.mark.anyio
async def test_fallback_can_be_disabled_for_evals(monkeypatch):
    monkeypatch.setattr(llm_service, "settings", SimpleNamespace(chat_endpoint=PRIMARY, base_chat_endpoint=FALLBACK))
    calls: list[str] = []

    async def attempt(endpoint):
        calls.append(endpoint.label)
        raise _resp_error(429)

    with pytest.raises(httpx.HTTPStatusError):
        await llm_service.with_fallback(attempt, retry_delays=(0.0,), allow_fallback=False)
    assert calls == [PRIMARY.label, PRIMARY.label]  # 重试了，但没有换到基础组
