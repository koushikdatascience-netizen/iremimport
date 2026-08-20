"""
Database models for Madhushala Excise Bridge
"""
from sqlalchemy import Column, Integer, String, DateTime, Text, Boolean, Float
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime

Base = declarative_base()

class AutomationSession(Base):
    __tablename__ = "automation_sessions"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String, unique=True, index=True)
    started_at = Column(DateTime, default=datetime.utcnow)
    ended_at = Column(DateTime, nullable=True)
    status = Column(String, default="active")
    last_url = Column(String, nullable=True)
    last_error = Column(Text, nullable=True)

class CommittedBatch(Base):
    __tablename__ = "committed_batches"

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(String, unique=True, index=True)
    session_id = Column(String, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    status = Column(String, default="pending")
    retry_count = Column(Integer, default=0)
    last_error = Column(Text, nullable=True)

class CapturedExciseItem(Base):
    __tablename__ = "captured_excise_items"

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(String, index=True)
    canonical_key = Column(String, index=True)
    canonical_hash = Column(String, index=True)

    brand = Column(String)
    normalized_brand = Column(String)
    strength_raw = Column(String, nullable=True)
    measure_ml = Column(Integer)
    package_type = Column(String, nullable=True)

    retailer_margin = Column(Float, nullable=True)
    round_off_govt = Column(Float, nullable=True)
    special_purpose_fee = Column(Float, nullable=True)
    mrp_per_unit = Column(Float, nullable=True)
    mrp_per_case = Column(Float, nullable=True)

    flavour_type = Column(String, nullable=True)
    supplier = Column(String, nullable=True)

    warehouse_cases_raw = Column(String, nullable=True)
    warehouse_bottles = Column(Integer, nullable=True)

    requested_cases = Column(Integer, default=0)
    requested_bottles = Column(Integer, default=0)

    raw_json = Column(Text)
    captured_at = Column(DateTime, default=datetime.utcnow)

    sync_status = Column(String, default="pending")
    madhushala_excise_item_code = Column(Integer, nullable=True)
    madhushala_response_json = Column(Text, nullable=True)

class LocalMapping(Base):
    __tablename__ = "local_mappings"

    id = Column(Integer, primary_key=True, index=True)
    canonical_key = Column(String, unique=True, index=True)
    excise_item_code = Column(Integer)
    madhushala_item_code = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class ValueSnapshot(Base):
    __tablename__ = "value_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    canonical_key = Column(String, index=True)
    captured_at = Column(DateTime, default=datetime.utcnow)
    mrp_per_unit = Column(Float, nullable=True)
    mrp_per_case = Column(Float, nullable=True)
    retailer_margin = Column(Float, nullable=True)
    round_off_govt = Column(Float, nullable=True)
    special_purpose_fee = Column(Float, nullable=True)
    supplier = Column(String, nullable=True)
    raw_json = Column(Text)