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

def test_delta_text_tracker():
    from src.sanitizer import DeltaTextTracker
    tracker = DeltaTextTracker()

    # Step 1: First word
    delta1 = tracker.process_incoming_text("สวัสดี")
    assert delta1 == "สวัสดี"

    # Step 2: Extended partial response
    delta2 = tracker.process_incoming_text("สวัสดีครับ ผมกำลังทดสอบ")
    assert "ผมกำลังทดสอบ" in delta2

    # Step 3: Same text again yields empty delta
    delta3 = tracker.process_incoming_text("สวัสดีครับ ผมกำลังทดสอบ")
    assert delta3 == ""

    # Step 4: Reset tracker
    tracker.reset()
    delta4 = tracker.process_incoming_text("ข้อความใหม่")
    assert delta4 == "ข้อความใหม่"


