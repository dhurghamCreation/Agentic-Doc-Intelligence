"""
OCR Engine using Tesseract and OpenCV for document text extraction.
Supports multiple languages and document types.
"""
import pytesseract
import cv2
import numpy as np
from PIL import Image
import logging
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class OCRResult:
    """Result from OCR processing"""
    text: str
    confidence: float
    language: str
    raw_text: str
    bounding_boxes: List[Dict]


class OCREngine:
    """Advanced OCR engine with preprocessing and multi-language support"""
    
    SUPPORTED_LANGUAGES = {
        'eng': 'English',
        'fra': 'French',
        'deu': 'German',
        'spa': 'Spanish',
        'jpn': 'Japanese',
        'chi_sim': 'Chinese (Simplified)',
    }
    
    def __init__(self, languages: Optional[List[str]] = None):
        """Initialize OCR engine"""
        self.languages = languages or ['eng']
        self.lang_string = '+'.join(self.languages)
        
    def preprocess_image(self, image_path: str) -> np.ndarray:
        """Preprocess image for better OCR accuracy"""
        img = cv2.imread(image_path)
        
        if img is None:
            raise ValueError(f"Cannot read image: {image_path}")
        
        # Convert to grayscale
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        
        # Apply CLAHE (Contrast Limited Adaptive Histogram Equalization)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced = clahe.apply(gray)
        
        # Denoise
        denoised = cv2.fastNlMeansDenoising(enhanced)
        
        # Threshold
        _, binary = cv2.threshold(denoised, 150, 255, cv2.THRESH_BINARY)
        
        # Dilate to connect nearby characters
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
        dilated = cv2.dilate(binary, kernel, iterations=1)
        
        return dilated
    
    def extract_text(self, image_path: str) -> OCRResult:
        """Extract text from image using OCR"""
        try:
            # Preprocess image
            processed = self.preprocess_image(image_path)
            
            # Convert to PIL Image for pytesseract
            pil_image = Image.fromarray(processed)
            
            # Extract text with Tesseract
            raw_text = pytesseract.image_to_string(
                pil_image,
                lang=self.lang_string,
                config='--psm 3'
            )
            
            # Get confidence scores
            data = pytesseract.image_to_data(
                pil_image,
                lang=self.lang_string,
                output_type=pytesseract.Output.DICT
            )
            
            # Calculate average confidence
            confidences = [int(conf) for conf in data['confidence'] if int(conf) > 0]
            avg_confidence = np.mean(confidences) / 100 if confidences else 0.0
            
            # Extract bounding boxes
            bounding_boxes = []
            for i in range(len(data['text'])):
                if int(data['conf'][i]) > 0:
                    bounding_boxes.append({
                        'text': data['text'][i],
                        'x': data['left'][i],
                        'y': data['top'][i],
                        'width': data['width'][i],
                        'height': data['height'][i],
                        'confidence': int(data['conf'][i])
                    })
            
            # Clean text
            cleaned_text = self._clean_text(raw_text)
            
            return OCRResult(
                text=cleaned_text,
                confidence=avg_confidence,
                language=self.lang_string,
                raw_text=raw_text,
                bounding_boxes=bounding_boxes
            )
            
        except Exception as e:
            logger.error(f"OCR extraction failed: {str(e)}")
            raise
    
    @staticmethod
    def _clean_text(text: str) -> str:
        """Clean extracted text"""
        # Remove extra whitespace
        text = ' '.join(text.split())
        # Remove special characters but keep punctuation
        text = text.replace('\x00', '')
        return text.strip()
    
    def extract_tables(self, image_path: str) -> List[Dict]:
        """Detect and extract table structures from image"""
        img = cv2.imread(image_path)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        
        # Detect lines
        edges = cv2.Canny(gray, 100, 200)
        lines = cv2.HoughLinesP(edges, 1, np.pi/180, 100, minLineLength=100, maxLineGap=10)
        
        if lines is None:
            return []
        
        # Find table cells
        tables = []
        # This is a simplified implementation
        return tables
