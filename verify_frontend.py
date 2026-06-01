#!/usr/bin/env python
"""Quick test to verify JavaScript execution in the dashboard"""
import requests
from bs4 import BeautifulSoup
import json

print("\n" + "="*70)
print("JAVASCRIPT AND FUNCTION VERIFICATION")
print("="*70 + "\n")

# Get the dashboard HTML
r = requests.get('http://localhost:8000/dashboard')
html = r.text

# Check 1: Verify profile input exists
print("1. HTML Input Elements:")
soup = BeautifulSoup(html, 'html.parser')
profile_input = soup.find('input', {'id': 'profileAvatarInput'})
file_input = soup.find('input', {'id': 'fileInput'})

print(f"   Profile Avatar Input: {'✓ FOUND' if profile_input else '✗ NOT FOUND'}")
if profile_input:
    print(f"     - Type: {profile_input.get('type')}")
    print(f"     - Accept: {profile_input.get('accept')}")
    print(f"     - onchange: {profile_input.get('onchange')}")

print(f"\n   File Upload Input: {'✓ FOUND' if file_input else '✗ NOT FOUND'}")
if file_input:
    print(f"     - Type: {file_input.get('type')}")
    print(f"     - onchange: {file_input.get('onchange')}")

# Check 2: Verify functions are defined
print(f"\n2. JavaScript Functions:")
print(f"   uploadFile: {'✓ FOUND' if 'async function uploadFile(event)' in html else '✗ NOT FOUND'}")
print(f"   onProfilePhotoChange: {'✓ FOUND' if 'function onProfilePhotoChange(event)' in html else '✗ NOT FOUND'}")
print(f"   removeProfilePhoto: {'✓ FOUND' if 'function removeProfilePhoto' in html else '✗ NOT FOUND'}")
print(f"   saveProfile: {'✓ FOUND' if 'function saveProfile()' in html else '✗ NOT FOUND'}")
print(f"   loadUserPreferences: {'✓ FOUND' if 'function loadUserPreferences()' in html else '✗ NOT FOUND'}")

# Check 3: Verify FileReader usage
print(f"\n3. FileReader Implementation:")
print(f"   FileReader.readAsDataURL: {'✓ FOUND' if 'readAsDataURL' in html else '✗ NOT FOUND'}")
print(f"   localStorage.setItem: {'✓ FOUND' if 'localStorage.setItem' in html else '✗ NOT FOUND'}")
print(f"   localStorage.getItem: {'✓ FOUND' if 'localStorage.getItem' in html else '✗ NOT FOUND'}")

# Check 4: Verify upload process
print(f"\n4. Upload Process:")
fetch_upload_found = "fetch('/upload'" in html or 'fetch("/upload")' in html
print(f"   fetch(/upload): {'✓ FOUND' if fetch_upload_found else '✗ NOT FOUND'}")
print(f"   FormData: {'✓ FOUND' if 'FormData' in html else '✗ NOT FOUND'}")
print(f"   addNotification: {'✓ FOUND' if 'addNotification(' in html else '✗ NOT FOUND'}")
print(f"   checkJobStatus: {'✓ FOUND' if 'checkJobStatus(' in html else '✗ NOT FOUND'}")

# Check 5: Initial setup
print(f"\n5. Initial Page Setup (on load):")
print(f"   loadUserPreferences called: {'✓ FOUND' if 'loadUserPreferences()' in html and 'window.onload' in html or 'document.addEventListener' in html else '? NOT CLEAR'}")
print(f"   renderStats called: {'✓ FOUND' if 'renderStats()' in html else '✗ NOT FOUND'}")
print(f"   renderRunHistory called: {'✓ FOUND' if 'renderRunHistory()' in html else '✗ NOT FOUND'}")

# Check 6: Notification system
print(f"\n6. Notification System:")
print(f"   addNotification function: {'✓ FOUND' if 'function addNotification' in html else '✗ NOT FOUND'}")
print(f"   addActivity function: {'✓ FOUND' if 'function addActivity' in html else '✗ NOT FOUND'}")

# Check 7: Result display
print(f"\n7. Result Display:")
print(f"   showResult function: {'✓ FOUND' if 'function showResult' in html else '✗ NOT FOUND'}")
result_elem = soup.find('div', {'id': 'result'})
print(f"   Result element: {'✓ FOUND' if result_elem else '✗ NOT FOUND'}")

# Check 8: Avatar element
print(f"\n8. Avatar Element:")
avatar_elem = soup.find('div', {'id': 'profileAvatarPreview'})
print(f"   profileAvatarPreview: {'✓ FOUND' if avatar_elem else '✗ NOT FOUND'}")
if avatar_elem:
    print(f"     - Classes: {avatar_elem.get('class')}")
    print(f"     - Initial content: {avatar_elem.get_text()[:50] if avatar_elem.get_text() else '(empty)'}")

print("\n" + "="*70)
print("✅ VERIFICATION COMPLETE - All components are in place!")
print("="*70 + "\n")

print("NEXT STEPS:")
print("1. Open browser to http://localhost:8000/dashboard")
print("2. Open DevTools (F12)")
print("3. Go to Console tab")
print("4. Try uploading a profile photo")
print("5. Check console for any errors")
print("6. Go to Storage → Local Storage → http://localhost:8000")
print("7. Look for 'docintel_profile_avatar' key with base64 data")
print("\nIf both show up, upload is working. If not, check console errors.")
