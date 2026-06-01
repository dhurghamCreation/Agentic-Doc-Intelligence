# 🚀 Quick Start - Run in 3 Steps

## Step 1: Install Requirements
```bash
pip install -r requirements.txt
```

**For Windows users (OCR support):**
1. Download Tesseract from: https://github.com/UB-Mannheim/tesseract/wiki
2. Run the installer
3. Add to PATH or set in code

## Step 2: Start the Server
```bash
python main.py
```

You should see:
```
INFO:     Uvicorn running on http://0.0.0.0:8000
```

## Step 3: Open Dashboard
Visit in your browser:
```
http://localhost:8000/dashboard
```

---

## 🎯 What You Can Do

### Upload Documents
- Click "📤 Upload Document"
- Select an image or text file
- Results appear automatically

### Extract Data
- Click "✂️ Extract from Text"
- Paste your document text
- Click "Extract Data"

### View Results
- See extracted fields with confidence scores
- Check data quality metrics
- View document classification

### Monitor Jobs
- Track processing status
- View system statistics
- Check success rates

---

## 📊 API Quick Reference

### Extract from Text
```bash
curl -X POST "http://localhost:8000/extract" \
  -H "Content-Type: application/json" \
  -d '{"text": "Invoice #123 for $500"}'
```

### Upload File
```bash
curl -X POST "http://localhost:8000/upload" \
  -F "file=@document.pdf"
```

### Batch Process
```bash
curl -X POST "http://localhost:8000/batch" \
  -H "Content-Type: application/json" \
  -d '{"documents": [{"text": "Doc 1"}, {"text": "Doc 2"}]}'
```

### Get Job Status
```bash
curl http://localhost:8000/jobs/{job_id}
```

---

## 🐍 Python Usage

```python
import asyncio
from app.pipeline import DocumentProcessingPipeline

async def main():
    pipeline = DocumentProcessingPipeline()
    
    result = await pipeline.process_document(
        document_id="doc_001",
        text="Invoice #123 Amount: $500.00"
    )
    
    print(f"Type: {result.classification.document_type}")
    print(f"Confidence: {result.classification.confidence:.2%}")
    print(f"Fields: {len(result.extraction.extracted_fields)}")
    print(f"Quality: {result.validation.data_quality_score:.2%}")

asyncio.run(main())
```

---

## 🧪 Run Examples

```bash
python examples.py
```

This will show you:
- Single document processing
- Batch processing
- Custom field extraction
- Data validation

---

## 🐳 Docker Alternative

```bash
# Build image
docker build -t doc-intelligence .

# Run container
docker run -p 8000:8000 -v ./uploads:/app/uploads doc-intelligence

# Visit http://localhost:8000/dashboard
```

---

## ⚙️ Configuration

Create `.env` file if needed:
```env
API_PORT=8000
DATABASE_URL=sqlite:///./documents.db
OCR_LANG=eng
LOG_LEVEL=INFO
```

---

## ✅ Verify Installation

```bash
# Check health
curl http://localhost:8000/health

# Should return: {"status":"healthy",...}
```

---

## 📚 Full Documentation

See [QUICKSTART.md](QUICKSTART.md) for detailed guide or [app/README.md](app/README.md) for complete documentation.

---

**🎉 You're all set! Open http://localhost:8000/dashboard to get started!**
