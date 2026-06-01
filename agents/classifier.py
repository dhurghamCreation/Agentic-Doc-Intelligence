"""
Document Classifier Agent - Classifies documents into categories.
Uses machine learning and heuristics for accurate classification.
"""
import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.naive_bayes import MultinomialNB
from sklearn.pipeline import Pipeline

logger = logging.getLogger(__name__)


class DocumentType(str, Enum):
    """Supported document types"""
    INVOICE = "invoice"
    RECEIPT = "receipt"
    CONTRACT = "contract"
    REPORT = "report"
    EMAIL = "email"
    FORM = "form"
    LETTER = "letter"
    UNKNOWN = "unknown"


@dataclass
class ClassificationResult:
    """Result from document classification"""
    document_type: DocumentType
    confidence: float
    probabilities: Dict[str, float]
    relevant_keywords: List[str]
    metadata: Dict


class DocumentClassifier:
    """Classify documents into predefined categories"""
    
    # Keywords for different document types
    KEYWORDS = {
        DocumentType.INVOICE: [
            'invoice', 'bill', 'amount due', 'invoice number',
            'tax', 'subtotal', 'total amount', 'payment terms'
        ],
        DocumentType.RECEIPT: [
            'receipt', 'paid', 'purchase', 'amount paid',
            'transaction', 'date', 'total', 'items'
        ],
        DocumentType.CONTRACT: [
            'agreement', 'contract', 'terms', 'conditions',
            'hereby', 'whereas', 'party', 'signature'
        ],
        DocumentType.REPORT: [
            'report', 'summary', 'analysis', 'findings',
            'conclusion', 'recommendation', 'data', 'chart'
        ],
        DocumentType.EMAIL: [
            'from:', 'to:', 'subject:', 'cc:', 'date:',
            'regards', 'sincerely', 'dear', 'hello'
        ],
        DocumentType.FORM: [
            'form', 'field', 'checkbox', 'signature line',
            'required', 'optional', 'please enter', 'x', '☑'
        ],
        DocumentType.LETTER: [
            'letter', 'dear', 'yours truly', 'sincerely',
            'regards', 'yours', 'faithfully'
        ],
    }
    
    def __init__(self):
        """Initialize classifier with pre-trained models"""
        self.vectorizer = TfidfVectorizer(max_features=100, lowercase=True)
        self.classifier = None
        self._initialize_classifier()
    
    def _initialize_classifier(self):
        """Initialize ML classifier"""
        try:
            # Training data (simplified for demo)
            training_docs = [
                ("Invoice #123 amount due $500", DocumentType.INVOICE),
                ("Receipt for purchase $45.99", DocumentType.RECEIPT),
                ("This agreement is entered into", DocumentType.CONTRACT),
                ("Monthly report and findings", DocumentType.REPORT),
            ]
            
            texts = [doc[0] for doc in training_docs]
            labels = [doc[1].value for doc in training_docs]
            
            # Create pipeline
            self.classifier = Pipeline([
                ('tfidf', TfidfVectorizer(max_features=100, lowercase=True)),
                ('clf', MultinomialNB())
            ])
            
            self.classifier.fit(texts, labels)
            logger.info("Document classifier initialized")
            
        except Exception as e:
            logger.warning(f"Classifier initialization failed: {e}")
    
    def classify(self, text: str, metadata: Optional[Dict] = None) -> ClassificationResult:
        """
        Classify a document based on its text content.
        
        Args:
            text: Document text to classify
            metadata: Optional metadata (filename, file type, etc.)
        
        Returns:
            ClassificationResult with document type and confidence
        """
        try:
            if not text or not text.strip():
                return ClassificationResult(
                    document_type=DocumentType.UNKNOWN,
                    confidence=0.0,
                    probabilities={},
                    relevant_keywords=[],
                    metadata=metadata or {}
                )
            
            # Keyword-based classification
            keyword_scores = self._score_by_keywords(text)
            best_type = max(keyword_scores, key=keyword_scores.get)
            best_confidence = keyword_scores[best_type]
            
            # ML-based classification (if available)
            ml_type, ml_confidence = self._classify_with_ml(text)
            
            # Combine results (weighted average)
            if best_confidence > 0.3:
                final_type = best_type
                final_confidence = best_confidence * 0.7 + ml_confidence * 0.3
            else:
                final_type = ml_type
                final_confidence = ml_confidence
            
            # Extract relevant keywords
            relevant_keywords = self._extract_relevant_keywords(text, final_type)
            
            # Prepare probabilities
            probabilities = {doc_type.value: score for doc_type, score in keyword_scores.items()}
            
            return ClassificationResult(
                document_type=final_type,
                confidence=min(final_confidence, 1.0),
                probabilities=probabilities,
                relevant_keywords=relevant_keywords,
                metadata=metadata or {}
            )
            
        except Exception as e:
            logger.error(f"Classification failed: {str(e)}")
            return ClassificationResult(
                document_type=DocumentType.UNKNOWN,
                confidence=0.0,
                probabilities={},
                relevant_keywords=[],
                metadata=metadata or {}
            )
    
    def _score_by_keywords(self, text: str) -> Dict[DocumentType, float]:
        """Score document by keyword matching"""
        text_lower = text.lower()
        scores = {}
        
        for doc_type, keywords in self.KEYWORDS.items():
            matches = sum(1 for keyword in keywords if keyword in text_lower)
            score = matches / len(keywords) if keywords else 0
            scores[doc_type] = score
        
        return scores
    
    def _classify_with_ml(self, text: str) -> Tuple[DocumentType, float]:
        """Classify using ML model"""
        try:
            if self.classifier:
                prediction = self.classifier.predict([text])[0]
                probabilities = self.classifier.predict_proba([text])[0]
                confidence = float(np.max(probabilities))
                
                doc_type = DocumentType(prediction)
                return doc_type, confidence
        except Exception as e:
            logger.warning(f"ML classification failed: {e}")
        
        return DocumentType.UNKNOWN, 0.0
    
    def _extract_relevant_keywords(self, text: str, doc_type: DocumentType) -> List[str]:
        """Extract keywords relevant to detected document type"""
        text_lower = text.lower()
        keywords = self.KEYWORDS.get(doc_type, [])
        
        found_keywords = [kw for kw in keywords if kw in text_lower]
        return found_keywords
    
    def batch_classify(self, documents: List[Dict]) -> List[ClassificationResult]:
        """Classify multiple documents"""
        results = []
        for doc in documents:
            text = doc.get('text', '')
            metadata = doc.get('metadata')
            result = self.classify(text, metadata)
            results.append(result)
        
        return results
