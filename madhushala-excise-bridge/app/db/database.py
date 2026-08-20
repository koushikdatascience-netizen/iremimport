"""
Database initialization and connection module
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
import logging
from datetime import datetime

# Import models
from .models import Base, AutomationSession, CommittedBatch, CapturedExciseItem, LocalMapping, ValueSnapshot

logger = logging.getLogger("madhushala-excise-bridge")

# Create engine and session
engine = create_engine("sqlite:///data/excise_bridge.db", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Create tables
def init_db():
    """Initialize database tables"""
    try:
        Base.metadata.create_all(bind=engine)
        logger.info("Database tables initialized")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        raise

# Dependency for FastAPI
def get_db():
    """Get database session dependency"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()