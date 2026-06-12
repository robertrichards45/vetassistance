from datetime import datetime

from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean

from .base import Base


class PublicComment(Base):
    __tablename__ = "public_comments"

    id = Column(Integer, primary_key=True)
    name = Column(String(120), default="")
    branch = Column(String(80), default="")
    comment = Column(Text, default="")
    is_approved = Column(Boolean, default=False)
    is_rejected = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
