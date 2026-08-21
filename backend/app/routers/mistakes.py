from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Mistake, ReviewCard, User
from app.routers.auth import get_current_user
from app.services.mistake_agent import analyze_mistake, extract_politics_keywords

router = APIRouter(prefix="/api/mistakes", tags=["mistakes"])


class MistakeCreate(BaseModel):
    title: str
    raw_text: str | None = None
    clean_text: str
    knowledge_points: str | None = None
    source: str = "manual"


@router.post("")
async def create_mistake(
    req: MistakeCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    analysis = await analyze_mistake(req.clean_text)
    keywords = req.knowledge_points
    if not keywords:
        keywords = "、".join(extract_politics_keywords(f"{req.title}\n{req.clean_text}\n{analysis}"))

    mistake = Mistake(
        user_id=current_user.id,
        title=req.title,
        raw_text=req.raw_text,
        clean_text=req.clean_text,
        analysis=analysis,
        knowledge_points=keywords,
        source=req.source if req.source in ("manual", "chat", "agents", "pipeline") else "manual",
    )

    db.add(mistake)
    db.commit()
    db.refresh(mistake)

    return {
        "id": mistake.id,
        "title": mistake.title,
        "analysis": mistake.analysis,
    }


@router.get("")
def list_mistakes(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return (
        db.query(Mistake)
        .filter(Mistake.user_id == current_user.id)
        .order_by(Mistake.id.desc())
        .all()
    )


@router.get("/{mistake_id}")
def get_mistake(
    mistake_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    mistake = (
        db.query(Mistake)
        .filter(Mistake.id == mistake_id, Mistake.user_id == current_user.id)
        .first()
    )

    if not mistake:
        raise HTTPException(status_code=404, detail="错题不存在")

    review_cards = (
        db.query(ReviewCard)
        .filter(ReviewCard.mistake_id == mistake_id, ReviewCard.user_id == current_user.id)
        .order_by(ReviewCard.id.desc())
        .all()
    )

    return {
        "mistake": mistake,
        "review_cards": review_cards,
    }


@router.delete("/{mistake_id}")
def delete_mistake(
    mistake_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    mistake = (
        db.query(Mistake)
        .filter(Mistake.id == mistake_id, Mistake.user_id == current_user.id)
        .first()
    )

    if not mistake:
        raise HTTPException(status_code=404, detail="错题不存在")

    db.query(ReviewCard).filter(
        ReviewCard.mistake_id == mistake_id,
        ReviewCard.user_id == current_user.id,
    ).delete(synchronize_session=False)
    db.delete(mistake)
    db.commit()

    return {"deleted": True, "id": mistake_id}


@router.delete("")
def delete_all_mistakes(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    mistake_ids = [
        row.id
        for row in db.query(Mistake.id)
        .filter(Mistake.user_id == current_user.id)
        .all()
    ]

    if mistake_ids:
        db.query(ReviewCard).filter(
            ReviewCard.user_id == current_user.id,
            ReviewCard.mistake_id.in_(mistake_ids),
        ).delete(synchronize_session=False)
        deleted_count = (
            db.query(Mistake)
            .filter(Mistake.user_id == current_user.id)
            .delete(synchronize_session=False)
        )
    else:
        deleted_count = 0

    db.commit()

    return {"deleted": deleted_count}
