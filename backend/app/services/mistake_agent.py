import re

from app.services.llm_service import call_qwen


POLITICS_KEYWORD_BANK = [
    "马克思主义",
    "辩证唯物主义",
    "历史唯物主义",
    "实践",
    "矛盾",
    "认识论",
    "毛中特",
    "新民主主义革命",
    "社会主义改造",
    "改革开放",
    "中国式现代化",
    "习近平新时代中国特色社会主义思想",
    "史纲",
    "近代史",
    "辛亥革命",
    "五四运动",
    "抗日战争",
    "思修法基",
    "理想信念",
    "法治",
    "道德",
    "形势与政策",
    "时政",
    "单选题",
    "多选题",
    "分析题",
    "材料题",
]


def extract_politics_keywords(text: str, limit: int = 8) -> list[str]:
    found = []
    normalized = text or ""

    for keyword in POLITICS_KEYWORD_BANK:
      if keyword in normalized and keyword not in found:
          found.append(keyword)

    if "A." in normalized or "B." in normalized or re.search(r"[A-D][．.、]", normalized):
        if "选择题" not in found:
            found.append("选择题")

    if "材料" in normalized and "材料题" not in found:
        found.append("材料题")

    return found[:limit]


async def analyze_mistake(clean_text: str) -> str:
    prompt = f"""
请分析下面这道政治错题。

输出结构：
## 题目类型
判断是单选题 / 多选题 / 分析题 / 材料题。

## 关键词
列出 3-6 个适合错题索引的关键词。

## 模块定位
判断属于马克思主义基本原理 / 毛中特 / 史纲 / 思修法基 / 形势与政策。

## 正确思路
讲透题干关键词、选项依据或分析题框架，说明为什么对、为什么错，让考生下次遇到同类题能自己做对。

## 易错原因
2-3 条，指出这道题设置的干扰点和考生常见的错误理解。

## 举一反三
1-2 条：这个考点还会怎么变形出题，如何识别。

## 背诵提示
给出一句适合复习时背诵的提示。

不要输出乱码、代码块、反斜杠或聊天角色标记。

题目：
{clean_text}
"""

    return await call_qwen(
        prompt,
        system="你是一名政治错题分析老师。先独立解题再分析，解析要讲透，不编造时政事实。",
        max_tokens=1300,
        thinking=True,
    )
