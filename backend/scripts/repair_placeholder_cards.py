"""修复旧数据：把问题是模板占位符（如"复习问题"）的复习卡，按关联错题重新生成。

用法（backend 目录下）：
    .venv/bin/python scripts/repair_placeholder_cards.py
"""

import asyncio
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal  # noqa: E402
from app.models import Mistake, ReviewCard  # noqa: E402
from app.routers.review import (  # noqa: E402
    build_review_card_from_mistake,
    is_placeholder_card,
    parse_cards_text,
)
from app.services.card_agent import generate_review_cards  # noqa: E402


async def main():
    db = SessionLocal()
    try:
        cards = db.query(ReviewCard).all()
        bad = [c for c in cards if is_placeholder_card(c.question or "", c.answer or "")]
        print(f"共 {len(cards)} 张卡，需修复 {len(bad)} 张")

        for card in bad:
            mistake = db.get(Mistake, card.mistake_id) if card.mistake_id else None
            if not mistake:
                print(f"卡 #{card.id} 没有关联错题，删除")
                db.delete(card)
                db.commit()
                continue

            try:
                text = await generate_review_cards(mistake.clean_text or "", mistake.analysis or "")
                parsed = parse_cards_text(text)
            except Exception as error:
                print(f"卡 #{card.id} 调模型失败：{error}，改用规则生成")
                parsed = []

            if not parsed:
                parsed = [build_review_card_from_mistake(mistake)]

            new = parsed[0]
            card.question = new["question"]
            card.answer = new["answer"]
            card.knowledge_point = new.get("knowledge_point") or card.knowledge_point
            card.next_review_date = min(card.next_review_date or date.today(), date.today())
            db.commit()
            print(f"卡 #{card.id} 已重生成：{card.question[:50]}")
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
