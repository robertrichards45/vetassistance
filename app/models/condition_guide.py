from datetime import datetime
from sqlalchemy import String, DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base


class ConditionGuide(Base):
    """AI-written staff guide for one diagnostic code, generated once and shared by the team."""
    __tablename__ = "condition_guides"
    id: Mapped[int] = mapped_column(primary_key=True)
    diagnostic_code: Mapped[str] = mapped_column(String(8), unique=True, index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="QUEUED")  # QUEUED | RUNNING | READY | FAILED
    content_json: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(80), default="")
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
