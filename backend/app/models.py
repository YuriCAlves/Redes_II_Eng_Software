from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.sql import func
from sqlalchemy.orm import declarative_base

Base = declarative_base()

class HostnameHistory(Base):
    __tablename__ = "hostname_history"
    id       = Column(Integer, primary_key=True, index=True)
    original = Column(String, nullable=False)
    saved_at = Column(DateTime, server_default=func.now())
