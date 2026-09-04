"""判卷评测：题集读取、判卷输出解析、逐题打分与汇总。

评测集：evals/grading/questions.jsonl，每行 {id, module, question, answer, student_answer}。
指标：
- judgement_acc：判卷"正确/错误"的结论是否与标准一致（学生选项 == 标准答案 ⇔ 正确）
- answer_acc：判卷给出的正确答案是否与标准答案一致（按字母集合比较）
- both_acc：两者同时正确
"""

import json
import re
from pathlib import Path

LETTERS = "ABCD"


def load_questions(path: str | Path) -> list[dict]:
    items = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        item = json.loads(line)
        item["answer"] = normalize_letters(item["answer"])
        item["student_answer"] = normalize_letters(item["student_answer"])
        items.append(item)
    return items


def normalize_letters(text: str) -> str:
    text = (text or "").upper().replace("Ａ", "A").replace("Ｂ", "B").replace("Ｃ", "C").replace("Ｄ", "D")
    return "".join(sorted(set(ch for ch in text if ch in LETTERS)))


def expected_judgement(item: dict) -> str:
    return "正确" if item["student_answer"] == item["answer"] else "错误"


_JUDGE_SECTION = re.compile(r"##\s*判断\s*\n(.*?)(?:\n##|\Z)", re.S)


def parse_judgement(text: str) -> str | None:
    """从判卷输出里抽"正确/错误"。找不到 ## 判断 段时退回全文首段。"""
    m = _JUDGE_SECTION.search(text or "")
    head = (m.group(1) if m else (text or "")[:120]).strip()
    head = head.splitlines()[0] if head else ""
    if not head:
        return None
    if re.search(r"错误|不正确|不对|漏选|不完整|部分正确|错选|多选了|少选", head):
        return "错误"
    if "正确" in head or "对" in head:
        return "正确"
    return None


_LETTER = r"[A-DＡ-Ｄ]"
# 标记后面紧跟的选项字母列表："B""BC""B、C""B 和 C""A/B"；只取紧跟的这一段，不扫全行
_LEADING_LIST = re.compile(rf"\s*({_LETTER}{{1,4}}(?:\s*[、,，和与及/]\s*{_LETTER}{{1,4}})*)(?![A-Za-z])")
# 枚举写法："A. 绝对剩余价值生产；B. 相对剩余价值生产"
_ENUM_ITEM = re.compile(rf"(?<![A-Za-z])({_LETTER})[.．、]\s*\S")
_ENUM_SEP = re.compile(rf"[;；]\s*{_LETTER}[.．、]")
_MARKER = re.compile(
    r"(?P<norm>规范答案(?:为|是|应为|应该是)?)|"
    r"(?P<m>选择题[：:]\s*(?:正确(?:选项|答案)(?:是|为)?)?|正确答案(?:为|是|应为|应该是|应选|选)?|正确选项(?:为|是|应为|应该是|应选)?|(?<![正规])答案(?:为|是|应为|应该是|应选)|应选)"
    r"[：:\s]*"
)
_ANSWER_SECTION = re.compile(r"##\s*正确答案[^\n]*\n(.*?)(?=\n##|\Z)", re.S)


def _letters_after(rest: str) -> str | None:
    m = _LEADING_LIST.match(rest)
    if not m:
        return None
    letters = normalize_letters(m.group(1))
    line = rest.split("\n", 1)[0]
    if len(letters) == 1 and _ENUM_SEP.search(line):
        letters = normalize_letters("".join(_ENUM_ITEM.findall(line)))
    return letters or None


def parse_answer(text: str) -> str | None:
    """从判卷输出里抽模型认定的正确答案（字母集合）。

    优先级："规范答案为 X"（多选提示，全文任意位置）> ## 正确答案及解析 段里按出现顺序第一个
    "正确答案 / 正确选项 / 选择题：/ 答案是 / 应选"标记 > 全文第一个标记。
    每个标记只取紧跟其后的字母列表，兼容"B、C""B 和 C""A. xxx；B. xxx"；不扫整行，避免把
    "学生选了 A"这类字母抓进来。
    """
    text = text or ""
    for m in _MARKER.finditer(text):
        if m.group("norm"):
            letters = _letters_after(text[m.end():])
            if letters:
                return letters
    sec = _ANSWER_SECTION.search(text)
    scopes = [sec.group(1)] if sec else []
    scopes.append(text)
    for scope in scopes:
        for m in _MARKER.finditer(scope):
            if m.group("m"):
                letters = _letters_after(scope[m.end():])
                if letters:
                    return letters
    if sec:  # 段首行兜底："B. xxx"
        first = sec.group(1).strip().split("\n", 1)[0]
        letters = _letters_after(first)
        if letters:
            return letters
    return None


def score_item(item: dict, output: str) -> dict:
    judged = parse_judgement(output)
    answered = parse_answer(output)
    exp_j = expected_judgement(item)
    j_ok = judged == exp_j
    a_ok = answered == item["answer"]
    return {
        "id": item["id"],
        "module": item["module"],
        "expected_judgement": exp_j,
        "judged": judged,
        "judgement_ok": j_ok,
        "expected_answer": item["answer"],
        "answered": answered,
        "answer_ok": a_ok,
        "both_ok": j_ok and a_ok,
    }


def summarize(rows: list[dict]) -> dict:
    n = len(rows)
    if n == 0:
        return {"n": 0}
    ok = [r for r in rows if not r.get("error")]
    # 准确率只在"成功拿到判卷结果"的题上算；失败题单独计数（coverage），不混进分母
    def rate(key: str) -> float | None:
        return round(100.0 * sum(1 for r in ok if r.get(key)) / len(ok), 1) if ok else None
    by_module: dict[str, dict] = {}
    for r in rows:
        m = by_module.setdefault(r["module"], {"n": 0, "judgement_ok": 0, "answer_ok": 0, "both_ok": 0})
        m["n"] += 1
        for k in ("judgement_ok", "answer_ok", "both_ok"):
            m[k] += 1 if r.get(k) else 0
    latencies = sorted(r["latency_s"] for r in ok if "latency_s" in r)
    return {
        "n": n,
        "graded": len(ok),
        "errors": n - len(ok),
        "judgement_acc": rate("judgement_ok"),
        "answer_acc": rate("answer_ok"),
        "both_acc": rate("both_ok"),
        "unparsed_judgement": sum(1 for r in ok if r.get("judged") is None),
        "unparsed_answer": sum(1 for r in ok if r.get("answered") is None),
        "latency_p50_s": round(latencies[len(latencies) // 2], 1) if latencies else None,
        "latency_max_s": round(latencies[-1], 1) if latencies else None,
        "by_module": by_module,
    }
