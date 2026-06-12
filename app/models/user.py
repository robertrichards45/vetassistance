import enum
from datetime import datetime
from sqlalchemy import String, DateTime, Boolean, Enum, Integer, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import UserMixin
from .base import Base

class Role(str, enum.Enum):
    DIRECTOR = "DIRECTOR"
    EMPLOYEE = "EMPLOYEE"
    CLIENT = "CLIENT"
    DIY = "DIY"

class User(Base, UserMixin):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False)
    organization = relationship("Organization")
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    preferences_json: Mapped[str] = mapped_column(Text, default="{}")
    role: Mapped[Role] = mapped_column(Enum(Role), default=Role.EMPLOYEE, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    must_reset_password: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)
    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)
    def get_id(self):
        return str(self.id)

    @property
    def is_director(self) -> bool:
        return self.role == Role.DIRECTOR
