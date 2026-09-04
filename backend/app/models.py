"""数据模型。

设计要点：
- 所有业务表通过外键关联 users，并在数据库层级联删除（删用户即清空其全部数据）。
- 复习卡可以不挂错题（学习管家直接创建的卡），所以 mistake_id 可空；挂了错题的卡随错题级联删除。
- review_logs 记录每一次打分，供学习曲线 / 掌握度分析使用（SM-2 状态只保存在 review_cards 上）。
- 高频查询路径建复合索引：今日待复习 (user_id, next_review_date)、记录列表 (user_id, created_at)。
- conversations / conversation_messages 是学习管家的多轮对话记忆：只存用户说的话和最终答复，
  工具调用的中间消息不存（步骤轨迹以 JSON 存在答复消息的 steps_json 里供回看）。
"""

from datetime import date, datetime

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String(80), unique=True, index=True, nullable=False)
    password_hash = Column(String(256), nullable=False)
    token = Column(String(64), unique=True, index=True, nullable=True)
    token_expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class Mistake(Base):
    __tablename__ = "mistakes"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    title = Column(String(200), nullable=False)
    raw_text = Column(Text, nullable=True)
    clean_text = Column(Text, nullable=True)
    analysis = Column(Text, nullable=True)
    knowledge_points = Column(String(500), nullable=True)
    difficulty = Column(Integer, default=3, nullable=False)
    # 错题来源：manual 手动录入 / pipeline 拍题判卷 / chat 问答保存 / agents 练习保存
    source = Column(String(20), default="manual", nullable=False)
    student_answer = Column(Text, nullable=True)
    judgement = Column(String(20), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (Index("ix_mistakes_user_created", "user_id", "created_at"),)


class ReviewCard(Base):
    __tablename__ = "review_cards"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    mistake_id = Column(Integer, ForeignKey("mistakes.id", ondelete="CASCADE"), index=True, nullable=True)
    question = Column(Text, nullable=False)
    answer = Column(Text, nullable=False)
    knowledge_point = Column(String(100), nullable=True)

    # SM-2 状态
    ease_factor = Column(Float, default=2.5, nullable=False)
    interval_days = Column(Integer, default=1, nullable=False)
    repetition = Column(Integer, default=0, nullable=False)
    lapses = Column(Integer, default=0, nullable=False)  # 累计"忘了"的次数
    next_review_date = Column(Date, default=date.today, nullable=False)
    last_reviewed_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (Index("ix_review_cards_user_due", "user_id", "next_review_date"),)


class ReviewLog(Base):
    __tablename__ = "review_logs"

    id = Column(Integer, primary_key=True)
    card_id = Column(Integer, ForeignKey("review_cards.id", ondelete="CASCADE"), index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    quality = Column(Integer, nullable=False)
    interval_before = Column(Integer, nullable=False)
    interval_after = Column(Integer, nullable=False)
    ease_after = Column(Float, nullable=False)
    reviewed_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class SearchRecord(Base):
    __tablename__ = "search_records"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    source = Column(String(20), nullable=False)  # chat / ocr / agents
    title = Column(String(200), nullable=False)
    question = Column(Text, nullable=True)
    content = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (Index("ix_search_records_user_created", "user_id", "created_at"),)


class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    title = Column(String(120), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (Index("ix_conversations_user_updated", "user_id", "updated_at"),)


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"

    id = Column(Integer, primary_key=True)
    conversation_id = Column(Integer, ForeignKey("conversations.id", ondelete="CASCADE"), index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    role = Column(String(16), nullable=False)  # user / assistant
    content = Column(Text, nullable=False)
    steps_json = Column(Text, nullable=True)  # 助手消息附带的工具调用轨迹
    model = Column(String(80), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (Index("ix_conv_messages_conv_id_id", "conversation_id", "id"),)
