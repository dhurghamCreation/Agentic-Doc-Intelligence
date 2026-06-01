"""
Agentic Document Intelligence System
Advanced AI-powered document processing
"""

__version__ = "1.0.0"
__author__ = "AI Development Team"
__title__ = "Document Intelligence System"

from app.pipeline import DocumentProcessingPipeline
from agents.classifier import DocumentClassifier
from agents.extractor import DataExtractor
from agents.validator import DataValidator
from tools.ocr_engine import OCREngine
from tools.table_parser import TableParser

__all__ = [
    "DocumentProcessingPipeline",
    "DocumentClassifier",
    "DataExtractor",
    "DataValidator",
    "OCREngine",
    "TableParser",
]
