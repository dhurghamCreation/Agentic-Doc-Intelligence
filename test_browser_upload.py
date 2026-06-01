#!/usr/bin/env python
"""End-to-end browser test for uploads"""
import asyncio
import tempfile
import os
from pathlib import Path
from PIL import Image
from playwright.async_api import async_playwright, expect

async def run_browser_test():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, args=['--disable-gpu'])
        page = await browser.new_page()
        
        try:
            print("\n" + "="*70)
            print("END-TO-END BROWSER TEST FOR UPLOADS")
            print("="*70 + "\n")
            
            # Navigate to dashboard
            print("1. Navigating to dashboard...")
            await page.goto('http://localhost:8000/dashboard', wait_until='networkidle')
            print("   ✓ Dashboard loaded\n")
            
            # Wait for page to fully load
            await page.wait_for_timeout(2000)
            
            # Check if inputs exist
            print("2. Checking if file inputs exist...")
            profile_input = await page.locator('#profileAvatarInput').count()
            file_input = await page.locator('#fileInput').count()
            print(f"   Profile input exists: {profile_input == 1}")
            print(f"   File input exists: {file_input == 1}\n")
            
            # Check initial state
            print("3. Checking initial stats...")
            stats_text = await page.locator('.stats-display').text_content()
            print(f"   Stats: {stats_text}\n")
            
            # TEST 1: Upload profile photo
            print("4. TESTING PROFILE PHOTO UPLOAD...")
            img = Image.new('RGB', (100, 100), color='red')
            with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
                img.save(f.name)
                img_path = f.name
            
            try:
                print(f"   Uploading test image from {img_path}...")
                await page.locator('#profileAvatarInput').set_input_files(img_path)
                print("   ✓ File selected\n")
                
                # Wait for UI to update
                await page.wait_for_timeout(2000)
                
                # Check if avatar background-image is set
                avatar_style = await page.locator('#profileAvatarPreview').get_attribute('style')
                avatar_class = await page.locator('#profileAvatarPreview').get_attribute('class')
                print(f"   Avatar style: {avatar_style}")
                print(f"   Avatar class: {avatar_class}")
                
                # Check localStorage
                avatar_data_url = await page.evaluate("() => localStorage.getItem('docintel_profile_avatar')")
                if avatar_data_url:
                    print(f"   ✓ localStorage has avatar data (length: {len(avatar_data_url)})\n")
                else:
                    print("   ✗ localStorage does NOT have avatar data\n")
                
                # Check browser console for errors
                print("   Checking browser console for errors...")
                messages = []
                page.on("console", lambda msg: messages.append(f"[{msg.type}] {msg.text}"))
                await page.wait_for_timeout(1000)
                if messages:
                    for msg in messages[-10:]:  # Last 10 messages
                        print(f"     {msg}")
                else:
                    print("     (no console messages)")
                print()
                
            finally:
                os.unlink(img_path)
            
            # TEST 2: Create a test file and upload it
            print("5. TESTING FILE UPLOAD...")
            with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
                f.write("Invoice #12345\nAmount: $500\nDate: 2024-01-15\nClient: ABC Corp")
                txt_path = f.name
            
            try:
                print(f"   Uploading test file from {txt_path}...")
                await page.locator('#fileInput').set_input_files(txt_path)
                print("   ✓ File selected\n")
                
                # Wait for upload to process
                await page.wait_for_timeout(3000)
                
                # Check if notification appeared
                notif_count = await page.locator('.notification').count()
                print(f"   Notifications visible: {notif_count}\n")
                
                # Get the most recent notification text
                if notif_count > 0:
                    last_notif = await page.locator('.notification').last.text_content()
                    print(f"   Last notification: {last_notif}\n")
                
                # Check for result panel
                result_text = await page.locator('#result').text_content()
                if result_text and len(result_text) > 0:
                    print(f"   Result panel text (first 200 chars):\n   {result_text[:200]}...\n")
                
            finally:
                os.unlink(txt_path)
            
            # TEST 3: Check live job stream
            print("6. CHECKING LIVE JOB STREAM...")
            stream_items = await page.locator('.job-item').count()
            print(f"   Job items visible: {stream_items}\n")
            
            if stream_items > 0:
                job_text = await page.locator('.job-item').first.text_content()
                print(f"   First job item:\n   {job_text[:200]}...\n")
            
            print("="*70)
            print("BROWSER TEST COMPLETE")
            print("="*70)
            print("\nKeeping browser open for inspection. Press Ctrl+C to close.")
            
            # Keep browser open for 30 seconds for inspection
            await page.wait_for_timeout(30000)
            
        except Exception as e:
            print(f"\n❌ TEST FAILED: {e}\n")
            import traceback
            traceback.print_exc()
        finally:
            await browser.close()

if __name__ == '__main__':
    asyncio.run(run_browser_test())
