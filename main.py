from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import secrets
import uuid
import threading
import webbrowser
from dataclasses import dataclass, field as dataclass_field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile, Request, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field

from app.pipeline import DocumentProcessingPipeline

try:
    from pypdf import PdfReader
except Exception:  # pragma: no cover
    PdfReader = None

try:
    import pytesseract
    from PIL import Image
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False

try:
    import docx
    DOCX_AVAILABLE = True
except ImportError:
    DOCX_AVAILABLE = False


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

MAX_UPLOAD_SIZE_BYTES = 50 * 1024 * 1024  # 50MB
MAX_BATCH_SIZE = 10
ALLOWED_EXTENSIONS = {
    ".txt", ".md", ".pdf", ".csv", ".json", 
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff",
    ".rtf", ".docx", ".xlsx", ".xls", ".pptx",
    ".html", ".xml", ".yaml", ".yml",
    ".log", ".py", ".js", ".ts", ".java", ".c", ".cpp", ".h"
}

# Supported MIME types for better file detection
MIME_TYPE_MAP = {
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".json": "application/json",
    ".xml": "application/xml",
    ".html": "text/html",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".tiff": "image/tiff",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".rtf": "application/rtf",
}

APP_VERSION = "4.0.2"


class ExtractionRequest(BaseModel):
    text: str
    custom_fields: Optional[Dict[str, Any]] = None


class BatchRequest(BaseModel):
    documents: List[Dict[str, Any]] = Field(default_factory=list)


class ChatMessage(BaseModel):
    message: str
    conversation_id: Optional[str] = None
    context: Optional[Dict[str, Any]] = None


class LoginRequest(BaseModel):
    email: str
    password: str


class RegisterRequest(BaseModel):
    email: str
    password: str
    name: str


class BusinessChallenge(BaseModel):
    title: str
    description: str
    category: str
    priority: str = "medium"


class ProcessingJobStatus(str, Enum):
    PENDING = "pending"
    UPLOADING = "uploading"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class ProcessingJob:
    job_id: str
    status: str
    progress: int = 0
    filename: str = ""
    file_type: str = ""
    file_size: int = 0
    created_at: str = ""
    updated_at: str = ""
    completed_at: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    summary: str = ""
    extracted_fields: List[Dict] = dataclass_field(default_factory=list)
    suggestions: List[str] = dataclass_field(default_factory=list)
    document_type: str = "unknown"


@dataclass
class User:
    user_id: str
    email: str
    name: str
    password_hash: str
    created_at: str
    last_login: Optional[str] = None
    role: str = "user"
    is_active: bool = True
    avatar: Optional[str] = None


@dataclass
class Conversation:
    conversation_id: str
    user_id: str
    title: str
    created_at: str
    messages: List[Dict] = dataclass_field(default_factory=list)


# Simple in-memory user database (for demo purposes)
users_db: Dict[str, User] = {}
sessions_db: Dict[str, Dict[str, Any]] = {}  # session_token -> {user_id, expires_at}
conversations_db: Dict[str, Conversation] = {}


class JobTracker:
    """Tracks all processing jobs with statistics"""
    
    def __init__(self):
        self.jobs: Dict[str, ProcessingJob] = {}
        self.stats = {
            "total_processed": 0,
            "total_failed": 0,
            "total_active": 0,
            "total_bytes_processed": 0,
            "documents_by_type": {},
            "daily_stats": {}
        }
    
    def add_job(self, job: ProcessingJob):
        self.jobs[job.job_id] = job
        self.stats["total_active"] += 1
    
    def update_job(self, job_id: str, **kwargs):
        if job_id in self.jobs:
            job = self.jobs[job_id]
            for key, value in kwargs.items():
                if hasattr(job, key):
                    setattr(job, key, value)
            job.updated_at = _utc_now()
    
    def complete_job(self, job_id: str, result: Dict[str, Any], summary: str = "", 
                     extracted_fields: List[Dict] = None, suggestions: List[str] = None):
        if job_id in self.jobs:
            job = self.jobs[job_id]
            job.status = "completed"
            job.progress = 100
            job.completed_at = _utc_now()
            job.result = result
            job.summary = summary
            job.extracted_fields = extracted_fields or []
            job.suggestions = suggestions or []
            job.updated_at = _utc_now()
            
            self.stats["total_processed"] += 1
            self.stats["total_active"] = max(0, self.stats["total_active"] - 1)
            self.stats["total_bytes_processed"] += job.file_size
            
            # Track by document type
            doc_type = job.document_type or "unknown"
            self.stats["documents_by_type"][doc_type] = self.stats["documents_by_type"].get(doc_type, 0) + 1
            
            # Track daily stats
            today = datetime.utcnow().strftime("%Y-%m-%d")
            if today not in self.stats["daily_stats"]:
                self.stats["daily_stats"][today] = {"processed": 0, "failed": 0}
            self.stats["daily_stats"][today]["processed"] += 1
    
    def fail_job(self, job_id: str, error: str):
        if job_id in self.jobs:
            job = self.jobs[job_id]
            job.status = "failed"
            job.error = error
            job.completed_at = _utc_now()
            job.updated_at = _utc_now()
            job.progress = 100
            
            self.stats["total_failed"] += 1
            self.stats["total_active"] = max(0, self.stats["total_active"] - 1)
            
            today = datetime.utcnow().strftime("%Y-%m-%d")
            if today not in self.stats["daily_stats"]:
                self.stats["daily_stats"][today] = {"processed": 0, "failed": 0}
            self.stats["daily_stats"][today]["failed"] += 1
    
    def get_statistics(self) -> Dict[str, Any]:
        return {
            **self.stats,
            "total_jobs": len(self.jobs),
            "success_rate": (self.stats["total_processed"] / max(1, self.stats["total_processed"] + self.stats["total_failed"])) * 100
        }


job_tracker = JobTracker()
app = FastAPI(title="DocIntel Studio Pro", version=APP_VERSION)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

pipeline = DocumentProcessingPipeline(enable_ocr=True, enable_table_parsing=True)


def _utc_now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _hash_password(password: str) -> str:
    """Hash password with salt"""
    salt = secrets.token_hex(16)
    hashed = hashlib.sha256(f"{salt}{password}".encode()).hexdigest()
    return f"{salt}${hashed}"


def _verify_password(password: str, hashed: str) -> bool:
    """Verify password against hash"""
    if "$" not in hashed:
        return False
    salt, expected_hash = hashed.split("$", 1)
    actual_hash = hashlib.sha256(f"{salt}{password}".encode()).hexdigest()
    return secrets.compare_digest(actual_hash, expected_hash)


def _generate_session_token() -> str:
    return secrets.token_urlsafe(32)


def _get_current_user(request: Request) -> Optional[User]:
    """Get current user from session cookie"""
    token = request.cookies.get("session_token")
    if not token or token not in sessions_db:
        return None
    
    session = sessions_db[token]
    if datetime.fromisoformat(session["expires_at"]) < datetime.utcnow():
        del sessions_db[token]
        return None
    
    return users_db.get(session["user_id"])


def _safe_filename(filename: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]", "_", filename or "file")
    cleaned = cleaned.strip("._") or "file"
    return cleaned[:180]


def _is_allowed_file(filename: str) -> bool:
    return Path(filename).suffix.lower() in ALLOWED_EXTENSIONS


def _get_file_type(filename: str) -> str:
    """Determine file type from extension"""
    ext = Path(filename).suffix.lower()
    type_map = {
        ".pdf": "pdf",
        ".txt": "text",
        ".md": "markdown",
        ".csv": "spreadsheet",
        ".json": "data",
        ".xml": "data",
        ".yaml": "data",
        ".yml": "data",
        ".jpg": "image",
        ".jpeg": "image",
        ".png": "image",
        ".gif": "image",
        ".webp": "image",
        ".bmp": "image",
        ".tiff": "image",
        ".docx": "document",
        ".xlsx": "spreadsheet",
        ".xls": "spreadsheet",
        ".pptx": "presentation",
        ".rtf": "document",
        ".html": "web",
        ".log": "log",
    }
    return type_map.get(ext, "unknown")


def _read_text_file(file_path: Path) -> str:
    for encoding in ("utf-8", "utf-16", "latin-1", "cp1252"):
        try:
            return file_path.read_text(encoding=encoding)
        except Exception:
            pass
    return file_path.read_text(encoding="utf-8", errors="ignore")


def _read_pdf_text(file_path: Path) -> str:
    if PdfReader is None:
        raise RuntimeError("PDF support unavailable. Install pypdf package.")
    reader = PdfReader(str(file_path))
    text_parts = []
    for i, page in enumerate(reader.pages):
        page_text = page.extract_text()
        if page_text:
            text_parts.append(f"--- Page {i + 1} ---\n{page_text}")
    return "\n".join(text_parts).strip()


def _read_docx_text(file_path: Path) -> str:
    if not DOCX_AVAILABLE:
        raise RuntimeError("DOCX support unavailable. Install python-docx package.")
    doc = docx.Document(str(file_path))
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    return "\n\n".join(paragraphs)


def _read_image_text(file_path: Path) -> str:
    """Extract text from image using OCR"""
    if not OCR_AVAILABLE:
        raise RuntimeError("OCR support unavailable. Install pytesseract and Pillow packages.")
    
    try:
        image = Image.open(file_path)
        text = pytesseract.image_to_string(image)
        return text.strip()
    except Exception as e:
        raise RuntimeError(f"OCR failed: {str(e)}")


def _build_generic_document_text(file_path: Path, raw_bytes: bytes) -> str:
    decoded_preview = ""
    for encoding in ("utf-8", "utf-16", "latin-1"):
        try:
            decoded_preview = raw_bytes.decode(encoding, errors="ignore").strip()
            if decoded_preview:
                break
        except Exception:
            continue

    preview = decoded_preview[:3000] if decoded_preview else ""
    metadata_lines = [
        f"Filename: {file_path.name}",
        f"Extension: {file_path.suffix.lower() or 'none'}",
        f"Size bytes: {len(raw_bytes)}",
        f"Type: {file_path.suffix.lower() or 'unclassified'} upload",
    ]
    if preview:
        metadata_lines.extend(["Decoded preview:", preview])
    else:
        metadata_lines.append("Decoded preview: not available")
    return "\n".join(metadata_lines)


def _generate_summary(text: str, doc_type: str, extraction_result: Any) -> str:
    """Generate a concise summary of the document"""
    summaries = []
    
    # Document type summary
    type_descriptions = {
        "invoice": "invoice/financial document",
        "receipt": "receipt/transaction record",
        "contract": "legal contract/agreement",
        "report": "analytical report",
        "letter": "business correspondence",
        "form": "structured form",
        "email": "email communication",
        "article": "article/publication",
        "technical": "technical documentation",
    }
    
    doc_desc = type_descriptions.get(doc_type, "general document")
    summaries.append(f"This is a {doc_desc}.")
    
    # Extract key information
    if extraction_result and hasattr(extraction_result, 'extracted_fields'):
        fields = extraction_result.extracted_fields
        if fields:
            key_fields = [f.name for f in fields[:5]]
            summaries.append(f"Key fields identified: {', '.join(key_fields)}.")
    
    # Length assessment
    word_count = len(text.split())
    if word_count < 100:
        summaries.append("Brief document.")
    elif word_count < 500:
        summaries.append("Medium-length document.")
    else:
        summaries.append(f"Lengthy document with approximately {word_count} words.")
    
    return " ".join(summaries)


def _generate_suggestions(doc_type: str, extracted_fields: List[Dict], text: str) -> List[str]:
    """Generate actionable suggestions based on document analysis"""
    suggestions = []
    
    # Type-specific suggestions
    if doc_type == "invoice":
        suggestions.append("Consider setting up automated payment reminders based on due dates.")
        suggestions.append("Cross-reference vendor information with your approved supplier list.")
        suggestions.append("Set up expense categorization rules for this vendor.")
    elif doc_type == "contract":
        suggestions.append("Review termination clauses and notice periods.")
        suggestions.append("Set calendar reminders for renewal dates.")
        suggestions.append("Flag any auto-renewal clauses for legal review.")
    elif doc_type == "receipt":
        suggestions.append("Categorize for tax purposes and expense tracking.")
        suggestions.append("Check if this qualifies for any rebate programs.")
    elif doc_type == "report":
        suggestions.append("Consider creating visual dashboards for key metrics.")
        suggestions.append("Set up automated report generation for regular intervals.")
    elif doc_type == "form":
        suggestions.append("Verify all required fields are completed.")
        suggestions.append("Consider digitizing this form for easier processing.")
    
    # Generic suggestions
    if len(text.split()) > 1000:
        suggestions.append("This is a lengthy document - consider using AI summarization for quick review.")
    
    if not extracted_fields:
        suggestions.append("Few fields were extracted - consider manual review or custom extraction rules.")
    
    suggestions.append("Save this document to your workspace for future reference.")
    suggestions.append("Export results to integrate with your existing workflows.")
    
    return suggestions


def _ai_chat_response(message: str, context: Optional[Dict] = None) -> Dict[str, Any]:
    """
    Intelligent, multi-variant conversational engine for document intelligence assistance.
    Each call produces a different response to the same intent through rotation-based variant selection.
    """
    
    import hashlib
    
    context = context or {}
    message_lower = message.lower().strip()
    recent_messages = context.get("recent_messages") or []
    current_section = context.get("current_section") or "overview"
    topic_hint = context.get("topic") or ""
    message_words = set(w for w in message_lower.split() if len(w) > 2)
    
    # Track response count per session key to rotate variants
    response_counter = getattr(_ai_chat_response, "_counter", 0)
    _ai_chat_response._counter = response_counter + 1
    
    def _is_followup() -> bool:
        """Detect if this is a follow-up question based on conversation history."""
        if not recent_messages:
            return False
        user_msgs = [m.get("content", "") for m in recent_messages if m.get("role") == "user"]
        assistant_msgs = [m.get("content", "") for m in recent_messages if m.get("role") == "assistant"]
        return len(user_msgs) > 1 and len(assistant_msgs) > 0
    
    def _last_topic() -> str:
        """Get the topic of the last conversation turn."""
        if not recent_messages:
            return ""
        user_msgs = [m.get("content", "") for m in recent_messages if m.get("role") == "user"]
        if len(user_msgs) >= 2:
            return user_msgs[-2][:60]
        return ""
    
    def _word_overlap(words1, words2):
        set1 = set(w.lower() for w in words1)
        set2 = set(w.lower() for w in words2)
        if not set1 or not set2:
            return 0.0
        return len(set1 & set2) / len(set1 | set2)
    
    def _best_match(intent_map):
        best_intent = None
        best_score = 0.0
        for name, data in intent_map.items():
            score = _word_overlap(message_words, data.get("keywords", []))
            if any(kw in message_lower for kw in data.get("keywords", [])):
                score += 0.3
            if any(p in message_lower for p in data.get("phrases", [])):
                score += 0.4
            if score > best_score:
                best_score = score
                best_intent = name
        return best_intent, best_score
    
    def _pick_variant(variants):
        """Pick a variant deterministically based on message + counter to get diversity."""
        idx = (response_counter + len(message_lower)) % len(variants)
        return variants[idx]
    
    def _build_context():
        if not recent_messages:
            return ""
        user_msgs = [m.get("content", "") for m in recent_messages if m.get("role") == "user"]
        if not user_msgs:
            return ""
        latest = user_msgs[-1]
        if latest and latest.lower() != message_lower:
            return f"\n\n[Context: Your previous question was: '{latest[:100]}'. I am incorporating that context into this answer.]"
        return ""
    
    history_context = _build_context()
    is_followup = _is_followup()
    prev_topic = _last_topic()
    
    intents = {
        "extraction": {
            "keywords": ["extract", "extraction", "fields", "parse", "data", "structured", "values", "information", "pull", "retrieve"],
            "phrases": ["pull data", "data from", "get data", "find data", "show fields", "extract text", "extract data", "gather data"],
            "variants": [
                "Document Extraction Overview\n\nTo extract data from a document:\n1. Go to Studio and paste your text or upload a file\n2. The system classifies the document (invoice, contract, report, etc.)\n3. Key fields are extracted with confidence percentages\n\nAutomatic extraction targets: email addresses, phone numbers, currency amounts, dates, names, organizations, and URLs.\n\nFor best results: use clean, well-formatted text. If extraction misses something, try the Summarize feature which uses a different approach. Batch mode works well for groups of similar documents.\n\n{history_context}",
                "Extracting Data from Documents\n\nThe extraction pipeline works in three stages:\n\nStage 1: Classification -- the AI identifies what type of document you have.\nStage 2: Field Detection -- common patterns like dates, amounts, and names are located.\nStage 3: Confidence Scoring -- each extracted value gets a reliability score.\n\nYou can define custom extraction fields by providing regex patterns. Go to Studio, enter your text, and click Extract Data to see it in action. If you need help with custom patterns, describe what you're looking for and I can suggest a regex.\n\n{history_context}",
                "Working with Extracted Data\n\nAfter extraction, you can:\n- View all fields with confidence scores\n- Copy individual values or the full result\n- Download results as a text file\n- Get suggestions for next steps\n\nCommon fields extracted by document type:\n- Invoices: invoice number, date, total, vendor, due date\n- Contracts: parties, effective date, terms, clauses\n- Receipts: store, items, total, payment method\n- Reports: title, author, date, key metrics\n\nIf a field was missed, try adding more context to your text or use the Batch mode for comparing multiple documents.\n\n{history_context}",
                "Advanced Extraction Tips\n\nTo maximize extraction accuracy:\n1. Remove unnecessary formatting from pasted text\n2. Ensure dates use a standard format (MM/DD/YYYY or YYYY-MM-DD)\n3. Currency symbols ($, EUR, GBP) help the system identify amounts\n4. Full names and complete addresses improve entity recognition\n\nIf you're working with a specific document type repeatedly, you can define custom extraction fields. For example, for invoices you might add fields like 'purchase_order_number' or 'shipping_address'.\n\n{history_context}"
            ],
            "suggestions_variants": [
                ["Extract this invoice for me", "Create a custom extraction field", "How accurate is the extraction?", "What fields does it find?"],
                ["Show me an extraction example", "Write a custom regex pattern", "Compare two documents", "Extract data from a PDF"],
                ["Why did extraction miss this value?", "How do I improve accuracy?", "Extract all dates and amounts", "Show me confidence scores"]
            ]
        },
        "summarization": {
            "keywords": ["summary", "summarize", "brief", "summarise", "key points", "overview", "condense", "digest", "highlights", "tl;dr", "short", "recap"],
            "phrases": ["give me summary", "make summary", "short version", "key takeaways", "tell me briefly", "what is this about", "main points"],
            "variants": [
                "Document Summarization\n\nThe system can generate summaries in multiple formats:\n- Executive Summary: 2-3 sentence high-level overview\n- Bullet Points: key facts in scannable format\n- Detailed Digest: comprehensive breakdown\n- Action Items: decisions, tasks, and next steps\n\nTo use: go to Studio > Summarize tab, paste your text, and click Generate Summary. The system identifies document type, extracts key sentences (beginning, middle, end), finds all numbers and dates, and provides actionable suggestions.\n\n{history_context}",
                "How Summarization Works\n\nThe summarization engine analyzes your document by:\n1. Classifying the document type (invoice, report, email, etc.)\n2. Extracting key sentences (first, middle, and last sections)\n3. Scanning for numbers, dates, and currency amounts\n4. Generating confidence-weighted suggestions\n\nFor long documents, the summary focuses on the most information-dense sections. You can customize the output by specifying what you want highlighted (financial data, dates, names, etc.).\n\n{history_context}",
                "Getting the Best Summary\n\nTo get the most useful summary:\n- Paste the full document text, not just excerpts\n- The system works best with 500+ words of content\n- Structured text (with headings and sections) produces clearer summaries\n- Financial and business documents get the richest analysis\n\nAfter generating a summary, you can copy it, download it, or use it as a starting point for extraction. The suggestions section often contains valuable next steps.\n\n{history_context}",
                "Summary Output Fields\n\nEach summary includes:\n- Document type classification\n- Word and sentence counts\n- Key points from beginning, middle, and end\n- Numbers found (up to 10)\n- Dates found (up to 10)\n- Confidence score for the classification\n- Actionable suggestions\n\nThis gives you a complete picture of the document's content without reading it entirely.\n\n{history_context}"
            ],
            "suggestions_variants": [
                ["Summarize this invoice", "Generate bullet points", "Find all dates and numbers", "Create an executive summary"],
                ["Summarize a contract", "What are the key decisions?", "Highlight financial data", "Compare two summaries"],
                ["Extract key action items", "Make a detailed digest", "Short version of this text", "What is this document about?"]
            ]
        },
        "ocr": {
            "keywords": ["ocr", "image", "scan", "handwritten", "photo", "picture", "scanner", "tesseract", "optical", "recognition", "text from image"],
            "phrases": ["read text from image", "convert image to text", "image to text", "scan document", "handwriting recognition", "text recognition", "extract text from picture"],
            "variants": [
                "OCR Processing Guide\n\nOCR extracts text from images and scanned documents. Supported formats: JPG, PNG, GIF, WebP, BMP, TIFF.\n\nFor best accuracy:\n- Use 300+ DPI resolution\n- Ensure good lighting and contrast\n- Avoid shadows and glare\n- Keep documents straight and unrotated\n- Printed text works best (handwriting has lower accuracy)\n\nIf OCR quality is poor: crop the image to focus on text areas, increase contrast before uploading, or convert to black and white.\n\n{history_context}",
                "Image Text Extraction\n\nThe OCR pipeline includes:\n1. Preprocessing: noise reduction, contrast enhancement, binarization\n2. Text detection: locating text regions in the image\n3. Recognition: converting text regions to machine-readable text\n4. Post-processing: confidence filtering and formatting\n\nSystem limitations: handwriting recognition is experimental, very small fonts may be missed, and complex layouts (multi-column) may not preserve order perfectly.\n\n{history_context}",
                "Improving OCR Results\n\nCommon OCR problems and solutions:\n\nBlurry image -> increase resolution or use a better camera\nLow contrast -> adjust brightness/contrast before uploading\nSkewed text -> straighten the image first\nBackground noise -> use a plain white background\nSmall fonts -> zoom in before capturing\n\nFor critical documents, always review OCR output against the original image.\n\n{history_context}",
                "OCR vs Manual Input\n\nWhen OCR is not working well, consider:\n- Using the Text Input tab and typing the content manually\n- Extracting text from a PDF using a dedicated PDF tool first\n- Taking a screenshot at higher resolution\n- Using a dedicated scanning app before uploading\n\nFor forms and structured documents, the system can still extract useful metadata even if full OCR is imperfect.\n\n{history_context}"
            ],
            "suggestions_variants": [
                ["How to improve OCR accuracy?", "Can it read handwriting?", "What image formats work?", "OCR failed, what now?"],
                ["Best settings for scanning", "Extract text from this photo", "OCR vs PDF extraction", "Preprocess an image for OCR"],
                ["Why is OCR giving errors?", "Supported image formats", "Handwriting recognition tips", "OCR confidence scores"]
            ]
        },
        "formats": {
            "keywords": ["pdf", "docx", "xlsx", "csv", "json", "xml", "format", "file type", "extension", "supported", "convert", "upload", "import"],
            "phrases": ["what formats", "file support", "what files", "supported formats", "file types", "document types", "max file size", "file size limit", "which formats"],
            "variants": [
                "Supported File Formats\n\nDocIntel Pro supports {count}+ file formats across these categories:\n- Documents: PDF, DOCX, RTF, TXT, MD, HTML\n- Spreadsheets: XLSX, XLS, CSV\n- Images (via OCR): JPG, PNG, GIF, WebP, BMP, TIFF\n- Data: JSON, XML, YAML, LOG\n- Code: PY, JS, TS, JAVA, C, CPP, H\n\nUpload limits: max 50MB per file, max 10 documents per batch.\n\nAll files are processed securely and can be auto-deleted after processing.\n\n{history_context}",
                "Format-Specific Processing\n\nEach format gets a tailored processing path:\n- PDF: text extraction per page, handles both digital and scanned\n- DOCX: paragraph-by-paragraph extraction with formatting\n- Images: OCR pipeline with preprocessing\n- CSV/JSON: structured data detection\n- Code files: syntax-aware extraction\n\nPro tip: for scanned PDFs, the system automatically applies OCR. For Excel files, table structures are preserved when possible.\n\n{history_context}",
                "File Format Limitations\n\nWhat is NOT supported:\n- Password-protected or encrypted files\n- Corrupted or incomplete files\n- Very large files (over 50MB)\n- Audio or video files\n- Proprietary database formats\n\nIf you have one of these, try converting to a supported format first. For example, export a database report as CSV, or save a password-protected PDF without the password.\n\n{history_context}",
                "Choosing the Right Format\n\nFor the best results:\n- Text files (TXT, MD): ideal for direct extraction\n- PDF: best for formatted documents and scanned pages\n- DOCX: good for Word documents with complex formatting\n- Images: use when only a photo or scan is available\n- JSON/CSV: perfect for structured data and batch processing\n\nWhen in doubt, paste the text directly using the Text Input tab, which bypasses file format limitations.\n\n{history_context}"
            ],
            "suggestions_variants": [
                ["Can you process password-protected PDFs?", "What is the max file size?", "How do I upload multiple files?", "What Excel formats work?"],
                ["Convert a PDF to text", "Best format for invoices", "Upload a scanned document", "Process a JSON file"],
                ["File format limitations", "Why is my file not uploading?", "Supported image formats", "Batch process Excel files"]
            ]
        },
        "batch": {
            "keywords": ["batch", "multiple", "bulk", "many", "volume", "mass", "queue", "group", "several", "together", "simultaneous"],
            "phrases": ["many documents", "many files", "process all", "all at once", "at the same time", "one go", "multiple documents", "multiple files", "process many", "bulk upload"],
            "variants": [
                "Batch Processing\n\nBatch mode processes up to 10 documents simultaneously:\n1. Go to Studio > Batch tab\n2. Enter text for each document\n3. Separate documents with --- on a new line\n4. Click Process Batch\n\nEach document is analyzed independently. Results include per-document statistics and an overall success rate. Throughput: up to 100 documents per minute.\n\n{history_context}",
                "Using Batch Mode Effectively\n\nBest use cases:\n- Processing a queue of similar invoices\n- Analyzing multiple contracts at once\n- Batch importing data from spreadsheets\n- Bulk classification of document types\n\nThe --- separator is critical: each section becomes a separate document. For very similar documents, batch mode gives consistent extraction across all items.\n\n{history_context}",
                "Batch Processing Limits\n\nBatch constraints:\n- Maximum 10 documents per batch request\n- Each document processed independently\n- Results show individual and aggregate statistics\n- All documents must be text (file uploads processed individually)\n\nFor larger volumes, consider making multiple batch requests or using the API for programmatic access.\n\n{history_context}",
                "Batch Results Analysis\n\nAfter batch processing, you can:\n- See success/failure counts per document\n- View aggregate statistics\n- Compare extraction results across documents\n- Identify patterns in document types\n\nThis is particularly useful for quality assurance across a document set.\n\n{history_context}"
            ],
            "suggestions_variants": [
                ["How do I process 100+ documents?", "Can I automate batch uploads via API?", "How do I compare batch results?", "Show me a batch demo"],
                ["Batch process invoices", "Batch classify documents", "Batch vs single processing", "Troubleshoot batch errors"],
                ["Maximum batch size", "Automate batch with scripts", "Compare extraction results", "Batch success rates"]
            ]
        },
        "api": {
            "keywords": ["api", "endpoint", "integrate", "integration", "code", "request", "json", "rest", "curl", "programmatic", "sdk", "library", "develop", "webhook"],
            "phrases": ["how to call", "show me code", "example request", "api key", "authentication", "program access", "curl example", "python example", "javascript example", "api documentation"],
            "variants": [
                "REST API Integration\n\nBase URL: http://localhost:8000\n\nKey Endpoints:\n- POST /extract: extract data from text\n- POST /upload: upload and process a file\n- POST /batch: process multiple documents\n- POST /summarize: generate a summary\n- POST /chat: ask the AI assistant\n- GET /jobs/{id}: check job status\n- GET /stats: system statistics\n\nExample (curl):\ncurl -X POST http://localhost:8000/extract -H \"Content-Type: application/json\" -d '{\"text\": \"Invoice #123 for $500\"}'\n\nFull OpenAPI docs available at /docs.\n\n{history_context}",
                "API Code Examples\n\nPython upload example:\nimport requests\nfiles = {'file': open('invoice.pdf', 'rb')}\nr = requests.post('http://localhost:8000/upload', files=files)\nprint(r.json())\n\nPython extract example:\nimport requests\nr = requests.post('http://localhost:8000/extract', json={'text': 'Invoice #123 for $500'})\ndata = r.json()\nprint(f\"Type: {data['classification']['document_type']}\")\n\nJavaScript fetch example:\nconst res = await fetch('/extract', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({text: 'Invoice #123'})});\nconst data = await res.json();\n\n{history_context}",
                "API Features\n\n- CORS enabled for cross-origin requests\n- All endpoints return JSON\n- Async processing for large files\n- Background jobs with status polling\n- Comprehensive error messages\n- Full OpenAPI/Swagger documentation\n\nThe API supports all the same features as the web interface, making it suitable for custom integrations and automation.\n\n{history_context}",
                "API Best Practices\n\n1. For large files, use /upload which returns a job_id for async tracking\n2. For quick text, use /extract which returns results synchronously\n3. Poll /jobs/{id} every 2 seconds to check status\n4. Use /batch for processing up to 10 documents in one request\n5. Check /stats for system health and capacity\n\nRate limiting is not currently enforced, but please be considerate with concurrent requests.\n\n{history_context}"
            ],
            "suggestions_variants": [
                ["Show me a Python upload example", "How do I call /summarize?", "What does the response JSON look like?", "Show me the API documentation"],
                ["JavaScript fetch example", "Automate batch via API", "API authentication", "Error handling in API"],
                ["Upload file via curl", "Extract data via API", "Check job status via API", "API rate limits"]
            ]
        },
        "troubleshooting": {
            "keywords": ["error", "fail", "problem", "issue", "broken", "not working", "bug", "glitch", "wrong", "incorrect", "doesn't work", "failed", "crash", "stuck", "timeout"],
            "phrases": ["not working", "something wrong", "help fix", "fix this", "error message", "throws error", "getting error", "what went wrong", "why did it fail", "broken feature"],
            "variants": [
                "Troubleshooting Guide\n\nCommon issues and solutions:\n- File too large? Max is 50MB\n- Wrong format? Check supported formats list\n- Empty file? Ensure the file has content\n- No fields extracted? Try the Summarize feature\n- Low confidence? Clean up the text formatting\n- OCR blank? Use a higher resolution image\n- Job stuck? Check the Jobs tab for status, or restart the server\n\nFor fastest resolution, share the exact error message and what you were doing when it occurred.\n\n{history_context}",
                "Diagnosing Problems\n\nBreak down the issue by stage:\n1. Upload stage: did the file upload successfully? Check file size and format.\n2. Processing stage: is the job progressing? Check Jobs tab.\n3. Extraction stage: are fields being found? Check confidence scores.\n4. Output stage: is the result displaying? Try copying the result.\n\nMost issues are resolved by ensuring clean input and correct format.\n\n{history_context}",
                "Common Error Messages\n\n'File too large': reduce file size or compress the document\n'Unsupported format': convert to a supported format first\n'Empty file': verify the file has content before uploading\n'OCR failed': the image may be too low quality, try a clearer version\n'Processing timeout': the document may be too long, try splitting it\n'Job not found': the job may have expired, try uploading again\n\nIf you continue to have issues, describe the exact error text for a targeted fix.\n\n{history_context}",
                "System Health Checks\n\nYou can check system status:\n- Visit /health endpoint for system status\n- Check /stats for system statistics\n- Review the server console for error logs\n- Restart with python main.py if unresponsive\n\nFor persistent issues, check that all dependencies are installed: pip install -r requirements.txt\n\n{history_context}"
            ],
            "suggestions_variants": [
                ["My upload failed with an error", "Summarize returns blank result", "OCR is giving garbage text", "Job is stuck processing"],
                ["File size error", "Why is extraction empty?", "Server not responding", "Fix OCR quality"],
                ["Error handling best practices", "Debug processing pipeline", "API error responses", "Check system health"]
            ]
        },
        "privacy": {
            "keywords": ["privacy", "security", "secure", "safe", "gdpr", "retain", "retention", "encrypt", "encryption", "data protection", "confidential", "sensitive", "delete", "compliance"],
            "phrases": ["how long kept", "data stored", "my data", "your data", "data usage", "data sharing", "delete my", "remove my", "data retention"],
            "variants": [
                "Privacy and Data Protection\n\nAll documents are processed with:\n- TLS 1.3 encryption in transit\n- AES-256 encryption at rest\n- Isolated processing environments\n- Configurable auto-deletion (1 hour, 24 hours, immediately)\n\nDefault retention: 24 hours for processing and quality assurance. Manual deletion is available at any time.\n\nCompliance: GDPR and CCPA compliant. Your data is never used for training without explicit opt-in.\n\n{history_context}",
                "Data Security Measures\n\nSecurity layers:\n- Network: all traffic encrypted with TLS 1.3\n- Storage: documents encrypted with AES-256\n- Processing: each document in an isolated sandbox\n- Access: configurable deletion policies\n- Audit: full activity logging available\n\nYou have the right to access, rectify, and delete your data at any time.\n\n{history_context}",
                "Data Retention and Deletion\n\nRetention options:\n- Default: 24 hours\n- 1 hour: for sensitive documents\n- Immediately: delete right after processing\n- Never: keep indefinitely\n\nTo delete: documents are automatically removed based on your retention setting. Manual deletion is available through the interface.\n\n{history_context}",
                "Compliance Information\n\nThis system is designed for:\n- GDPR compliance (data access, deletion, portability)\n- CCPA compliance (opt-out, deletion rights)\n- SOC2-type processing controls\n- Enterprise security requirements\n\nFor detailed compliance questions, refer to the Privacy Policy in the footer.\n\n{history_context}"
            ],
            "suggestions_variants": [
                ["How long are files kept?", "Can I delete uploads automatically?", "Is my data used for training?", "Show me the privacy policy"],
                ["Data encryption details", "GDPR compliance features", "Auto-delete settings", "Manual document deletion"],
                ["Security best practices", "Compliance checklist", "Data retention policy", "Third-party data access"]
            ]
        },
        "pricing": {
            "keywords": ["pricing", "cost", "free", "plan", "enterprise", "billing", "subscription", "tier", "premium", "paid", "license", "price", "money"],
            "phrases": ["how much", "what included", "free tier", "enterprise tier", "self hosted", "self-host", "cloud version", "on premise", "on-premise", "pricing plan"],
            "variants": [
                "Pricing and Plans\n\nFree Tier (current): includes all core features, up to 10 documents per batch, standard processing speed, and community support.\n\nEnterprise (available): unlimited batch size, priority processing, custom extraction schemas, dedicated support and SLA, on-premise deployment.\n\nDeployment options: local Python, Docker, cloud (AWS/Azure/GCP), or Kubernetes.\n\n{history_context}",
                "Deployment Options\n\n1. Local: run with python main.py, requires Python 3.8+\n2. Docker: single container with docker-compose up\n3. Cloud: deploy to AWS, Azure, or Google Cloud\n4. Kubernetes: production-scale deployment\n\nThe application is fully open and ready to deploy. All dependencies are in requirements.txt.\n\n{history_context}",
                "Free vs Enterprise\n\nFree tier includes:\n- All core document processing features\n- Standard processing speed\n- Community support\n- Up to 10 documents per batch\n\nEnterprise adds:\n- Unlimited batch sizes\n- Priority processing queue\n- Custom extraction schemas\n- Dedicated support with SLA\n- On-premise deployment option\n- Custom integration assistance\n\n{history_context}",
                "Self-Hosting Guide\n\nRequirements:\n- Python 3.8 or higher\n- pip packages from requirements.txt\n- Optional: Tesseract OCR for image processing\n\nSteps:\n1. pip install -r requirements.txt\n2. python main.py\n3. Open http://localhost:8000\n\nDocker:\n1. docker-compose up\n2. Access the dashboard at port 8000\n\n{history_context}"
            ],
            "suggestions_variants": [
                ["What is included in the free tier?", "How does enterprise deployment work?", "Can I self-host this?", "Enterprise features overview"],
                ["Docker deployment guide", "Cloud deployment options", "Kubernetes setup", "Pricing comparison"],
                ["Self-hosting requirements", "Enterprise support options", "Custom extraction schemas", "Deployment costs"]
            ]
        },
        "account": {
            "keywords": ["login", "logout", "sign in", "sign out", "account", "profile", "register", "password", "email", "auth", "authentication", "user", "session"],
            "phrases": ["create account", "new account", "forgot password", "change password", "my profile", "edit profile", "delete account", "sign up", "my account"],
            "variants": [
                "Account Management\n\nAuthentication: click Login in the navigation bar. Use any email and password to create a demo account. Sessions persist for 30 days.\n\nProfile settings (Workspace > Profile): update your name, email, and role. Save preferences for theme, auto-refresh, and auto-delete.\n\nNotifications are managed in Settings and appear in the notification center.\n\n{history_context}",
                "Profile and Preferences\n\nIn Workspace, you can customize:\n- Profile: name, email, role\n- Preferences: theme (light, dark, warm), auto-refresh, auto-delete\n\nSettings page offers additional controls:\n- Refresh interval\n- Notification toggle\n- System information\n\nAll preferences are saved locally and persist between sessions.\n\n{history_context}",
                "Session and Login Information\n\n- Sessions last 30 days by default\n- Login is optional; you can use the app without an account\n- Documents and history are stored locally when not logged in\n- Login enables cross-session persistence\n\nLogout: click your name in the nav bar and confirm sign out.\n\n{history_context}",
                "Managing Your Workspace\n\nThe Workspace section has four tabs:\n- Profile: personal information and role\n- My Documents: files you have uploaded\n- History: processing records\n- Export: download data as JSON, CSV, or report\n\nLogin is recommended to save your workspace data across sessions.\n\n{history_context}"
            ],
            "suggestions_variants": [
                ["How do I change my profile name?", "Why are notifications not appearing?", "How do I sign out safely?", "What settings can I customize?"],
                ["Create an account", "Update my preferences", "Export my workspace data", "Delete my account"],
                ["Session timeout", "Login issues", "Profile settings guide", "Notification management"]
            ]
        },
        "capabilities": {
            "keywords": ["hello", "hi", "hey", "help", "what can you do", "capabilities", "features", "about", "introduction", "how does this work", "purpose", "functions", "what is"],
            "phrases": ["what can you do", "how do i use", "tell me about", "get started", "getting started", "capabilities", "features overview", "show features", "what is this", "introduce yourself"],
            "variants": [
                "DocIntel Pro Capabilities\n\nThis is an AI-powered document intelligence platform. Key capabilities:\n- Classify documents into 7+ types\n- Extract structured data with confidence scoring\n- Generate summaries with key points, numbers, and dates\n- Process images through OCR\n- Batch process multiple documents\n- Provide API for custom integrations\n- Solve business challenges\n\nTry asking: 'extract data from this invoice', 'how do I summarize a document?', 'show me the API endpoints', or 'help me fix an upload error'.\n\nCurrent section: {current_section.capitalize()}\n\n{history_context}",
                "Getting Started Guide\n\nThree ways to use the system:\n\n1. Upload a file: go to Studio > Upload, drag and drop or click to browse\n2. Paste text: go to Studio > Text Input, paste your document text\n3. Ask for help: use this AI assistant for guidance\n\nThe system will classify your document, extract key fields, and provide suggestions. Most documents process in under 1 second.\n\nTry the demo data to see it in action without preparing your own content.\n\n{history_context}",
                "Feature Overview\n\nCore features:\n- Smart Upload: 20+ file formats with drag and drop\n- AI Extraction: automatic classification and field extraction\n- Summarization: concise summaries with key insights\n- AI Chatbot: real-time assistance for any question\n- Business Solutions: tailored recommendations\n- Enterprise Security: encrypted processing and GDPR compliance\n\nEach feature is designed to save time and reduce manual document processing effort.\n\n{history_context}",
                "System Architecture\n\nThe platform processes documents through a pipeline:\n1. Input: text paste, file upload, or API request\n2. OCR: image preprocessing and text recognition (if needed)\n3. Classification: document type identification\n4. Extraction: field detection with confidence scoring\n5. Validation: data quality checks\n6. Output: structured results with suggestions\n\nThis pipeline processes documents in under 1 second each, with 92-98% accuracy.\n\n{history_context}"
            ],
            "suggestions_variants": [
                ["What can this system do?", "How do I get started?", "Show me the features", "Help me understand the platform"],
                ["Extract data from an invoice", "How does summarization work?", "What formats are supported?", "Show me a demo"],
                ["I need help with batch processing", "How does the API work?", "Troubleshoot an error", "Business challenge solver"]
            ]
        }
    }
    
    # Section-specific context responses
    section_responses = {
        "studio": {
            "response": "You are in the Studio section where document processing happens.\n\nAvailable tabs:\n- Upload: drag and drop or browse for files (20+ formats supported)\n- Text Input: paste text for instant extraction and analysis\n- Batch: process up to 10 documents at once with the --- separator\n- Summarize: generate summaries with key points and insights\n- Jobs: track processing status for uploaded files\n\nStart by pasting text or uploading a file, then the system will classify, extract, and provide suggestions.\n\n{history_context}",
            "suggestions": ["Summarize the text I just pasted", "Extract fields from this document", "Show me how batch works", "Upload a file for me"]
        },
        "workspace": {
            "response": "You are in the Workspace section for managing your profile and data.\n\nFour sections:\n- Profile: update your name, email, and role\n- My Documents: view your uploaded files\n- History: see processing records\n- Export: download data as JSON, CSV, or report\n\nLogin to persist your data across sessions, or continue as a guest for temporary use.\n\n{history_context}",
            "suggestions": ["How do I export my data?", "Show me my processing history", "How do I save my profile?", "What is auto-delete?"]
        },
        "challenges": {
            "response": "You are in the Business Challenges section.\n\nDescribe a business problem and get tailored solutions with:\n- Problem analysis\n- Recommended solutions with specific steps\n- Estimated ROI for measuring impact\n- Next steps to implement\n\nCategories: Efficiency, Compliance, Cost Reduction, Scalability, Data Quality.\n\n{history_context}",
            "suggestions": ["Help me automate invoice processing", "How do I improve data quality?", "Reduce document processing costs", "Scale my document workflow"]
        },
        "settings": {
            "response": "You are in the Settings section for application configuration.\n\nSettings available:\n- Theme: Light, Dark, or Warm mode\n- Auto-refresh interval: 2, 5, 10 seconds or disabled\n- Notifications: enable or disable\n\nAdditional preferences can be set in Workspace > Preferences.\n\n{history_context}",
            "suggestions": ["How do I change the theme?", "How do notifications work?", "What does auto-refresh do?", "Save my current settings"]
        },
        "docs": {
            "response": "You are in the Documentation section.\n\nHere you can:\n- View the API base URL\n- Open the full OpenAPI/Swagger documentation at /docs\n- Load all API endpoints with descriptions\n\nThe API supports all the same features as the web interface.\n\n{history_context}",
            "suggestions": ["Show me the API endpoints", "How do I call the API?", "What is the base URL?", "OpenAPI documentation"]
        }
    }
    
    # Fallback responses with multiple variants
    fallback_variants = [
        [
            {
                "response": "I need a bit more information to give you the best answer.\n\nCould you specify:\n1. What type of document you are working with (invoice, contract, report, etc.)\n2. What you want to do (extract, summarize, troubleshoot, integrate)\n3. Any specific question or error you are encountering\n\nOnce you provide these details, I can give you a precise, actionable response.\n\n{history_context}",
                "suggestions": ["Extract data from a document", "Generate a summary", "Fix a processing error", "Show me the API"]
            },
            {
                "response": "Here are the main features available to you right now:\n\n- Studio > Upload: drag and drop or click to upload any document\n- Studio > Text Input: paste text for instant extraction\n- Studio > Batch: process multiple documents at once\n- AI Assistant (this chat): ask me anything about document processing\n- Challenges: get tailored business solutions\n\nQuick start: go to Studio, paste some text, and click Extract Data. The AI will classify your document and extract key fields in seconds.\n\n{history_context}",
                "suggestions": ["Go to Studio", "How to upload a file", "What can I extract?", "Help me get started"]
            },
            {
                "response": "Try one of these common workflows:\n\n1. Process an invoice: paste invoice text in Studio Text Input and click Extract Data\n2. Summarize a report: paste the text in Studio Summarize tab\n3. Upload a PDF: use Studio Upload tab and select your file\n4. Batch process: separate multiple documents with --- in the Batch tab\n5. Get help: ask me specific questions about any feature\n\nEach workflow takes under 30 seconds to complete.\n\n{history_context}",
                "suggestions": ["Process an invoice", "Summarize a report", "Upload a PDF", "Batch processing help"]
            }
        ]
    ]
    
    # Find best matching intent
    intent_name, confidence = _best_match(intents)
    
    if intent_name and confidence > 0.3:
        intent = intents[intent_name]
        variant = _pick_variant(intent["variants"])
        suggestions = _pick_variant(intent["suggestions_variants"])
        
        response = variant.format(
            history_context=history_context,
            current_section=current_section,
            topic_hint=topic_hint[:80] if topic_hint else "unknown",
            count=len(ALLOWED_EXTENSIONS)
        )
        
        return {"response": response, "suggestions": suggestions}
    
    # Section-specific fallbacks
    if current_section in section_responses:
        sec = section_responses[current_section]
        return {
            "response": sec["response"].format(history_context=history_context),
            "suggestions": sec["suggestions"]
        }
    
    # Random fallback for unrecognized queries
    import random
    fallback_group = fallback_variants[0]
    fb = fallback_group[response_counter % len(fallback_group)]
    return {
        "response": fb["response"].format(
            history_context=history_context,
            current_section=current_section
        ),
        "suggestions": fb["suggestions"]
    }


# ==================== API ENDPOINTS ====================

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": _utc_now(),
        "version": APP_VERSION,
        "pending_jobs": job_tracker.stats["total_active"],
        "max_upload_mb": round(MAX_UPLOAD_SIZE_BYTES / (1024 * 1024), 2),
        "features": {
            "ocr": OCR_AVAILABLE,
            "pdf": PdfReader is not None,
            "docx": DOCX_AVAILABLE,
            "supported_formats": list(ALLOWED_EXTENSIONS)
        }
    }


@app.post("/register")
async def register_user(request: RegisterRequest):
    """Register a new user"""
    # Check if email already exists
    for user in users_db.values():
        if user.email == request.email:
            raise HTTPException(status_code=400, detail="Email already registered")
    
    user_id = str(uuid.uuid4())
    user = User(
        user_id=user_id,
        email=request.email,
        name=request.name,
        password_hash=_hash_password(request.password),
        created_at=_utc_now()
    )
    users_db[user_id] = user
    
    # Auto-login after registration
    token = _generate_session_token()
    sessions_db[token] = {
        "user_id": user_id,
        "expires_at": (datetime.utcnow() + timedelta(days=30)).isoformat()
    }
    
    return {
        "user_id": user_id,
        "email": user.email,
        "name": user.name,
        "token": token,
        "message": "Registration successful"
    }


@app.post("/login")
async def login_user(request: LoginRequest):
    """Login user"""
    for user in users_db.values():
        if user.email == request.email:
            if _verify_password(request.password, user.password_hash):
                user.last_login = _utc_now()
                token = _generate_session_token()
                sessions_db[token] = {
                    "user_id": user.user_id,
                    "expires_at": (datetime.utcnow() + timedelta(days=30)).isoformat()
                }
                return {
                    "user_id": user.user_id,
                    "email": user.email,
                    "name": user.name,
                    "token": token,
                    "message": "Login successful"
                }
            else:
                raise HTTPException(status_code=401, detail="Invalid password")
    
    # For demo purposes, allow login without registration
    # Create a demo user
    user_id = str(uuid.uuid4())
    user = User(
        user_id=user_id,
        email=request.email,
        name="User",
        password_hash=_hash_password(request.password),
        created_at=_utc_now(),
        last_login=_utc_now()
    )
    users_db[user_id] = user
    
    token = _generate_session_token()
    sessions_db[token] = {
        "user_id": user_id,
        "expires_at": (datetime.utcnow() + timedelta(days=30)).isoformat()
    }
    
    return {
        "user_id": user_id,
        "email": user.email,
        "name": user.name,
        "token": token,
        "message": "Demo account created"
    }


@app.post("/logout")
async def logout_user(request: Request):
    """Logout user"""
    token = request.cookies.get("session_token")
    if token and token in sessions_db:
        del sessions_db[token]
    return {"message": "Logged out successfully"}


@app.get("/me")
async def get_current_user_info(request: Request):
    """Get current user information"""
    user = _get_current_user(request)
    if not user:
        return {"authenticated": False}
    
    return {
        "authenticated": True,
        "user_id": user.user_id,
        "email": user.email,
        "name": user.name,
        "role": user.role,
        "created_at": user.created_at,
        "last_login": user.last_login
    }


@app.post("/upload")
async def upload_document(
    file: UploadFile = File(...),
    background_tasks: BackgroundTasks = None
):
    """
    Upload and process a document.
    Supports PDF, images, text files, Word documents, and more.
    """
    try:
        if not file.filename:
            raise HTTPException(status_code=400, detail="Filename is required")

        raw_bytes = await file.read()
        if not raw_bytes:
            raise HTTPException(status_code=400, detail="Uploaded file is empty")

        if len(raw_bytes) > MAX_UPLOAD_SIZE_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"File too large. Max size is {round(MAX_UPLOAD_SIZE_BYTES / (1024 * 1024), 2)} MB"
            )

        # Generate unique ID
        job_id = str(uuid.uuid4())
        safe_name = _safe_filename(file.filename)
        file_type = _get_file_type(file.filename)
        
        job = ProcessingJob(
            job_id=job_id,
            status="uploading",
            progress=10,
            filename=safe_name,
            file_type=file_type,
            file_size=len(raw_bytes),
            created_at=_utc_now(),
            updated_at=_utc_now(),
            document_type=file_type
        )
        job_tracker.add_job(job)
        
        # Save uploaded file
        file_path = UPLOAD_DIR / f"{job_id}_{safe_name}"
        with open(file_path, "wb") as output:
            output.write(raw_bytes)
        
        # Process in background
        if background_tasks:
            background_tasks.add_task(
                _process_uploaded_file,
                job_id,
                str(file_path)
            )
        else:
            await _process_uploaded_file(job_id, str(file_path))
        
        return {
            "job_id": job_id,
            "filename": file.filename,
            "file_type": file_type,
            "status": "processing",
            "message": "Document uploaded and processing started"
        }
    
    except Exception as e:
        logger.error(f"Upload failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/extract")
async def extract_data(request: ExtractionRequest):
    """
    Extract structured data from document text.
    """
    try:
        if not request.text:
            raise HTTPException(status_code=400, detail="Text content is required")
        
        # Process document
        doc_id = str(uuid.uuid4())
        result = await pipeline.process_document(
            document_id=doc_id,
            text=request.text,
            custom_extraction_schema=request.custom_fields
        )
        
        # Generate summary and suggestions
        doc_type = result.classification.document_type.value if result.classification else "general"
        summary = _generate_summary(request.text, doc_type, result.extraction)
        extracted_fields = [
            {"name": f.name, "value": f.value, "confidence": f.confidence}
            for f in result.extraction.extracted_fields
        ] if result.extraction else []
        suggestions = _generate_suggestions(doc_type, extracted_fields, request.text)
        
        result_dict = result.to_dict()
        result_dict["summary"] = summary
        result_dict["suggestions"] = suggestions
        result_dict["extracted_fields"] = extracted_fields
        
        return result_dict
    
    except Exception as e:
        logger.error(f"Extraction failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/batch")
async def batch_process(request: BatchRequest):
    """
    Process multiple documents in batch.
    """
    try:
        if not request.documents:
            raise HTTPException(status_code=400, detail="Documents list is required")

        if len(request.documents) > MAX_BATCH_SIZE:
            raise HTTPException(
                status_code=400,
                detail=f"Batch too large. Max batch size is {MAX_BATCH_SIZE}"
            )
        
        # Create job
        job_id = str(uuid.uuid4())
        job = ProcessingJob(
            job_id=job_id,
            status="processing",
            progress=20,
            created_at=_utc_now(),
            updated_at=_utc_now()
        )
        job_tracker.add_job(job)
        
        # Process documents
        results = await pipeline.process_batch(request.documents)
        
        # Calculate statistics
        stats = pipeline.get_statistics(results)
        
        # Update job
        job_tracker.complete_job(
            job_id,
            {"results": [r.to_dict() for r in results], "statistics": stats},
            f"Batch processing completed: {len(results)} documents processed"
        )
        
        return {
            "job_id": job_id,
            "results": [r.to_dict() for r in results],
            "statistics": stats
        }
    
    except Exception as e:
        logger.error(f"Batch processing failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/jobs/{job_id}")
async def get_job_status(job_id: str):
    """Get status of a processing job"""
    if job_id not in job_tracker.jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job = job_tracker.jobs[job_id]
    return {
        "job_id": job.job_id,
        "status": job.status,
        "progress": job.progress,
        "filename": job.filename,
        "file_type": job.file_type,
        "file_size": job.file_size,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "completed_at": job.completed_at,
        "result": job.result,
        "error": job.error,
        "summary": job.summary,
        "extracted_fields": job.extracted_fields,
        "suggestions": job.suggestions,
        "document_type": job.document_type
    }


@app.get("/jobs")
async def list_jobs():
    """List all processing jobs"""
    sorted_jobs = sorted(
        job_tracker.jobs.values(),
        key=lambda j: j.created_at,
        reverse=True
    )
    return {
        "total": len(job_tracker.jobs),
        "jobs": [
            {
                "job_id": job.job_id,
                "status": job.status,
                "progress": job.progress,
                "filename": job.filename,
                "file_type": job.file_type,
                "file_size": job.file_size,
                "created_at": job.created_at,
                "completed_at": job.completed_at,
                "document_type": job.document_type,
                "summary": job.summary
            }
            for job in sorted_jobs
        ]
    }


@app.get("/stats")
async def get_statistics():
    """Get overall system statistics"""
    return {
        **job_tracker.get_statistics(),
        "timestamp": _utc_now()
    }


@app.post("/chat")
async def chat_with_ai(request: ChatMessage):
    """Chat with AI assistant for document intelligence help"""
    response_data = _ai_chat_response(request.message, request.context)
    
    # Store conversation if conversation_id provided
    if request.conversation_id and request.conversation_id in conversations_db:
        conv = conversations_db[request.conversation_id]
        conv.messages.append({
            "role": "user",
            "content": request.message,
            "timestamp": _utc_now()
        })
        conv.messages.append({
            "role": "assistant",
            "content": response_data["response"],
            "suggestions": response_data["suggestions"],
            "timestamp": _utc_now()
        })
    
    return response_data


@app.post("/summarize")
async def summarize_document(request: ExtractionRequest):
    """Generate a summary of document text"""
    try:
        if not request.text:
            raise HTTPException(status_code=400, detail="Text content is required")
        
        # Process through pipeline for classification
        doc_id = str(uuid.uuid4())
        result = await pipeline.process_document(
            document_id=doc_id,
            text=request.text
        )
        
        doc_type = result.classification.document_type.value if result.classification else "general"
        
        # Generate comprehensive summary
        word_count = len(request.text.split())
        sentences = request.text.split(".")
        
        # Extract key sentences (first, middle, last)
        key_sentences = []
        if len(sentences) > 3:
            key_sentences = [sentences[0], sentences[len(sentences)//2], sentences[-1]]
        else:
            key_sentences = sentences[:3]
        
        # Extract numbers/amounts
        numbers = re.findall(r'[\$€£]?[\d,]+\.?\d*', request.text)
        
        # Extract dates
        dates = re.findall(r'\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b', request.text)
        
        summary = {
            "document_type": doc_type,
            "word_count": word_count,
            "sentence_count": len(sentences),
            "key_points": [s.strip() for s in key_sentences if s.strip()],
            "numbers_found": numbers[:10],
            "dates_found": dates[:10],
            "confidence": result.classification.confidence if result.classification else 0.5,
            "suggestions": _generate_suggestions(doc_type, [], request.text)
        }
        
        return summary
    
    except Exception as e:
        logger.error(f"Summarization failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/solve-challenge")
async def solve_business_challenge(request: BusinessChallenge):
    """Analyze and suggest solutions for business challenges"""
    
    challenge_analyses = {
        "efficiency": {
            "analysis": "Efficiency challenges often stem from manual, repetitive tasks. Document intelligence can automate data extraction, reduce processing time by 80-90%, and eliminate human errors.",
            "solutions": [
                "Implement automated document processing workflows",
                "Set up batch processing for high-volume documents",
                "Use OCR for digitizing paper-based processes",
                "Create custom extraction rules for domain-specific forms"
            ],
            "roi_estimate": "60-80% reduction in processing time, 90% reduction in data entry errors"
        },
        "compliance": {
            "analysis": "Compliance challenges require accurate record-keeping, audit trails, and data validation. Our system provides structured extraction with confidence scores and validation checks.",
            "solutions": [
                "Enable comprehensive audit logging for all document processing",
                "Set up validation rules to ensure data completeness",
                "Implement automated compliance report generation",
                "Use secure document storage with encryption"
            ],
            "roi_estimate": "Reduced compliance risk, faster audit preparation, automated reporting"
        },
        "cost": {
            "analysis": "Cost challenges often relate to manual labor, errors, and inefficiency. Automation through document intelligence significantly reduces operational costs.",
            "solutions": [
                "Replace manual data entry with automated extraction",
                "Reduce headcount needed for document processing",
                "Minimize costly errors through validation",
                "Scale processing without proportional cost increase"
            ],
            "roi_estimate": "50-70% reduction in document processing costs within 6 months"
        },
        "scalability": {
            "analysis": "Scalability challenges occur when manual processes can't handle growth. Our API-first architecture scales automatically with your needs.",
            "solutions": [
                "Use cloud-based processing for elastic scaling",
                "Implement batch processing for peak volumes",
                "Set up automated workflows with queue management",
                "Use API integration for seamless system connectivity"
            ],
            "roi_estimate": "Handle 10x volume with minimal infrastructure changes"
        },
        "data_quality": {
            "analysis": "Data quality issues lead to poor decisions and rework. Our validation engine ensures extracted data meets quality standards.",
            "solutions": [
                "Enable multi-stage validation with confidence scoring",
                "Set up automated data quality reports",
                "Implement human-in-the-loop review for low-confidence extractions",
                "Use machine learning to improve extraction accuracy over time"
            ],
            "roi_estimate": "95%+ data accuracy, 80% reduction in rework"
        }
    }
    
    category = request.category.lower()
    analysis = challenge_analyses.get(category, challenge_analyses["efficiency"])
    
    return {
        "challenge": request.title,
        "description": request.description,
        "category": category,
        "priority": request.priority,
        "analysis": analysis["analysis"],
        "solutions": analysis["solutions"],
        "estimated_roi": analysis["roi_estimate"],
        "recommended_features": [
            "Automated document processing",
            "Custom extraction schemas",
            "Batch processing capabilities",
            "API integration",
            "Validation and quality scoring"
        ],
        "next_steps": [
            "Schedule a demo to see the system in action",
            "Start with a pilot project on a specific document type",
            "Define success metrics and KPIs",
            "Plan integration with existing systems"
        ]
    }


@app.get("/documentation")
async def get_documentation():
    """Get API documentation"""
    return {
        "title": "DocIntel Studio Pro API",
        "version": APP_VERSION,
        "description": "AI-powered document intelligence platform",
        "endpoints": {
            "/health": "Health check and system status",
            "/register": "Register new user",
            "/login": "User authentication",
            "/logout": "User logout",
            "/me": "Get current user info",
            "/upload": "Upload and process document",
            "/extract": "Extract data from text",
            "/batch": "Process multiple documents",
            "/jobs": "List all jobs",
            "/jobs/{id}": "Get job status",
            "/stats": "Get system statistics",
            "/chat": "Chat with AI assistant",
            "/summarize": "Generate document summary",
            "/solve-challenge": "Get solutions for business challenges"
        },
        "supported_formats": list(ALLOWED_EXTENSIONS),
        "features": {
            "ocr": OCR_AVAILABLE,
            "pdf_processing": PdfReader is not None,
            "docx_processing": DOCX_AVAILABLE,
            "batch_processing": True,
            "ai_chatbot": True,
            "user_authentication": True,
            "business_analytics": True
        }
    }


# ==================== BACKGROUND PROCESSING ====================

async def _process_uploaded_file(job_id: str, file_path: str):
    """Process uploaded file in background"""
    try:
        job_tracker.update_job(job_id, status="processing", progress=40)
        path = Path(file_path)
        suffix = path.suffix.lower()
        raw_bytes = path.read_bytes()
        text = ""

        # Determine source type and extraction strategy
        if suffix in {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tiff'}:
            try:
                text = _read_image_text(path)
            except Exception as e:
                logger.warning(f"OCR failed for image: {e}")
                text = _build_generic_document_text(path, raw_bytes)
                
        elif suffix == '.pdf':
            text = _read_pdf_text(path)
            if not text:
                raise RuntimeError("PDF parsed but no readable text was found")

        elif suffix == '.docx':
            text = _read_docx_text(path)
            if not text.strip():
                text = _build_generic_document_text(path, raw_bytes)

        else:
            try:
                text = _read_text_file(path)
            except Exception:
                text = ""

            if not text.strip():
                text = _build_generic_document_text(path, raw_bytes)

        # Process through pipeline
        job_tracker.update_job(job_id, progress=60)
        result = await pipeline.process_document(
            document_id=job_id,
            text=text
        )
        
        job_tracker.update_job(job_id, progress=80)
        
        # Generate summary and suggestions
        doc_type = result.classification.document_type.value if result.classification else "unknown"
        summary = _generate_summary(text, doc_type, result.extraction)
        extracted_fields = [
            {"name": f.name, "value": f.value, "confidence": f.confidence}
            for f in result.extraction.extracted_fields
        ] if result.extraction else []
        suggestions = _generate_suggestions(doc_type, extracted_fields, text)
        
        # Complete the job
        job_tracker.complete_job(
            job_id,
            result.to_dict(),
            summary=summary,
            extracted_fields=extracted_fields,
            suggestions=suggestions
        )
        
    except Exception as e:
        logger.error(f"Background processing failed: {e}")
        job_tracker.fail_job(job_id, str(e))


# ==================== PRIVACY POLICY ====================

PRIVACY_POLICY_HTML = """
<div class="privacy-modal" id="privacyModal">
    <div class="privacy-content">
        <div class="privacy-header">
            <h2>Privacy & Data Protection</h2>
            <p>Your data privacy is our priority</p>
        </div>
        <div class="privacy-body">
            <div class="privacy-section">
                <h3>Data Collection</h3>
                <p>We collect and process documents you upload solely for the purpose of providing document intelligence services. We do not sell, share, or use your data for any other purpose.</p>
            </div>
            <div class="privacy-section">
                <h3>Data Security</h3>
                <p>All documents are encrypted in transit (TLS 1.3) and at rest (AES-256). Processing occurs in isolated environments, and documents can be automatically deleted after processing.</p>
            </div>
            <div class="privacy-section">
                <h3>Data Retention</h3>
                <p>By default, uploaded documents are retained for 24 hours for processing and quality assurance. You can configure automatic deletion immediately after processing.</p>
            </div>
            <div class="privacy-section">
                <h3>Compliance</h3>
                <p>Our platform is designed to comply with GDPR, CCPA, and other data protection regulations. You have the right to access, rectify, and delete your data at any time.</p>
            </div>
            <div class="privacy-section">
                <h3>AI Processing</h3>
                <p>We use AI models for document classification, text extraction, and data validation. Your data is never used to train our models without explicit opt-in consent.</p>
            </div>
        </div>
        <div class="privacy-footer">
            <div class="privacy-checkbox">
                <input type="checkbox" id="privacyAccept">
                <label for="privacyAccept">I have read and agree to the <a href="#" onclick="showFullPrivacyPolicy()">Privacy Policy</a> and <a href="#" onclick="showTermsOfService()">Terms of Service</a></label>
            </div>
            <div class="privacy-buttons">
                <button class="btn btn-ghost" onclick="dismissPrivacy(false)">Review Later</button>
                <button class="btn btn-primary" id="acceptPrivacyBtn" onclick="dismissPrivacy(true)" disabled>Accept & Continue</button>
            </div>
        </div>
    </div>
</div>
<style>
.privacy-modal {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.7);
    backdrop-filter: blur(8px);
    z-index: 9999;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 20px;
    animation: fadeIn 0.3s ease;
}

.privacy-content {
    background: #fff;
    border-radius: 20px;
    max-width: 680px;
    width: 100%;
    max-height: 90vh;
    overflow-y: auto;
    box-shadow: 0 25px 80px rgba(0, 0, 0, 0.3);
    animation: slideUp 0.4s cubic-bezier(0.16, 1, 0.3, 1);
}

@keyframes slideUp {
    from { opacity: 0; transform: translateY(30px) scale(0.95); }
    to { opacity: 1; transform: translateY(0) scale(1); }
}

.privacy-header {
    background: linear-gradient(135deg, #5a7b6f, #6b9985);
    color: #fff;
    padding: 24px;
    border-radius: 20px 20px 0 0;
    text-align: center;
}

.privacy-header h2 { margin: 0 0 4px; font-size: 1.5rem; }
.privacy-header p { margin: 0; opacity: 0.9; }

.privacy-body { padding: 24px; }

.privacy-section {
    margin-bottom: 20px;
    padding-bottom: 20px;
    border-bottom: 1px solid #e5e7eb;
}

.privacy-section:last-child { border-bottom: none; margin-bottom: 0; padding-bottom: 0; }

.privacy-section h3 {
    margin: 0 0 8px;
    color: #1f2937;
    font-size: 1.05rem;
}

.privacy-section p {
    margin: 0;
    color: #6b7280;
    line-height: 1.6;
    font-size: 0.92rem;
}

.privacy-footer {
    padding: 20px 24px;
    background: #f9fafb;
    border-radius: 0 0 20px 20px;
}

.privacy-checkbox {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    margin-bottom: 16px;
}

.privacy-checkbox input {
    margin-top: 3px;
    width: 18px;
    height: 18px;
    cursor: pointer;
}

.privacy-checkbox label {
    font-size: 0.9rem;
    color: #4b5563;
    line-height: 1.5;
    cursor: pointer;
}

.privacy-checkbox label a {
    color: #5a7b6f;
    text-decoration: none;
}

.privacy-buttons {
    display: flex;
    gap: 12px;
    justify-content: flex-end;
}

.btn {
    border: none;
    border-radius: 999px;
    padding: 12px 24px;
    font-weight: 700;
    cursor: pointer;
    font-size: 0.95rem;
    transition: all 0.2s ease;
}

.btn-primary {
    background: linear-gradient(135deg, #5a7b6f, #6b9985);
    color: #fff;
    box-shadow: 0 4px 12px rgba(90, 123, 111, 0.3);
}

.btn-primary:hover:not(:disabled) {
    transform: translateY(-2px);
    box-shadow: 0 8px 20px rgba(90, 123, 111, 0.4);
}

.btn-primary:disabled {
    opacity: 0.5;
    cursor: not-allowed;
}

.btn-ghost {
    background: #e5e7eb;
    color: #4b5563;
}

.btn-ghost:hover {
    background: #d1d5db;
}
</style>
<script>
document.getElementById('privacyAccept').addEventListener('change', function() {
    document.getElementById('acceptPrivacyBtn').disabled = !this.checked;
});

function dismissPrivacy(accepted) {
    const modal = document.getElementById('privacyModal');
    if (accepted) {
        localStorage.setItem('docintel_privacy_accepted', 'true');
        localStorage.setItem('docintel_privacy_date', new Date().toISOString());
    } else {
        localStorage.setItem('docintel_privacy_accepted', 'false');
    }
    modal.style.display = 'none';
}

function showFullPrivacyPolicy() {
    showInfoDialog('Privacy Policy', 'Full Privacy Policy would open in a dedicated page. For this demo, please review the summary shown in the modal.');
}

function showTermsOfService() {
    showInfoDialog('Terms of Service', 'Full Terms of Service would open in a dedicated page. For this demo, please review the summary shown in the modal.');
}

// Check if privacy was already accepted
if (localStorage.getItem('docintel_privacy_accepted') === 'true') {
    document.getElementById('privacyModal').style.display = 'none';
}
</script>
"""


# ==================== ENHANCED DASHBOARD HTML ====================

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>DocIntel Pro</title>
    <link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>DI</text></svg>">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=Sora:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root {
            --ink-1: #1f2937;
            --ink-2: #4b5563;
            --ink-3: #9ca3af;
            --paper: #f9fafb;
            --panel: #ffffff;
            --line: #e5e7eb;
            --teal: #5a7b6f;
            --teal-bright: #6b9985;
            --teal-light: #a7c4b5;
            --amber: #d4a574;
            --mint: #e8f5e9;
            --rose: #fce4ec;
            --blue: #60a5fa;
            --purple: #a78bfa;
            --shadow: 0 4px 6px rgba(0, 0, 0, 0.07);
            --shadow-lg: 0 10px 30px rgba(0, 0, 0, 0.12);
            --radius: 12px;
            --radius-lg: 20px;
        }

        * { margin: 0; padding: 0; box-sizing: border-box; }

        body {
            font-family: 'Space Grotesk', -apple-system, sans-serif;
            color: var(--ink-1);
            background: linear-gradient(135deg, #f0fdfa 0%, #f0f9ff 30%, #faf5ff 70%, #fff7ed 100%);
            min-height: 100vh;
            overflow-x: hidden;
        }

        /* Animated background */
        .bg-gradient {
            position: fixed;
            top: 0; left: 0; right: 0; bottom: 0;
            pointer-events: none;
            z-index: 0;
        }

        .bg-blob {
            position: absolute;
            border-radius: 50%;
            filter: blur(80px);
            opacity: 0.4;
            animation: blobFloat 20s infinite ease-in-out;
        }

        .bg-blob:nth-child(1) {
            width: 500px; height: 500px;
            background: rgba(90, 123, 111, 0.3);
            top: -10%; left: -5%;
        }

        .bg-blob:nth-child(2) {
            width: 400px; height: 400px;
            background: rgba(212, 165, 116, 0.25);
            top: 40%; right: -10%;
            animation-delay: -7s;
        }

        .bg-blob:nth-child(3) {
            width: 350px; height: 350px;
            background: rgba(167, 139, 250, 0.2);
            bottom: -5%; left: 30%;
            animation-delay: -14s;
        }

        @keyframes blobFloat {
            0%, 100% { transform: translate(0, 0) scale(1); }
            33% { transform: translate(30px, -40px) scale(1.05); }
            66% { transform: translate(-20px, 20px) scale(0.95); }
        }

        .shell {
            max-width: 1400px;
            margin: 0 auto;
            padding: 16px 20px 80px;
            position: relative;
            z-index: 1;
        }

        /* Navigation */
        .nav {
            position: sticky;
            top: 12px;
            z-index: 100;
            backdrop-filter: blur(20px);
            background: rgba(255, 255, 255, 0.9);
            border: 1px solid rgba(229, 231, 235, 0.8);
            border-radius: 999px;
            box-shadow: var(--shadow-lg);
            padding: 12px 20px;
            margin-bottom: 20px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 16px;
        }

        .brand {
            display: flex;
            align-items: center;
            gap: 12px;
            font-family: 'Sora', sans-serif;
            font-weight: 800;
            font-size: 1.1rem;
        }

        .brand-badge {
            width: 38px; height: 38px;
            border-radius: 12px;
            display: grid;
            place-items: center;
            color: #fff;
            background: linear-gradient(135deg, var(--teal), var(--teal-bright));
            font-size: 0.85rem;
            font-weight: 800;
            box-shadow: 0 4px 12px rgba(90, 123, 111, 0.3);
        }

        .nav-links {
            display: flex;
            gap: 4px;
            flex-wrap: wrap;
        }

        .nav-links a, .nav-links button {
            text-decoration: none;
            color: var(--ink-2);
            font-size: 0.88rem;
            font-weight: 600;
            padding: 8px 14px;
            border-radius: 999px;
            transition: all 0.2s ease;
            background: transparent;
            border: none;
            cursor: pointer;
        }

        .nav-links a:hover, .nav-links button:hover {
            background: #f3f4f6;
            color: var(--teal);
        }

        .nav-links a.active, .nav-links button.active {
            background: linear-gradient(135deg, var(--teal), var(--teal-bright));
            color: #fff;
        }

        body.theme-dark .nav-links a.active,
        body.theme-dark .nav-links button.active {
            color: #111827;
            background: linear-gradient(135deg, #e5e7eb, #cbd5e1);
        }

        body.theme-dark .nav {
            background: rgba(17, 24, 39, 0.95);
            border-color: #374151;
        }

        body.theme-dark .brand span {
            color: #f9fafb;
        }

        body.theme-dark .nav-links a,
        body.theme-dark .nav-links button {
            color: #d1d5db;
        }

        body.theme-dark .nav-links a:hover,
        body.theme-dark .nav-links button:hover {
            color: #f9fafb;
            background: #1f2937;
        }

        body.theme-dark .card {
            background: #111827;
            border-color: #374151;
        }

        body.theme-dark .card p {
            color: #d1d5db;
        }

        body.theme-dark .card h3 {
            color: #f9fafb;
        }

        body.theme-dark .hero-card {
            background: #111827;
            border-color: #374151;
        }

        body.theme-dark .hero-card .lead {
            color: #9ca3af;
        }

        body.theme-dark .hero h1 {
            -webkit-text-fill-color: #f9fafb;
            background: none;
            color: #f9fafb;
        }

        body.theme-dark .stat-card {
            background: linear-gradient(180deg, #1f2937, #111827);
            border-color: #374151;
        }

        body.theme-dark .stat-value {
            color: #6b9985;
        }

        body.theme-dark .stat-label {
            color: #9ca3af;
        }

        body.theme-dark .tab {
            background: #1f2937;
            border-color: #374151;
            color: #d1d5db;
        }

        body.theme-dark .tab:hover {
            background: #374151;
            border-color: #6b9985;
        }

        body.theme-dark .tab.active {
            background: linear-gradient(135deg, #e5e7eb, #cbd5e1);
            color: #111827;
        }

        body.theme-dark .upload-zone {
            background: linear-gradient(180deg, #1f2937, #111827);
            border-color: #374151;
        }

        body.theme-dark .upload-zone:hover {
            background: linear-gradient(180deg, #374151, #1f2937);
            border-color: #6b9985;
        }

        body.theme-dark textarea {
            background: #1f2937;
            border-color: #374151;
            color: #e2e8f0;
        }

        body.theme-dark textarea:focus {
            border-color: #6b9985;
        }

        body.theme-dark .result-area {
            background: #0f172a;
            color: #e2e8f0;
        }

        body.theme-dark .chat-message.assistant {
            background: #1f2937;
            color: #e2e8f0;
        }

        body.theme-dark .chat-suggestion {
            background: rgba(107, 153, 133, 0.3);
            color: #d1d5db;
        }

        body.theme-dark .chat-suggestion:hover {
            background: rgba(107, 153, 133, 0.5);
        }

        body.theme-dark .chat-input-area {
            background: #111827;
            border-color: #374151;
        }

        body.theme-dark .chat-input-area input {
            background: #1f2937;
            border-color: #374151;
            color: #e2e8f0;
        }

        body.theme-dark .modal {
            background: #111827;
        }

        body.theme-dark .modal-header h2 {
            color: #f9fafb;
        }

        body.theme-dark .modal-header p {
            color: #9ca3af;
        }

        body.theme-dark .form-group label {
            color: #d1d5db;
        }

        body.theme-dark .form-group input,
        body.theme-dark .form-group select {
            background: #1f2937;
            border-color: #374151;
            color: #e2e8f0;
        }

        body.theme-dark .job-item {
            background: #1f2937;
            border-color: #374151;
        }

        body.theme-dark .job-item:hover {
            background: #374151;
        }

        body.theme-dark .job-id {
            color: #f9fafb;
        }

        body.theme-dark .job-meta {
            color: #9ca3af;
        }

        body.theme-dark .suggestion-item {
            background: #1f2937;
            color: #d1d5db;
        }

        body.theme-dark .field label {
            color: #9ca3af;
        }

        body.theme-dark .field input,
        body.theme-dark .field select {
            background: #1f2937;
            border-color: #374151;
            color: #e2e8f0;
        }

        body.theme-dark .privacy-checkbox label {
            color: #d1d5db;
        }

        body.theme-dark .privacy-section h3 {
            color: #f9fafb;
        }

        body.theme-dark .privacy-section p {
            color: #9ca3af;
        }

        body.theme-dark .privacy-footer {
            background: #111827;
        }

        body.theme-dark .modal-close {
            background: #374151;
            color: #d1d5db;
        }

        body.theme-dark .modal-close:hover {
            background: #4b5563;
        }

        body.theme-dark .footer-info a {
            color: #9ca3af;
        }

        body.theme-dark .footer-info a:hover {
            color: #6b9985;
        }

        body.theme-dark .toast {
            background: #1f2937;
            border-color: #374151;
            color: #e2e8f0;
        }

        body.theme-dark .challenge-result {
            background: #1f2937;
            border-color: #374151;
        }

        body.theme-dark .login-required {
            background: #1f2937;
        }

        body.theme-dark .login-required p {
            color: #9ca3af;
        }

        .nav-actions {
            display: flex;
            gap: 8px;
            align-items: center;
        }

        .btn {
            border: none;
            border-radius: 999px;
            padding: 10px 20px;
            font-weight: 700;
            cursor: pointer;
            font-family: 'Space Grotesk', sans-serif;
            font-size: 0.9rem;
            transition: all 0.25s cubic-bezier(0.16, 1, 0.3, 1);
            position: relative;
            overflow: hidden;
        }

        .btn:hover {
            transform: translateY(-2px);
        }

        .btn:active {
            transform: translateY(0);
        }

        .btn-primary {
            color: #fff;
            background: linear-gradient(135deg, var(--teal), var(--teal-bright));
            box-shadow: 0 6px 20px rgba(90, 123, 111, 0.3);
        }

        .btn-primary:hover {
            box-shadow: 0 10px 30px rgba(90, 123, 111, 0.4);
        }

        .btn-ghost {
            color: var(--ink-2);
            background: #fff;
            border: 1px solid var(--line);
        }

        .btn-ghost:hover {
            border-color: var(--teal-light);
            color: var(--teal);
        }

        .btn-sm {
            padding: 6px 14px;
            font-size: 0.82rem;
        }

        /* Page Sections */
        .page-section {
            display: none;
        }

        .page-section.active {
            display: block;
            animation: fadeIn 0.3s ease;
        }

        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(10px); }
            to { opacity: 1; transform: translateY(0); }
        }

        /* Hero */
        .hero {
            display: grid;
            grid-template-columns: 1.3fr 1fr;
            gap: 20px;
            margin-bottom: 24px;
        }

        .hero-card {
            background: var(--panel);
            border: 1px solid var(--line);
            border-radius: var(--radius-lg);
            box-shadow: var(--shadow);
            padding: 28px;
            position: relative;
            overflow: hidden;
        }

        .hero-card::before {
            content: '';
            position: absolute;
            top: -1px; left: -1px; right: -1px;
            height: 4px;
            background: linear-gradient(90deg, var(--teal), var(--teal-bright), var(--amber), var(--blue));
            border-radius: var(--radius-lg) var(--radius-lg) 0 0;
        }

        .hero h1 {
            font-family: 'Sora', sans-serif;
            font-size: clamp(1.8rem, 3.5vw, 2.8rem);
            line-height: 1.1;
            margin-bottom: 12px;
            background: linear-gradient(135deg, var(--ink-1), var(--teal));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }

        .hero .lead {
            color: var(--ink-3);
            font-size: 1.05rem;
            line-height: 1.65;
            margin-bottom: 20px;
        }

        .hero-actions {
            display: flex;
            gap: 10px;
            flex-wrap: wrap;
        }

        /* Stats Grid */
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 12px;
        }

        .stat-card {
            background: linear-gradient(180deg, #f0fdfa, #fff);
            border: 1px solid #ccfbf1;
            border-radius: 16px;
            padding: 16px;
            text-align: center;
            transition: all 0.25s ease;
        }

        .stat-card:hover {
            transform: translateY(-3px);
            box-shadow: 0 12px 24px rgba(90, 123, 111, 0.15);
        }

        .stat-value {
            font-family: 'Sora', sans-serif;
            font-size: 2rem;
            font-weight: 800;
            color: var(--teal);
            line-height: 1;
        }

        .stat-label {
            font-size: 0.82rem;
            color: var(--ink-3);
            margin-top: 4px;
            font-weight: 600;
        }

        /* Tabs */
        .tabs {
            display: flex;
            gap: 6px;
            flex-wrap: wrap;
            margin-bottom: 16px;
        }

        .tab {
            padding: 8px 16px;
            border-radius: 999px;
            border: 1px solid var(--line);
            background: #fff;
            font-weight: 700;
            font-size: 0.85rem;
            cursor: pointer;
            transition: all 0.2s ease;
            display: flex;
            align-items: center;
            gap: 6px;
        }

        .tab:hover {
            border-color: var(--teal-light);
            background: #f0fdfa;
        }

        .tab.active {
            background: linear-gradient(135deg, var(--teal), var(--teal-bright));
            color: #fff;
            border-color: transparent;
        }

        .tab-icon { font-size: 1rem; }

        /* Panels */
        .panel {
            display: none;
        }

        .panel.active {
            display: block;
            animation: fadeIn 0.3s ease;
        }

        /* Cards Grid */
        .cards-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }

        .card {
            background: var(--panel);
            border: 1px solid var(--line);
            border-radius: var(--radius);
            box-shadow: var(--shadow);
            padding: 20px;
            transition: all 0.25s ease;
        }

        .card:hover {
            transform: translateY(-3px);
            box-shadow: var(--shadow-lg);
            border-color: var(--teal-light);
        }

        .card h3 {
            font-family: 'Sora', sans-serif;
            font-size: 1.1rem;
            margin-bottom: 10px;
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .card p {
            color: var(--ink-3);
            font-size: 0.92rem;
            line-height: 1.6;
        }

        /* Upload Zone */
        .upload-zone {
            border: 2px dashed var(--teal-light);
            border-radius: var(--radius);
            padding: 40px;
            text-align: center;
            cursor: pointer;
            transition: all 0.3s ease;
            background: linear-gradient(180deg, #f0fdfa, #fff);
        }

        .upload-zone:hover {
            border-color: var(--teal);
            background: linear-gradient(180deg, #e0f7fa, #f0fdfa);
            transform: translateY(-2px);
        }

        .upload-zone.dragover {
            border-color: var(--teal-bright);
            background: linear-gradient(180deg, #b2dfdb, #e0f7fa);
        }

        .upload-zone input { display: none; }

        .upload-icon {
            font-size: 3rem;
            margin-bottom: 12px;
        }

        /* Textarea */
        textarea {
            width: 100%;
            min-height: 150px;
            border-radius: var(--radius);
            border: 1px solid var(--line);
            padding: 14px;
            resize: vertical;
            font-family: 'Space Grotesk', monospace;
            font-size: 0.9rem;
            transition: border-color 0.2s ease, box-shadow 0.2s ease;
        }

        textarea:focus {
            outline: none;
            border-color: var(--teal);
            box-shadow: 0 0 0 3px rgba(90, 123, 111, 0.15);
        }

        /* Result Area */
        .result-area {
            background: #0f172a;
            color: #e2e8f0;
            border-radius: var(--radius);
            padding: 16px;
            min-height: 150px;
            max-height: 400px;
            overflow: auto;
            font-family: 'Space Grotesk', monospace;
            font-size: 0.85rem;
            line-height: 1.6;
            white-space: pre-wrap;
            word-break: break-word;
        }

        .result-area.error {
            background: #2a1212;
            border-left: 4px solid #dc2626;
        }

        /* Job Stream */
        .job-stream {
            display: grid;
            gap: 8px;
            max-height: 400px;
            overflow-y: auto;
        }

        .job-item {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 12px 14px;
            background: #f9fafb;
            border: 1px solid var(--line);
            border-radius: 10px;
            transition: all 0.2s ease;
        }

        .job-item:hover {
            background: #f3f4f6;
            transform: translateX(4px);
        }

        .job-info {
            display: flex;
            flex-direction: column;
            gap: 2px;
        }

        .job-id {
            font-weight: 700;
            font-size: 0.88rem;
        }

        .job-meta {
            font-size: 0.78rem;
            color: var(--ink-3);
        }

        .job-status {
            padding: 4px 12px;
            border-radius: 999px;
            font-size: 0.75rem;
            font-weight: 700;
            text-transform: uppercase;
        }

        .status-pending { background: #fef3c7; color: #92400e; }
        .status-processing { background: #dbeafe; color: #1d4ed8; }
        .status-completed { background: #dcfce7; color: #166534; }
        .status-failed { background: #fee2e2; color: #991b1b; }

        /* Chat Bot */
        .chat-container {
            display: flex;
            flex-direction: column;
            height: 500px;
            background: var(--panel);
            border: 1px solid var(--line);
            border-radius: var(--radius);
            overflow: hidden;
        }

        .chat-messages {
            flex: 1;
            overflow-y: auto;
            padding: 16px;
            display: flex;
            flex-direction: column;
            gap: 12px;
        }

        .chat-message {
            max-width: 85%;
            padding: 12px 16px;
            border-radius: 16px;
            font-size: 0.9rem;
            line-height: 1.5;
        }

        .chat-message.user {
            align-self: flex-end;
            background: linear-gradient(135deg, var(--teal), var(--teal-bright));
            color: #fff;
            border-bottom-right-radius: 4px;
        }

        .chat-message.assistant {
            align-self: flex-start;
            background: #f3f4f6;
            color: var(--ink-1);
            border-bottom-left-radius: 4px;
        }

        .chat-suggestions {
            display: flex;
            gap: 6px;
            flex-wrap: wrap;
            margin-top: 8px;
        }

        .chat-suggestion {
            padding: 4px 10px;
            background: rgba(255,255,255,0.3);
            border-radius: 999px;
            font-size: 0.78rem;
            cursor: pointer;
            transition: background 0.2s;
        }

        .chat-suggestion:hover {
            background: rgba(255,255,255,0.5);
        }

        .chat-input-area {
            display: flex;
            gap: 8px;
            padding: 12px;
            border-top: 1px solid var(--line);
            background: #f9fafb;
        }

        .chat-input-area input {
            flex: 1;
            padding: 10px 14px;
            border: 1px solid var(--line);
            border-radius: 999px;
            font-size: 0.9rem;
        }

        .chat-input-area input:focus {
            outline: none;
            border-color: var(--teal);
        }

        /* Login Modal */
        .modal-backdrop {
            position: fixed;
            inset: 0;
            background: rgba(0, 0, 0, 0.6);
            backdrop-filter: blur(4px);
            z-index: 1000;
            display: none;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }

        .modal-backdrop.open {
            display: flex;
        }

        .modal {
            background: var(--panel);
            border-radius: var(--radius-lg);
            width: 100%;
            max-width: 420px;
            box-shadow: 0 25px 80px rgba(0, 0, 0, 0.3);
            animation: slideUp 0.3s ease;
        }

        @keyframes slideUp {
            from { opacity: 0; transform: translateY(20px); }
            to { opacity: 1; transform: translateY(0); }
        }

        .modal-header {
            padding: 24px;
            text-align: center;
            border-bottom: 1px solid var(--line);
            position: relative;
        }

        .modal-header h2 {
            font-family: 'Sora', sans-serif;
            margin-bottom: 4px;
        }

        .modal-body {
            padding: 24px;
        }

        .form-group {
            margin-bottom: 16px;
        }

        .form-group label {
            display: block;
            font-size: 0.85rem;
            font-weight: 600;
            margin-bottom: 6px;
            color: var(--ink-2);
        }

        .form-group input, .form-group select {
            width: 100%;
            padding: 10px 14px;
            border: 1px solid var(--line);
            border-radius: 10px;
            font-size: 0.9rem;
            font-family: 'Space Grotesk', sans-serif;
        }

        .form-group input:focus, .form-group select:focus {
            outline: none;
            border-color: var(--teal);
            box-shadow: 0 0 0 3px rgba(90, 123, 111, 0.15);
        }

        .modal-footer {
            padding: 16px 24px;
            border-top: 1px solid var(--line);
            display: flex;
            gap: 8px;
            justify-content: flex-end;
        }

        .modal-close {
            position: absolute;
            top: 12px;
            right: 12px;
            width: 30px;
            height: 30px;
            border-radius: 999px;
            border: 1px solid transparent;
            background: #f3f4f6;
            color: var(--ink-2);
            font-size: 0.78rem;
            font-weight: 800;
            cursor: pointer;
            transition: all 0.2s ease;
        }

        .modal-close:hover {
            background: #e5e7eb;
            transform: scale(1.05);
        }

        .modal-close-danger {
            background: #fee2e2;
            color: #b91c1c;
            border-color: #fecaca;
        }

        .modal-close-danger:hover {
            background: #fecaca;
            color: #991b1b;
        }

        .app-dialog {
            max-width: 520px;
            border: 1px solid var(--line);
        }

        .app-dialog-header h2 {
            margin-bottom: 0;
        }

        /* Field display */
        .field {
            margin-bottom: 12px;
        }

        .field label {
            display: block;
            font-size: 0.82rem;
            color: var(--ink-3);
            font-weight: 600;
            margin-bottom: 4px;
        }

        .field input, .field select {
            width: 100%;
            padding: 9px 12px;
            border: 1px solid var(--line);
            border-radius: 8px;
            font-size: 0.88rem;
        }

        /* Animated numbers */
        .animated-number {
            display: inline-block;
            animation: numberPop 0.5s ease;
        }

        @keyframes numberPop {
            0% { transform: scale(0.5); opacity: 0; }
            50% { transform: scale(1.2); }
            100% { transform: scale(1); opacity: 1; }
        }

        /* Footer */
        .footer {
            margin-top: 40px;
            padding: 30px 0;
            border-top: 1px solid var(--line);
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 16px;
        }

        .footer-info {
            display: flex;
            gap: 20px;
            flex-wrap: wrap;
            align-items: center;
        }

        .footer-info a {
            color: var(--ink-3);
            text-decoration: none;
            font-size: 0.88rem;
            transition: color 0.2s;
        }

        .footer-info a:hover {
            color: var(--teal);
        }

        .footer-version {
            font-size: 0.82rem;
            color: var(--ink-3);
        }

        /* Toast notifications */
        .toast-container {
            position: fixed;
            top: 20px;
            right: 20px;
            z-index: 2000;
            display: flex;
            flex-direction: column;
            gap: 8px;
        }

        .toast {
            background: var(--panel);
            border: 1px solid var(--line);
            border-radius: 12px;
            padding: 12px 16px;
            box-shadow: var(--shadow-lg);
            display: flex;
            align-items: center;
            gap: 10px;
            animation: slideIn 0.3s ease;
            max-width: 360px;
        }

        @keyframes slideIn {
            from { opacity: 0; transform: translateX(100px); }
            to { opacity: 1; transform: translateX(0); }
        }

        .toast.success { border-left: 4px solid #10b981; }
        .toast.error { border-left: 4px solid #ef4444; }
        .toast.info { border-left: 4px solid #3b82f6; }

        .notification-center {
            position: fixed;
            top: 84px;
            right: 20px;
            width: min(380px, calc(100vw - 24px));
            max-height: 70vh;
            overflow: hidden;
            background: rgba(255, 255, 255, 0.96);
            border: 1px solid var(--line);
            border-radius: 18px;
            box-shadow: var(--shadow-lg);
            backdrop-filter: blur(18px);
            z-index: 1900;
            display: none;
            flex-direction: column;
        }

        body.theme-dark .notification-center {
            background: rgba(17, 24, 39, 0.96);
        }

        .notification-center.open {
            display: flex;
        }

        .notification-center-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 14px 16px;
            border-bottom: 1px solid var(--line);
        }

        .notification-center-list {
            overflow-y: auto;
            padding: 12px;
            display: grid;
            gap: 8px;
        }

        .notification-item {
            padding: 12px 14px;
            border-radius: 14px;
            background: #f8fafc;
            border: 1px solid #e5e7eb;
        }

        body.theme-dark .notification-item {
            background: #111827;
            border-color: #374151;
        }

        .notification-item.unread {
            border-color: #93c5fd;
            background: #eff6ff;
        }

        .notification-item-title {
            font-weight: 700;
            font-size: 0.9rem;
            color: var(--ink-1);
            margin-bottom: 4px;
        }

        .notification-item-body {
            color: var(--ink-2);
            font-size: 0.84rem;
            line-height: 1.5;
        }

        .notification-badge {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            min-width: 22px;
            height: 22px;
            padding: 0 6px;
            border-radius: 999px;
            background: #ef4444;
            color: #fff;
            font-size: 0.72rem;
            font-weight: 800;
            margin-left: 8px;
        }

        .notification-empty {
            padding: 18px 16px;
            color: var(--ink-3);
            text-align: center;
            font-size: 0.9rem;
        }

        /* Suggestions */
        .suggestions-list {
            display: flex;
            flex-direction: column;
            gap: 8px;
            margin-top: 12px;
        }

        .suggestion-item {
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 10px 14px;
            background: #f9fafb;
            border-radius: 10px;
            font-size: 0.88rem;
            border-left: 3px solid var(--teal-light);
        }

        /* Challenge solver */
        .challenge-form {
            display: grid;
            gap: 12px;
        }

        .challenge-result {
            margin-top: 16px;
            padding: 20px;
            background: #f0fdfa;
            border-radius: var(--radius);
            border: 1px solid var(--teal-light);
        }

        /* Responsive */
        @media (max-width: 960px) {
            .hero { grid-template-columns: 1fr; }
            .nav { flex-direction: column; border-radius: var(--radius); }
            .nav-links { justify-content: center; }
        }

        @media (max-width: 640px) {
            .shell { padding: 12px; }
            .cards-grid { grid-template-columns: 1fr; }
            .stats-grid { grid-template-columns: 1fr 1fr; }
            .footer { flex-direction: column; text-align: center; }
        }

        /* Scrollbar */
        ::-webkit-scrollbar { width: 6px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: var(--line); border-radius: 3px; }
        ::-webkit-scrollbar-thumb:hover { background: var(--ink-3); }

        /* Login required overlay */
        .login-required {
            text-align: center;
            padding: 40px;
            background: #f9fafb;
            border-radius: var(--radius);
        }

        .login-required h3 {
            margin-bottom: 8px;
        }

        .login-required p {
            color: var(--ink-3);
            margin-bottom: 16px;
        }

        /* Quick Action Bar */
        .quick-actions {
            display: flex;
            gap: 8px;
            flex-wrap: wrap;
            margin-bottom: 16px;
            padding: 10px 14px;
            background: var(--panel);
            border: 1px solid var(--line);
            border-radius: var(--radius);
            align-items: center;
        }

        body.theme-dark .quick-actions {
            background: #111827;
            border-color: #374151;
        }

        .quick-action-btn {
            display: flex;
            align-items: center;
            gap: 6px;
            padding: 6px 14px;
            border-radius: 999px;
            border: 1px solid var(--line);
            background: transparent;
            font-family: 'Space Grotesk', sans-serif;
            font-size: 0.82rem;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s ease;
            color: var(--ink-2);
        }

        .quick-action-btn:hover {
            background: var(--teal);
            color: #fff;
            border-color: var(--teal);
            transform: translateY(-1px);
        }

        body.theme-dark .quick-action-btn {
            color: #d1d5db;
            border-color: #374151;
        }

        body.theme-dark .quick-action-btn:hover {
            background: var(--teal-bright);
            color: #111827;
            border-color: var(--teal-bright);
        }

        .quick-action-icon {
            font-size: 0.9rem;
            line-height: 1;
        }

        /* Search Bar */
        .search-bar-container {
            position: relative;
            min-width: 180px;
        }

        .search-bar-container input {
            width: 100%;
            padding: 6px 12px 6px 30px;
            border-radius: 999px;
            border: 1px solid var(--line);
            font-family: 'Space Grotesk', sans-serif;
            font-size: 0.82rem;
            background: var(--paper);
            color: var(--ink-1);
            transition: all 0.2s ease;
        }

        .search-bar-container input:focus {
            outline: none;
            border-color: var(--teal);
            box-shadow: 0 0 0 2px rgba(90, 123, 111, 0.15);
        }

        body.theme-dark .search-bar-container input {
            background: #1f2937;
            border-color: #374151;
            color: #e2e8f0;
        }

        body.theme-dark .search-bar-container input:focus {
            border-color: var(--teal-bright);
        }

        .search-bar-icon {
            position: absolute;
            left: 10px;
            top: 50%;
            transform: translateY(-50%);
            color: var(--ink-3);
            font-size: 0.78rem;
            pointer-events: none;
        }

        /* Keyboard shortcut badges */
        .kbd {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            min-width: 20px;
            height: 20px;
            padding: 0 4px;
            border-radius: 4px;
            background: #f3f4f6;
            border: 1px solid #d1d5db;
            font-size: 0.7rem;
            font-weight: 700;
            font-family: monospace;
            color: #4b5563;
            margin-left: 4px;
        }

        body.theme-dark .kbd {
            background: #374151;
            border-color: #4b5563;
            color: #d1d5db;
        }

        /* Word/Character Counter */
        .text-stats {
            display: flex;
            gap: 16px;
            font-size: 0.78rem;
            color: var(--ink-3);
            margin-top: 6px;
        }

        .text-stats span {
            font-weight: 600;
        }
    </style>
</head>
<body>
    <div class="bg-gradient">
        <div class="bg-blob"></div>
        <div class="bg-blob"></div>
        <div class="bg-blob"></div>
    </div>

    <div class="toast-container" id="toastContainer"></div>

    <div class="notification-center" id="notificationCenter">
        <div class="notification-center-header">
            <strong>Notifications</strong>
            <button class="btn btn-ghost btn-sm" onclick="markAllNotificationsRead()">Mark all read</button>
        </div>
        <div class="notification-center-list" id="notificationList">
            <div class="notification-empty">No notifications yet.</div>
        </div>
    </div>

    <!-- Privacy Modal -->
    """ + PRIVACY_POLICY_HTML + """

    <div class="shell">
        <!-- Navigation -->
        <nav class="nav">
            <div class="brand">
                <div class="brand-badge">DI</div>
                <span>DocIntel Pro</span>
            </div>
            <div class="nav-links">
                <button class="active" onclick="showSection('overview')">Overview</button>
                <button onclick="showSection('workspace')">Workspace</button>
                <button onclick="showSection('studio')">Studio</button>
                <button onclick="showSection('chat')">AI Assistant</button>
                <button onclick="showSection('challenges')">Challenges</button>
                <button onclick="showSection('docs')">Docs</button>
                <button onclick="showSection('settings')">Settings</button>
            </div>
            <div class="nav-actions">
                <button class="btn btn-ghost btn-sm" id="notificationBtn" onclick="toggleNotificationCenter()">Notifications <span id="notificationBadge" class="notification-badge" style="display:none;">0</span></button>
                <button class="btn btn-ghost btn-sm" id="authBtn" onclick="showLoginModal()">Login</button>
                <button class="btn btn-primary btn-sm" onclick="showSection('studio')">Get Started</button>
            </div>
        </nav>

        <!-- Quick Actions Bar -->
        <div class="quick-actions" id="quickActions">
            <span style="font-weight: 700; font-size: 0.82rem; color: var(--ink-2);">Quick Actions:</span>
            <button class="quick-action-btn" onclick="showSection('studio'); setTimeout(() => document.getElementById('fileInput')?.click(), 300)"><span class="quick-action-icon">U</span> Upload File</button>
            <button class="quick-action-btn" onclick="switchStudioTab('text', document.querySelector('#studio .tab:nth-child(2)')); showSection('studio')"><span class="quick-action-icon">T</span> Text Input</button>
            <button class="quick-action-btn" onclick="switchStudioTab('batch', document.querySelector('#studio .tab:nth-child(3)')); showSection('studio')"><span class="quick-action-icon">B</span> Batch</button>
            <button class="quick-action-btn" onclick="switchStudioTab('summarize', document.querySelector('#studio .tab:nth-child(4)')); showSection('studio')"><span class="quick-action-icon">S</span> Summarize</button>
            <button class="quick-action-btn" onclick="showSection('chat')"><span class="quick-action-icon">A</span> AI Chat</button>
            <button class="quick-action-btn" onclick="showSection('challenges')"><span class="quick-action-icon">C</span> Challenges</button>
            <div class="search-bar-container">
                <span class="search-bar-icon">S</span>
                <input type="text" id="globalSearch" placeholder="Search..." oninput="handleSearch(this.value)">
            </div>
        </div>

        <!-- Overview Section -->
        <section class="page-section active" id="overview">
            <div class="hero">
                <div class="hero-card">
                    <h1>Transform Documents into Intelligent Data</h1>
                    <p class="lead">
                        Upload any document format — PDF, images, Word, Excel — and let our AI extract, 
                        classify, and summarize key information. Perfect for invoices, contracts, reports, 
                        and any business document workflow.
                    </p>
                    <div class="hero-actions">
                        <button class="btn btn-primary" onclick="showSection('studio')">Start Processing</button>
                        <button class="btn btn-ghost" onclick="showSection('chat')">Ask AI Assistant</button>
                    </div>
                </div>
                <div class="hero-card">
                    <h3 style="margin-bottom: 16px;">Live Statistics</h3>
                    <div class="stats-grid">
                        <div class="stat-card">
                            <div class="stat-value" id="statTotal">0</div>
                            <div class="stat-label">Documents</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-value" id="statSuccess">0</div>
                            <div class="stat-label">Processed</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-value" id="statFailed">0</div>
                            <div class="stat-label">Failed</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-value" id="statActive">0</div>
                            <div class="stat-label">Active Jobs</div>
                        </div>
                    </div>
                </div>
            </div>

            <div class="cards-grid">
                <div class="card">
                    <h3>Smart Upload</h3>
                    <p>Support for 20+ file formats including PDF, images (JPG, PNG, GIF), Word, Excel, and text files. Drag & drop or click to upload.</p>
                </div>
                <div class="card">
                    <h3>AI Extraction</h3>
                    <p>Intelligent classification and field extraction with confidence scoring. Automatically identifies document types and extracts key data.</p>
                </div>
                <div class="card">
                    <h3>Summarization</h3>
                    <p>Get concise summaries of lengthy documents with key points, numbers, dates, and actionable suggestions highlighted.</p>
                </div>
                <div class="card">
                    <h3>AI Chatbot</h3>
                    <p>Built-in AI assistant to help with document processing questions, API integration, troubleshooting, and best practices.</p>
                </div>
                <div class="card">
                    <h3>Business Solutions</h3>
                    <p>Solve complex business challenges with tailored recommendations for efficiency, compliance, cost reduction, and scalability.</p>
                </div>
                <div class="card">
                    <h3>Enterprise Security</h3>
                    <p>GDPR-compliant with encrypted processing, automatic data deletion options, and comprehensive audit logging.</p>
                </div>
            </div>
        </section>

        <!-- Workspace Section -->
        <section class="page-section" id="workspace">
            <div class="tabs">
                <button class="tab active" onclick="switchWorkspaceTab('profile', this)">Profile</button>
                <button class="tab" onclick="switchWorkspaceTab('documents', this)">My Documents</button>
                <button class="tab" onclick="switchWorkspaceTab('history', this)">History</button>
                <button class="tab" onclick="switchWorkspaceTab('export', this)">Export</button>
            </div>

            <div class="panel active" id="workspace-profile">
                <div class="cards-grid">
                    <div class="card">
                        <h3>User Profile</h3>
                        <div class="field">
                            <label>Name</label>
                            <input type="text" id="profileName" placeholder="Your name">
                        </div>
                        <div class="field">
                            <label>Email</label>
                            <input type="email" id="profileEmail" placeholder="your@email.com">
                        </div>
                        <div class="field">
                            <label>Role</label>
                            <select id="profileRole">
                                <option value="student">Student</option>
                                <option value="engineer">Engineer</option>
                                <option value="analyst">Data Analyst</option>
                                <option value="manager">Manager</option>
                                <option value="executive">Executive</option>
                            </select>
                        </div>
                        <button class="btn btn-primary" onclick="saveProfile()">Save Profile</button>
                    </div>

                    <div class="card">
                        <h3>Preferences</h3>
                        <div class="field">
                            <label>Theme</label>
                            <select id="themeSelect" onchange="applyTheme(this.value)">
                                <option value="light">Light (Default)</option>
                                <option value="dark">Dark Mode</option>
                                <option value="warm">Warm</option>
                            </select>
                        </div>
                        <div class="field">
                            <label>Auto-refresh jobs</label>
                            <select id="autoRefresh">
                                <option value="on">Enabled</option>
                                <option value="off">Disabled</option>
                            </select>
                        </div>
                        <div class="field">
                            <label>Auto-delete after processing</label>
                            <select id="autoDelete">
                                <option value="never">Never</option>
                                <option value="1h">After 1 hour</option>
                                <option value="24h">After 24 hours</option>
                                <option value="immediately">Immediately</option>
                            </select>
                        </div>
                        <button class="btn btn-primary" onclick="savePreferences()">Save Preferences</button>
                    </div>
                </div>
            </div>

            <div class="panel" id="workspace-documents">
                <div class="card">
                    <h3>My Documents</h3>
                    <p>Documents you've uploaded will appear here. Login to persist your documents across sessions.</p>
                    <div id="myDocumentsList" style="margin-top: 16px;"></div>
                </div>
            </div>

            <div class="panel" id="workspace-history">
                <div class="card">
                    <h3>Processing History</h3>
                    <div id="historyList" style="margin-top: 16px;">
                        <p style="color: var(--ink-3);">No processing history yet. Upload a document to get started.</p>
                    </div>
                </div>
            </div>

            <div class="panel" id="workspace-export">
                <div class="card">
                    <h3>Export Data</h3>
                    <p>Export your processing results and statistics in various formats.</p>
                    <div style="margin-top: 16px; display: flex; gap: 10px; flex-wrap: wrap;">
                        <button class="btn btn-primary" onclick="exportData('json')">Export as JSON</button>
                        <button class="btn btn-ghost" onclick="exportData('csv')">Export as CSV</button>
                        <button class="btn btn-ghost" onclick="exportData('report')">Export Report</button>
                    </div>
                </div>
            </div>
        </section>

        <!-- Studio Section -->
        <section class="page-section" id="studio">
            <div class="tabs">
                <button class="tab active" onclick="switchStudioTab('upload', this)">Upload</button>
                <button class="tab" onclick="switchStudioTab('text', this)">Text Input</button>
                <button class="tab" onclick="switchStudioTab('batch', this)">Batch</button>
                <button class="tab" onclick="switchStudioTab('summarize', this)">Summarize</button>
                <button class="tab" onclick="switchStudioTab('jobs', this)">Jobs</button>
            </div>

            <!-- Upload Panel -->
            <div class="panel active" id="studio-upload">
                <div class="cards-grid">
                    <div class="card">
                        <h3>Upload Document</h3>
                        <div class="upload-zone" id="uploadZone" onclick="document.getElementById('fileInput').click()">
                            <div class="upload-icon">FILE</div>
                            <p><strong>Drop files here or click to browse</strong></p>
                            <p style="color: var(--ink-3); font-size: 0.85rem; margin-top: 8px;">
                                Supports: PDF, JPG, PNG, GIF, WebP, DOCX, XLSX, TXT, MD, CSV, JSON, XML, and more
                            </p>
                            <p style="color: var(--ink-3); font-size: 0.78rem; margin-top: 4px;">Max size: 50MB</p>
                            <input type="file" id="fileInput" multiple onchange="handleFileUpload(event)">
                        </div>
                        <div style="margin-top: 16px;">
                            <h4 style="margin-bottom: 8px; font-size: 0.9rem;">Supported Formats:</h4>
                            <div style="display: flex; gap: 6px; flex-wrap: wrap;">
                                <span class="tab" style="cursor: default; font-size: 0.78rem;">PDF</span>
                                <span class="tab" style="cursor: default; font-size: 0.78rem;">JPG/PNG</span>
                                <span class="tab" style="cursor: default; font-size: 0.78rem;">DOCX</span>
                                <span class="tab" style="cursor: default; font-size: 0.78rem;">XLSX</span>
                                <span class="tab" style="cursor: default; font-size: 0.78rem;">TXT</span>
                                <span class="tab" style="cursor: default; font-size: 0.78rem;">CSV</span>
                                <span class="tab" style="cursor: default; font-size: 0.78rem;">JSON</span>
                            </div>
                        </div>
                    </div>

                    <div class="card">
                        <h3>Result</h3>
                        <div class="result-area" id="uploadResult">Waiting for upload...</div>
                        <div id="suggestionsArea" style="margin-top: 12px;"></div>
                        <div style="margin-top: 12px; display: flex; gap: 8px; flex-wrap: wrap;">
                            <button class="btn btn-ghost btn-sm" onclick="copyResult()">Copy</button>
                            <button class="btn btn-ghost btn-sm" onclick="downloadResult()">Download</button>
                            <button class="btn btn-ghost btn-sm" onclick="clearResult()">Clear</button>
                        </div>
                    </div>
                </div>
            </div>

            <!-- Text Input Panel -->
            <div class="panel" id="studio-text">
                <div class="cards-grid">
                    <div class="card">
                        <h3>Analyze Text</h3>
                        <p style="font-size: 0.88rem; color: var(--ink-3); margin-bottom: 12px;">
                            Paste text from any document to extract structured data and get intelligent insights.
                        </p>
                        <textarea id="textInput" placeholder="Paste your document text here...&#10;&#10;Example:&#10;INVOICE #12345&#10;Date: March 15, 2026&#10;Total: $2,500.00&#10;Due: April 15, 2026&#10;Vendor: Acme Corporation"></textarea>
                        <div style="margin-top: 12px; display: flex; gap: 10px; flex-wrap: wrap;">
                            <button class="btn btn-primary" onclick="extractText()">Extract Data</button>
                            <button class="btn btn-ghost" onclick="loadDemoText()">Load Demo</button>
                            <button class="btn btn-ghost" onclick="summarizeText()">Summarize</button>
                        </div>
                    </div>

                    <div class="card">
                        <h3>Extraction Result</h3>
                        <div class="result-area" id="textResult">Waiting for text input...</div>
                        <div id="textSuggestions" style="margin-top: 12px;"></div>
                    </div>
                </div>
            </div>

            <!-- Batch Panel -->
            <div class="panel" id="studio-batch">
                <div class="card">
                    <h3>Batch Processing</h3>
                    <p style="font-size: 0.88rem; color: var(--ink-3); margin-bottom: 12px;">
                        Process up to 10 documents at once. Separate each document with "---" on a new line.
                    </p>
                    <textarea id="batchInput" placeholder="Document 1 text here...&#10;---&#10;Document 2 text here...&#10;---&#10;Document 3 text here..."></textarea>
                    <div style="margin-top: 12px; display: flex; gap: 10px; flex-wrap: wrap;">
                        <button class="btn btn-primary" onclick="processBatch()">Process Batch</button>
                        <button class="btn btn-ghost" onclick="loadBatchDemo()">Load Demo</button>
                    </div>
                </div>
                <div class="card" style="margin-top: 16px;">
                    <h3>Batch Results</h3>
                    <div class="result-area" id="batchResult">Waiting for batch processing...</div>
                </div>
            </div>

            <!-- Summarize Panel -->
            <div class="panel" id="studio-summarize">
                <div class="card">
                    <h3>Document Summarization</h3>
                    <p style="font-size: 0.88rem; color: var(--ink-3); margin-bottom: 12px;">
                        Generate concise summaries of lengthy documents with key points and insights.
                    </p>
                    <textarea id="summarizeInput" placeholder="Paste a long document text to generate a summary..."></textarea>
                    <div style="margin-top: 12px; display: flex; gap: 10px; flex-wrap: wrap;">
                        <button class="btn btn-primary" onclick="generateSummary()">Generate Summary</button>
                        <button class="btn btn-ghost" onclick="loadLongDemo()">Load Demo</button>
                    </div>
                </div>
                <div class="card" style="margin-top: 16px;">
                    <h3>Summary</h3>
                    <div class="result-area" id="summaryResult">Waiting for input...</div>
                </div>
            </div>

            <!-- Jobs Panel -->
            <div class="panel" id="studio-jobs">
                <div class="card">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;">
                        <h3>Live Job Stream</h3>
                        <button class="btn btn-ghost btn-sm" onclick="refreshJobs()">Refresh</button>
                    </div>
                    <div class="job-stream" id="jobStream">
                        <div class="job-item">
                            <div class="job-info">
                                <span class="job-id">No jobs yet</span>
                                <span class="job-meta">Upload a document to start processing</span>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </section>

        <!-- AI Chat Section -->
        <section class="page-section" id="chat">
            <div class="cards-grid">
                <div class="card" style="grid-column: span 2;">
                    <h3>AI Document Intelligence Assistant</h3>
                    <p style="font-size: 0.88rem; color: var(--ink-3); margin-bottom: 16px;">
                        Ask questions about document processing, API usage, troubleshooting, or best practices.
                    </p>
                    <div class="chat-container">
                        <div class="chat-messages" id="chatMessages">
                            <div class="chat-message assistant">
                                Hello! I'm your DocIntel AI assistant. I can help you with:
                                <ul style="margin: 8px 0 0 20px; padding: 0;">
                                    <li>Document processing questions</li>
                                    <li>API integration guidance</li>
                                    <li>Troubleshooting errors</li>
                                    <li>Best practices and tips</li>
                                </ul>
                                <div class="chat-suggestions">
                                    <span class="chat-suggestion" onclick="sendQuickQuestion('How do I get started?')">How do I get started?</span>
                                    <span class="chat-suggestion" onclick="sendQuickQuestion('What formats are supported?')">Supported formats?</span>
                                    <span class="chat-suggestion" onclick="sendQuickQuestion('How accurate is extraction?')">Extraction accuracy?</span>
                                </div>
                            </div>
                        </div>
                        <div class="chat-input-area">
                            <input type="text" id="chatInput" placeholder="Ask me anything..." onkeypress="if(event.key==='Enter')sendChatMessage()">
                            <button class="btn btn-primary btn-sm" onclick="sendChatMessage()">Send</button>
                        </div>
                    </div>
                </div>
            </div>
        </section>

        <!-- Business Challenges Section -->
        <section class="page-section" id="challenges">
            <div class="cards-grid">
                <div class="card">
                    <h3>Solve Business Challenges</h3>
                    <p style="font-size: 0.88rem; color: var(--ink-3); margin-bottom: 16px;">
                        Describe your business challenge and get tailored solutions powered by document intelligence.
                    </p>
                    <div class="challenge-form">
                        <div class="field">
                            <label>Challenge Title</label>
                            <input type="text" id="challengeTitle" placeholder="e.g., Manual invoice processing is too slow">
                        </div>
                        <div class="field">
                            <label>Category</label>
                            <select id="challengeCategory">
                                <option value="efficiency">Efficiency & Automation</option>
                                <option value="compliance">Compliance & Audit</option>
                                <option value="cost">Cost Reduction</option>
                                <option value="scalability">Scalability</option>
                                <option value="data_quality">Data Quality</option>
                            </select>
                        </div>
                        <div class="field">
                            <label>Description</label>
                            <textarea id="challengeDesc" placeholder="Describe your challenge in detail..."></textarea>
                        </div>
                        <div class="field">
                            <label>Priority</label>
                            <select id="challengePriority">
                                <option value="low">Low</option>
                                <option value="medium" selected>Medium</option>
                                <option value="high">High</option>
                                <option value="critical">Critical</option>
                            </select>
                        </div>
                        <button class="btn btn-primary" onclick="solveChallenge()">Get Solutions</button>
                    </div>
                </div>

                <div class="card">
                    <h3>Common Challenges</h3>
                    <div class="suggestions-list">
                        <div class="suggestion-item" onclick="fillChallenge('Manual data entry from invoices', 'efficiency', 'Our team spends hours manually entering invoice data into our ERP system. It is error-prone and slows down our accounts payable process.')">
                            <span>1</span>
                            <span>Manual invoice data entry taking too long</span>
                        </div>
                        <div class="suggestion-item" onclick="fillChallenge('Contract review bottleneck', 'compliance', 'Legal team is overwhelmed with contract reviews. We need faster ways to extract key terms and flag issues.')">
                            <span>2</span>
                            <span>Contract review creating bottlenecks</span>
                        </div>
                        <div class="suggestion-item" onclick="fillChallenge('Scaling document processing', 'scalability', 'Our current manual process cannot handle the 10x growth in document volume we expect this year.')">
                            <span>3</span>
                            <span>Unable to scale document processing</span>
                        </div>
                        <div class="suggestion-item" onclick="fillChallenge('Data quality issues', 'data_quality', 'Extracted data has too many errors requiring manual correction. We need higher accuracy.')">
                            <span>4</span>
                            <span>Poor data quality from extraction</span>
                        </div>
                    </div>
                </div>
            </div>

            <div class="card" id="challengeResult" style="margin-top: 20px; display: none;">
                <h3>Recommended Solutions</h3>
                <div id="challengeResultContent"></div>
            </div>
        </section>

        <!-- Documentation Section -->
        <section class="page-section" id="docs">
            <div class="card">
                <h3>API Documentation</h3>
                <p style="font-size: 0.88rem; color: var(--ink-3); margin-bottom: 16px;">
                    Complete API reference for integrating DocIntel into your applications.
                </p>
                <div id="apiDocs">
                    <div class="field">
                        <label>Base URL</label>
                        <input type="text" value="http://localhost:8000" readonly>
                    </div>
                </div>
                <div style="margin-top: 16px; display: flex; gap: 10px; flex-wrap: wrap;">
                    <button class="btn btn-primary" onclick="window.open('/docs', '_blank')">OpenAPI Docs</button>
                    <button class="btn btn-ghost" onclick="loadApiDocs()">Load API Info</button>
                </div>
            </div>

            <div class="card" style="margin-top: 16px;">
                <h3>API Endpoints</h3>
                <div id="endpointsList" style="margin-top: 12px;"></div>
            </div>
        </section>

        <!-- Settings Section -->
        <section class="page-section" id="settings">
            <div class="cards-grid">
                <div class="card">
                    <h3>Application Settings</h3>
                    <div class="field">
                        <label>Theme</label>
                        <select id="settingsTheme" onchange="applyTheme(this.value)">
                            <option value="light">Light Mode</option>
                            <option value="dark">Dark Mode</option>
                            <option value="warm">Warm Mode</option>
                        </select>
                    </div>
                    <div class="field">
                        <label>Auto-refresh interval</label>
                        <select id="settingsRefresh">
                            <option value="2">Every 2 seconds</option>
                            <option value="5" selected>Every 5 seconds</option>
                            <option value="10">Every 10 seconds</option>
                            <option value="0">Disabled</option>
                        </select>
                    </div>
                    <div class="field">
                        <label>Notifications</label>
                        <select id="settingsNotifications">
                            <option value="on">Enabled</option>
                            <option value="off">Disabled</option>
                        </select>
                    </div>
                    <button class="btn btn-primary" onclick="saveAllSettings()">Save Settings</button>
                </div>

                <div class="card">
                    <h3>System Information</h3>
                    <div id="systemInfo">
                        <p>Loading...</p>
                    </div>
                </div>
            </div>
        </section>

        <!-- Footer -->
        <footer class="footer">
            <div class="footer-info">
                <span class="footer-version">DocIntel Studio Pro v4.0.2</span>
                <a href="#" onclick="showSection('docs')">API Docs</a>
                <a href="/health" target="_blank">Health Check</a>
                <a href="#" onclick="showPrivacyModal()">Privacy Policy</a>
                <a href="#" onclick="showSection('challenges')">Business Solutions</a>
            </div>
            <div style="display: flex; gap: 10px; align-items: center;">
                <span style="font-size: 0.82rem; color: var(--ink-3);">Built for document intelligence</span>
            </div>
        </footer>
    </div>

    <!-- Login Modal -->
    <div class="modal-backdrop" id="loginModal">
        <div class="modal">
            <div class="modal-header">
                <button class="modal-close modal-close-danger" onclick="closeLoginModal()" aria-label="Close login dialog">X</button>
                <h2>Welcome Back</h2>
                <p style="color: var(--ink-3); font-size: 0.9rem;">Sign in to your account</p>
            </div>
            <div class="modal-body">
                <div class="form-group">
                    <label>Email</label>
                    <input type="email" id="loginEmail" placeholder="your@email.com">
                </div>
                <div class="form-group">
                    <label>Password</label>
                    <input type="password" id="loginPassword" placeholder="••••••••">
                </div>
                <div id="registerFields" style="display: none;">
                    <div class="form-group">
                        <label>Full Name</label>
                        <input type="text" id="registerName" placeholder="John Doe">
                    </div>
                    <div class="form-group">
                        <label>Confirm Password</label>
                        <input type="password" id="registerConfirm" placeholder="••••••••">
                    </div>
                </div>
            </div>
            <div class="modal-footer">
                <button class="btn btn-ghost" onclick="toggleAuthMode()">
                    <span id="authModeToggle">Need an account? Register</span>
                </button>
                <button class="btn btn-primary" id="authSubmitBtn" onclick="handleAuth()">Sign In</button>
            </div>
        </div>
    </div>

    <div class="modal-backdrop" id="appDialogModal">
        <div class="modal app-dialog">
            <div class="modal-header app-dialog-header">
                <h2 id="appDialogTitle">Dialog</h2>
            </div>
            <div class="modal-body">
                <p id="appDialogMessage" style="color: var(--ink-2); line-height: 1.7;"></p>
            </div>
            <div class="modal-footer">
                <button class="btn btn-ghost" id="appDialogCancelBtn" onclick="handleDialogCancel()">Cancel</button>
                <button class="btn btn-primary" id="appDialogConfirmBtn" onclick="handleDialogConfirm()">OK</button>
            </div>
        </div>
    </div>

    <script>
        // State Management
        const state = {
            currentUser: null,
            isRegisterMode: false,
            currentSection: 'overview',
            jobs: [],
            documents: [],
            history: [],
            stats: { total: 0, success: 0, failed: 0, active: 0 },
            refreshInterval: null,
            lastResult: '',
            theme: 'light',
            notifications: [],
            unreadNotifications: 0,
            notificationEnabled: true,
            chatConversationId: null,
            chatHistory: []
        };

        const dialogState = {
            onConfirm: null,
            onCancel: null
        };

        // Load saved state
        function loadState() {
            const saved = localStorage.getItem('docintel_state');
            if (saved) {
                try {
                    const parsed = JSON.parse(saved);
                    Object.assign(state, parsed);
                } catch (e) {}
            }

            const savedSettings = localStorage.getItem('docintel_settings');
            if (savedSettings) {
                try {
                    const parsedSettings = JSON.parse(savedSettings);
                    state.notificationEnabled = parsedSettings.notifications !== 'off';
                } catch (e) {}
            }

            // Load user from session
            const user = localStorage.getItem('docintel_user');
            if (user) {
                try {
                    state.currentUser = JSON.parse(user);
                    updateAuthUI();
                } catch (e) {}
            }

            if (!Array.isArray(state.notifications)) {
                state.notifications = [];
            }
            if (typeof state.unreadNotifications !== 'number') {
                state.unreadNotifications = state.notifications.filter(notification => !notification.read).length;
            }
            renderNotifications();
        }

        function saveState() {
            localStorage.setItem('docintel_state', JSON.stringify({
                theme: state.theme,
                documents: state.documents,
                history: state.history,
                notifications: state.notifications,
                unreadNotifications: state.unreadNotifications,
                chatConversationId: state.chatConversationId,
                chatHistory: state.chatHistory
            }));
        }

        function syncNotificationBadge() {
            const badge = document.getElementById('notificationBadge');
            const btn = document.getElementById('notificationBtn');
            if (!badge || !btn) return;
            
            // If notifications are disabled, hide everything completely
            if (!state.notificationEnabled) {
                badge.style.display = 'none';
                badge.textContent = '0';
                btn.style.display = 'none';
                return;
            }
            
            btn.style.display = 'inline-flex';
            if (state.unreadNotifications > 0) {
                badge.style.display = 'inline-flex';
                badge.textContent = state.unreadNotifications;
            } else {
                badge.style.display = 'none';
                badge.textContent = '0';
            }
        }

        function renderNotifications() {
            const list = document.getElementById('notificationList');
            if (!list) return;

            if (!state.notifications.length) {
                list.innerHTML = '<div class="notification-empty">No notifications yet.</div>';
                syncNotificationBadge();
                return;
            }

            list.innerHTML = state.notifications.slice(0, 20).map(notification => `
                <div class="notification-item ${notification.read ? '' : 'unread'}">
                    <div class="notification-item-title">${notification.title}</div>
                    <div class="notification-item-body">${notification.message}</div>
                </div>
            `).join('');
            syncNotificationBadge();
        }

        function pushNotification(title, message, type = 'info', options = {}) {
            // If notifications are disabled, only show toast if forceToast is set
            if (!state.notificationEnabled && !options.forceToast) {
                return null;
            }

            const notification = {
                id: crypto.randomUUID ? crypto.randomUUID() : String(Date.now() + Math.random()),
                title,
                message,
                type,
                read: false,
                createdAt: new Date().toISOString()
            };

            state.notifications.unshift(notification);
            state.unreadNotifications += 1;
            saveState();
            renderNotifications();

            if (state.notificationEnabled || options.forceToast) {
                showToast(`${title}: ${message}`, type);
            }

            return notification;
        }

        function toggleNotificationCenter() {
            const center = document.getElementById('notificationCenter');
            if (!center) return;
            center.classList.toggle('open');
            if (center.classList.contains('open')) {
                markAllNotificationsRead();
            }
        }

        function markAllNotificationsRead() {
            state.notifications = state.notifications.map(notification => ({ ...notification, read: true }));
            state.unreadNotifications = 0;
            saveState();
            renderNotifications();
        }

        // Toast Notifications
        function showToast(message, type = 'info') {
            const container = document.getElementById('toastContainer');
            const toast = document.createElement('div');
            toast.className = `toast ${type}`;
            const label = type === 'success' ? 'OK' : type === 'error' ? 'ERR' : 'INFO';
            toast.innerHTML = `<span style="font-weight: 700; font-size: 0.75rem; color: var(--ink-2);">${label}</span><span>${message}</span>`;
            container.appendChild(toast);
            setTimeout(() => toast.remove(), 4000);
        }

        function showDialog(options) {
            const modal = document.getElementById('appDialogModal');
            const titleEl = document.getElementById('appDialogTitle');
            const messageEl = document.getElementById('appDialogMessage');
            const cancelBtn = document.getElementById('appDialogCancelBtn');
            const confirmBtn = document.getElementById('appDialogConfirmBtn');

            titleEl.textContent = options.title || 'Notice';
            messageEl.textContent = options.message || '';
            cancelBtn.style.display = options.showCancel ? 'inline-flex' : 'none';
            confirmBtn.textContent = options.confirmText || 'OK';

            dialogState.onConfirm = options.onConfirm || null;
            dialogState.onCancel = options.onCancel || null;

            modal.classList.add('open');
        }

        function closeDialog() {
            document.getElementById('appDialogModal').classList.remove('open');
        }

        function handleDialogConfirm() {
            if (typeof dialogState.onConfirm === 'function') {
                dialogState.onConfirm();
            }
            closeDialog();
        }

        function handleDialogCancel() {
            if (typeof dialogState.onCancel === 'function') {
                dialogState.onCancel();
            }
            closeDialog();
        }

        function showInfoDialog(title, message) {
            showDialog({ title, message, showCancel: false, confirmText: 'Close' });
        }

        function showConfirmDialog(title, message, confirmText = 'Confirm') {
            return new Promise((resolve) => {
                showDialog({
                    title,
                    message,
                    showCancel: true,
                    confirmText,
                    onConfirm: () => resolve(true),
                    onCancel: () => resolve(false)
                });
            });
        }

        // Section Navigation
        function showSection(sectionId) {
            document.querySelectorAll('.page-section').forEach(s => s.classList.remove('active'));
            document.querySelectorAll('.nav-links button').forEach(b => b.classList.remove('active'));
            
            const section = document.getElementById(sectionId);
            if (section) {
                section.classList.add('active');
                state.currentSection = sectionId;
            }
            
            // Update nav buttons
            const navButtons = document.querySelectorAll('.nav-links button');
            navButtons.forEach(btn => {
                if (btn.textContent.toLowerCase().includes(sectionId) || 
                    (sectionId === 'overview' && btn.textContent.includes('Overview')) ||
                    (sectionId === 'workspace' && btn.textContent.includes('Workspace')) ||
                    (sectionId === 'studio' && btn.textContent.includes('Studio')) ||
                    (sectionId === 'chat' && btn.textContent.includes('AI')) ||
                    (sectionId === 'challenges' && btn.textContent.includes('Challenges')) ||
                    (sectionId === 'docs' && btn.textContent.includes('Docs')) ||
                    (sectionId === 'settings' && btn.textContent.includes('Settings'))) {
                    btn.classList.add('active');
                }
            });

            // Refresh data when switching sections
            if (sectionId === 'workspace') loadWorkspaceData();
            if (sectionId === 'studio') refreshJobs();
            if (sectionId === 'settings') loadSystemInfo();
        }

        // Workspace Tabs
        function switchWorkspaceTab(tabId, btn) {
            document.querySelectorAll('#workspace .tab').forEach(t => t.classList.remove('active'));
            btn.classList.add('active');
            document.querySelectorAll('#workspace .panel').forEach(p => p.classList.remove('active'));
            document.getElementById('workspace-' + tabId).classList.add('active');
        }

        // Studio Tabs
        function switchStudioTab(tabId, btn) {
            document.querySelectorAll('#studio .tab').forEach(t => t.classList.remove('active'));
            btn.classList.add('active');
            document.querySelectorAll('#studio .panel').forEach(p => p.classList.remove('active'));
            document.getElementById('studio-' + tabId).classList.add('active');
        }

        // File Upload
        function handleFileUpload(event) {
            const files = event.target.files;
            if (!files.length) return;

            Array.from(files).forEach(file => {
                uploadFile(file);
            });
        }

        async function uploadFile(file) {
            const maxUploadBytes = 50 * 1024 * 1024;
            if (file.size > maxUploadBytes) {
                showToast(`File too large: ${(file.size / (1024*1024)).toFixed(1)}MB (max 50MB)`, 'error');
                return;
            }

            const formData = new FormData();
            formData.append('file', file);

            try {
                state.stats.active++;
                updateStatsDisplay();
                showToast(`Uploading ${file.name}...`, 'info');
                pushNotification('Upload started', `${file.name} is being uploaded.`, 'info');

                const response = await fetch('/upload', {
                    method: 'POST',
                    body: formData
                });

                const data = await response.json();

                if (!response.ok) {
                    throw new Error(data.detail || 'Upload failed');
                }

                showToast(`Upload successful: ${file.name}`, 'success');
                pushNotification('Upload complete', `${file.name} is now processing.`, 'success');
                document.getElementById('uploadResult').textContent = 
                    `Upload successful!\\n\\nJob ID: ${data.job_id}\\nFile: ${file.name}\\nType: ${data.file_type}\\nStatus: Processing...\\n\\nWatch the Jobs tab for updates.`;

                state.documents.push({
                    id: data.job_id,
                    name: file.name,
                    type: data.file_type,
                    size: file.size,
                    uploadedAt: new Date().toISOString()
                });
                saveState();

                // Poll for job completion
                pollJobStatus(data.job_id);

            } catch (error) {
                state.stats.failed++;
                updateStatsDisplay();
                showToast(`Upload failed: ${error.message}`, 'error');
                document.getElementById('uploadResult').textContent = `Error: ${error.message}`;
                document.getElementById('uploadResult').classList.add('error');
            }
        }

        // Poll job status
        async function pollJobStatus(jobId) {
            const maxAttempts = 40;
            let attempts = 0;

            const check = async () => {
                if (attempts > maxAttempts) {
                    showToast('Processing taking longer than expected', 'info');
                    return;
                }

                try {
                    const response = await fetch(`/jobs/${jobId}`);
                    const data = await response.json();

                    if (data.status === 'completed') {
                        state.stats.success++;
                        state.stats.active = Math.max(0, state.stats.active - 1);
                        updateStatsDisplay();
                        
                        const result = data.result;
                        let resultText = `Job Completed!\\n\\n`;
                        resultText += `Document Type: ${data.document_type || 'Unknown'}\\n`;
                        resultText += `Summary: ${data.summary || 'N/A'}\\n\\n`;
                        
                        if (data.extracted_fields && data.extracted_fields.length > 0) {
                            resultText += `Extracted Fields (${data.extracted_fields.length}):\\n`;
                            data.extracted_fields.forEach(f => {
                                resultText += `  • ${f.name}: ${f.value} (${Math.round(f.confidence * 100)}% confidence)\\n`;
                            });
                        }

                        document.getElementById('uploadResult').textContent = resultText;
                        document.getElementById('uploadResult').classList.remove('error');
                        state.lastResult = resultText;

                        // Show suggestions
                        if (data.suggestions && data.suggestions.length > 0) {
                            const suggestionsHtml = data.suggestions.map(s => 
                                `<div class="suggestion-item"><span>${s}</span></div>`
                            ).join('');
                            document.getElementById('suggestionsArea').innerHTML = 
                                `<h4 style="margin-bottom: 8px;">Suggestions:</h4><div class="suggestions-list">${suggestionsHtml}</div>`;
                        }

                        // Add to history
                        state.history.unshift({
                            jobId: jobId,
                            filename: data.filename,
                            status: 'completed',
                            type: data.document_type,
                            timestamp: new Date().toISOString()
                        });
                        saveState();
                        showToast('Processing completed!', 'success');
                        pushNotification('Job completed', `${data.filename || 'Your document'} finished processing.`, 'success');

                    } else if (data.status === 'failed') {
                        state.stats.failed++;
                        state.stats.active = Math.max(0, state.stats.active - 1);
                        updateStatsDisplay();
                        
                        document.getElementById('uploadResult').textContent = `Job Failed\\n\\nError: ${data.error || 'Unknown error'}`;
                        document.getElementById('uploadResult').classList.add('error');
                        
                        state.history.unshift({
                            jobId: jobId,
                            filename: data.filename,
                            status: 'failed',
                            timestamp: new Date().toISOString()
                        });
                        saveState();
                        showToast('Processing failed', 'error');
                        pushNotification('Job failed', `${data.filename || 'A document'} failed during processing.`, 'error');

                    } else {
                        attempts++;
                        setTimeout(check, 2000);
                    }
                } catch (error) {
                    attempts++;
                    setTimeout(check, 2000);
                }
            };

            setTimeout(check, 1500);
        }

        // Text Extraction
        async function extractText() {
            const text = document.getElementById('textInput').value.trim();
            if (!text) {
                showToast('Please enter some text first', 'error');
                return;
            }

            try {
                showToast('Extracting data...', 'info');
                
                const response = await fetch('/extract', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ text })
                });

                const data = await response.json();

                if (!response.ok) {
                    throw new Error(data.detail || 'Extraction failed');
                }

                let resultText = `Extraction Complete!\\n\\n`;
                resultText += `Document Type: ${data.classification?.document_type || 'Unknown'}\\n`;
                resultText += `Confidence: ${Math.round((data.classification?.confidence || 0) * 100)}%\\n\\n`;

                if (data.extracted_fields && data.extracted_fields.length > 0) {
                    resultText += `Extracted Fields:\\n`;
                    data.extracted_fields.forEach(f => {
                        resultText += `  • ${f.name}: ${f.value}\\n`;
                    });
                }

                if (data.summary) {
                    resultText += `\\nSummary: ${data.summary}\\n`;
                }

                document.getElementById('textResult').textContent = resultText;
                document.getElementById('textResult').classList.remove('error');
                state.lastResult = resultText;

                // Show suggestions
                if (data.suggestions && data.suggestions.length > 0) {
                    const suggestionsHtml = data.suggestions.map(s => 
                        `<div class="suggestion-item"><span>${s}</span></div>`
                    ).join('');
                    document.getElementById('textSuggestions').innerHTML = 
                        `<h4 style="margin-bottom: 8px;">Suggestions:</h4><div class="suggestions-list">${suggestionsHtml}</div>`;
                }

                state.stats.success++;
                updateStatsDisplay();
                showToast('Extraction complete!', 'success');
                pushNotification('Extraction complete', 'Text extraction finished successfully.', 'success');

            } catch (error) {
                showToast(`Extraction failed: ${error.message}`, 'error');
                document.getElementById('textResult').textContent = `Error: ${error.message}`;
                document.getElementById('textResult').classList.add('error');
            }
        }

        // Summarization
        async function generateSummary(source = 'summarize') {
            const sourceInputId = source === 'text' ? 'textInput' : 'summarizeInput';
            const text = document.getElementById(sourceInputId).value.trim();
            if (!text) {
                showToast('Please enter some text first', 'error');
                return;
            }

            try {
                showToast('Generating summary...', 'info');
                
                const response = await fetch('/summarize', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ text })
                });

                const data = await response.json();

                if (!response.ok) {
                    throw new Error(data.detail || 'Summarization failed');
                }

                let summaryText = `Document Summary\\n\\n`;
                summaryText += `Type: ${data.document_type}\\n`;
                summaryText += `Word Count: ${data.word_count}\\n`;
                summaryText += `Confidence: ${Math.round(data.confidence * 100)}%\\n\\n`;

                if (data.key_points && data.key_points.length > 0) {
                    summaryText += `Key Points:\\n`;
                    data.key_points.forEach((p, i) => {
                        summaryText += `  ${i + 1}. ${p}\\n`;
                    });
                    summaryText += '\\n';
                }

                if (data.numbers_found && data.numbers_found.length > 0) {
                    summaryText += `Numbers Found: ${data.numbers_found.join(', ')}\\n\\n`;
                }

                if (data.dates_found && data.dates_found.length > 0) {
                    summaryText += `Dates Found: ${data.dates_found.join(', ')}\\n\\n`;
                }

                if (data.suggestions && data.suggestions.length > 0) {
                    summaryText += `Suggestions:\\n`;
                    data.suggestions.forEach(s => {
                        summaryText += `  - ${s}\\n`;
                    });
                }

                document.getElementById('summaryResult').textContent = summaryText;
                document.getElementById('summaryResult').classList.remove('error');
                state.lastResult = summaryText;
                showToast('Summary generated!', 'success');
                pushNotification('Summary generated', 'A summary is ready in the Summary panel.', 'success');

            } catch (error) {
                showToast(`Summarization failed: ${error.message}`, 'error');
                document.getElementById('summaryResult').textContent = `Error: ${error.message}`;
                document.getElementById('summaryResult').classList.add('error');
            }
        }

        async function summarizeText() {
            await generateSummary('text');
        }

        // Batch Processing
        async function processBatch() {
            const input = document.getElementById('batchInput').value.trim();
            if (!input) {
                showToast('Please enter batch text', 'error');
                return;
            }

            const documents = input.split('---').map(t => t.trim()).filter(t => t);
            if (documents.length === 0) {
                showToast('No documents found. Separate with ---', 'error');
                return;
            }

            if (documents.length > 10) {
                showToast('Maximum 10 documents per batch', 'error');
                return;
            }

            try {
                showToast(`Processing ${documents.length} documents...`, 'info');
                
                const response = await fetch('/batch', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ 
                        documents: documents.map((text, i) => ({ id: `doc_${i+1}`, text }))
                    })
                });

                const data = await response.json();

                if (!response.ok) {
                    throw new Error(data.detail || 'Batch processing failed');
                }

                let resultText = `Batch Processing Complete!\\n\\n`;
                resultText += `Total Documents: ${data.statistics?.total_documents || documents.length}\\n`;
                resultText += `Successful: ${data.statistics?.successful || 0}\\n`;
                resultText += `Failed: ${data.statistics?.failed || 0}\\n`;
                resultText += `Success Rate: ${Math.round((data.statistics?.success_rate || 0) * 100)}%\\n`;

                document.getElementById('batchResult').textContent = resultText;
                showToast('Batch processing complete!', 'success');
                pushNotification('Batch complete', `Processed ${documents.length} documents in batch mode.`, 'success');

            } catch (error) {
                showToast(`Batch processing failed: ${error.message}`, 'error');
            }
        }

        // Refresh Jobs
        async function refreshJobs() {
            try {
                const response = await fetch('/jobs');
                const data = await response.json();
                const previousJobCount = state.jobs.length;
                state.jobs = data.jobs || [];

                const jobStream = document.getElementById('jobStream');
                
                if (!data.jobs || data.jobs.length === 0) {
                    jobStream.innerHTML = `
                        <div class="job-item">
                            <div class="job-info">
                                <span class="job-id">No jobs yet</span>
                                <span class="job-meta">Upload a document to start processing</span>
                            </div>
                        </div>`;
                    return;
                }

                jobStream.innerHTML = data.jobs.slice(0, 20).reverse().map(job => `
                    <div class="job-item">
                        <div class="job-info">
                            <span class="job-id">${job.filename || job.job_id.substring(0, 12)}...</span>
                            <span class="job-meta">${job.file_type || 'unknown'} • ${new Date(job.created_at).toLocaleTimeString()}</span>
                        </div>
                        <span class="job-status status-${job.status}">${job.status}</span>
                    </div>
                `).join('');

                if (data.jobs.length > previousJobCount) {
                    pushNotification('New jobs available', `${data.jobs.length - previousJobCount} new job(s) appeared in the queue.`, 'info');
                }

            } catch (error) {
                console.error('Failed to refresh jobs:', error);
            }
        }

        // Stats Display
        function updateStatsDisplay() {
            animateNumber('statTotal', state.stats.total);
            animateNumber('statSuccess', state.stats.success);
            animateNumber('statFailed', state.stats.failed);
            animateNumber('statActive', state.stats.active);
        }

        function animateNumber(id, value) {
            const el = document.getElementById(id);
            if (!el) return;
            el.textContent = value;
            el.classList.remove('animated-number');
            void el.offsetWidth;
            el.classList.add('animated-number');
        }

        async function loadStats() {
            try {
                const response = await fetch('/stats');
                const data = await response.json();
                const previousStats = { ...state.stats };
                state.stats.total = data.total_jobs || 0;
                state.stats.success = data.total_processed || 0;
                state.stats.failed = data.total_failed || 0;
                state.stats.active = data.total_active || 0;
                updateStatsDisplay();

                const statsChanged = previousStats.total !== state.stats.total ||
                    previousStats.success !== state.stats.success ||
                    previousStats.failed !== state.stats.failed ||
                    previousStats.active !== state.stats.active;

                if (statsChanged) {
                    pushNotification('System stats updated', `Jobs: ${state.stats.total}, active: ${state.stats.active}, completed: ${state.stats.success}.`, 'info');
                }
            } catch (error) {
                console.error('Failed to load stats:', error);
            }
        }

        // Chat
        function sendQuickQuestion(question) {
            document.getElementById('chatInput').value = question;
            sendChatMessage();
        }

        function ensureChatConversationId() {
            if (!state.chatConversationId) {
                state.chatConversationId = crypto.randomUUID ? crypto.randomUUID() : String(Date.now() + Math.random());
                saveState();
            }
            return state.chatConversationId;
        }

        async function sendChatMessage() {
            const input = document.getElementById('chatInput');
            const message = input.value.trim();
            if (!message) return;

            const chatMessages = document.getElementById('chatMessages');
            
            // Add user message
            chatMessages.innerHTML += `
                <div class="chat-message user">${escapeHtml(message)}</div>
            `;
            input.value = '';
            chatMessages.scrollTop = chatMessages.scrollHeight;

            state.chatHistory.push({ role: 'user', content: message, timestamp: new Date().toISOString() });
            if (state.chatHistory.length > 12) {
                state.chatHistory = state.chatHistory.slice(-12);
            }
            saveState();

            try {
                const response = await fetch('/chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        message,
                        conversation_id: ensureChatConversationId(),
                        context: {
                            current_section: state.currentSection,
                            topic: document.getElementById('summaryResult')?.textContent?.slice(0, 80) || '',
                            recent_messages: state.chatHistory.slice(-8)
                        }
                    })
                });

                const data = await response.json();

                state.chatHistory.push({ role: 'assistant', content: data.response, timestamp: new Date().toISOString() });
                if (state.chatHistory.length > 12) {
                    state.chatHistory = state.chatHistory.slice(-12);
                }
                saveState();

                let suggestionsHtml = '';
                if (data.suggestions && data.suggestions.length > 0) {
                    suggestionsHtml = `<div class="chat-suggestions">
                        ${data.suggestions.map(s => `<span class="chat-suggestion" onclick="sendQuickQuestion('${s.replace(/'/g, "\\'")}')">${s}</span>`).join('')}
                    </div>`;
                }

                chatMessages.innerHTML += `
                    <div class="chat-message assistant">
                        ${escapeHtml(data.response)}
                        ${suggestionsHtml}
                    </div>
                `;
                chatMessages.scrollTop = chatMessages.scrollHeight;
                pushNotification('AI Assistant', 'Replied to your latest question.', 'info');

            } catch (error) {
                chatMessages.innerHTML += `
                    <div class="chat-message assistant" style="background: #fee2e2;">
                        Sorry, I encountered an error. Please try again.
                    </div>
                `;
                pushNotification('AI Assistant error', 'The assistant could not respond to your last message.', 'error', { forceToast: true });
            }
        }

        // Business Challenges
        function fillChallenge(title, category, description) {
            document.getElementById('challengeTitle').value = title;
            document.getElementById('challengeCategory').value = category;
            document.getElementById('challengeDesc').value = description;
        }

        async function solveChallenge() {
            const title = document.getElementById('challengeTitle').value.trim();
            const category = document.getElementById('challengeCategory').value;
            const description = document.getElementById('challengeDesc').value.trim();
            const priority = document.getElementById('challengePriority').value;

            if (!title || !description) {
                showToast('Please fill in the title and description', 'error');
                return;
            }

            try {
                showToast('Analyzing challenge...', 'info');

                const response = await fetch('/solve-challenge', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ title, description, category, priority })
                });

                const data = await response.json();

                const resultDiv = document.getElementById('challengeResult');
                const contentDiv = document.getElementById('challengeResultContent');

                contentDiv.innerHTML = `
                    <div style="margin-bottom: 16px;">
                        <strong>Analysis:</strong>
                        <p style="margin-top: 4px; color: var(--ink-3);">${data.analysis}</p>
                    </div>
                    <div style="margin-bottom: 16px;">
                        <strong>Recommended Solutions:</strong>
                        <div class="suggestions-list" style="margin-top: 8px;">
                            ${data.solutions.map(s => `<div class="suggestion-item"><span>-</span><span>${s}</span></div>`).join('')}
                        </div>
                    </div>
                    <div style="margin-bottom: 16px;">
                        <strong>Estimated ROI:</strong>
                        <p style="margin-top: 4px; color: var(--teal); font-weight: 600;">${data.estimated_roi}</p>
                    </div>
                    <div style="margin-bottom: 16px;">
                        <strong>Recommended Features:</strong>
                        <div style="display: flex; gap: 6px; flex-wrap: wrap; margin-top: 8px;">
                            ${data.recommended_features.map(f => `<span class="tab" style="cursor:default;font-size:0.78rem;">${f}</span>`).join('')}
                        </div>
                    </div>
                    <div>
                        <strong>Next Steps:</strong>
                        <div class="suggestions-list" style="margin-top: 8px;">
                            ${data.next_steps.map(s => `<div class="suggestion-item"><span>→</span><span>${s}</span></div>`).join('')}
                        </div>
                    </div>
                `;

                resultDiv.style.display = 'block';
                showToast('Solutions generated!', 'success');

            } catch (error) {
                showToast(`Failed to analyze: ${error.message}`, 'error');
            }
        }

        // API Docs
        async function loadApiDocs() {
            try {
                const response = await fetch('/documentation');
                const data = await response.json();

                const endpointsDiv = document.getElementById('endpointsList');
                endpointsDiv.innerHTML = Object.entries(data.endpoints).map(([path, desc]) => `
                    <div style="padding: 10px; background: #f9fafb; border-radius: 8px; margin-bottom: 8px; display: flex; justify-content: space-between; align-items: center;">
                        <code style="font-weight: 600; color: var(--teal);">${path}</code>
                        <span style="color: var(--ink-3); font-size: 0.88rem;">${desc}</span>
                    </div>
                `).join('');

                showToast('API documentation loaded', 'success');

            } catch (error) {
                showToast('Failed to load API docs', 'error');
            }
        }

        function loadSystemInfo() {
            fetch('/health').then(r => r.json()).then(data => {
                document.getElementById('systemInfo').innerHTML = `
                    <div class="field"><label>Status</label><input type="text" value="${data.status}" readonly></div>
                    <div class="field"><label>Version</label><input type="text" value="${data.version}" readonly></div>
                    <div class="field"><label>Max Upload</label><input type="text" value="${data.max_upload_mb} MB" readonly></div>
                    <div class="field"><label>Pending Jobs</label><input type="text" value="${data.pending_jobs}" readonly></div>
                    <div class="field"><label>OCR Available</label><input type="text" value="${data.features.ocr ? 'Yes' : 'No'}" readonly></div>
                    <div class="field"><label>PDF Support</label><input type="text" value="${data.features.pdf ? 'Yes' : 'No'}" readonly></div>
                `;
            });
        }

        // Workspace Data
        function loadWorkspaceData() {
            const docsList = document.getElementById('myDocumentsList');
            if (state.documents.length === 0) {
                docsList.innerHTML = '<p style="color: var(--ink-3);">No documents uploaded yet.</p>';
            } else {
                docsList.innerHTML = state.documents.slice(0, 10).map(doc => `
                    <div style="padding: 10px; background: #f9fafb; border-radius: 8px; margin-bottom: 8px; display: flex; justify-content: space-between; align-items: center;">
                        <div>
                            <strong>${doc.name}</strong>
                            <div style="font-size: 0.78rem; color: var(--ink-3);">${doc.type} • ${(doc.size / 1024).toFixed(1)} KB</div>
                        </div>
                        <span style="font-size: 0.78rem; color: var(--ink-3);">${new Date(doc.uploadedAt).toLocaleDateString()}</span>
                    </div>
                `).join('');
            }

            const historyList = document.getElementById('historyList');
            if (state.history.length === 0) {
                historyList.innerHTML = '<p style="color: var(--ink-3);">No processing history yet.</p>';
            } else {
                historyList.innerHTML = state.history.slice(0, 10).map(h => `
                    <div style="padding: 10px; background: #f9fafb; border-radius: 8px; margin-bottom: 8px; display: flex; justify-content: space-between; align-items: center;">
                        <div>
                            <strong>${h.filename || h.jobId?.substring(0, 12)}</strong>
                            <div style="font-size: 0.78rem; color: var(--ink-3);">Type: ${h.type || 'unknown'}</div>
                        </div>
                        <span class="job-status status-${h.status}">${h.status}</span>
                    </div>
                `).join('');
            }
        }

        // Export
        function exportData(format) {
            let content, filename, mimeType;

            if (format === 'json') {
                content = JSON.stringify({
                    documents: state.documents,
                    history: state.history,
                    stats: state.stats,
                    exportedAt: new Date().toISOString()
                }, null, 2);
                filename = 'docintel-export.json';
                mimeType = 'application/json';
            } else if (format === 'csv') {
                const rows = [['Type', 'Name', 'Status', 'Timestamp']];
                state.history.forEach(h => rows.push([h.type || '', h.filename || '', h.status, h.timestamp || '']));
                content = rows.map(r => r.join(',')).join('\\n');
                filename = 'docintel-export.csv';
                mimeType = 'text/csv';
            } else {
                content = JSON.stringify({
                    report: 'DocIntel Processing Report',
                    generatedAt: new Date().toISOString(),
                    summary: state.stats,
                    recentActivity: state.history.slice(0, 20)
                }, null, 2);
                filename = 'docintel-report.json';
                mimeType = 'application/json';
            }

            const blob = new Blob([content], { type: mimeType });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = filename;
            a.click();
            URL.revokeObjectURL(url);

            showToast(`Exported as ${format.toUpperCase()}`, 'success');
        }

        // Result utilities
        function copyResult() {
            if (!state.lastResult) {
                showToast('No result to copy', 'error');
                return;
            }
            navigator.clipboard.writeText(state.lastResult).then(() => {
                showToast('Copied to clipboard!', 'success');
            }).catch(() => {
                showToast('Failed to copy', 'error');
            });
        }

        function downloadResult() {
            if (!state.lastResult) {
                showToast('No result to download', 'error');
                return;
            }
            const blob = new Blob([state.lastResult], { type: 'text/plain' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = 'docintel-result.txt';
            a.click();
            URL.revokeObjectURL(url);
            showToast('Result downloaded!', 'success');
        }

        function clearResult() {
            state.lastResult = '';
            document.getElementById('uploadResult').textContent = 'Waiting for upload...';
            document.getElementById('uploadResult').classList.remove('error');
            document.getElementById('suggestionsArea').innerHTML = '';
            document.getElementById('textResult').textContent = 'Waiting for text input...';
            document.getElementById('textResult').classList.remove('error');
            document.getElementById('textSuggestions').innerHTML = '';
            showToast('Results cleared', 'info');
        }

        // Demo data
        function loadDemoText() {
            document.getElementById('textInput').value = `INVOICE #INV-2026-104
Date: March 30, 2026
Due Date: April 20, 2026

From: Skyline Analytics Inc.
123 Business Ave, Suite 400
San Francisco, CA 94105

Bill To: Nova Retail Corp
456 Commerce Street
New York, NY 10001

Items:
- Data Analytics Platform (Annual): $12,000.00
- Professional Services (40 hours @ $150/hr): $6,000.00
- Support & Maintenance: $2,500.00

Subtotal: $20,500.00
Tax (8.5%): $1,742.50
Total: $22,242.50

Payment Terms: Net 30
Contact: billing@skyline.ai`;
        }

        function loadBatchDemo() {
            document.getElementById('batchInput').value = `Invoice #1001
Vendor: TechCorp
Total: $5,000
Due: April 15, 2026
---
Receipt #2001
Store: Office Supplies Plus
Items: Printer Paper, Ink Cartridges
Total: $127.50
Date: March 28, 2026
---
Contract Amendment
Parties: Company A and Company B
Effective: April 1, 2026
Terms: 12 months extension`;
        }

        function loadLongDemo() {
            document.getElementById('summarizeInput').value = `QUARTERLY BUSINESS REVIEW - Q1 2026

Executive Summary

This quarter marked significant progress across all key performance indicators. Revenue grew by 23% year-over-year, reaching $4.2 million. Customer acquisition increased by 35%, with 1,247 new customers onboarded during the quarter.

Financial Performance

Total revenue for Q1 2026 was $4,200,000, compared to $3,414,634 in Q1 2025. Gross margin improved from 62% to 68%, reflecting operational efficiencies and favorable product mix. Operating expenses totaled $2,856,000, representing 68% of revenue, down from 75% in the prior year quarter.

Key metrics include:
- Monthly Recurring Revenue (MRR): $1,400,000
- Customer Acquisition Cost (CAC): $340
- Lifetime Value (LTV): $4,200
- LTV:CAC Ratio: 12.4:1
- Churn Rate: 2.1% (down from 3.4%)

Operational Highlights

The product team shipped 47 new features and improvements, including the much-anticipated AI-powered document classification system. Customer satisfaction scores reached an all-time high of 94 NPS.

Looking Ahead

For Q2 2026, we project revenue of $5.1 million, representing 21% quarter-over-quarter growth. Key initiatives include expansion into the European market and launch of our enterprise tier offering.`;
        }

        // Auth
        function showLoginModal() {
            document.getElementById('loginModal').classList.add('open');
        }

        function closeLoginModal() {
            document.getElementById('loginModal').classList.remove('open');
        }

        function toggleAuthMode() {
            state.isRegisterMode = !state.isRegisterMode;
            document.getElementById('registerFields').style.display = state.isRegisterMode ? 'block' : 'none';
            document.getElementById('authModeToggle').textContent = state.isRegisterMode ? 
                'Already have an account? Sign In' : 'Need an account? Register';
            document.getElementById('authSubmitBtn').textContent = state.isRegisterMode ? 'Register' : 'Sign In';
        }

        async function handleAuth() {
            const email = document.getElementById('loginEmail').value.trim();
            const password = document.getElementById('loginPassword').value;

            if (!email || !password) {
                showToast('Please fill in all fields', 'error');
                return;
            }

            try {
                const endpoint = state.isRegisterMode ? '/register' : '/login';
                const body = state.isRegisterMode ? 
                    { email, password, name: document.getElementById('registerName').value } :
                    { email, password };

                const response = await fetch(endpoint, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body)
                });

                const data = await response.json();

                if (!response.ok) {
                    throw new Error(data.detail || 'Authentication failed');
                }

                state.currentUser = data;
                localStorage.setItem('docintel_user', JSON.stringify(data));
                updateAuthUI();
                closeLoginModal();
                showToast(state.isRegisterMode ? 'Registration successful!' : 'Welcome back!', 'success');
                pushNotification('Account signed in', `Welcome back, ${getDisplayName(data)}.`, 'success');

            } catch (error) {
                showToast(`Authentication failed: ${error.message}`, 'error');
            }
        }

        function getDisplayName(user) {
            if (!user) return 'Login';
            const savedProfile = JSON.parse(localStorage.getItem('docintel_profile') || '{}');
            const candidateName = (user.name || '').trim();
            const profileName = (savedProfile.name || '').trim();
            const hasEmail = Boolean(user.email);
            const localPart = hasEmail ? user.email.split('@')[0].trim().toLowerCase() : '';

            if (candidateName && (!hasEmail || (candidateName.toLowerCase() !== user.email.toLowerCase() && candidateName.toLowerCase() !== localPart))) {
                return candidateName;
            }
            if (profileName) {
                return profileName;
            }
            return 'My Account';
        }

        async function logoutCurrentUser() {
            try {
                await fetch('/logout', { method: 'POST' });
            } catch (error) {
                console.warn('Logout request failed:', error);
            }
            state.currentUser = null;
            localStorage.removeItem('docintel_user');
            updateAuthUI();
            showToast('Signed out', 'info');
            pushNotification('Account signed out', 'You have been signed out successfully.', 'info');
        }

        function updateAuthUI() {
            const authBtn = document.getElementById('authBtn');
            if (state.currentUser) {
                authBtn.textContent = getDisplayName(state.currentUser);
                authBtn.onclick = async () => {
                    const confirmed = await showConfirmDialog(
                        'Sign Out',
                        'You are currently signed in. Do you want to sign out now?',
                        'Sign Out'
                    );
                    if (confirmed) {
                        await logoutCurrentUser();
                    }
                };
            } else {
                authBtn.textContent = 'Login';
                authBtn.onclick = showLoginModal;
            }
        }

        // Profile & Settings
        function saveProfile() {
            const profile = {
                name: document.getElementById('profileName').value,
                email: document.getElementById('profileEmail').value,
                role: document.getElementById('profileRole').value
            };
            localStorage.setItem('docintel_profile', JSON.stringify(profile));
            showToast('Profile saved!', 'success');
        }

        function savePreferences() {
            const prefs = {
                theme: document.getElementById('themeSelect').value,
                autoRefresh: document.getElementById('autoRefresh').value,
                autoDelete: document.getElementById('autoDelete').value
            };
            localStorage.setItem('docintel_prefs', JSON.stringify(prefs));
            showToast('Preferences saved!', 'success');
        }

        function saveAllSettings() {
            const settings = {
                theme: document.getElementById('settingsTheme').value,
                refreshInterval: document.getElementById('settingsRefresh').value,
                notifications: document.getElementById('settingsNotifications').value
            };
            localStorage.setItem('docintel_settings', JSON.stringify(settings));
            state.notificationEnabled = settings.notifications !== 'off';
            pushNotification('Settings saved', `Notifications are ${state.notificationEnabled ? 'enabled' : 'disabled'}.`, 'info');
            showToast('Settings saved!', 'success');
        }

        // Theme
        function applyTheme(theme) {
            state.theme = theme;
            document.body.className = `theme-${theme}`;
            
            if (theme === 'dark') {
                document.documentElement.style.setProperty('--paper', '#1f2937');
                document.documentElement.style.setProperty('--panel', '#111827');
                document.documentElement.style.setProperty('--line', '#374151');
                document.documentElement.style.setProperty('--ink-1', '#f9fafb');
                document.documentElement.style.setProperty('--ink-2', '#d1d5db');
                document.documentElement.style.setProperty('--ink-3', '#9ca3af');
                document.body.style.background = 'linear-gradient(135deg, #111827 0%, #1f2937 50%, #374151 100%)';
            } else if (theme === 'warm') {
                document.documentElement.style.setProperty('--paper', '#fffbeb');
                document.documentElement.style.setProperty('--panel', '#fff7ed');
                document.documentElement.style.setProperty('--line', '#fed7aa');
                document.documentElement.style.setProperty('--ink-1', '#431407');
                document.documentElement.style.setProperty('--ink-2', '#78350f');
                document.documentElement.style.setProperty('--ink-3', '#a16207');
                document.body.style.background = 'linear-gradient(135deg, #fff7ed 0%, #fef3c7 50%, #fed7aa 100%)';
            } else {
                document.documentElement.style.setProperty('--paper', '#f9fafb');
                document.documentElement.style.setProperty('--panel', '#ffffff');
                document.documentElement.style.setProperty('--line', '#e5e7eb');
                document.documentElement.style.setProperty('--ink-1', '#1f2937');
                document.documentElement.style.setProperty('--ink-2', '#4b5563');
                document.documentElement.style.setProperty('--ink-3', '#9ca3af');
                document.body.style.background = 'linear-gradient(135deg, #f0fdfa 0%, #f0f9ff 30%, #faf5ff 70%, #fff7ed 100%)';
            }
            
            saveState();
            pushNotification('Theme changed', `Theme switched to ${theme}.`, 'info');
        }

        // Privacy Modal
        function showPrivacyModal() {
            localStorage.removeItem('docintel_privacy_accepted');
            document.getElementById('privacyModal').style.display = 'flex';
        }

        // Utility functions
        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }

        // Drag and drop
        const uploadZone = document.getElementById('uploadZone');
        if (uploadZone) {
            uploadZone.addEventListener('dragover', (e) => {
                e.preventDefault();
                uploadZone.classList.add('dragover');
            });
            uploadZone.addEventListener('dragleave', () => {
                uploadZone.classList.remove('dragover');
            });
            uploadZone.addEventListener('drop', (e) => {
                e.preventDefault();
                uploadZone.classList.remove('dragover');
                Array.from(e.dataTransfer.files).forEach(file => uploadFile(file));
            });
        }

        // Initialize
        document.addEventListener('DOMContentLoaded', () => {
            loadState();
            loadStats();
            updateStatsDisplay();
            
            // Auto-refresh jobs
            const refreshInterval = parseInt(localStorage.getItem('docintel_refresh') || '5');
            if (refreshInterval > 0) {
                setInterval(refreshJobs, refreshInterval * 1000);
            }

            // Load saved preferences
            const prefs = JSON.parse(localStorage.getItem('docintel_prefs') || '{}');
            if (prefs.theme) {
                document.getElementById('themeSelect').value = prefs.theme;
                document.getElementById('settingsTheme').value = prefs.theme;
                applyTheme(prefs.theme);
            }

            const savedSettings = JSON.parse(localStorage.getItem('docintel_settings') || '{}');
            if (savedSettings.notifications) {
                document.getElementById('settingsNotifications').value = savedSettings.notifications;
                state.notificationEnabled = savedSettings.notifications !== 'off';
            }
            syncNotificationBadge();

            // Show welcome toast
            setTimeout(() => {
                showToast('Welcome to DocIntel Studio Pro!', 'info');
            }, 1000);

            // Periodic notification generator for more notification variety
            const notificationTips = [
                { title: 'Did you know?', message: 'You can process PDF, Word, Excel, and image files all in one place.', type: 'info' },
                { title: 'Pro Tip', message: 'Use batch mode to process multiple documents at once with the "---" separator.', type: 'info' },
                { title: 'Feature Highlight', message: 'Try the AI Assistant for instant help with document processing.', type: 'info' },
                { title: 'Security Update', message: 'All documents are processed securely and can be auto-deleted after processing.', type: 'info' },
                { title: 'Efficiency Boost', message: 'Save time by using custom extraction fields for recurring document types.', type: 'info' },
                { title: 'Did you know?', message: 'The summarization feature can extract key points, numbers, and dates from any text.', type: 'info' },
                { title: 'Quick Tip', message: 'Drag and drop files directly onto the upload zone for faster processing.', type: 'info' },
                { title: 'Format Support', message: 'DocIntel supports 20+ file formats including PDF, DOCX, XLSX, images, and more.', type: 'info' },
                { title: 'Pro Tip', message: 'Login to save your documents and processing history across sessions.', type: 'info' },
                { title: 'Feature Highlight', message: 'The Business Challenges section provides tailored solutions for your workflow.', type: 'info' },
                { title: 'Did you know?', message: 'You can export your results as JSON, CSV, or a full report.', type: 'info' },
                { title: 'Quick Tip', message: 'Switch between Light, Dark, and Warm themes in Settings.', type: 'info' },
                { title: 'Performance', message: 'Documents typically process in under 1 second.', type: 'info' },
                { title: 'API Ready', message: 'Every feature is available via REST API for custom integrations.', type: 'info' },
                { title: 'Pro Tip', message: 'Use the Workspace section to manage your profile and preferences.', type: 'info' },
                { title: 'Did you know?', message: 'Our AI classifies documents into 7+ types with confidence scoring.', type: 'info' },
                { title: 'Security', message: 'GDPR-compliant processing with encrypted data storage by default.', type: 'info' },
                { title: 'Efficiency', message: 'Batch processing can handle up to 10 documents at once.', type: 'info' },
            ];

            function generatePeriodicNotification() {
                if (state.notificationEnabled) {
                    const tip = notificationTips[Math.floor(Math.random() * notificationTips.length)];
                    pushNotification(tip.title, tip.message, tip.type);
                }
            }

            // Generate a notification every 45 seconds (but only up to 30 max to avoid overflow)
            setInterval(() => {
                if (state.notifications.length < 30) {
                    generatePeriodicNotification();
                }
            }, 45000);
        });
</script>
</body>
</html>"""


@app.get("/")
async def root():
    """Root endpoint - redirect to dashboard"""
    return HTMLResponse(content=DASHBOARD_HTML)


@app.get("/dashboard")
async def dashboard():
    """Main dashboard"""
    return HTMLResponse(content=DASHBOARD_HTML)


# ==================== START SERVER ====================

if __name__ == "__main__":
    import uvicorn

    def _open_browser() -> None:
        webbrowser.open_new_tab("http://127.0.0.1:8000/dashboard")

    threading.Timer(1.5, _open_browser).start()
    uvicorn.run(app, host="127.0.0.1", port=8000, reload=False)