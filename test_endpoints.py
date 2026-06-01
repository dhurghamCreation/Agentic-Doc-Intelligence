#!/usr/bin/env python
from fastapi.testclient import TestClient
from main import app

c = TestClient(app)

print("=== Testing All API Endpoints ===")
print()

# Test 1: API docs endpoint  
print("1. Testing /api-docs endpoint:")
r = c.get('/api-docs')
print(f"   Status Code: {r.status_code}")
print(f"   Has 'Return to Dashboard': {'Return to Dashboard' in r.text}")
print(f"   Has 'API Documentation': {'API Documentation' in r.text}")
print(f"   Is HTML: {r.headers.get('content-type', '').startswith('text/html')}")
print()

# Test 2: Health endpoint
print("2. Testing /health endpoint:")
r = c.get('/health')
print(f"   Status Code: {r.status_code}")
data = r.json()
print(f"   Status: {data.get('status')}")
print(f"   Pending Jobs: {data.get('pending_jobs')}")
print()

# Test 3: Upload endpoint
print("3. Testing /upload endpoint:")
files = {'file': ('sample.txt', b'Invoice #999 Total $1200 Due 05/15/2026', 'text/plain')}
r = c.post('/upload', files=files)
print(f"   Status Code: {r.status_code}")
if r.status_code == 200:
    data = r.json()
    job_id = data.get('job_id')
    print(f"   Job ID Created: {job_id[:12]}...")
    print(f"   Status: {data.get('status')}")
    print(f"   Filename: {data.get('filename')}")
    print()
    
    # Test 4: Check job status
    print("4. Testing /jobs/{job_id} endpoint:")
    import time
    time.sleep(0.5)
    r = c.get(f'/jobs/{job_id}')
    print(f"   Status Code: {r.status_code}")
    if r.status_code == 200:
        data = r.json()
        print(f"   Job Status: {data.get('status')}")
        print(f"   Progress: {data.get('progress')}%")
else:
    print(f"   Error: {r.json() if r.text else 'Unknown error'}")

print()

# Test 5: Extract endpoint
print("5. Testing /extract endpoint:")
r = c.post('/extract', json={'text': 'Invoice #12345 Total $2500 Due 06/01/2026'})
print(f"   Status Code: {r.status_code}")
if r.status_code == 200:
    print("   Extraction successful")
    data = r.json()
    if 'classification' in data:
        print(f"   Doc Type: {data['classification'].get('document_type', 'unknown')}")
else:
    print(f"   Error: {r.json() if r.text else 'Unknown error'}")

print()
print("=== All Endpoint Tests Complete ===")
