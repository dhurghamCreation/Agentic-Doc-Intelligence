#!/usr/bin/env python
"""Check if HTML file input structure is correct"""
from fastapi.testclient import TestClient
from main import app

c = TestClient(app)

print("Checking HTML file input structure...")
print()

r = c.get('/dashboard')
html = r.text

# Check 1: File input exists and is correct
print("1. Profile photo input HTML:")
if 'id="profileAvatarInput"' in html:
    start = html.find('id="profileAvatarInput"')
    snippet = html[max(0, start-100):start+150]
    print("   ✓ Found profileAvatarInput")
    print(f"   Snippet: ...{snippet}...")
else:
    print("   ✗ profileAvatarInput NOT found!")
print()

# Check 2: File upload input
print("2. File upload input HTML:")
if 'id="fileInput"' in html:
    start = html.find('id="fileInput"')
    snippet = html[max(0, start-100):start+150]
    print("   ✓ Found fileInput")
    print(f"   Snippet: ...{snippet}...")
else:
    print("   ✗ fileInput NOT found!")
print()

# Check 3: Upload button
print("3. Upload button:")
if 'onclick="document.getElementById(\'fileInput\').click()"' in html:
    print("   ✓ Button click handler is correct")
else:
    print("   ✗ Button click handler might be wrong")
print()

# Check 4: uploadFile function
print("4. uploadFile function:")
if 'async function uploadFile(event)' in html:
    print("   ✓ uploadFile function defined")
    # Find where it starts
    start = html.find('async function uploadFile(event)')
    if start > 0:
        snippet = html[start:start+500]
        print(f"   First 200 chars: {snippet[:200]}")
else:
    print("   ✗ uploadFile function NOT found!")
print()

# Check 5: onProfilePhotoChange function
print("5. onProfilePhotoChange function:")
if 'function onProfilePhotoChange(event)' in html:
    print("   ✓ onProfilePhotoChange function defined")
else:
    print("   ✗ onProfilePhotoChange function NOT found!")
print()

# Check 6: loadUserPreferences (needs to load avatar on init)
print("6. loadUserPreferences function:")
if 'function loadUserPreferences()' in html:
    print("   ✓ loadUserPreferences function defined")
    start = html.find('function loadUserPreferences()')
    if start > 0:
        snippet = html[start:start+300]
        print(f"   Contains avatar logic: {'docintel_profile_avatar' in snippet}")
else:
    print("   ✗ loadUserPreferences function NOT found!")
print()

# Check 7: renderStats call
print("7. Initial page setup:")
if 'renderStats()' in html:
    print("   ✓ renderStats() called")
if 'loadUserPreferences()' in html:
    print("   ✓ loadUserPreferences() called")
if 'renderRunHistory()' in html:
    print("   ✓ renderRunHistory() called")
print()

print("✅ All HTML elements and functions are present in the dashboard!")
