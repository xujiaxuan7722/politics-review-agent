from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import SearchRecord, User
from app.routers.auth import get_current_user

router = APIRouter(prefix="/api/records", tags=["records"])

SOURCE_LABELS = {"chat": "智能问答", "ocr": "拍题判卷", "agents": "练习与规划"}


def add_record(
    db: Session,
    user_id: int,
    source: str,
    title: str,
    question: str | None,
    content: str | None,
) -> None:
    db.add(
        SearchRecord(
            user_id=user_id,
            source=source,
            title=(title or "未命名记录")[:200],
            question=question,
            content=content,
        )
    )
    db.commit()


@router.get("")
def list_records(
    q: str = "",
    source: str = "all",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = db.query(SearchRecord).filter(SearchRecord.user_id == current_user.id)

    if source in SOURCE_LABELS:
        query = query.filter(SearchRecord.source == source)

    keyword = q.strip()
    if keyword:
        pattern = f"%{keyword}%"
        query = query.filter(
            or_(
                SearchRecord.title.like(pattern),
                SearchRecord.question.like(pattern),
                SearchRecord.content.like(pattern),
            )
        )

    records = query.order_by(SearchRecord.id.desc()).limit(200).all()
    return [
        {
            "id": r.id,
            "source": r.source,
            "source_label": SOURCE_LABELS.get(r.source, "其他"),
            "title": r.title,
            "question": r.question,
            "content": r.content,
            "created_at": r.created_at,
        }
        for r in records
    ]


@router.delete("/{record_id}")
def delete_record(
    record_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    record = (
        db.query(SearchRecord)
        .filter(SearchRecord.id == record_id, SearchRecord.user_id == current_user.id)
        .first()
    )
    if not record:
        raise HTTPException(status_code=404, detail="记录不存在")

    db.delete(record)
    db.commit()
    return {"deleted": True, "id": record_id}


@router.delete("")
def delete_all_records(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    deleted = (
        db.query(SearchRecord)
        .filter(SearchRecord.user_id == current_user.id)
        .delete(synchronize_session=False)
    )
    db.commit()
    return {"deleted": deleted}
