#!/usr/bin/env python
"""Extract and validate JavaScript from main.py"""
import re
from main import DASHBOARD_HTML

# Extract all script blocks
scripts = re.findall(r'<script[^>]*>(.*?)</script>', DASHBOARD_HTML, re.DOTALL)

print(f"Found {len(scripts)} script blocks\n")

for i, script in enumerate(scripts):
    print(f"Script Block {i+1}:")
    print(f"  Length: {len(script)} characters")
    
    # Check for common syntax errors
    open_braces = script.count('{')
    close_braces = script.count('}')
    open_parens = script.count('(')
    close_parens = script.count(')')
    open_brackets = script.count('[')
    close_brackets = script.count(']')
    
    brace_match = "OK" if open_braces == close_braces else f"MISMATCH: {open_braces} vs {close_braces}"
    paren_match = "OK" if open_parens == close_parens else f"MISMATCH: {open_parens} vs {close_parens}"
    bracket_match = "OK" if open_brackets == close_brackets else f"MISMATCH: {open_brackets} vs {close_brackets}"
    
    print(f"  Braces: {brace_match}")
    print(f"  Parens: {paren_match}")
    print(f"  Brackets: {bracket_match}")
    
    # Check for function definitions
    function_matches = re.findall(r'function (\w+)\(|const (\w+) = function', script)
    functions = [f[0] or f[1] for f in function_matches]
    unique_functions = sorted(set(functions))
    print(f"  Functions found: {len(unique_functions)}")
    
    # Check for specific functions
    has_onProfilePhotoChange = 'function onProfilePhotoChange' in script
    has_uploadFile = 'async function uploadFile' in script
    has_saveProfile = 'function saveProfile()' in script
    
    print(f"  - onProfilePhotoChange: {has_onProfilePhotoChange}")
    print(f"  - uploadFile: {has_uploadFile}")
    print(f"  - saveProfile: {has_saveProfile}")
    
    print()
