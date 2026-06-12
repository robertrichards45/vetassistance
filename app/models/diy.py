from datetime import datetime
from sqlalchemy import String, DateTime, Integer, ForeignKey, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base


class DIYAccount(Base):
    __tablename__ = "diy_accounts"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(60), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class DIYSubscription(Base):
    __tablename__ = "diy_subscriptions"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False)
    diy_id: Mapped[int] = mapped_column(Integer, ForeignKey("diy_accounts.id"), nullable=False)
    plan: Mapped[str] = mapped_column(String(40), default="basic")
    status: Mapped[str] = mapped_column(String(40), default="inactive")
    stripe_customer_id: Mapped[str] = mapped_column(String(255), default="")
    stripe_subscription_id: Mapped[str] = mapped_column(String(255), default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    refund_status: Mapped[str] = mapped_column(String(40), default="")
    refund_amount_cents: Mapped[int] = mapped_column(Integer, default=0)
    refund_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class DIYDocument(Base):
    __tablename__ = "diy_documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False)
    diy_id: Mapped[int] = mapped_column(Integer, ForeignKey("diy_accounts.id"), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), default="")
    mime_type: Mapped[str] = mapped_column(String(120), default="")
    storage_path: Mapped[str] = mapped_column(String(600), default="")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class DIYDocumentScan(Base):
    __tablename__ = "diy_document_scans"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False)
    diy_id: Mapped[int] = mapped_column(Integer, ForeignKey("diy_accounts.id"), nullable=False)
    diy_document_id: Mapped[int] = mapped_column(Integer, ForeignKey("diy_documents.id"), nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="")
    score: Mapped[int] = mapped_column(Integer, default=0)
    issues_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class DIYChecklistItem(Base):
    __tablename__ = "diy_checklist_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False)
    diy_id: Mapped[int] = mapped_column(Integer, ForeignKey("diy_accounts.id"), nullable=False)
    label: Mapped[str] = mapped_column(String(255), default="")
    category: Mapped[str] = mapped_column(String(120), default="Evidence")
    is_done: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class DIYClaimUpdate(Base):
    __tablename__ = "diy_claim_updates"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False)
    diy_id: Mapped[int] = mapped_column(Integer, ForeignKey("diy_accounts.id"), nullable=False)
    status_label: Mapped[str] = mapped_column(String(200), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
