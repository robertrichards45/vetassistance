from datetime import datetime

from sqlalchemy import Column, Integer, String, Text, DateTime

from .base import Base


class VACallLog(Base):
    __tablename__ = "va_call_logs"

    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, index=True)
    client_id = Column(Integer, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    call_date = Column(String(20), default="")
    call_time = Column(String(20), default="")
    phone = Column(String(60), default="")
    call_type = Column(String(80), default="")
    topic = Column(String(120), default="")
    agent_name = Column(String(120), default="")
    reference_id = Column(String(120), default="")
    outcome = Column(String(200), default="")
    summary = Column(Text, default="")
    next_steps = Column(Text, default="")
