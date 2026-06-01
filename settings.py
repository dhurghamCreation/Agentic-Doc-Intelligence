"""
Configuration management for Document Intelligence System
"""
import os
from typing import Optional
from functools import lru_cache


class Settings:
    """Application settings"""
    
    # API
    API_HOST: str = os.getenv("API_HOST", "0.0.0.0")
    API_PORT: int = int(os.getenv("API_PORT", 8000))
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"
    
    # Database
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./documents.db")
    
    # OCR
    OCR_LANG: str = os.getenv("OCR_LANG", "eng")
    OCR_PSM: int = int(os.getenv("OCR_PSM", 3))
    
    # File Upload
    UPLOAD_DIR: str = os.getenv("UPLOAD_DIR", "./uploads")
    MAX_FILE_SIZE: int = int(os.getenv("MAX_FILE_SIZE", 52428800))  # 50MB
    
    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    
    # CORS
    ENABLE_CORS: bool = os.getenv("ENABLE_CORS", "true").lower() == "true"
    
    # Redis (optional)
    REDIS_URL: Optional[str] = os.getenv("REDIS_URL")
    
    # Processing
    MAX_BATCH_SIZE: int = 100
    PROCESSING_TIMEOUT: int = 300  # seconds
    
    class Config:
        env_file = ".env"


@lru_cache()
def get_settings() -> Settings:
    """Get application settings"""
    return Settings()


# Export settings
settings = get_settings()
