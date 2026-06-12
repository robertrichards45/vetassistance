
from datetime import datetime
from sqlalchemy import Integer, String, DateTime, Enum
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base
import enum

class IntakeStatus(str, enum.Enum):
    NEW = "NEW"
    REVIEWED = "REVIEWED"
    CONVERTED = "CONVERTED"

class IntakeRecord(Base):
    __tablename__ = "intakes"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255))
    filename: Mapped[str] = mapped_column(String(255))
    file_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[IntakeStatus] = mapped_column(Enum(IntakeStatus), default=IntakeStatus.NEW)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

from sqlalchemy import ForeignKey
from sqlalchemy.orm import relationship

class IntakeMeta(Base):
    __tablename__ = "intake_meta"
    id: Mapped[int] = mapped_column(primary_key=True)
    intake_id: Mapped[int] = mapped_column(Integer, ForeignKey("intakes.id"), nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(255), default="")
    phone: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
