"""
4-Stage Client-Side Document Sanitizer for Drag-and-Drop Ingestion [INV-06].
Security Pipeline:
  Stage 1: Magic bytes & MIME whitelist verification.
  Stage 2: File size cap and decompression bomb ratio limits.
  Stage 3: Active macro and JavaScript payload stripping.
  Stage 4: Plain-text / markdown extraction before cloud dispatch.
"""

from __future__ import annotations
import io
import re
import zipfile
import logging
from typing import Optional, Tuple, Dict, Any

logger = logging.getLogger("zero_ui.sanitizer")

# Stage 1: Whitelist Configurations
ALLOWED_MIME_TYPES = {
    "application/pdf": [b"%PDF-"],
    "image/jpeg": [b"\xff\xd8\xff"],
    "image/png": [b"\x89PNG\r\n\x1a\n"],
    "image/webp": [b"RIFF"],  # Checked with WEBP at offset 8
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": [b"PK\x03\x04"],
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": [b"PK\x03\x04"],
    "text/plain": [],
    "text/markdown": [],
    "text/csv": []
}

# Stage 2: Resource Limits
MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024  # 25MB
MAX_UNCOMPRESSED_SIZE_BYTES = 100 * 1024 * 1024  # 100MB
MAX_COMPRESSION_RATIO = 100.0  # 100:1 Zip bomb threshold

# Stage 3: Harmful Signature Patterns
HARMFUL_PATTERNS = [
    re.compile(rb"<script[\s>]", re.IGNORECASE),
    re.compile(rb"javascript:", re.IGNORECASE),
    re.compile(rb"/JavaScript", re.IGNORECASE),
    re.compile(rb"/JS\s*<<", re.IGNORECASE),
    re.compile(rb"vbscript:", re.IGNORECASE),
    re.compile(rb"onload\s*=", re.IGNORECASE),
    re.compile(rb"onerror\s*=", re.IGNORECASE),
]


class DocumentSanitizationError(Exception):
    """Raised when an ingested document fails security verification."""
    pass


class DocumentSanitizer:
    """
    Client-Side 4-Stage Security Pipeline for Ingested Documents.
    """

    @classmethod
    def sanitize(
        cls,
        file_name: str,
        content_bytes: bytes,
        mime_type: Optional[str] = None
    ) -> Tuple[bytes, str, str]:
        """
        Runs the 4-stage sanitization pipeline.
        Returns:
            Tuple of (sanitized_bytes, sanitized_mime_type, extracted_text_or_md)
        """
        # --- Stage 1: Magic Bytes & MIME Whitelist Verification ---
        detected_mime = cls._verify_stage_1(file_name, content_bytes, mime_type)

        # --- Stage 2: File Size Cap & Decompression Bomb Check ---
        cls._verify_stage_2(content_bytes, detected_mime)

        # --- Stage 3: Active Macro and JavaScript Payload Stripping ---
        sanitized_bytes = cls._verify_and_strip_stage_3(content_bytes, detected_mime)

        # --- Stage 4: Plain-Text / Markdown Extraction ---
        extracted_text = cls._extract_stage_4(sanitized_bytes, detected_mime, file_name)

        return sanitized_bytes, detected_mime, extracted_text

    @classmethod
    def _verify_stage_1(cls, file_name: str, content: bytes, mime_type: Optional[str]) -> str:
        ext = file_name.lower().split(".")[-1] if "." in file_name else ""

        # Determine target MIME based on explicit mime_type or file extension
        target_mime = mime_type
        if not target_mime or target_mime == "application/octet-stream":
            if ext == "pdf":
                target_mime = "application/pdf"
            elif ext in ("jpg", "jpeg"):
                target_mime = "image/jpeg"
            elif ext == "png":
                target_mime = "image/png"
            elif ext == "webp":
                target_mime = "image/webp"
            elif ext == "docx":
                target_mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            elif ext == "xlsx":
                target_mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            elif ext in ("txt", "log"):
                target_mime = "text/plain"
            elif ext == "md":
                target_mime = "text/markdown"
            elif ext == "csv":
                target_mime = "text/csv"
            else:
                raise DocumentSanitizationError(f"Unsupported file extension: .{ext}")

        if target_mime not in ALLOWED_MIME_TYPES:
            raise DocumentSanitizationError(f"MIME type '{target_mime}' is not in the allowed whitelist.")

        # Check magic bytes for binary files
        signatures = ALLOWED_MIME_TYPES[target_mime]
        if signatures:
            matched = False
            for sig in signatures:
                if content.startswith(sig):
                    if target_mime == "image/webp" and len(content) >= 12:
                        if content[8:12] != b"WEBP":
                            continue
                    matched = True
                    break
            if not matched:
                raise DocumentSanitizationError(f"Magic bytes mismatch for expected MIME type '{target_mime}'.")

        # For text files, verify valid UTF-8/ASCII encoding
        if target_mime.startswith("text/"):
            try:
                content.decode("utf-8")
            except UnicodeDecodeError:
                raise DocumentSanitizationError(f"Text file '{file_name}' contains invalid non-UTF-8 characters.")

        return target_mime

    @classmethod
    def _verify_stage_2(cls, content: bytes, mime_type: str) -> None:
        size = len(content)
        if size > MAX_FILE_SIZE_BYTES:
            raise DocumentSanitizationError(
                f"File size {size} bytes exceeds maximum allowed limit of {MAX_FILE_SIZE_BYTES} bytes (25MB)."
            )

        # For ZIP-based files (DOCX, XLSX), inspect total uncompressed size and compression ratio
        if "openxmlformats" in mime_type:
            try:
                with zipfile.ZipFile(io.BytesIO(content), "r") as zf:
                    total_uncompressed = 0
                    for info in zf.infolist():
                        total_uncompressed += info.file_size
                        if info.compress_size > 0:
                            ratio = info.file_size / info.compress_size
                            if ratio > MAX_COMPRESSION_RATIO:
                                raise DocumentSanitizationError(
                                    f"Decompression bomb ratio exceeded: {ratio:.1f}:1 for entry '{info.filename}'."
                                )
                    if total_uncompressed > MAX_UNCOMPRESSED_SIZE_BYTES:
                        raise DocumentSanitizationError(
                            f"Total uncompressed size {total_uncompressed} bytes exceeds limit of {MAX_UNCOMPRESSED_SIZE_BYTES} bytes."
                        )
            except zipfile.BadZipFile:
                raise DocumentSanitizationError("Corrupted or invalid OpenXML ZIP container.")

    @classmethod
    def _verify_and_strip_stage_3(cls, content: bytes, mime_type: str) -> bytes:
        # Strip or reject active macros in DOCX/XLSX
        if "openxmlformats" in mime_type:
            try:
                buf_in = io.BytesIO(content)
                buf_out = io.BytesIO()
                with zipfile.ZipFile(buf_in, "r") as zin, zipfile.ZipFile(buf_out, "w") as zout:
                    for item in zin.infolist():
                        # Block VBA macros and executable artifacts
                        filename_lower = item.filename.lower()
                        if "vbaproject" in filename_lower or filename_lower.endswith((".exe", ".dll", ".bat", ".ps1", ".vbs")):
                            logger.warning(f"Stripped malicious artifact: {item.filename}")
                            continue
                        # Sanitize XML files for script injections
                        data = zin.read(item.filename)
                        for pattern in HARMFUL_PATTERNS:
                            data = pattern.sub(b"", data)
                        zout.writestr(item, data)
                return buf_out.getvalue()
            except Exception as e:
                raise DocumentSanitizationError(f"Failed processing OpenXML container: {e}")

        # For plain text / markdown / CSV / PDF, check and strip or reject dangerous script injections
        if mime_type.startswith("text/") or mime_type == "application/pdf":
            for pattern in HARMFUL_PATTERNS:
                if pattern.search(content):
                    logger.warning(f"Harmful script payload detected and stripped from {mime_type}")
                    content = pattern.sub(b"", content)

        return content

    @classmethod
    def _extract_stage_4(cls, content: bytes, mime_type: str, file_name: str) -> str:
        """
        Extracts clean plain-text or markdown summary for LLM context grounding [INV-06].
        """
        if mime_type.startswith("text/"):
            return content.decode("utf-8", errors="replace")

        if mime_type == "application/pdf":
            # Extract basic PDF text stream markers or fallback to structure summary
            text_chunks = []
            try:
                # Basic text extraction from PDF stream objects
                for match in re.finditer(rb"\((.*?)\)\s*Tj", content):
                    text_chunks.append(match.group(1).decode("latin-1", errors="ignore"))
            except Exception:
                pass
            if text_chunks:
                return " ".join(text_chunks[:500])
            return f"[Sanitized PDF Document: {file_name}, Size: {len(content)} bytes]"

        if "openxmlformats" in mime_type:
            # Extract text from word/document.xml or xl/sharedStrings.xml
            try:
                with zipfile.ZipFile(io.BytesIO(content), "r") as zf:
                    for name in zf.namelist():
                        if name.endswith("document.xml") or name.endswith("sharedStrings.xml"):
                            xml_data = zf.read(name).decode("utf-8", errors="ignore")
                            # Strip XML tags
                            clean_text = re.sub(r"<[^>]+>", " ", xml_data)
                            return " ".join(clean_text.split()[:1000])
            except Exception:
                pass
            return f"[Sanitized OpenXML Document: {file_name}, Size: {len(content)} bytes]"

        if mime_type.startswith("image/"):
            return f"[Sanitized Image: {file_name}, Format: {mime_type}, Size: {len(content)} bytes]"

        return f"[Sanitized Attachment: {file_name}]"
