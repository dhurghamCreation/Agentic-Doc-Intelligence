"""Application configuration values and helpers."""

import os


class Config:
	API_HOST = os.getenv("API_HOST", "0.0.0.0")
	API_PORT = int(os.getenv("API_PORT", "8000"))
	DEBUG = os.getenv("DEBUG", "false").lower() == "true"

	DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./documents.db")
	OCR_LANG = os.getenv("OCR_LANG", "eng")
	LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

	UPLOAD_DIR = os.getenv("UPLOAD_DIR", "./uploads")
	MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE", str(50 * 1024 * 1024)))
	MAX_BATCH_SIZE = int(os.getenv("MAX_BATCH_SIZE", "100"))
	ENABLE_CORS = os.getenv("ENABLE_CORS", "true").lower() == "true"

	REDIS_URL = os.getenv("REDIS_URL", "")


config = Config()
