# Document Intelligence System - Quick Start Guide

## 🚀 Getting Started in 5 Minutes

### Option 1: Local Installation (Using Python)

#### Step 1: Install Dependencies
```bash
# Install Python dependencies
pip install -r requirements.txt

# Install Tesseract OCR
# Windows: Download from https://github.com/UB-Mannheim/tesseract/wiki
# Linux: sudo apt-get install tesseract-ocr
# macOS: brew install tesseract
```

#### Step 2: Run the Application
```bash
# Start the API server
python main.py
```

#### Step 3: Access Dashboard
Open your browser and go to:
```
http://localhost:8000/dashboard
```

---

### Option 2: Docker Installation

#### Step 1: Build and Run
```bash
# Using Docker Compose (recommended)
docker-compose up -d

# Or using Docker directly
docker build -t doc-intelligence .
docker run -p 8000:8000 doc-intelligence
```

#### Step 2: Access Dashboard
```
http://localhost:8000/dashboard
```

---

## 📖 Usage Examples

### 1. Extract Data from Text

#### Via Dashboard
1. Go to the "Extract from Text" card
2. Paste your document text
3. Click "Extract Data"
4. View results

#### Via API
```bash
curl -X POST "http://localhost:8000/extract" \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Invoice #123 Amount Due: $500"
  }'
```

#### Via Python
```python
import asyncio
from app.pipeline import DocumentProcessingPipeline

async def main():
    pipeline = DocumentProcessingPipeline()
    result = await pipeline.process_document(
        document_id="doc_001",
        text="Your document text here"
    )
    print(result.classification.document_type)
    print(result.extraction.extracted_fields)

asyncio.run(main())
```

### 2. Upload Document

#### Via Dashboard
1. Click "Upload Document" card
2. Select a file (image, PDF, or text)
3. System processes automatically
4. View results in "Results" tab

#### Via API
```bash
curl -X POST "http://localhost:8000/upload" \
  -F "file=@document.pdf"
```

### 3. Batch Processing

#### Via API
```bash
curl -X POST "http://localhost:8000/batch" \
  -H "Content-Type: application/json" \
  -d '{
    "documents": [
      {"text": "Document 1 text"},
      {"text": "Document 2 text"},
      {"text": "Document 3 text"}
    ]
  }'
```

#### Via Python
```python
documents = [
    {"id": "doc_1", "text": "Invoice text..."},
    {"id": "doc_2", "text": "Receipt text..."}
]
results = asyncio.run(pipeline.process_batch(documents))
stats = pipeline.get_statistics(results)
print(stats)
```

---

## 🔍 Interactive Dashboard Features

### Features Available
- ✅ **Upload Documents** - Upload images, PDFs, or text files
- ✅ **Extract Data** - Extract structured data from text
- ✅ **View Results** - See extraction and validation results in real-time
- ✅ **Monitor Jobs** - Track processing jobs and their status
- ✅ **View Statistics** - Monitor system performance
- ✅ **API Documentation** - Browse available endpoints

### Navigation
| Tab | Purpose |
|-----|---------|
| Results | View processing results |
| Jobs | Monitor active and completed jobs |
| API Docs | View API endpoint documentation |

---

## 📊 How It Works

### Processing Pipeline
```
Document Input
    ↓
[OCR] Extract text from images
    ↓
[Classify] Determine document type
    ↓
[Extract] Pull out structured data
    ↓
[Validate] Check data quality
    ↓
Results & Insights
```

### Example: Processing an Invoice

1. **Input**: Invoice image or text
2. **Classification**: "invoice" (95% confidence)
3. **Extraction**: 
   - Invoice Number: INV-2024-001
   - Date: 01/15/2024
   - Amount: $500.00
   - Vendor: Acme Corp
4. **Validation**: 
   - All required fields present ✓
   - Data format correct ✓
   - Quality score: 92%
5. **Output**: Structured JSON with confidence scores

---

## 🎯 Supported Document Types

1. **Invoice** - Bill, sales invoice, receipt
2. **Receipt** - Purchase receipt, transaction record
3. **Contract** - Legal agreement, terms & conditions
4. **Report** - Business report, analysis
5. **Email** - Email message, correspondence
6. **Form** - Application, questionnaire
7. **Letter** - Business letter, notification

---

## ⚙️ Configuration

### Environment Variables
Create a `.env` file in the root directory:

```env
# API Configuration
API_HOST=0.0.0.0
API_PORT=8000
DEBUG=false

# Database
DATABASE_URL=sqlite:///./documents.db

# OCR Settings
OCR_LANG=eng
OCR_PSM=3

# File Upload
UPLOAD_DIR=./uploads
MAX_FILE_SIZE=52428800

# Logging
LOG_LEVEL=INFO
```

### For Production
```env
# Use PostgreSQL instead of SQLite
DATABASE_URL=postgresql://user:password@localhost/doc_intelligence

# Use Redis for caching
REDIS_URL=redis://localhost:6379

# Enable CORS
ENABLE_CORS=true

# Disable debug mode
DEBUG=false
```

---

## 🧪 Testing

### Run Examples
```bash
python examples.py
```

This will:
- ✓ Process a single invoice document
- ✓ Batch process multiple documents  
- ✓ Demonstrate custom field extraction
- ✓ Show validation results

### Check Health
```bash
curl http://localhost:8000/health
```

---

## 📈 API Endpoints

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/` | API information |
| GET | `/health` | Health check |
| POST | `/upload` | Upload document file |
| POST | `/extract` | Extract from text |
| POST | `/batch` | Batch process documents |
| GET | `/jobs` | List all jobs |
| GET | `/jobs/{job_id}` | Get job status |
| GET | `/stats` | System statistics |
| GET | `/dashboard` | Web dashboard |

---

## 🐳 Docker-Compose Services

The included `docker-compose.yml` sets up:

1. **App** - Main FastAPI application (port 8000)
2. **PostgreSQL** - Database (port 5432)
3. **Redis** - Cache/Queue (port 6379)

### Start All Services
```bash
docker-compose up -d
```

### View Logs
```bash
docker-compose logs -f app
```

### Stop Services
```bash
docker-compose down
```

---

## 📁 Project Structure

```
Agentic-Doc-Intelligence/
├── agents/
│   ├── classifier.py      # Document classification
│   ├── extractor.py       # Data extraction
│   └── validator.py       # Data validation
├── tools/
│   ├── ocr_engine.py      # OCR processing
│   └── table_parser.py    # Table extraction
├── app/
│   ├── pipeline.py        # Main pipeline
│   ├── main.py            # FastAPI app
│   └── README.md          # Documentation
├── database.py            # Database models
├── settings.py            # Configuration
├── utils.py               # Utilities
├── examples.py            # Usage examples
├── requirements.txt       # Dependencies
├── Dockerfile             # Docker image
├── docker-compose.yml     # Docker services
└── .env.example           # Environment template
```

---

## 🚨 Troubleshooting

### "Tesseract not found" Error
**Solution**: Install Tesseract OCR
- Windows: Download installer from [GitHub](https://github.com/UB-Mannheim/tesseract/wiki)
- Linux: `sudo apt-get install tesseract-ocr`
- macOS: `brew install tesseract`

### "Port 8000 already in use" Error
**Solution**: Change port in settings or stop the service using port 8000

### Low OCR Accuracy
**Tips**:
- Ensure image resolution is at least 150 DPI
- Keep documents upright and well-lit
- Use PNG or TIFF formats
- Remove noise/shadows if possible

### Database Errors
**Solution**: Reset the database
```bash
rm documents.db
python database.py
```

---

## 📚 Additional Resources

- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [Tesseract OCR](https://github.com/tesseract-ocr)
- [SQLAlchemy ORM](https://docs.sqlalchemy.org/)
- [OpenCV Documentation](https://opencv.org/)

---

## 🎓 Learning Path

1. Start with the **Dashboard** to familiarize yourself
2. Run **examples.py** to see different capabilities
3. Try the **API** using curl or Python
4. Explore the **source code** to understand the architecture
5. Customize for your use case with **custom fields and schemas**

---

## 💡 Tips & Tricks

- **Batch Processing**: Process multiple documents at once for efficiency
- **Custom Fields**: Define extraction patterns for your specific needs
- **Validation Schemas**: Create custom validation rules
- **Caching**: Enable Redis for faster repeated processing
- **Database**: Upgrade to PostgreSQL for production use

---

## 📞 Support

For issues or questions:
1. Check the [detailed README](app/README.md)
2. Review [examples.py](examples.py)
3. Check API documentation at `/docs`

---

**Ready to process documents?** 🚀

Start the application and go to: **http://localhost:8000/dashboard**
