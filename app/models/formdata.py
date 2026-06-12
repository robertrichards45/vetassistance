from datetime import datetime
from sqlalchemy import DateTime, Integer, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base

class FormData(Base):
    __tablename__ = "form_data"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False)
    client_id: Mapped[int] = mapped_column(Integer, ForeignKey("clients.id"), nullable=False)
    form_key: Mapped[str] = mapped_column(String(120), nullable=False)  # e.g., INTAKE_V1
    data_json: Mapped[str] = mapped_column(Text, default="{}")
    created_by_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
