"""学习管家（工具调用型 Agent）：用假模型回复驱动循环，验证工具注册、分发与复习规划兜底。"""

import json
from datetime import date, timedelta

import pytest

from app.models import Mistake, ReviewCard
from app.services import manager_agent
from app.routers import agents as agents_router


def _tool_call(name: str, args: dict, call_id: str = "c1") -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}],
    }


def _final(text: str) -> dict:
    return {"role": "assistant", "content": text}


def _scripted_chat(replies: list[dict], seen: list[list[dict]]):
    """按顺序返回预设的模型回复，并记录每轮收到的 messages（附 use_tools 标记）。"""
    queue = list(replies)

    async def fake_chat(messages, use_tools=True):
        seen.append([{"_use_tools": use_tools}] + list(messages))
        return queue.pop(0)

    return fake_chat


def _seed(db, user_id):
    m = Mistake(user_id=user_id, title="剩余价值率的计算", clean_text="剩余价值率 m' = m/v，下列说法正确的是……",
                knowledge_points="剩余价值、马原", student_answer="B", analysis="混淆了剩余价值率与利润率。")
    db.add(m)
    db.add(ReviewCard(user_id=user_id, question="剩余价值的源泉？", answer="雇佣工人的剩余劳动。",
                      knowledge_point="剩余价值", next_review_date=date.today(), ease_factor=1.6, lapses=2))
    db.add(ReviewCard(user_id=user_id, question="矛盾的同一性？", answer="……",
                      knowledge_point="矛盾", next_review_date=date.today() + timedelta(days=10)))
    db.commit()


def test_tool_specs_cover_all_registered_tools():
    names = {spec["function"]["name"] for spec in manager_agent.TOOL_SPECS}
    assert names == set(manager_agent.TOOL_LABELS)
    assert {"explain_topic", "grade_answer", "get_due_cards", "get_mistakes_by_topic"} <= names


@pytest.mark.anyio
async def test_manager_dispatches_new_tools_and_feeds_results_back(db, user, monkeypatch):
    _seed(db, user["id"])
    seen: list[list[dict]] = []
    monkeypatch.setattr(manager_agent, "_chat", _scripted_chat([
        _tool_call("get_mistakes_by_topic", {"topic": "剩余价值"}),
        _tool_call("explain_topic", {"topic": "剩余价值率"}, "c2"),
        _tool_call("grade_answer", {"question": "剩余价值率公式？", "student_answer": "m/v"}, "c3"),
        _final("讲解与批改已完成。"),
    ], seen))

    async def fake_explainer(topic):
        return f"[讲解] {topic}"

    async def fake_grader(question, student_answer, **kwargs):
        return f"[批改] {question} <- {student_answer}"

    monkeypatch.setattr(manager_agent, "explainer_agent", fake_explainer)
    monkeypatch.setattr(manager_agent, "grader_agent", fake_grader)

    result = await manager_agent.run_manager("剩余价值这块我总错，讲讲再判一下", db, user["id"])

    assert result["answer"] == "讲解与批改已完成。"
    assert result["exhausted"] is False
    assert [s["tool"] for s in result["steps"]] == ["get_mistakes_by_topic", "explain_topic", "grade_answer"]
    assert [s["label"] for s in result["steps"]] == ["翻阅相关错题", "讲解知识点", "批改答案"]

    # 错题工具确实查到了库里的原文，且结果以 role=tool 回灌给了模型
    assert "剩余价值率的计算" in result["steps"][0]["result_preview"]
    tool_messages = [m for m in seen[-1] if m.get("role") == "tool"]
    assert all(m["_use_tools"] for m in (seen[i][0] for i in range(len(seen))))
    assert [m["tool_call_id"] for m in tool_messages] == ["c1", "c2", "c3"]
    assert tool_messages[1]["content"] == "[讲解] 剩余价值率"
    assert tool_messages[2]["content"].startswith("[批改]")


@pytest.mark.anyio
async def test_due_cards_tool_respects_horizon_and_orders_weakest_first(db, user):
    _seed(db, user["id"])
    three_days = await manager_agent._execute_tool("get_due_cards", {"days": 3}, db, user["id"])
    assert "剩余价值" in three_days and "矛盾" not in three_days
    assert "[今日]" in three_days and "遗忘 2 次" in three_days

    two_weeks = await manager_agent._execute_tool("get_due_cards", {"days": 14}, db, user["id"])
    assert "矛盾" in two_weeks


@pytest.mark.anyio
async def test_planner_marks_consulted_only_when_data_tools_were_used(db, user, monkeypatch):
    _seed(db, user["id"])

    monkeypatch.setattr(manager_agent, "_chat", _scripted_chat([_final("凭空编的计划")], []))
    result = await manager_agent.plan_with_manager(db, user["id"])
    assert result["consulted"] is False

    seen: list[list[dict]] = []
    monkeypatch.setattr(manager_agent, "_chat", _scripted_chat([
        _tool_call("get_study_status", {}),
        _tool_call("get_due_cards", {}, "c2"),
        _final("## 通读\n……\n## 深挖\n……\n## 巩固\n……"),
    ], seen))
    result = await manager_agent.plan_with_manager(db, user["id"], manual_note="明天只有 2 小时")
    assert result["consulted"] is True
    assert "明天只有 2 小时" in seen[0][2]["content"]
    assert manager_agent.PLANNER_RULES in seen[0][1]["content"]


@pytest.mark.anyio
async def test_repeated_tool_call_is_blocked_and_loop_wraps_up_without_tools(db, user, monkeypatch):
    _seed(db, user["id"])
    seen: list[list[dict]] = []
    same = {"query": "矛盾的同一性"}
    monkeypatch.setattr(manager_agent, "_chat", _scripted_chat([
        _tool_call("get_study_status", {}),
        _tool_call("search_knowledge", same, "c2"),
        _tool_call("search_knowledge", same, "c3"),   # 重复 1：拦截，不执行
        _tool_call("search_knowledge", same, "c4"),   # 重复 2：拦截并触发收尾
        _final("基于已有数据的收尾答复"),
    ], seen))

    async def fake_retrieve(query, top_k=4):
        return [{"heading": "矛盾", "text": "同一性……"}]

    monkeypatch.setattr(manager_agent, "retrieve", fake_retrieve)

    result = await manager_agent.run_manager("讲讲矛盾", db, user["id"], max_steps=10)

    assert result["exhausted"] is True
    assert result["answer"] == "基于已有数据的收尾答复"
    # 只真正执行了两次工具，重复的两次没有进入轨迹
    assert [s["tool"] for s in result["steps"]] == ["get_study_status", "search_knowledge"]
    # 被拦截的调用收到的是提醒而不是检索结果
    last_tool_msgs = [m for m in seen[-1] if m.get("role") == "tool"]
    assert last_tool_msgs[-1]["content"] == manager_agent.DUPLICATE_NOTICE
    # 收尾那一轮不带工具，且附了收尾指令
    assert seen[-1][0]["_use_tools"] is False
    assert seen[-1][-1] == {"role": "user", "content": manager_agent.WRAP_UP_NOTICE}


@pytest.mark.anyio
async def test_exhausted_steps_still_wrap_up_and_count_as_consulted_for_planner(db, user, monkeypatch):
    _seed(db, user["id"])
    monkeypatch.setattr(manager_agent, "_chat", _scripted_chat([
        _tool_call("get_study_status", {}),
        _tool_call("get_due_cards", {"days": 3}, "c2"),
        _final("## 通读……（收尾）"),
    ], []))
    result = await manager_agent.run_manager("做计划", db, user["id"], max_steps=2)
    assert result["exhausted"] is True and result["answer"] == "## 通读……（收尾）"

    monkeypatch.setattr(manager_agent, "_chat", _scripted_chat([
        _tool_call("get_study_status", {}),
        _tool_call("get_due_cards", {"days": 3}, "c2"),
        _tool_call("search_knowledge", {"query": "x"}, "c3"),
        _tool_call("search_knowledge", {"query": "y"}, "c4"),
        _tool_call("search_knowledge", {"query": "z"}, "c5"),
        _tool_call("search_knowledge", {"query": "w"}, "c6"),
        _tool_call("search_knowledge", {"query": "v"}, "c7"),
        _tool_call("search_knowledge", {"query": "u"}, "c8"),
        _final("## 通读……（收尾）"),
    ], []))

    async def fake_retrieve(query, top_k=4):
        return []

    monkeypatch.setattr(manager_agent, "retrieve", fake_retrieve)
    result = await manager_agent.plan_with_manager(db, user["id"])
    assert result["exhausted"] is True and result["consulted"] is True


def test_plan_endpoint_falls_back_when_manager_did_not_consult_data(client, db, user, monkeypatch):
    _seed(db, user["id"])

    async def no_consult(db_, user_id, manual_note=""):
        return {"answer": "编的", "steps": [], "consulted": False, "exhausted": False}

    async def deterministic_plan(summary):
        return "[兜底计划] " + summary.splitlines()[0]

    monkeypatch.setattr(agents_router, "plan_with_manager", no_consult)
    monkeypatch.setattr(agents_router, "review_planner_agent", deterministic_plan)

    resp = client.post("/api/agents/plan", json={"mistakes_summary": ""}, headers=user["headers"])
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["mode"] == "fallback"
    assert data["answer"].startswith("[兜底计划] 错题总数：1")


def test_plan_endpoint_uses_manager_answer_and_exposes_steps(client, db, user, monkeypatch):
    _seed(db, user["id"])

    async def consulted(db_, user_id, manual_note=""):
        return {
            "answer": "## 通读……",
            "steps": [{"tool": "get_study_status", "label": "查看学习状态", "args": {}, "result_preview": ""}],
            "consulted": True,
            "exhausted": False,
        }

    async def must_not_run(summary):
        raise AssertionError("管家已取数，不应再走兜底")

    monkeypatch.setattr(agents_router, "plan_with_manager", consulted)
    monkeypatch.setattr(agents_router, "review_planner_agent", must_not_run)

    data = client.post("/api/agents/plan", json={"mistakes_summary": "主攻马原"}, headers=user["headers"]).json()
    assert data["mode"] == "manager"
    assert data["answer"] == "## 通读……"
    assert data["steps"][0]["label"] == "查看学习状态"


@pytest.mark.anyio
async def test_tool_failure_is_reported_to_model_instead_of_crashing(db, user, monkeypatch):
    seen: list[list[dict]] = []
    monkeypatch.setattr(manager_agent, "_chat", _scripted_chat([
        _tool_call("grade_answer", {"question": "q", "student_answer": "B"}),
        _final("批改服务暂时不可用，稍后再试。"),
    ], seen))

    async def broken_grader(question, student_answer, **kwargs):
        raise TimeoutError("upstream slow")

    monkeypatch.setattr(manager_agent, "grader_agent", broken_grader)

    result = await manager_agent.run_manager("判一下", db, user["id"])
    assert result["answer"] == "批改服务暂时不可用，稍后再试。"
    assert result["steps"][0]["tool"] == "grade_answer"
    tool_msg = [m for m in seen[-1] if m.get("role") == "tool"][0]
    assert "执行失败：TimeoutError" in tool_msg["content"]


@pytest.mark.anyio
async def test_run_manager_emits_progress_events_in_order(db, user, monkeypatch):
    _seed(db, user["id"])
    monkeypatch.setattr(manager_agent, "_chat", _scripted_chat([
        _tool_call("get_study_status", {}),
        _tool_call("grade_answer", {"question": "q", "student_answer": "B"}, "c2"),
        _final("完成"),
    ], []))

    async def broken_grader(question, student_answer, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(manager_agent, "grader_agent", broken_grader)
    events: list[dict] = []

    async def on_event(event):
        events.append(event)

    result = await manager_agent.run_manager("看状态再判卷", db, user["id"], on_event=on_event)
    assert result["answer"] == "完成"
    assert [(e["type"], e.get("status"), e.get("tool"), e.get("round")) for e in events] == [
        ("thinking", None, None, 1),
        ("step", "running", "get_study_status", None),
        ("step", "done", "get_study_status", None),
        ("thinking", None, None, 2),
        ("step", "running", "grade_answer", None),
        ("step", "failed", "grade_answer", None),
        ("thinking", None, None, 3),
    ]
    assert "错题总数" in events[2]["result_preview"]


def test_manager_stream_endpoint_relays_events_then_done(client, user, monkeypatch):
    async def fake_run_manager(instruction, db, user_id, on_event=None, **kwargs):
        await on_event({"type": "thinking", "round": 1})
        await on_event({"type": "step", "status": "running", "tool": "get_study_status", "label": "查看学习状态", "args": {}})
        await on_event({"type": "step", "status": "done", "tool": "get_study_status", "label": "查看学习状态", "args": {}, "result_preview": "x"})
        return {"answer": "最终答复", "steps": [{"tool": "get_study_status", "label": "查看学习状态", "args": {}, "result_preview": "x"}],
                "exhausted": False, "model": "sensenova/deepseek-v4-pro", "degraded": False}

    monkeypatch.setattr(agents_router, "run_manager", fake_run_manager)
    with client.stream("POST", "/api/agents/manager-stream", json={"instruction": "看看状态"}, headers=user["headers"]) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        body = "".join(resp.iter_text())

    events = [json.loads(line[5:]) for line in body.split("\n\n") if line.startswith("data:")]
    assert [e["type"] for e in events] == ["meta", "thinking", "step", "step", "done"]
    assert events[0]["conversation_id"] == events[-1]["conversation_id"] and events[0]["history_messages"] == 0
    assert events[-1]["answer"] == "最终答复" and events[-1]["model"] == "sensenova/deepseek-v4-pro"
    # 有答复就写入记录
    records = client.get("/api/records", headers=user["headers"]).json()
    assert any(r["title"].startswith("学习管家：看看状态") for r in records)


def test_manager_stream_endpoint_reports_failure_as_error_event(client, user, monkeypatch):
    async def failing(instruction, db, user_id, on_event=None, **kwargs):
        raise TimeoutError("upstream")

    monkeypatch.setattr(agents_router, "run_manager", failing)
    with client.stream("POST", "/api/agents/manager-stream", json={"instruction": "x"}, headers=user["headers"]) as resp:
        body = "".join(resp.iter_text())
    events = [json.loads(line[5:]) for line in body.split("\n\n") if line.startswith("data:")]
    assert events[-1]["type"] == "error" and "TimeoutError" in events[-1]["detail"]


# ---------------- 多轮对话 ----------------

from app.services import conversation_service as cs  # noqa: E402


def test_history_truncation_keeps_recent_and_starts_with_user():
    msgs = [{"role": "assistant", "content": "孤零零的助手"}]
    msgs += [{"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}" + "x" * 1000} for i in range(10)]
    out = cs.truncate_history(msgs)
    assert out[0]["role"] == "user"
    assert sum(len(m["content"]) for m in out) <= cs.HISTORY_MAX_CHARS
    assert out[-1]["content"].startswith("m9")
    long_reply = [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a" * 5000}]
    assert cs.truncate_history(long_reply)[1]["content"].endswith("…（已截断）")


def test_manager_threads_history_into_following_turns(client, user, monkeypatch):
    seen_histories: list[list[dict]] = []

    async def fake_run_manager(instruction, db, user_id, history=None, **kwargs):
        seen_histories.append(list(history or []))
        return {"answer": f"答：{instruction}", "steps": [{"tool": "generate_quiz", "label": "生成练习题", "args": {}, "result_preview": ""}],
                "exhausted": False, "model": "m", "degraded": False}

    monkeypatch.setattr(agents_router, "run_manager", fake_run_manager)
    h = user["headers"]

    first = client.post("/api/agents/manager", json={"instruction": "出一道矛盾的题"}, headers=h).json()
    cid = first["conversation_id"]
    second = client.post("/api/agents/manager", json={"instruction": "再来一道类似的", "conversation_id": cid}, headers=h).json()
    assert second["conversation_id"] == cid
    assert seen_histories[0] == []
    assert seen_histories[1] == [
        {"role": "user", "content": "出一道矛盾的题"},
        {"role": "assistant", "content": "答：出一道矛盾的题"},
    ]

    listing = client.get("/api/agents/conversations", headers=h).json()
    assert [c["id"] for c in listing] == [cid] and listing[0]["title"] == "出一道矛盾的题"
    detail = client.get(f"/api/agents/conversations/{cid}", headers=h).json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant", "user", "assistant"]
    assert detail["messages"][1]["steps"][0]["label"] == "生成练习题"

    # 不新开对话：传空 conversation_id 会再建一个
    third = client.post("/api/agents/manager", json={"instruction": "新话题"}, headers=h).json()
    assert third["conversation_id"] != cid

    assert client.delete(f"/api/agents/conversations/{cid}", headers=h).json() == {"ok": True}
    assert client.get(f"/api/agents/conversations/{cid}", headers=h).status_code == 404


def test_conversations_are_isolated_between_users(client, user, monkeypatch):
    async def fake_run_manager(instruction, db, user_id, history=None, **kwargs):
        return {"answer": "x", "steps": [], "exhausted": False, "model": "m", "degraded": False}

    monkeypatch.setattr(agents_router, "run_manager", fake_run_manager)
    cid = client.post("/api/agents/manager", json={"instruction": "私密"}, headers=user["headers"]).json()["conversation_id"]

    other = client.post("/api/auth/register", json={"username": f"o_{cid}_x", "password": "secret123"}).json()
    oh = {"Authorization": f"Bearer {other['token']}"}
    assert client.get(f"/api/agents/conversations/{cid}", headers=oh).status_code == 404
    assert client.post("/api/agents/manager", json={"instruction": "蹭", "conversation_id": cid}, headers=oh).status_code == 404
    with client.stream("POST", "/api/agents/manager-stream", json={"instruction": "蹭", "conversation_id": cid}, headers=oh) as resp:
        assert resp.status_code == 404


# ---------------- 题型由用户指定 ----------------

from app.services import agents as agents_module  # noqa: E402


@pytest.mark.anyio
async def test_grader_prompt_carries_user_specified_question_type(monkeypatch):
    prompts: list[str] = []

    async def fake_call_qwen(prompt, **kwargs):
        prompts.append(prompt)
        return "## 判断\n错误"

    async def no_rag(*a, **k):
        return []

    monkeypatch.setattr(agents_module, "call_qwen", fake_call_qwen)
    monkeypatch.setattr(agents_module, "retrieve", no_rag)

    await agents_module.grader_agent("题干 A.x B.y C.z D.w", "A", question_type="multi")
    await agents_module.grader_agent("题干 A.x B.y C.z D.w", "ABD", question_type="single")
    await agents_module.grader_agent("题干 A.x B.y C.z D.w", "A")
    assert "题型：多选题（由用户指定）" in prompts[0] and "学生选项已由系统解析为：A" in prompts[0]
    assert "题型：单选题（由用户指定）" in prompts[1] and "学生选项已由系统解析为：ABD" in prompts[1]
    assert "题型：未指定" in prompts[2]


def test_grade_archive_passes_question_type_through(client, user, monkeypatch):
    from app.routers import pipeline as pipeline_router
    seen: list[dict] = []

    async def fake_grader(question, student_answer, **kwargs):
        seen.append(kwargs)
        return "## 判断\n错误\n\n## 正确答案及解析\n规范答案为 ABCD"

    monkeypatch.setattr(pipeline_router, "grader_agent", fake_grader)
    resp = client.post("/api/pipeline/grade-archive", json={
        "question_text": "32. 不断增进对党的创新理论的\nA.政治认同\nB.思想认同\nC.理论认同\nD.情感认同",
        "student_answer": "A", "question_type": "multi",
    }, headers=user["headers"])
    assert resp.status_code == 200, resp.text
    assert seen[0]["question_type"] == "multi"
    assert resp.json()["judgement"] == "错误"


# ---------------- 护栏四：只说计划不执行 ----------------

def test_intent_only_detector():
    assert manager_agent.looks_like_intent_only("你掌握最弱的考点是「马克思主义的理论体系」。我先看看你在这个考点上具体错在哪，再针对性出题。")
    assert manager_agent.looks_like_intent_only("接下来我会为你出一道题。")
    assert not manager_agent.looks_like_intent_only("题目已生成：\n\n【单选题】矛盾的普遍性……\nA. …\nB. …")
    assert not manager_agent.looks_like_intent_only("")


@pytest.mark.anyio
async def test_manager_nudges_once_when_model_only_narrates_plan(db, user, monkeypatch):
    _seed(db, user["id"])
    seen: list[list[dict]] = []
    monkeypatch.setattr(manager_agent, "_chat", _scripted_chat([
        _tool_call("get_study_status", {}),
        _final("你最弱的是剩余价值。我先看看具体错在哪，再针对性出题。"),   # 只说计划 → 被推一把
        _tool_call("generate_quiz", {"topic": "剩余价值"}, "c2"),
        _final("题目已生成：……"),
    ], seen))

    async def fake_quiz(topic, difficulty=3, quiz_type="single"):
        return "## 单选题\n题干：…"

    monkeypatch.setattr(manager_agent, "quiz_agent", fake_quiz)
    events: list[dict] = []

    async def on_event(e):
        events.append(e)

    result = await manager_agent.run_manager("看看我最弱的考点并出题", db, user["id"], on_event=on_event)
    assert result["answer"] == "题目已生成：……"
    assert [s["tool"] for s in result["steps"]] == ["get_study_status", "generate_quiz"]
    assert any(e["type"] == "nudge" for e in events)
    # 推一把的话进了对话历史
    assert seen[2][-1] == {"role": "user", "content": manager_agent.INTENT_NUDGE}


@pytest.mark.anyio
async def test_manager_nudges_at_most_once(db, user, monkeypatch):
    monkeypatch.setattr(manager_agent, "_chat", _scripted_chat([
        _final("我先看看你的情况。"),
        _final("接下来我会出题。"),   # 第二次仍只说计划：不再推，直接返回
    ], []))
    result = await manager_agent.run_manager("x", db, user["id"])
    assert result["answer"] == "接下来我会出题。" and result["steps"] == []
