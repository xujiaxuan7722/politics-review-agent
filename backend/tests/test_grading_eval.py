"""判卷评测：题集完整性、输出解析、打分汇总（不调模型）。"""

from pathlib import Path

from app.services import grading_eval as ge

QUESTIONS = Path(__file__).resolve().parents[1] / "evals" / "grading" / "questions.jsonl"


def test_question_set_is_well_formed_and_balanced():
    items = ge.load_questions(QUESTIONS)
    assert len(items) >= 40
    assert len({i["id"] for i in items}) == len(items)
    for it in items:
        assert it["answer"] and set(it["answer"]) <= set("ABCD")
        assert it["student_answer"] and set(it["student_answer"]) <= set("ABCD")
        assert all(f"\n{ch}." in it["question"] for ch in "ABCD"), it["id"]
        assert ("多选" in it["question"]) == (len(it["answer"]) > 1), it["id"]
    correct = sum(1 for it in items if ge.expected_judgement(it) == "正确")
    assert 0.35 <= correct / len(items) <= 0.65  # 学生答对/答错大致对半，判断准确率才不会被"全判正确"蒙混
    assert len({it["module"] for it in items}) >= 4


def test_parse_judgement_reads_the_judgement_section():
    assert ge.parse_judgement("## 判断\n正确\n\n## 正确答案及解析\n…") == "正确"
    assert ge.parse_judgement("## 判断\n错误\n\n## 正确答案及解析\n正确答案是 C") == "错误"
    assert ge.parse_judgement("## 判断\n部分正确（漏选了 C）\n") == "错误"
    assert ge.parse_judgement("## 判断\n\n错误\n") == "错误"
    assert ge.parse_judgement("你的作答判为错误——漏选了 C。\n## 批改结论") == "错误"
    assert ge.parse_judgement("") is None


def test_parse_answer_prefers_normative_answer_and_normalizes_letters():
    assert ge.parse_answer("## 正确答案及解析\n此题疑为多选题，规范答案为 BC。\n…正确答案是 B") == "BC"
    assert ge.parse_answer("## 正确答案及解析\n选择题：正确选项是 B. 剩余价值率总是大于利润率") == "B"
    assert ge.parse_answer("正确答案：Ｃ") == "C"
    assert ge.parse_answer("规范答案为 A、B 和 D") == "ABD"
    assert ge.parse_answer("正确答案应为 D。学生选了 A") == "D"
    assert ge.parse_answer("## 正确答案及解析\n选择题：B. 德国古典哲学\n解析：…") == "B"
    assert ge.parse_answer("## 正确答案及解析\n选择题：B 实践") == "B"
    assert ge.parse_answer("## 正确答案及解析\n本题应选 A、C 两项。") == "AC"
    assert ge.parse_answer("正确选项：A. 绝对剩余价值生产；B. 相对剩余价值生产。") == "AB"
    assert ge.parse_answer("正确答案：B（Apple 公司的 A 股无关）") == "B"
    assert ge.parse_answer("正确选项：B。资本各部分在剩余价值生产中所起作用的不同。") == "B"
    assert ge.parse_answer("正确答案：ABC（统一战线、武装斗争、党的建设）") == "ABC"
    assert ge.parse_answer("选择题：正确选项为 ABD。矛盾的同一性是指") == "ABD"
    assert ge.parse_answer("这道题没有给出答案字母") is None


def test_score_and_summarize():
    item = {"id": "x1", "module": "马原", "answer": "B", "student_answer": "A"}
    good = ge.score_item(item, "## 判断\n错误\n\n## 正确答案及解析\n正确答案是 B")
    assert good["judgement_ok"] and good["answer_ok"] and good["both_ok"]
    bad = ge.score_item(item, "## 判断\n正确\n\n## 正确答案及解析\n正确答案是 A")
    assert not bad["judgement_ok"] and not bad["answer_ok"]
    rows = [dict(good, latency_s=3.0), dict(bad, latency_s=5.0), {"id": "x3", "module": "史纲", "error": "Timeout"}]
    s = ge.summarize(rows)
    assert s["n"] == 3 and s["errors"] == 1 and s["graded"] == 2
    assert s["judgement_acc"] == 50.0 and s["both_acc"] == 50.0  # 准确率只算判到的题，失败题不进分母
    assert ge.summarize([{"id": "e", "module": "m", "error": "x"}])["judgement_acc"] is None
    assert s["by_module"]["马原"] == {"n": 2, "judgement_ok": 1, "answer_ok": 1, "both_ok": 1}
    assert s["latency_p50_s"] == 5.0 and s["latency_max_s"] == 5.0
