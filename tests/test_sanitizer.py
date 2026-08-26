import pytest
from src.sanitizer import TextSanitizer

def test_sanitize_basic_spacing():
    sanitizer = TextSanitizer()
    raw = "  hello   world   "
    expected = "hello world"
    assert sanitizer.sanitize(raw) == expected

def test_sanitize_code_switching_thai_english():
    sanitizer = TextSanitizer()
    raw = "  เราต้องเช็ค   data flow  ของ  pipeline  นี้ก่อน  "
    expected = "เราต้องเช็ค data flow ของ pipeline นี้ก่อน"
    assert sanitizer.sanitize(raw) == expected

def test_sanitize_thai_unicode_nfc():
    sanitizer = TextSanitizer()
    # Decomposed Thai characters check
    decomposed = "น\u0e49\u0e33" # น + ้ + ำ
    sanitized = sanitizer.sanitize(decomposed)
    assert len(sanitized) > 0
    assert sanitizer.sanitize("") == ""
    assert sanitizer.sanitize(None) == ""

def test_sanitize_deduplication():
    sanitizer = TextSanitizer()
    first = sanitizer.sanitize("กูต้องทำยังไงวะ")
    assert first == "กูต้องทำยังไงวะ"
    
    # Duplicate phrase should be dropped (returns "")
    duplicate = sanitizer.sanitize("กูต้องทำยังไงวะ")
    assert duplicate == ""
    
    # Near duplicate (>=80% similarity) should also be dropped
    near_dup = sanitizer.sanitize("กูต้องทำยังไงวะเนี่ย")
    assert near_dup == ""
    
    # Different text should be kept
    new_phrase = sanitizer.sanitize("เราจะสตรีมมิ่งต่อ")
    assert new_phrase == "เราจะสตรีมมิ่งต่อ"

def test_sanitize_fillers():
    sanitizer = TextSanitizer()
    assert sanitizer.sanitize("อืม") == ""
    assert sanitizer.sanitize("ครับ") == ""
    assert sanitizer.sanitize("ก็คือ") == ""
    assert sanitizer.sanitize("อึ๊บ") == ""


