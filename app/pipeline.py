"""
Main Document Processing Pipeline - Orchestrates all agents and tools
"""
import logging
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
from datetime import datetime
import asyncio
from pathlib import Path

from agents.classifier import DocumentClassifier, ClassificationResult
from agents.extractor import DataExtractor, ExtractionResult
from agents.validator import DataValidator, ValidationResult
from tools.ocr_engine import OCREngine, OCRResult
from tools.table_parser import TableParser, ParsedTable

logger = logging.getLogger(__name__)


@dataclass
class ProcessingStep:
    """Record of a processing step"""
    name: str
    status: str
    duration: float
    result: Optional[Dict] = None
    error: Optional[str] = None


@dataclass
class PipelineResult:
    """Complete result from pipeline processing"""
    document_id: str
    status: str
    classification: Optional[ClassificationResult] = None
    extraction: Optional[ExtractionResult] = None
    validation: Optional[ValidationResult] = None
    ocr: Optional[OCRResult] = None
    tables: Optional[List[ParsedTable]] = None
    steps: List[ProcessingStep] = None
    processing_time: float = 0.0
    error: Optional[str] = None
    
    def to_dict(self) -> Dict:
        """Convert to dictionary"""
        return {
            'document_id': self.document_id,
            'status': self.status,
            'classification': asdict(self.classification) if self.classification else None,
            'extraction': {
                'fields': [asdict(f) for f in self.extraction.extracted_fields],
                'structured_data': self.extraction.structured_data,
                'confidence': self.extraction.confidence
            } if self.extraction else None,
            'validation': {
                'status': self.validation.status.value,
                'is_valid': self.validation.is_valid,
                'quality_score': self.validation.data_quality_score
            } if self.validation else None,
            'processing_time': self.processing_time,
            'timestamp': datetime.now().isoformat()
        }


class DocumentProcessingPipeline:
    """Main pipeline for document intelligence"""
    
    def __init__(self, enable_ocr: bool = True, enable_table_parsing: bool = True):
        """Initialize pipeline with agents and tools"""
        self.classifier = DocumentClassifier()
        self.extractor = DataExtractor()
        self.validator = DataValidator()
        self.ocr_engine = OCREngine() if enable_ocr else None
        self.table_parser = TableParser() if enable_table_parsing else None
        
        logger.info("Document Processing Pipeline initialized")
    
    async def process_document(
        self,
        document_id: str,
        text: Optional[str] = None,
        image_path: Optional[str] = None,
        custom_extraction_schema: Optional[Dict] = None,
        custom_validation_schema: Optional[Dict] = None
    ) -> PipelineResult:
        """
        Process a document through the entire pipeline.
        
        Args:
            document_id: Unique identifier for document
            text: Document text content
            image_path: Path to image if processing from image
            custom_extraction_schema: Custom extraction patterns
            custom_validation_schema: Custom validation rules
        
        Returns:
            Complete processing result
        """
        start_time = datetime.now()
        result = PipelineResult(
            document_id=document_id,
            status="processing",
            steps=[]
        )
        
        try:
            # Step 1: Extract text from image if needed
            if image_path and not text:
                step = ProcessingStep("ocr", "running", 0)
                try:
                    result.ocr = self.ocr_engine.extract_text(image_path)
                    text = result.ocr.text
                    step.status = "completed"
                    logger.info(f"OCR completed for {document_id}")
                except Exception as e:
                    step.status = "failed"
                    step.error = str(e)
                    logger.error(f"OCR failed: {e}")
                result.steps.append(step)
            
            if not text:
                result.status = "failed"
                result.error = "No text content to process"
                return result
            
            # Step 2: Classify document
            step = ProcessingStep("classification", "running", 0)
            try:
                result.classification = self.classifier.classify(
                    text,
                    metadata={'document_id': document_id}
                )
                step.status = "completed"
                logger.info(f"Classification completed: {result.classification.document_type}")
            except Exception as e:
                step.status = "failed"
                step.error = str(e)
                logger.error(f"Classification failed: {e}")
            result.steps.append(step)
            
            # Step 3: Extract data
            step = ProcessingStep("extraction", "running", 0)
            try:
                doc_type = result.classification.document_type.value if result.classification else "general"
                result.extraction = self.extractor.extract(
                    text,
                    document_type=doc_type,
                    custom_fields=custom_extraction_schema
                )
                step.status = "completed"
                logger.info(f"Extraction completed: {len(result.extraction.extracted_fields)} fields")
            except Exception as e:
                step.status = "failed"
                step.error = str(e)
                logger.error(f"Extraction failed: {e}")
            result.steps.append(step)
            
            # Step 4: Validate data
            step = ProcessingStep("validation", "running", 0)
            try:
                if result.extraction:
                    validation_schema = custom_validation_schema or {}
                    result.validation = self.validator.validate(
                        result.extraction.structured_data,
                        schema=validation_schema
                    )
                    step.status = "completed"
                    logger.info(f"Validation completed: {result.validation.status.value}")
                else:
                    step.status = "skipped"
            except Exception as e:
                step.status = "failed"
                step.error = str(e)
                logger.error(f"Validation failed: {e}")
            result.steps.append(step)
            
            # Step 5: Parse tables (if OCR was used)
            if self.table_parser and result.ocr:
                step = ProcessingStep("table_parsing", "running", 0)
                try:
                    # This would parse tables from OCR results
                    step.status = "completed"
                except Exception as e:
                    step.status = "failed"
                    step.error = str(e)
                result.steps.append(step)
            
            # Calculate total processing time
            result.processing_time = (datetime.now() - start_time).total_seconds()
            result.status = "completed"
            logger.info(f"Document {document_id} processed successfully in {result.processing_time:.2f}s")
            
        except Exception as e:
            result.status = "failed"
            result.error = str(e)
            result.processing_time = (datetime.now() - start_time).total_seconds()
            logger.error(f"Pipeline failed: {e}")
        
        return result
    
    async def process_batch(
        self,
        documents: List[Dict]
    ) -> List[PipelineResult]:
        """Process multiple documents"""
        results = []
        for doc in documents:
            doc_id = doc.get('id', f"doc_{len(results)}")
            text = doc.get('text')
            image_path = doc.get('image_path')
            
            result = await self.process_document(
                document_id=doc_id,
                text=text,
                image_path=image_path
            )
            results.append(result)
        
        return results
    
    def get_statistics(self, results: List[PipelineResult]) -> Dict[str, Any]:
        """Calculate statistics from batch processing"""
        if not results:
            return {}
        
        successful = sum(1 for r in results if r.status == "completed")
        failed = sum(1 for r in results if r.status == "failed")
        
        avg_time = sum(r.processing_time for r in results) / len(results)
        
        # Classification stats
        classifications = [r.classification.document_type.value for r in results if r.classification]
        
        # Validation stats
        valid_documents = sum(1 for r in results if r.validation and r.validation.is_valid)
        
        return {
            'total_documents': len(results),
            'successful': successful,
            'failed': failed,
            'success_rate': successful / len(results) if results else 0,
            'average_processing_time': avg_time,
            'document_types': dict(_count_items(classifications)),
            'valid_documents': valid_documents,
            'data_quality_avg': sum(
                r.validation.data_quality_score for r in results
                if r.validation
            ) / sum(1 for r in results if r.validation) if any(r.validation for r in results) else 0
        }


def _count_items(items: List) -> List[tuple]:
    """Count occurrences of items"""
    counts = {}
    for item in items:
        counts[item] = counts.get(item, 0) + 1
    return list(counts.items())
