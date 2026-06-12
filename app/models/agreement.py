from datetime import datetime
from sqlalchemy import String, DateTime, Integer, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base


class Agreement(Base):
    __tablename__ = "agreements"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False)
    client_id: Mapped[int] = mapped_column(Integer, ForeignKey("clients.id"), nullable=False)
    created_by_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)

    title: Mapped[str] = mapped_column(String(255), default="Service Agreement")
    body: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(40), default="DRAFT")  # DRAFT/SENT/SIGNED

    signed_name: Mapped[str] = mapped_column(String(255), default="")
    signed_email: Mapped[str] = mapped_column(String(255), default="")
    signed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    signature_type: Mapped[str] = mapped_column(String(60), default="")  # typed/ink/uploaded
    signature_ip: Mapped[str] = mapped_column(String(100), default="")
    signature_user_agent: Mapped[str] = mapped_column(String(255), default="")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
