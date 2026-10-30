from datetime import datetime
from sqlalchemy import String, DateTime, Text, Integer, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base


class ClaimPacket(Base):
    """AI claim builder: recommended claims for a client, then a filled 21-526EZ and 21-4138 statements.

    status: QUEUED | READING | ANALYZING | READY | DRAFTING | DRAFTED | FAILED
    """
    __tablename__ = "claim_packets"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    client_id: Mapped[int] = mapped_column(Integer, ForeignKey("clients.id"), nullable=False, index=True)
    created_by_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="QUEUED")
    analysis_json: Mapped[str] = mapped_column(Text, default="")     # recommended claims + extracted facts
    selected_json: Mapped[str] = mapped_column(Text, default="")     # indexes of claims chosen for the forms
    form_json: Mapped[str] = mapped_column(Text, default="")         # editable 21-526EZ answers
    statements_json: Mapped[str] = mapped_column(Text, default="")   # editable 21-4138 statements
    model: Mapped[str] = mapped_column(String(80), default="")
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
