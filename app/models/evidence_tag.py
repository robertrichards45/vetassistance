from datetime import datetime
from sqlalchemy import Integer, String, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base

class DocumentTag(Base):
    __tablename__ = "document_tags"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), index=True)
    client_id: Mapped[int] = mapped_column(Integer, ForeignKey("clients.id"), index=True)
    document_id: Mapped[int] = mapped_column(Integer, ForeignKey("documents.id"), index=True)
    tag: Mapped[str] = mapped_column(String(120), index=True)   # e.g., "PTSD", "Tinnitus", "Nexus Letter"
    category: Mapped[str] = mapped_column(String(80), default="")  # e.g., "Condition", "EvidenceType"
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
