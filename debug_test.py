#!/usr/bin/env python
"""Debug test for file uploads and profile photo handling"""
import sys
from fastapi.testclient import TestClient
from main import app
from pathlib import Path

c = TestClient(app)

print("=" * 70)
print("COMPREHENSIVE UPLOAD DEBUG TEST")
print("=" * 70)
print()

# Test 1: Dashboard page loads
print("1. Dashboard HTML loads correctly:")
r = c.get('/dashboard')
print(f"   Status: {r.status_code}")
print(f"   Contains 'profileAvatarInput': {'profileAvatarInput' in r.text}")
print(f"   Contains 'fileInput': {'fileInput' in r.text}")
print(f"   Contains 'uploadFile': {'uploadFile' in r.text}")
print(f"   Contains 'onProfilePhotoChange': {'onProfilePhotoChange' in r.text}")
print()

# Test 2: JavaScript functions exist
print("2. JavaScript functions are defined:")
print(f"   Contains 'function uploadFile': {'function uploadFile' in r.text}")
print(f"   Contains 'function onProfilePhotoChange': {'function onProfilePhotoChange' in r.text}")
print()

# Test 3: Upload endpoint accepts files
print("3. Upload endpoint with text file:")
test_file = Path("d:\\Agentic-Doc-Intelligence\\uploads\\test_sample.txt")
test_file.write_text("Invoice #123 Total $500 Due 05/15/2026")

with open(test_file, 'rb') as f:
    files = {'file': ('test_sample.txt', f, 'text/plain')}
    r = c.post('/upload', files=files)
    print(f"   Status: {r.status_code}")
    if r.status_code == 200:
        data = r.json()
        print(f"   ✓ Job ID: {data.get('job_id', 'MISSING')[:12]}...")
        print(f"   ✓ Status: {data.get('status')}")
        print(f"   ✓ Filename: {data.get('filename')}")
        print(f"   ✓ Message: {data.get('message')}")
    else:
        print(f"   ✗ Error: {r.text}")
print()

# Test 4: Upload endpoint with image
print("4. Upload endpoint with image file:")
# Create a minimal valid PNG (1x1 transparent pixel)
png_bytes = bytes([
    0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A,  # PNG signature
    0x00, 0x00, 0x00, 0x0D,  # IHDR chunk length
    0x49, 0x48, 0x44, 0x52,  # IHDR
    0x00, 0x00, 0x00, 0x01,  # width: 1
    0x00, 0x00, 0x00, 0x01,  # height: 1
    0x08, 0x06, 0x00, 0x00, 0x00,  # bit depth, color type, etc.
    0x1F, 0x15, 0xC4, 0x89,  # CRC
    0x00, 0x00, 0x00, 0x0A,  # IDAT chunk length
    0x49, 0x44, 0x41, 0x54,  # IDAT
    0x78, 0x9C, 0x63, 0x00, 0x01,  # compressed data
    0x00, 0x00, 0x05, 0x00, 0x01,
    0x0D, 0x0A, 0x2D, 0xB4,  # CRC
    0x00, 0x00, 0x00, 0x00,  # IEND chunk length
    0x49, 0x45, 0x4E, 0x44,  # IEND
    0xAE, 0x42, 0x60, 0x82   # CRC
])

image_file = Path("d:\\Agentic-Doc-Intelligence\\uploads\\test_image.png")
image_file.write_bytes(png_bytes)

with open(image_file, 'rb') as f:
    files = {'file': ('test_image.png', f, 'image/png')}
    r = c.post('/upload', files=files)
    print(f"   Status: {r.status_code}")
    if r.status_code == 200:
        data = r.json()
        print(f"   ✓ Job ID: {data.get('job_id', 'MISSING')[:12]}...")
        print(f"   ✓ Filename: {data.get('filename')}")
    else:
        print(f"   ✗ Error: {r.text}")
print()

# Test 5: Check file system
print("5. Check upload directory:")
upload_dir = Path("d:\\Agentic-Doc-Intelligence\\uploads")
files_in_upload = list(upload_dir.glob("*"))
print(f"   Total files in uploads/: {len(files_in_upload)}")
print(f"   Recent files:")
for f in sorted(files_in_upload, key=lambda x: x.stat().st_mtime, reverse=True)[:5]:
    size_kb = f.stat().st_size / 1024
    print(f"      - {f.name}: {size_kb:.1f} KB")
print()

# Test 6: Job status tracking
print("6. Job status tracking:")
test_file_data = Path("d:\\Agentic-Doc-Intelligence\\uploads\\test_job.txt")
test_file_data.write_text("Receipt #456 Amount $75")

with open(test_file_data, 'rb') as f:
    files = {'file': ('test_job.txt', f, 'text/plain')}
    r = c.post('/upload', files=files)
    if r.status_code == 200:
        job_id = r.json().get('job_id')
        print(f"   Created job: {job_id[:12]}...")
        
        # Check status
        import time
        time.sleep(0.5)
        r_status = c.get(f'/jobs/{job_id}')
        print(f"   Job status endpoint: {r_status.status_code}")
        if r_status.status_code == 200:
            job_data = r_status.json()
            print(f"   ✓ Status: {job_data.get('status')}")
            print(f"   ✓ Progress: {job_data.get('progress')}%")
            print(f"   ✓ Result keys: {list(job_data.get('result', {}).keys())}")
print()

print("=" * 70)
print("DIAGNOSIS COMPLETE")
print("=" * 70)
