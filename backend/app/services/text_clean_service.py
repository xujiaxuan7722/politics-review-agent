from app.services.llm_service import call_qwen


POLITICS_OCR_CLEAN_RULES = """
输出规则：
1. 只整理 OCR 文本，不重新解题，不编造材料。
2. 保留题号、材料段落、A/B/C/D 选项、(1)(2) 小问结构。
3. 识别不清的内容写成「[识别不清：原片段...]」，不要强行猜。
4. 如果出现年份、会议、政策表述、专有名词不清，放入「需要人工核对」。
5. 不输出代码块、HTML、JSON、反斜杠或聊天角色标记。
6. 固定结构如下：

## 题目

## 材料

## 选项或小问

## 关键词
- ...

## OCR 识别到的原答案或痕迹

## 需要人工核对
- ...
"""


async def clean_politics_ocr_text(raw_text: str) -> str:
    prompt = f"""
请把下面的考研政治 OCR 原文整理成适合复习的题目文本。

{POLITICS_OCR_CLEAN_RULES}

OCR 原文：
{raw_text}
"""

    return await call_qwen(
        prompt,
        system="你是谨慎的考研政治 OCR 整理助手。你只做文本整理和结构化，不重新解题，不臆造时政事实。",
        max_tokens=700,
    )

