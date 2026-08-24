"""Optional Phase 1 SQLite audit models.

JSON files in data/captures are the mandatory Phase 1 storage. These models
exist only for a future local audit table if SQLite is enabled.
"""
from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.orm import declarative_base


Base = declarative_base()


class CaptureBatch(Base):
    __tablename__ = "capture_batches"

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(String, unique=True, index=True, nullable=False)
    captured_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    page_url = Column(Text, nullable=True)
    item_count = Column(Integer, default=0, nullable=False)


class CapturedItem(Base):
    __tablename__ = "captured_items"

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(String, index=True, nullable=False)
    canonical_key = Column(String, index=True, nullable=False)
    brand = Column(Text, nullable=False)
    measure_ml = Column(Integer, nullable=False)
    package_type = Column(String, nullable=False)
    mrp_per_unit = Column(String, nullable=True)
    supplier = Column(Text, nullable=True)
    requested_cases = Column(Integer, default=0, nullable=False)
    requested_bottles = Column(Integer, default=0, nullable=False)
    raw_json = Column(Text, nullable=False)
