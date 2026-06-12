from datetime import datetime
from sqlalchemy import String, DateTime, Integer, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base

class RenderedArtifact(Base):
    __tablename__ = "rendered_artifacts"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False)
    client_id: Mapped[int] = mapped_column(Integer, ForeignKey("clients.id"), nullable=False)
    created_by_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)

    artifact_type: Mapped[str] = mapped_column(String(60), default="LETTER")  # LETTER / NEXUS / EMAIL / FORM
    title: Mapped[str] = mapped_column(String(255), default="")
    web_copy: Mapped[str] = mapped_column(Text, default="")
    docx_path: Mapped[str] = mapped_column(String(600), default="")
    pdf_path: Mapped[str] = mapped_column(String(600), default="")
    meta_json: Mapped[str] = mapped_column(Text, default="")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
