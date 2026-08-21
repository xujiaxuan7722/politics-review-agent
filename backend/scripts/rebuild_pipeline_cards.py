"""把拍题判卷来源的旧错题 / 复习卡对齐到新规则：

- 来源标记为 pipeline（迁移自旧库的数据默认成了 manual）；
- 标题若是图片文件名之类的短名，改为题干前 20 字；
- 关联复习卡的卡面改为完整原题，背面改为“正确答案及解析”。

用法（backend 目录下）：.venv/bin/python scripts/rebuild_pipeline_cards.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal  # noqa: E402
from app.models import Mistake, ReviewCard  # noqa: E402
from app.routers.review import build_card_from_exam_question, stem_title  # noqa: E402


def looks_like_pipeline(mistake: Mistake) -> bool:
    return mistake.source == "pipeline" or "我的答案：" in (mistake.raw_text or "")


def main():
    db = SessionLocal()
    try:
        mistakes = db.query(Mistake).all()
        touched = 0
        for mistake in mistakes:
            if not looks_like_pipeline(mistake):
                continue

            mistake.source = "pipeline"
            if len((mistake.title or "").strip()) < 6:
                mistake.title = stem_title(mistake.clean_text or mistake.raw_text)

            if mistake.student_answer is None and "我的答案：" in (mistake.raw_text or ""):
                mistake.student_answer = mistake.raw_text.split("我的答案：", 1)[1].strip()[:200]

            card_data = build_card_from_exam_question(mistake)
            cards = db.query(ReviewCard).filter(ReviewCard.mistake_id == mistake.id).all()
            for card in cards:
                card.question = card_data["question"]
                card.answer = card_data["answer"]
                card.knowledge_point = card_data["knowledge_point"] or card.knowledge_point
            touched += 1
            db.commit()
            print(f"错题 #{mistake.id} → 标题「{mistake.title}」，重建 {len(cards)} 张卡")

        print(f"完成：处理 {touched} 道拍题来源错题")
    finally:
        db.close()


if __name__ == "__main__":
    main()
