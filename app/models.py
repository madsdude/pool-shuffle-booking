from __future__ import annotations
import os
from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, DateTime, ForeignKey, create_engine, Index
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

DB_URL = os.environ.get("DB_URL", "postgresql+psycopg://booking:booking@db:5432/booking")
connect_args = {"check_same_thread": False} if DB_URL.startswith("sqlite:") else {}

engine = create_engine(DB_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
Base = declarative_base()

class Resource(Base):
    __tablename__ = "resources"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False)
    kind = Column(String(20), nullable=False)  # 'pool' eller 'shuffle'
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    bookings = relationship("Booking", back_populates="resource", cascade="all, delete-orphan")

class Booking(Base):
    __tablename__ = "bookings"
    id = Column(Integer, primary_key=True, index=True)
    resource_id = Column(Integer, ForeignKey("resources.id", ondelete="CASCADE"), nullable=False)
    start_utc = Column(DateTime(timezone=True), nullable=False)
    end_utc = Column(DateTime(timezone=True), nullable=False)
    name = Column(String(120), nullable=False)
    phone = Column(String(50), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    resource = relationship("Resource", back_populates="bookings")

# Indekser for hurtige overlap-søgninger
Index("ix_booking_res_start", Booking.resource_id, Booking.start_utc)
Index("ix_booking_res_end", Booking.resource_id, Booking.end_utc)

def init_db():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if db.query(Resource).count() == 0:
            db.add_all([
                Resource(name="Pool 1", kind="pool"),
                Resource(name="Pool 2", kind="pool"),
                Resource(name="Pool 3", kind="pool"),
                Resource(name="Shuffle 1", kind="shuffle"),
                Resource(name="Shuffle 2", kind="shuffle"),
            ])
            db.commit()
    finally:
        db.close()
