from datetime import datetime
from sqlalchemy import String, DateTime, Integer, ForeignKey, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base

class Document(Base):
    __tablename__ = "documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False)
    client_id: Mapped[int] = mapped_column(Integer, ForeignKey("clients.id"), nullable=False)

    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(120), default="")
    storage_path: Mapped[str] = mapped_column(String(500), nullable=False)

    category: Mapped[str] = mapped_column(String(80), default="Uncategorized")
    tags: Mapped[str] = mapped_column(String(400), default="")

    extracted_text_path: Mapped[str] = mapped_column(String(500), default="")
    status: Mapped[str] = mapped_column(String(40), default="READY")
    error: Mapped[str] = mapped_column(Text, default="")

    uploaded_by_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    is_client_visible: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
