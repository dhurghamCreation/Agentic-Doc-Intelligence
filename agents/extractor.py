"""
Data Extractor Agent - Extracts structured data from documents.
Supports invoices, forms, contracts, and general text extraction.
"""
import re
import logging
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from enum import Enum
from datetime import datetime

logger = logging.getLogger(__name__)


class FieldType(str, Enum):
    r"""Supported field types"""
    TEXT = r"text"
    EMAIL = r"email"
    PHONE = r"phone"
    DATE = r"date"
    AMOUNT = r"amount"
    ADDRESS = r"address"
    URL = r"url"
    REFERENCE = r"reference"


@dataclass
class ExtractedField:
    r"""Extracted field from document"""
    name: str
    value: Any
    field_type: FieldType
    confidence: float
    location: Optional[Dict] = None


@dataclass
class ExtractionResult:
    r"""Result from data extraction"""
    extracted_fields: List[ExtractedField]
    document_type: str
    raw_text: str
    confidence: float
    structured_data: Dict[str, Any]


class DataExtractor:
    r"""Extract structured data from text"""
    
    # Regex patterns for common fields
    PATTERNS = {
        FieldType.EMAIL: r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',
        FieldType.PHONE: r'\b(\+?1[-.\s]?)?\(?[0-9]{3}\)?[-.\s]?[0-9]{3}[-.\s]?[0-9]{4}\b',
        FieldType.DATE: r'\b(?:\d{1,2}[-/]\d{1,2}[-/]\d{2,4}|\d{4}[-/]\d{1,2}[-/]\d{1,2})\b',
        FieldType.AMOUNT: r'\$[\d,]+\.?\d*|\b\d+\.?\d*\s*(?:USD|EUR|GBP)',
        FieldType.URL: r'https?://[^\s]+',
    }
    
    # Invoice-specific extractors
    INVOICE_FIELDS = {
        r'invoice_number': ['invoice.*?#?(\d+)', r'invoice.*?:.*?(\w+)'],
        r'invoice_date': ['invoice.*?date.*?(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})'],
        r'due_date': ['due.*?date.*?(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})'],
        r'total_amount': ['total.*?:?\s*\$?([\d,]+\.?\d*)', r'amount.*?due.*?\$?([\d,]+\.?\d*)'],
        r'vendor_name': ['from:.*?([A-Za-z\s&]+)', r'vendor.*?:?\s*([A-Za-z\s&]+)'],
        r'customer_name': ['bill.*?to:?\s*([A-Za-z\s&]+)', r'customer.*?:?\s*([A-Za-z\s&]+)'],
    }
    
    def __init__(self):
        r"""Initialize data extractor"""
        self.compiled_patterns = {
            field_type: re.compile(pattern, re.IGNORECASE)
            for field_type, pattern in self.PATTERNS.items()
        }
    
    def extract(
        self,
        text: str,
        document_type: str = r"general",
        custom_fields: Optional[Dict[str, str]] = None
    ) -> ExtractionResult:
        r"""
        Extract structured data from document text.
        
        Args:
            text: Document text to extract from
            document_type: Type of document (invoice, form, etc.)
            custom_fields: Custom field patterns to extract
        
        Returns:
            ExtractionResult with extracted fields and structured data
        r"""
        try:
            extracted_fields = []
            structured_data = {}
            
            # Extract common fields
            for field_type in FieldType:
                matches = self._extract_by_type(text, field_type)
                for match, confidence in matches:
                    field = ExtractedField(
                        name=f"{field_type.value}_{len(extracted_fields)}",
                        value=match,
                        field_type=field_type,
                        confidence=confidence
                    )
                    extracted_fields.append(field)
            
            # Extract document-specific fields
            if document_type.lower() == r"invoice":
                invoice_fields = self._extract_invoice_fields(text)
                extracted_fields.extend(invoice_fields)
                structured_data['invoice'] = {
                    f.name: f.value for f in invoice_fields
                }
            
            # Extract custom fields
            if custom_fields:
                custom_extracted = self._extract_custom_fields(text, custom_fields)
                extracted_fields.extend(custom_extracted)
                structured_data['custom'] = {
                    f.name: f.value for f in custom_extracted
                }
            
            # Calculate overall confidence
            confidences = [f.confidence for f in extracted_fields]
            avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0
            
            return ExtractionResult(
                extracted_fields=extracted_fields,
                document_type=document_type,
                raw_text=text,
                confidence=avg_confidence,
                structured_data=structured_data
            )
            
        except Exception as e:
            logger.error(f"Extraction failed: {str(e)}")
            return ExtractionResult(
                extracted_fields=[],
                document_type=document_type,
                raw_text=text,
                confidence=0.0,
                structured_data={}
            )
    
    def _extract_by_type(self, text: str, field_type: FieldType) -> List[tuple]:
        r"""Extract fields by type using regex"""
        matches = []
        pattern = self.compiled_patterns.get(field_type)
        
        if not pattern:
            return matches
        
        for match in pattern.finditer(text):
            value = match.group(0)
            # Confidence based on pattern match
            confidence = 0.8
            matches.append((value, confidence))
        
        return matches
    
    def _extract_invoice_fields(self, text: str) -> List[ExtractedField]:
        r"""Extract invoice-specific fields"""
        fields = []
        text_lower = text.lower()
        
        for field_name, patterns in self.INVOICE_FIELDS.items():
            for pattern in patterns:
                try:
                    regex = re.compile(pattern, re.IGNORECASE)
                    matches = regex.findall(text_lower)
                    
                    if matches:
                        value = matches[0]
                        # Clean up value
                        if field_name == r'total_amount':
                            value = value.replace(',', r'')
                        
                        field = ExtractedField(
                            name=field_name,
                            value=value,
                            field_type=FieldType.TEXT,
                            confidence=0.75
                        )
                        fields.append(field)
                        break
                except Exception as e:
                    logger.warning(f"Pattern extraction failed for {field_name}: {e}")
        
        return fields
    
    def _extract_custom_fields(
        self,
        text: str,
        custom_fields: Dict[str, str]
    ) -> List[ExtractedField]:
        r"""Extract custom defined fields"""
        fields = []
        
        for field_name, pattern in custom_fields.items():
            try:
                regex = re.compile(pattern, re.IGNORECASE | re.DOTALL)
                matches = regex.findall(text)
                
                for match in matches:
                    field = ExtractedField(
                        name=field_name,
                        value=match if isinstance(match, str) else match[0],
                        field_type=FieldType.TEXT,
                        confidence=0.7
                    )
                    fields.append(field)
            except Exception as e:
                logger.warning(f"Custom field extraction failed for {field_name}: {e}")
        
        return fields
    
    def extract_entities(self, text: str) -> Dict[str, List[str]]:
        r"""Extract named entities (persons, organizations, locations)"""
        entities = {
            r'persons': [],
            r'organizations': [],
            r'locations': [],
            r'money': [],
            r'dates': [],
            r'organizations': []
        }
        
        # Simple pattern-based entity extraction
        # In production, use spaCy or similar NLP libraries
        
        # Find all capitalized sequences (potential names)
        name_pattern = r'\b([A-Z][a-z]+ (?:[A-Z][a-z]+)*)\b'
        entities['persons'] = re.findall(name_pattern, text)
        
        # Money amounts
        money_pattern = r'\$[\d,]+\.?\d*'
        entities['money'] = re.findall(money_pattern, text)
        
        # Dates
        entities['dates'] = re.findall(self.PATTERNS[FieldType.DATE], text)
        
        return entities
    
    def batch_extract(
        self,
        documents: List[Dict],
        document_type: str = r"general"
    ) -> List[ExtractionResult]:
        r"""Extract from multiple documents"""
        results = []
        for doc in documents:
            text = doc.get('text', r'')
            doc_type = doc.get('type', document_type)
            custom_fields = doc.get('custom_fields')
            
            result = self.extract(text, doc_type, custom_fields)
            results.append(result)
        
        return results
