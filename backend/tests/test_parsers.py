"""判卷 / 制卡解析逻辑的单元测试（不调用大模型）。"""

from app.routers.pipeline import parse_judgement
from app.routers.review import is_placeholder_card, parse_cards_text
from app.services.agents import extract_student_choice, extract_student_choices, normalize_question_type


def test_parse_judgement_reads_each_verdict():
    assert parse_judgement("## 判断\n错误\n\n## 正确答案及解析\n……") == "错误"
    assert parse_judgement("## 判断\n基本正确\n") == "基本正确"
    assert parse_judgement("## 判断\n\n正确\n") == "正确"


def test_parse_judgement_returns_unknown_when_section_missing():
    assert parse_judgement("模型输出了别的东西") == "未知"
    assert parse_judgement("") == "未知"


def test_parse_cards_text_extracts_q_a_k():
    text = "Q: 新民主主义革命的领导力量是谁？\nA: 无产阶级及其政党。领导权是区别新旧民主主义革命的根本标志。\nK: 新民主主义革命的领导力量"
    cards = parse_cards_text(text)
    assert len(cards) == 1
    assert cards[0]["question"].startswith("新民主主义革命的领导力量")
    assert "无产阶级" in cards[0]["answer"]
    assert cards[0]["knowledge_point"] == "新民主主义革命的领导力量"


def test_parse_cards_text_rejects_template_echo():
    # 模型把格式说明原样抄回来时，不能生成一张叫“复习问题”的卡
    text = "Q: 复习问题\nA: 标准答案\nK: 知识点"
    assert parse_cards_text(text) == []
    assert is_placeholder_card("「复习问题」", "「标准答案」")
    assert not is_placeholder_card("实践是检验真理的唯一标准说明了什么？", "实践具有直接现实性，是连接主观与客观的桥梁。")


def test_extract_student_choice_handles_fullwidth_and_noise():
    assert extract_student_choice("b") == "B"
    assert extract_student_choice("我选 Ｃ") == "C"
    assert extract_student_choice("答案是D。") == "D"
    assert extract_student_choice("ABCD 全选") is None  # 连续字母不是单选
    assert extract_student_choice("不知道") is None


def test_extract_student_choices_handles_multi_select_and_noise():
    assert extract_student_choices("B") == "B"
    assert extract_student_choices("abd") == "ABD"
    assert extract_student_choices("A、C") == "AC"
    assert extract_student_choices("B 和 D") == "BD"
    assert extract_student_choices("Ｂ,Ｃ") == "BC"
    assert extract_student_choices("我觉得是生产力") is None
    assert extract_student_choices("ABCDE") is None


def test_normalize_question_type_accepts_aliases():
    assert normalize_question_type("multi") == "multi"
    assert normalize_question_type("多选题") == "multi"
    assert normalize_question_type("单选") == "single"
    assert normalize_question_type(None) == "unknown"
    assert normalize_question_type("whatever") == "unknown"
