from datetime import datetime
from sqlalchemy import DateTime, Integer, ForeignKey, Boolean, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base

class OnboardingItem(Base):
    __tablename__ = "onboarding_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    task_url: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_required: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class EmployeeOnboardingProgress(Base):
    __tablename__ = "employee_onboarding_progress"
    __table_args__ = (UniqueConstraint("employee_id", "item_id", name="uix_employee_item"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    item_id: Mapped[int] = mapped_column(Integer, ForeignKey("onboarding_items.id"), nullable=False, index=True)
    is_complete: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class EmployeeOnboardingStatus(Base):
    __tablename__ = "employee_onboarding_status"
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    item_key: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    ack_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
