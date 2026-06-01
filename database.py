"""
Database models for Document Intelligence System
Uses SQLAlchemy ORM for data persistence
"""
from sqlalchemy import create_engine, Column, String, Integer, Float, DateTime, Text, JSON, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
import os

# Database configuration
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./documents.db")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {},
    echo=False
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Document(Base):
    """Document record in database"""
    __tablename__ = "documents"
    
    id = Column(String, primary_key=True, index=True)
    filename = Column(String, index=True)
    file_path = Column(String)
    document_type = Column(String, index=True)
    raw_text = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    file_size = Column(Integer)
    status = Column(String, default="pending")


class ExtractionResult(Base):
    """Data extraction results"""
    __tablename__ = "extraction_results"
    
    id = Column(String, primary_key=True, index=True)
    document_id = Column(String, index=True)
    extracted_fields = Column(JSON)
    confidence = Column(Float)
    extraction_time = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)


class ValidationResult(Base):
    """Validation results"""
    __tablename__ = "validation_results"
    
    id = Column(String, primary_key=True, index=True)
    document_id = Column(String, index=True)
    is_valid = Column(Boolean)
    quality_score = Column(Float)
    issues = Column(JSON)
    validation_time = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)


class ClassificationResult(Base):
    """Classification results"""
    __tablename__ = "classification_results"
    
    id = Column(String, primary_key=True, index=True)
    document_id = Column(String, index=True)
    document_type = Column(String, index=True)
    confidence = Column(Float)
    probabilities = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)


class ProcessingJob(Base):
    """Track document processing jobs"""
    __tablename__ = "processing_jobs"
    
    id = Column(String, primary_key=True, index=True)
    document_id = Column(String, index=True)
    status = Column(String, default="pending")
    progress = Column(Integer, default=0)
    result = Column(JSON)
    error = Column(Text)
    processing_time = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime)


# Create tables
Base.metadata.create_all(bind=engine)


def get_db():
    """Get database session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
