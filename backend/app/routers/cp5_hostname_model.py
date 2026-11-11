"""
Modelo SQLAlchemy: registra o hostname original antes de qualquer alteração.
Tabela simples — uma linha por sessão de alteração.
"""

from sqlalchemy import Column, Integer, String, DateTime, func
from app.database import Base


class HostnameHistory(Base):
    __tablename__ = "hostname_history"

    id         = Column(Integer, primary_key=True, index=True)
    original   = Column(String, nullable=False)
    saved_at   = Column(DateTime, server_default=func.now())
