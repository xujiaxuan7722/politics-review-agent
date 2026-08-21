import re

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.routers.auth import get_current_user
from app.routers.records import add_record
from app.services.llm_service import call_qwen, clean_ai_output_noise, is_dirty_qwen_output
from app.services.mistake_agent import extract_politics_keywords
from app.services.ocr_service import baidu_ocr_image
from app.services.text_clean_service import clean_politics_ocr_text

router = APIRouter(
    prefix="/api/ocr",
    tags=["ocr"],
    dependencies=[Depends(get_current_user)],
)

MAX_IMAGE_BYTES = 5 * 1024 * 1024


class OcrTextSolveRequest(BaseModel):
    raw_text: str = Field(min_length=1)
    title: str | None = None


async def read_image(file: UploadFile) -> bytes:
    if file.content_type and not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="只支持上传图片文件")

    content = await file.read()
    if len(content) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="图片超过 5MB，请压缩后重试")
    if not content:
        raise HTTPException(status_code=400, detail="上传的文件为空")

    return content


def external_error(stage: str, error: Exception) -> HTTPException:
    if isinstance(error, httpx.TimeoutException):
        detail = f"{stage}超时：外部接口响应过慢，请稍后重试，或换一张更清晰、更小的图片。"
    elif isinstance(error, httpx.ReadError):
        detail = f"{stage}连接中断：请检查网络、API 额度或稍后重试。"
    elif isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        body = error.response.text[:300]
        detail = f"{stage}返回 HTTP {status}：{body}"
    elif isinstance(error, httpx.HTTPError):
        detail = f"{stage}网络错误：{error}"
    else:
        detail = f"{stage}失败：{error}"

    return HTTPException(status_code=502, detail=detail)


async def solve_politics_problem(clean_text: str) -> str:
    question_type_rule = build_ocr_question_type_rule(clean_text)
    prompt = f"""
请直接解析并解答下面的考研政治题目。

要求：
1. 总字数控制在 350 字以内，回答要短、准、适合背诵。
2. 只允许输出三个标题：答案、解析、关键词。
3. {question_type_rule}
4. 分析题：答案写 2-4 条规范要点；解析说明材料关键词如何对应教材知识。
5. OCR 文本不清时，在解析开头写“识别不清处：……”，不要强行编造。
6. 不输出乱码、重复字母、重复句子、代码块、反斜杠、HTML、JSON、引用编号或聊天角色标记。
7. 禁止输出连续重复的“于于于”“可以可以可以”“D D D”这类污染文本。

固定格式：
## 答案
……

## 解析
……

## 关键词
……

题目文本：
{clean_text}
"""

    answer = await call_qwen(
        prompt,
        system="你是一名考研政治老师。只按“答案、解析、关键词”三段输出，禁止输出乱码、重复字母、无关标题。",
        max_tokens=700,
        temperature=0.1,
    )
    cleaned = clean_politics_solution_output(answer)

    if is_bad_politics_solution(cleaned):
        repair_prompt = f"""
请重新解答下面题目。上一次回答出现重复、乱码或格式错误。

硬性要求：
1. 只输出“答案、解析、关键词”三个标题。
2. 选择题的答案只写一个选项字母和对应选项内容。
3. 解析只写 2-3 句，不重复，不输出 D/E 污染字符。
4. 不输出代码、JSON、引用编号、过程说明。

题目文本：
{clean_text}
"""
        answer = await call_qwen(
            repair_prompt,
            system="你是一名考研政治老师。重新输出干净答案，只保留答案、解析、关键词三段。",
            max_tokens=500,
            temperature=0.05,
        )
        cleaned = clean_politics_solution_output(answer)

    return cleaned


def build_ocr_question_type_rule(clean_text: str) -> str:
    if re.search(r"(?m)^\s*[A-D]\s*[.．、:：]", clean_text or ""):
        return "选择题：答案只写一个正确选项字母和完整选项内容；解析只写 2-3 句话，不逐项长篇排除。"
    return "非选择题：答案写规范要点，解析说明题干材料如何对应教材知识。"


def clean_politics_solution_output(text: str) -> str:
    if not text:
        return ""

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = clean_ai_output_noise(text)
    text = re.sub(r"\[ID:\s*\d+\]", "", text)
    text = re.sub(r"\[\d+\]", "", text)
    text = re.sub(r"\*{3,}", "", text)
    text = re.sub(r"(?m)^\s*#+\s*(排除理由|分题框架|易错提醒|依据)\s*$", "", text)

    cleaned_lines = []
    allowed_headings = {"答案", "解析", "关键词"}
    current_heading = ""

    for line in text.splitlines():
        normalized = line.strip()
        if not normalized:
            cleaned_lines.append("")
            continue

        heading_match = re.match(r"^#{1,3}\s*(.+?)\s*$", normalized)
        if heading_match:
            heading = heading_match.group(1).strip()
            if heading not in allowed_headings:
                continue
            current_heading = heading
            cleaned_lines.append(f"## {heading}")
            continue

        if is_noise_line(normalized, current_heading):
            continue

        normalized = clean_stray_choice_letters(normalized)
        normalized = clean_ai_output_noise(normalized)
        normalized = re.sub(r"([一-龥])\1{3,}", r"\1", normalized)
        for phrase in ("选项", "正确", "符合", "错误", "影响", "说明", "观点", "答案"):
            normalized = re.sub(f"(?:{phrase}){{2,}}", phrase, normalized)
        normalized = re.sub(r"\s{2,}", " ", normalized)
        cleaned_lines.append(normalized)

    cleaned = "\n".join(cleaned_lines)
    cleaned = clean_ai_output_noise(cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned


def is_bad_politics_solution(text: str) -> bool:
    if not text:
        return True

    required = ("## 答案", "## 解析", "## 关键词")
    if any(item not in text for item in required):
        return True

    if is_dirty_qwen_output(text):
        return True

    if re.search(r"(请清理|请整理|原始文本|修复说明|下面是整理)", text):
        return True

    if len(re.findall(r"(?m)^\s*[A-D]\s*$", text)) > 1:
        return True

    return False


def is_noise_line(line: str, current_heading: str = "") -> bool:
    if current_heading != "答案" and re.fullmatch(r"[A-D]{1,8}", line):
        return True
    if re.search(r"[A-D]{3,}", line):
        return True
    if re.search(r"(易){3,}|([A-D])\1{2,}", line):
        return True
    return False


def clean_stray_choice_letters(line: str) -> str:
    if re.match(r"^[A-D]\s*[.．、:：]", line):
        return line

    line = re.sub(r"^[A-D]{1,3}(?=[一-龥])", "", line)
    line = re.sub(r"(?<=[一-龥，。；、])\s*[A-D]\s+(?=[一-龥])", "", line)
    line = re.sub(r"(?<=[一-龥，。；、])[A-D](?=[一-龥，。；、])", "", line)
    line = re.sub(r"([A-D]){3,}", "", line)
    return line


@router.post("/recognize")
async def recognize_image(file: UploadFile = File(...)):
    content = await read_image(file)
    try:
        text = await baidu_ocr_image(content)
    except Exception as error:
        raise external_error("百度 OCR 识别", error) from error
    return {
        "filename": file.filename,
        "text": text,
    }


@router.post("/recognize-clean")
async def recognize_and_clean(file: UploadFile = File(...)):
    content = await read_image(file)
    try:
        raw_text = await baidu_ocr_image(content)
    except Exception as error:
        raise external_error("百度 OCR 识别", error) from error

    try:
        clean_text = await clean_politics_ocr_text(raw_text)
    except Exception as error:
        raise external_error("千问题目整理", error) from error

    keywords = extract_politics_keywords(clean_text)

    return {
        "filename": file.filename,
        "raw_text": raw_text,
        "clean_text": clean_text,
        "keywords": keywords,
    }


@router.post("/solve")
async def recognize_clean_and_solve(file: UploadFile = File(...)):
    content = await read_image(file)
    try:
        raw_text = await baidu_ocr_image(content)
    except Exception as error:
        raise external_error("百度 OCR 识别", error) from error

    try:
        clean_text = await clean_politics_ocr_text(raw_text)
    except Exception as error:
        raise external_error("千问题目整理", error) from error

    try:
        answer = await solve_politics_problem(clean_text)
    except Exception as error:
        raise external_error("千问 AI 解答", error) from error

    keywords = extract_politics_keywords(f"{clean_text}\n{answer}")

    return {
        "filename": file.filename,
        "raw_text": raw_text,
        "clean_text": clean_text,
        "answer": answer,
        "keywords": keywords,
        "source": "ocr",
    }


@router.post("/solve-raw-text")
async def clean_and_solve_ocr_text(
    req: OcrTextSolveRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        clean_text = await clean_politics_ocr_text(req.raw_text)
    except Exception as error:
        raise external_error("千问题目整理", error) from error

    try:
        answer = await solve_politics_problem(clean_text)
    except Exception as error:
        raise external_error("千问 AI 解答", error) from error

    keywords = extract_politics_keywords(f"{clean_text}\n{answer}")

    add_record(
        db,
        current_user.id,
        source="ocr",
        title=(req.title or "").strip() or re.sub(r"\s+", " ", req.raw_text).strip()[:20],
        question=req.raw_text,
        content=answer,
    )

    return {
        "filename": req.title,
        "raw_text": req.raw_text,
        "clean_text": clean_text,
        "answer": answer,
        "keywords": keywords,
        "source": "ocr",
    }
