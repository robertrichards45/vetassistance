from datetime import datetime
from sqlalchemy import String, DateTime, Integer, ForeignKey, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base


class EvidenceChecklist(Base):
    __tablename__ = "evidence_checklists"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False)
    client_id: Mapped[int] = mapped_column(Integer, ForeignKey("clients.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(200), default="Evidence Checklist")
    description: Mapped[str] = mapped_column(Text, default="")
    created_by_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class EvidenceChecklistItem(Base):
    __tablename__ = "evidence_checklist_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    checklist_id: Mapped[int] = mapped_column(Integer, ForeignKey("evidence_checklists.id"), nullable=False)
    label: Mapped[str] = mapped_column(String(255), default="")
    category: Mapped[str] = mapped_column(String(120), default="Evidence")
    notes: Mapped[str] = mapped_column(Text, default="")
    is_required: Mapped[bool] = mapped_column(Boolean, default=True)
    is_done: Mapped[bool] = mapped_column(Boolean, default=False)
    done_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    done_by_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
