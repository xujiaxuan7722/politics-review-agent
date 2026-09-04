import re

from app.config import settings
from app.services.llm_service import call_qwen, clean_ai_output_noise
from app.services.rag_service import build_context, retrieve


async def explainer_agent(topic: str) -> str:
    topic = normalize_topic(topic)

    try:
        passages = await retrieve(topic, top_k=4)
    except Exception:
        passages = []

    context_block = ""
    if passages:
        context_block = f"""
可参考的教材资料（优先使用其中表述，不足处可自行补充，但不得矛盾）：
{build_context(passages, max_chars=2600)}
"""

    prompt = f"""
请深入讲解政治知识点：{topic}
{context_block}
输出分三个部分：

## 是什么
先给出准确定义，再说明它的提出背景和在所属模块中的理论地位。

## 怎么理解
展开讲透核心内容和内在逻辑，可以举例。这一部分是讲解的主体，写出让考生真正理解、而不是背标题的深度。

## 易混与考法
2-3 条：和它最容易混淆的概念如何区分、选择题和分析题分别怎么考。

不输出乱码、代码块、聊天角色标记。
"""
    answer = await call_qwen(
        prompt,
        system="你是政治资深辅导老师。讲解要准确、有深度、成体系，目标是让考生真正理解并能应对相关考题。",
        max_tokens=1600,
        temperature=0.3,
    )
    return clean_ai_output_noise(answer)


async def quiz_agent(topic: str, difficulty: int = 3, quiz_type: str = "single") -> str:
    topic = normalize_topic(topic)
    difficulty = normalize_difficulty(difficulty)
    quiz_type = normalize_quiz_type(quiz_type)
    difficulty_guide = describe_quiz_difficulty(difficulty)
    format_block = build_quiz_format_block(quiz_type)
    quiz_label = "单选题" if quiz_type == "single" else "分析题"
    prompt = f"""
你是政治出题智能体。
请围绕主题“{topic}”生成 1 道{quiz_label}。

难度为 {difficulty}/5。
难度要求：{difficulty_guide}

要求：
1. 题目必须围绕“{topic}”本身，不要生成答题方法题、格式题或泛化技巧题。
2. 只生成 1 道{quiz_label}，全文只能出现一次“## {quiz_label}”标题。
3. 不要输出单选题和分析题的混合结果，不要输出第二道题，不要输出多选题。
4. 每道题必须有“题干：”。如果是分析题，只能有“题干：”一行正文。
5. 暂时不要给答案、正确答案、解析、考点或排除项。
6. 必须使用下面固定格式，不要增加其他标题：

{format_block}

7. 不输出答案、正确答案、解析、考点、乱码、重复字母、代码块、HTML 或聊天角色标记。
8. 不要输出“请清理、请整理、原始文本、修复说明”等过程性话术。
"""
    strict_retry_prompt = f"""
你上一次输出不符合要求，请重新生成。

主题：{topic}
题型：{quiz_label}
难度：{difficulty}/5，{difficulty_guide}

硬性规则：
1. 只输出 1 道{quiz_label}。
2. 全文只能出现一次“## {quiz_label}”。
3. 必须包含“题干：”。
4. {'分析题只输出“题干：”，不要输出作答要求、考点、答案、解析。' if quiz_type == 'analysis' else '单选题只输出“题干：”、A-D 四个选项和“考点：”，不要输出答案、解析、E选项。'}
5. 不要输出其他题型、第二道题、解释过程、重复字母或乱码。

固定格式：
{format_block}
"""
    last_result = ""
    for attempt in range(2):
        source_prompt = prompt if attempt == 0 else strict_retry_prompt
        raw_answer = await call_qwen(
            source_prompt,
            system=f"你是政治出题助手。只输出 1 道{quiz_label}，全文只能有一个题型标题，不给答案，不输出其他题型、重复字母或乱码。",
            max_tokens=700,
            temperature=0.12 if attempt == 0 else 0.05,
        )
        cleaned = clean_quiz_output(raw_answer)
        result = finalize_quiz_output(cleaned, quiz_type)
        last_result = result
        if not has_quiz_source_issue(cleaned, quiz_type) and not is_bad_quiz_output(result, quiz_type):
            return result

        # 原始输出不合格时才追加一次包装整理调用
        wrapped = await wrap_quiz_output(topic, difficulty, difficulty_guide, raw_answer, quiz_type)
        cleaned = clean_quiz_output(wrapped)
        result = finalize_quiz_output(cleaned, quiz_type)
        last_result = result
        if not has_quiz_source_issue(cleaned, quiz_type) and not is_bad_quiz_output(result, quiz_type):
            return result

    return repair_quiz_missing_stem(last_result, quiz_type, topic)


async def wrap_quiz_output(
    topic: str,
    difficulty: int,
    difficulty_guide: str,
    raw_answer: str,
    quiz_type: str,
) -> str:
    quiz_type = normalize_quiz_type(quiz_type)
    quiz_label = "单选题" if quiz_type == "single" else "分析题"
    format_block = build_quiz_format_block(quiz_type)
    prompt = f"""
你是政治练习题输出包装器。

用户主题：{topic}
用户选择题型：{quiz_label}
难度：{difficulty}/5
难度要求：{difficulty_guide}

下面是上一步大模型生成的原始题目，可能存在选项缺失、D/E 字母污染、标题不规范、换行混乱等问题：
{raw_answer}

请只做“包装整理后输出”，不要解释你的处理过程。

必须输出下面固定结构，只能包含 1 道{quiz_label}：

{format_block}

包装规则：
1. 必须围绕“{topic}”本身生成或整理，不要变成答题方法题。
2. 只保留用户选择的{quiz_label}，删除其他题型，全文只能保留一个“## {quiz_label}”标题。
3. 删除孤立的 D、E、重复字母和乱码；不要把 D 或 E 粘在中文句子后面。
4. 如果是单选题，必须补齐 A-D 四个选项，禁止 E 选项。
5. 如果是分析题，只保留“题干：”，删除“作答要求：”、“考点：”、答案、解析和多余段落。
6. 暂时不要给答案、正确答案、解析或排除项。
7. 不输出代码块、HTML、JSON、聊天角色标记。
8. 不要输出“请清理后、请重新整理、原始代码、修复说明、下面是整理结果”等过程性话术。
9. 选项必须逐行写成“A. 内容”，不能写成“AD: 内容”“A：内容D”。
10. 不要输出多选题，不要同时输出单选题和分析题。
"""
    return await call_qwen(
        prompt,
        system=f"你是政治练习题格式包装助手。只输出 1 道{quiz_label}，全文只能有一个题型标题，禁止其他题型、答案、解析、E 选项和乱码。",
        max_tokens=700,
        temperature=0.04,
    )


def normalize_quiz_type(value: str) -> str:
    return "analysis" if str(value or "").lower() in ("analysis", "essay", "材料题", "分析题") else "single"


def build_quiz_format_block(quiz_type: str) -> str:
    if normalize_quiz_type(quiz_type) == "analysis":
        return """## 分析题
题干：……"""

    return """## 单选题
题干：……
A. ……
B. ……
C. ……
D. ……
考点：……"""


def normalize_topic(topic: str) -> str:
    topic = re.sub(r"\s+", " ", topic or "").strip()
    return topic[:40] or "政治核心知识点"


def normalize_difficulty(difficulty: int) -> int:
    try:
        value = int(difficulty)
    except (TypeError, ValueError):
        value = 3
    return max(1, min(5, value))


def classify_politics_module(topic: str) -> str:
    text = topic or ""

    if any(keyword in text for keyword in ("马克思", "马原", "辩证", "唯物", "实践", "认识", "矛盾", "生产力", "社会存在")):
        return "马克思主义基本原理"

    if any(
        keyword in text
        for keyword in (
            "毛泽东思想",
            "邓小平理论",
            "三个代表",
            "科学发展观",
            "习近平",
            "新时代",
            "毛中特",
            "新民主主义",
            "社会主义改造",
            "中国式现代化",
            "改革开放",
        )
    ):
        return "毛中特"

    if any(keyword in text for keyword in ("史纲", "近代史", "辛亥", "五四", "抗日", "解放战争", "土地革命")):
        return "中国近现代史纲要"

    if any(keyword in text for keyword in ("思修", "法基", "法治", "道德", "理想信念", "人生观", "价值观", "宪法")):
        return "思想道德与法治"

    if any(keyword in text for keyword in ("形势", "政策", "时政", "外交", "安全", "共同体")):
        return "形势与政策"

    return "政治综合"


def describe_quiz_difficulty(difficulty: int) -> str:
    guides = {
        1: "基础识记题，只考一个核心概念或教材原句，干扰项明显。",
        2: "基础理解题，考概念含义和相近概念区分，题干不要太长。",
        3: "常规应用题，考两个知识点之间的关系，接近普通选择题和简答题难度。",
        4: "提高题，加入简短材料，要求从材料中识别原理并做辨析。",
        5: "综合题，结合材料、现实表述和多知识点关系，题干更接近政治分析题。",
    }
    return guides.get(normalize_difficulty(difficulty), guides[3])


def clean_quiz_output(text: str) -> str:
    if not text:
        return ""

    text = clean_ai_output_noise(text)
    forbidden_line = re.compile(
        r"(答案|正确答案|排除项|答题框架|1\.1\.1|A\s*A|杂志社|请清理|请整理|重新整理|修复说明|原始代码|原始文本|下面是整理)"
    )
    cleaned_lines = []
    skip_multi_section = False

    for line in text.splitlines():
        normalized = line.strip()
        if not normalized:
            if not skip_multi_section:
                cleaned_lines.append("")
            continue

        if re.match(r"^E\s*[.．、:：]", normalized, flags=re.IGNORECASE):
            continue

        if re.search(r"([A-Za-z])\1{8,}|\b([A-Za-z])\b(?:\s+\2\b){8,}", normalized):
            continue

        if forbidden_line.search(normalized):
            continue

        normalized = normalize_quiz_line(normalized)
        if normalized == "__SKIP_MULTI_SECTION__":
            skip_multi_section = True
            continue
        if normalized in ("## 单选题", "## 分析题"):
            skip_multi_section = False
        if skip_multi_section:
            continue
        if normalized:
            cleaned_lines.append(normalized)

    cleaned = "\n".join(cleaned_lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def normalize_quiz_line(line: str) -> str:
    line = re.sub(r"^[\-*▪■●•◦\s]+", "", line.strip())
    line = line.replace("：", ":")

    heading_map = {
        "单题题": "单选题",
        "单选": "单选题",
        "分析题题": "分析题",
    }
    heading_match = re.match(r"^#{1,6}\s*(.+)$", line)
    if heading_match:
        heading = heading_match.group(1).strip(" #|丨")
        heading = re.sub(r"^[DＥE]+", "", heading)
        for wrong, right in heading_map.items():
            if wrong in heading:
                return f"## {right}"
        if "单选" in heading:
            return "## 单选题"
        if "多选" in heading:
            return "__SKIP_MULTI_SECTION__"
        if "分析" in heading:
            return "## 分析题"

    if re.fullmatch(r"[|丨\s]+", line):
        return ""

    line = re.sub(r"^题\s*[DＥE]\s*[:：.．、]?\s*", "题干：", line, flags=re.IGNORECASE)
    line = re.sub(r"^题干\s*[:：.．、]?\s*", "题干：", line)
    line = re.sub(r"^考点\s*[DＥE]?\s*[:：.．、]?\s*", "考点：", line, flags=re.IGNORECASE)
    line = re.sub(r"^作答要求\s*[DＥE]?\s*[:：.．、]?\s*", "作答要求：", line, flags=re.IGNORECASE)

    option_match = re.match(r"^([A-D])\s*[DＥE]?\s*[:：.．、]?\s*(.+)$", line, flags=re.IGNORECASE)
    if option_match:
        option = option_match.group(1).upper()
        content = option_match.group(2).strip()
        content = strip_noise_letters(content)
        return f"{option}. {content}" if content else ""

    if re.match(r"^[EＥ]\s*[:：.．、]", line, flags=re.IGNORECASE):
        return ""

    line = strip_noise_letters(line)
    line = re.sub(r"\s{2,}", " ", line)
    return line.strip()


def strip_noise_letters(text: str) -> str:
    text = re.sub(r"\b([A-Za-z])\b(?:\s+\1\b){2,}", "", text)
    text = re.sub(r"([A-Za-z])\1{2,}", "", text)
    text = re.sub(r"(?<=[一-龥，。；、])\s*[DＥE]+\s*(?=[一-龥，。；、])", "", text)
    text = re.sub(r"(?<=[一-龥，。；、])\s*[DＥE]+$", "", text)
    text = re.sub(r"(?<=[:：.．、])\s*[DＥE]+$", "", text)
    return text.strip(" 　。；;，,")


def finalize_quiz_output(text: str, quiz_type: str = "single") -> str:
    if not text:
        return ""

    quiz_type = normalize_quiz_type(quiz_type)
    expected_heading = "单选题" if quiz_type == "single" else "分析题"
    lines = [line.rstrip() for line in text.splitlines()]
    allowed = []
    current_section = ""
    kept_expected_section = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if current_section not in ("multi", "ignored") and kept_expected_section:
                allowed.append("")
            continue
        if re.match(r"^##\s*多选题$", stripped):
            current_section = "multi"
            continue
        if re.match(r"^##\s*(单选题|分析题)$", stripped):
            section_name = "单选题" if "单选" in stripped else "分析题"
            if section_name == expected_heading:
                if kept_expected_section:
                    current_section = "ignored"
                    continue
                current_section = "single" if section_name == "单选题" else "analysis"
                kept_expected_section = True
                allowed.append(f"## {expected_heading}")
            else:
                current_section = "ignored"
            continue
        if current_section in ("multi", "ignored"):
            continue
        if quiz_type == "single" and current_section != "single":
            continue
        if quiz_type == "analysis" and current_section != "analysis":
            continue
        if quiz_type == "analysis" and re.match(r"^题干：", stripped):
            allowed.append(stripped)
            continue
        if quiz_type == "single" and re.match(r"^(题干|考点)：", stripped):
            allowed.append(stripped)
            continue
        if quiz_type == "single" and re.match(r"^[A-D]\.\s+", stripped):
            allowed.append(stripped)
            continue

    result = "\n".join(allowed)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


def repair_quiz_missing_stem(text: str, quiz_type: str, topic: str) -> str:
    if not text:
        return text

    quiz_type = normalize_quiz_type(quiz_type)
    safe_topic = normalize_topic(topic)
    if has_meaningful_stem(text):
        return text

    if quiz_type == "analysis" and text.startswith("## 分析题"):
        text = re.sub(r"(?m)^题干：\s*$", "", text)
        return text.replace("## 分析题", f"## 分析题\n题干：请结合{safe_topic}进行分析。", 1)

    if quiz_type == "single" and text.startswith("## 单选题"):
        text = re.sub(r"(?m)^题干：\s*$", "", text)
        return text.replace("## 单选题", f"## 单选题\n题干：关于{safe_topic}，下列说法正确的是（ ）。", 1)

    return text


def has_meaningful_stem(text: str) -> bool:
    match = re.search(r"(?m)^题干：\s*(.+)$", text or "")
    if not match:
        return False
    stem = re.sub(r"\s+", "", match.group(1))
    return len(stem) >= 6 and stem not in {"……", "...", "。"}


def has_quiz_source_issue(text: str, quiz_type: str = "single") -> bool:
    if not text:
        return True

    quiz_type = normalize_quiz_type(quiz_type)
    expected_heading = "## 单选题" if quiz_type == "single" else "## 分析题"
    if text.count(expected_heading) != 1:
        return True

    if not has_meaningful_stem(text):
        return True

    if quiz_type == "analysis":
        if re.search(r"(?m)^(作答要求|考点|答案|正确答案|解析)：", text):
            return True
        if re.search(r"(?m)^##\s*(单选题|多选题)\s*$", text):
            return True
    else:
        if re.search(r"(?m)^##\s*(分析题|多选题)\s*$", text):
            return True
        if any(option not in text for option in ("A.", "B.", "C.", "D.")):
            return True
        if re.search(r"(?m)^[A-D]\.\s*(题干|考点|作答要求)[:：]?", text):
            return True

    return False


def is_bad_quiz_output(text: str, quiz_type: str = "single") -> bool:
    if not text:
        return True

    quiz_type = normalize_quiz_type(quiz_type)
    expected_heading = "## 单选题" if quiz_type == "single" else "## 分析题"
    if text.count(expected_heading) != 1:
        return True

    required = (
        ["## 单选题", "题干：", "A.", "B.", "C.", "D.", "考点："]
        if quiz_type == "single"
        else ["## 分析题", "题干："]
    )
    if any(item not in text for item in required):
        return True

    forbidden_sections = ["## 多选题"]
    if quiz_type == "single":
        forbidden_sections.append("## 分析题")
    else:
        forbidden_sections.append("## 单选题")

    if any(section in text for section in forbidden_sections):
        return True

    if not has_meaningful_stem(text):
        return True

    if quiz_type == "analysis" and re.search(r"(?m)^(作答要求|考点|答案|正确答案|解析)：", text):
        return True

    if quiz_type == "analysis" and re.search(r"(?m)^[A-D]\.\s+", text):
        return True

    if quiz_type == "single" and re.search(r"(?m)^[A-D]\.\s*(题干|考点|作答要求)[:：]?", text):
        return True

    if re.search(r"(答案|正确答案|排除项|答题框架|1\.1\.1|A\s*A|杂志社|请清理|请整理|重新整理|修复说明|原始代码|原始文本)", text):
        return True

    if re.search(r"(?m)^\s*E\s*[.．、:：]", text, flags=re.IGNORECASE):
        return True

    if re.search(r"([A-Za-z])\1{8,}|\b([A-Za-z])\b(?:\s+\2\b){8,}", text):
        return True

    weak_template_patterns = [
        "只需要背诵概念表述",
        "可以脱离教材语境",
        "应结合概念含义、理论依据和材料关键词",
        "与选择题和分析题的答题思路没有关系",
        "作答时",
        "作答方式",
        "答题方法",
        "抓住题干关键词",
        "只引用材料原句",
        "只写个人感受",
        "政治术语堆",
        "转化为答题要点",
    ]
    if any(pattern in text for pattern in weak_template_patterns):
        return True

    d_count = len(re.findall(r"(?<=[一-龥])D|D(?=[一-龥])|D{2,}", text))
    if d_count >= 3:
        return True

    return False





async def grader_agent(
    question: str,
    student_answer: str,
    endpoint=None,
    thinking: bool | None = None,
    allow_fallback: bool = True,
    question_type: str = "unknown",
) -> str:
    """endpoint / thinking 缺省走判卷专用组配置；评测脚本用它们逐个模型对比（并关掉降级）。
    question_type：single / multi / unknown，由拍题的人指定；不猜题号、不按试卷惯例推断。"""
    question_type = normalize_question_type(question_type)
    student_choices = extract_student_choices(student_answer)
    student_choice_line = (
        f"学生选项已由系统解析为：{student_choices}。批改时必须以这个选项为准，不要改成其他字母。"
        if student_choices
        else "学生答案不是选项字母，请按原文批改。"
    )
    question_type_line = QUESTION_TYPE_LINES[question_type]

    try:
        passages = await retrieve(question, top_k=3, min_score=0.5)
    except Exception:
        passages = []

    reference_block = ""
    if passages:
        reference_block = f"""
可参考的教材资料（判断时优先以此为依据）：
{build_context(passages, max_chars=1800)}
"""

    prompt = f"""
你是政治批改智能体。请先认真解题，再批改学生答案。

题目：
{question}

学生答案：
{student_answer}

{student_choice_line}
{question_type_line}
{reference_block}
输出下面三个部分：
## 判断
只写“正确”“基本正确”或“错误”。

## 正确答案及解析
选择题：先写正确选项字母和选项内容，再逐项说明为什么正确、其他选项错在哪里；如果学生选错，专门解释学生的选项为什么不对。
分析题：给出规范的答案框架和关键得分点，对照学生答案指出覆盖了哪些、缺了哪些。

## 易错提醒
1-2 条：这道题最容易掉的坑，以及同类题的识别方法。

判题规则：
1. 注意否定式题干（“不属于”“不正确”“错误的是”），先确认题目问的方向再判断。
2. 题型以上面“题型”一行为准：标明单选就只能有一个正确选项；标明多选就要给出全部正确选项，学生漏选、错选、多选都判“错误”。只有题型未指定时，如果你经过分析认为多个选项都成立，才在解析开头写出“此题疑为多选题，规范答案为 ×××”并说明依据，不要硬从几个都成立的选项里挑一个。
3. 涉及时政表述时，如果没有资料支撑且自己不确定，在解析中写明“此处表述以最新官方材料为准”，不要编造。
不要输出乱码、代码块、反斜杠、聊天角色标记或单独的选项字母。
"""
    answer = await call_qwen(
        prompt,
        system="你是政治批改老师。先独立解题再批改，判断必须准确、依据充分，解析要讲透。",
        max_tokens=1200,
        temperature=0.0,
        thinking=True,
        endpoint=endpoint or settings.grader_endpoint,  # 判卷走专用组（裁决型任务用纪律最好的模型）
        thinking_gate=settings.grader_thinking_enabled if thinking is None else thinking,
        allow_fallback=allow_fallback,
    )
    return clean_grade_output(answer)


QUESTION_TYPES = ("single", "multi", "unknown")
QUESTION_TYPE_LINES = {
    "single": "题型：单选题（由用户指定）。有且只有一个正确选项，不要按多选处理，不要写“疑为多选题”。",
    "multi": "题型：多选题（由用户指定）。正确答案可能是两个及以上选项，请给出全部正确选项；学生必须与全部正确选项完全一致才判“正确”，漏选、错选、多选一律判“错误”，并指出漏了或多了哪一项。",
    "unknown": "题型：未指定。请根据题干和选项自行判断是单选还是多选，并在解析里说明依据。",
}


def normalize_question_type(value: str | None) -> str:
    value = (value or "unknown").strip().lower()
    aliases = {"单选": "single", "单选题": "single", "多选": "multi", "多选题": "multi", "不确定": "unknown", "": "unknown"}
    value = aliases.get(value, value)
    return value if value in QUESTION_TYPES else "unknown"


def extract_student_choice(student_answer: str) -> str | None:
    """单个选项字母（兼容旧调用）；多选请用 extract_student_choices。"""
    normalized = (student_answer or "").strip().upper()
    normalized = normalized.replace("Ａ", "A").replace("Ｂ", "B").replace("Ｃ", "C").replace("Ｄ", "D")
    match = re.search(r"(?<![A-Z])([A-D])(?![A-Z])", normalized)
    return match.group(1) if match else None


def extract_student_choices(student_answer: str) -> str | None:
    """把“B”“ABD”“a、c”“B 和 D”解析成排好序的字母串；不是选项字母时返回 None。"""
    normalized = (student_answer or "").strip().upper()
    normalized = normalized.replace("Ａ", "A").replace("Ｂ", "B").replace("Ｃ", "C").replace("Ｄ", "D")
    stripped = re.sub(r"[\s、,，;；和与及/.．]+", "", normalized)
    if not stripped or not re.fullmatch(r"[A-D]{1,4}", stripped):
        return None
    return "".join(sorted(set(stripped)))


def clean_grade_output(text: str) -> str:
    if not text:
        return ""

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


async def review_planner_agent(mistakes_summary: str) -> str:
    topic = normalize_review_topic(mistakes_summary)
    prompt = f"""
你是政治抗遗忘复习规划智能体。

复习主题或错题摘要：
{mistakes_summary}

请围绕主题「{topic}」生成明天的复习计划。

必须按“通读→深挖→巩固”三阶推进：

## 通读
目标：……
任务：……
用时：……

## 深挖
目标：……
任务：……
用时：……

## 巩固
目标：……
任务：……
用时：……

## 背诵关键词
1. ……
2. ……
3. ……

要求：
1. 不要输出上午、下午、晚上。
2. 不要复述原始摘要，不要输出 user、assistant、system、聊天记录。
3. 每一阶段只写 2-3 条任务，必须具体可执行。
4. 总用时控制在 2-3 小时。
5. 背诵关键词只给 3 个，不要重复，不要长串堆砌。
6. 总字数控制在 350 字以内。
7. 不输出乱码、重复词、代码块或无关标题。
"""
    answer = await call_qwen(
        prompt,
        system="你是政治复习规划助手。必须按“通读、深挖、巩固、背诵关键词”四个标题输出，不要输出上午下午晚上，不要重复关键词。",
        max_tokens=650,
        temperature=0.1,
    )
    cleaned = clean_review_plan_output(answer)

    if is_bad_review_plan(cleaned):
        return build_fallback_review_plan(topic)

    return cleaned


def normalize_review_topic(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    text = re.sub(r"(user|assistant|system)\s*[:：]?", "", text, flags=re.IGNORECASE)
    return text[:30] or "政治薄弱知识点"


def clean_review_plan_output(text: str) -> str:
    if not text:
        return ""

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"(?m)^\s*(上午|下午|晚上)[：:].*$", "", text)
    text = re.sub(r"(?m)^\s*\d+\s*[-－]\s*助理\s*$", "", text)
    text = re.sub(r"(基础、决定、){3,}", "基础、决定、", text)
    text = re.sub(r"([一-龥]{2,6}[、，])\1{2,}", r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return trim_keyword_section(text.strip())


def trim_keyword_section(text: str) -> str:
    match = re.search(r"(##\s*背诵关键词\s*\n)([\s\S]*)$", text)
    if not match:
        return text

    prefix = text[: match.start(2)]
    keyword_block = match.group(2)
    keywords = []

    for raw_line in keyword_block.splitlines():
        line = re.sub(r"^\s*(?:[-*]|\d+[.、])\s*", "", raw_line).strip()
        if not line:
            continue
        for part in re.split(r"[、，,；;]\s*", line):
            part = part.strip(" 。；;，,")
            if part and part not in keywords:
                keywords.append(part)
            if len(keywords) >= 3:
                break
        if len(keywords) >= 3:
            break

    if not keywords:
        return text

    return prefix + "\n".join(f"{index}. {keyword}" for index, keyword in enumerate(keywords, start=1))


def is_bad_review_plan(text: str) -> bool:
    required = ["## 通读", "## 深挖", "## 巩固", "## 背诵关键词"]
    if any(item not in text for item in required):
        return True

    if any(time_word in text for time_word in ("上午：", "下午：", "晚上：", "上午:", "下午:", "晚上:")):
        return True

    if len(re.findall(r"基础|决定|关键词", text)) > 15:
        return True

    return False


def build_fallback_review_plan(topic: str) -> str:
    return f"""## 通读
目标：先把「{topic}」的基本框架过一遍，建立整体印象。
任务：阅读教材或笔记中的定义、背景和核心结论；整理 3 个最重要的概念。
用时：40 分钟。

## 深挖
目标：把容易混淆的关系讲清楚。
任务：围绕「是什么、为什么、怎么考」各写 2 句话；补做 2 道相关选择题或材料题。
用时：60 分钟。

## 巩固
目标：把知识转成可背、可答的表达。
任务：合上资料复述一遍核心内容；把错因和易混点写进错题本。
用时：40 分钟。

## 背诵关键词
1. {topic}
2. 核心原理
3. 易混点"""
