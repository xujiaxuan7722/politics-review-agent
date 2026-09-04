from app.services.llm_service import call_qwen
from app.services.prompt_rules import POLITICS_OUTPUT_RULES


async def generate_review_cards(mistake_text: str, analysis: str) -> str:
    prompt = f"""
请基于下面的政治错题和分析，生成 1 张复习卡片，内容精简。

只输出三行，每行一个标签，「」内替换成你写的实际内容，不要保留「」符号，不要用 JSON、markdown 或标题：

Q: 「针对这道题核心考点提出的一个复习问题，要具体到知识点本身，不能是泛泛的"这道题考什么"」
A: 「这个问题的标准答案：先给可背诵的关键结论，再用一句话解释为什么」
K: 「该考点的名称，如"新民主主义革命的领导力量"」

硬性要求：
1. Q、A、K 三个标签必须保留，每个标签后都要有实际内容。
2. 不要输出"解析如下""好的""已整理"等说明文字，不要把上面的格式说明原样抄进去。
3. 不要重复句子，不要输出乱码或无意义字符。

{POLITICS_OUTPUT_RULES}

错题：
{mistake_text}

分析：
{analysis}
"""
    return await call_qwen(
        prompt,
        system="你是一个政治抗遗忘复习卡片生成助手，严格按照 Q/A/K 格式输出，不要有多余内容。",
        max_tokens=350,
    )
