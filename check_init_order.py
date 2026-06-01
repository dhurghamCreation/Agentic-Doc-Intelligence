#!/usr/bin/env python
"""Test to capture any JavaScript errors from the dashboard"""
import asyncio
import subprocess
import time
from pathlib import Path

async def test_with_curl():
    """Use curl to fetch the page and check for malformed HTML"""
    result = subprocess.run(
        ['powershell', '-Command', 'curl.exe -s http://localhost:8000/dashboard | Select-String -Pattern "renderStats|renderTutorialStep|loadUserPreferences" | Select-Object -First 10'],
        capture_output=True,
        text=True
    )
    print("Initialization code found in HTML:")
    print(result.stdout if result.stdout else "(none)")
    print()

def check_html_structure():
    """Check if all required elements and functions exist"""
    from fastapi.testclient import TestClient
    from main import app
    from bs4 import BeautifulSoup
    
    c = TestClient(app)
    r = c.get('/dashboard')
    soup = BeautifulSoup(r.text, 'html.parser')
    
    # Check for elements needed by renderStats
    required_ids = ['totalDocs', 'successDocs', 'failedDocs', 'activeJobs']
    print("Checking for required elements:")
    for elem_id in required_ids:
        elem = soup.find(id=elem_id)
        print(f"  {elem_id}: {'FOUND' if elem else 'MISSING'}")
    
    print()
    
    # Check for other critical elements
    critical_ids = ['profileAvatarInput', 'fileInput', 'profileAvatarPreview', 'resultArea', 'tutorialModal']
    print("Checking for critical UI elements:")
    for elem_id in critical_ids:
        elem = soup.find(id=elem_id)
        print(f"  {elem_id}: {'FOUND' if elem else 'MISSING'}")
    
    print()
    
    # Look for the initialization code and check it
    html = r.text
    print("Checking initialization code order:")
    positions = {
        'renderStats called': html.find('renderStats();'),
        'renderTutorialStep called': html.find('renderTutorialStep();'),
        'loadUserPreferences called': html.find('loadUserPreferences();'),
        'renderStats defined': html.find('function renderStats()'),
        'renderTutorialStep defined': html.find('function renderTutorialStep()'),
        'loadUserPreferences defined': html.find('function loadUserPreferences()'),
    }
    
    for name, pos in positions.items():
        print(f"  {name}: {pos if pos >= 0 else 'NOT FOUND'}")
    
    print()
    
    # Check if initialization is BEFORE or AFTER definitions
    func_def_end = max([v for k, v in positions.items() if 'defined' in k and v >= 0])
    init_start = min([v for k, v in positions.items() if 'called' in k and v >= 0])
    
    if init_start > func_def_end:
        print("✓ GOOD: Initialization happens AFTER all functions are defined")
    else:
        print("✗ PROBLEM: Initialization happens BEFORE some functions are defined!")

if __name__ == '__main__':
    check_html_structure()
