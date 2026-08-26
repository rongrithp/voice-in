import re
import unicodedata
from difflib import SequenceMatcher

FILLERS = {"อืม", "อือ", "ครับ", "ค่ะ", "ก็คือ", "ชึบ", "ปึ๊บ", "อึ๊บ", "นะ"}
_last_text = ""

def reset_dedup_memory():
    global _last_text
    _last_text = ""

def is_duplicate(new_text: str, threshold: float = 0.8) -> bool:
    global _last_text
    if not new_text or not _last_text:
        return False
    
    # Calculate similarity ratio between last text and new text
    ratio = SequenceMatcher(None, _last_text.lower(), new_text.lower()).ratio()
    if ratio >= threshold:
        return True

    # Check substring containment for phrase repeats
    if len(new_text) > 4 and new_text.lower() in _last_text.lower():
        return True

    return False

def sanitize_text(text: str, check_dedup: bool = True) -> str:
    global _last_text
    if not text:
        return ""

    # 1. Unicode NFC normalization (combines decomposed Thai diacritics/vowels)
    normalized = unicodedata.normalize("NFC", text)

    # 2. Remove subtitle timestamps (e.g. 00:00, 00:00-00:01, 0:00)
    cleaned = re.sub(r"\d{1,2}:\d{2}(-\d{1,2}:\d{2})?", "", normalized)

    # 3. Remove bracketed noise markers like [ Silence ], [Silence], [Audio], etc.
    cleaned = re.sub(r"\[\s*[^\]]*\s*\]", "", cleaned)

    # 4. Remove unnecessary spaces between Thai characters (keep spaces around English / numbers)
    cleaned = re.sub(r"(?<=[%\u0e00-\u0e7f])\s+(?=[\u0e00-\u0e7f])", "", cleaned)

    # 5. Collapse multiple whitespace characters into single space
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    if not cleaned or cleaned.lower() in FILLERS:
        return ""

    if check_dedup:
        if is_duplicate(cleaned, threshold=0.8):
            print(f"[Sanitizer] Dropped duplicate text (>=80% match): '{cleaned}'", flush=True)
            return ""
        _last_text = cleaned

    return cleaned

class TextSanitizer:
    """Cleans transcribed text, normalizes Thai Unicode tone marks & vowels, and fixes spacing."""

    def __init__(self):
        self.last_text = ""

    def sanitize(self, text: str) -> str:
        res = sanitize_text(text, check_dedup=False)
        if not res or res.lower() in FILLERS:
            return ""
        if self.last_text:
            ratio = SequenceMatcher(None, self.last_text.lower(), res.lower()).ratio()
            if ratio >= 0.8 or (len(res) > 4 and res.lower() in self.last_text.lower()):
                return ""
        self.last_text = res
        return res

