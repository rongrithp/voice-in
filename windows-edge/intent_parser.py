"""
intent_parser.py - Rule-Based Natural Language Intent Parser
Windows Edge Module: Maps transcribed voice text (Thai & English) to OS CLI commands with safe fallback.

Supported Core Intents:
- "เปิดบราวเซอร์" / "open browser" -> start chrome
- "เช็คสถานะ git" / "git status" -> git status --short
- "เปิดโปรเจกต์" / "open project" -> explorer .
- Safe fallback: Returns warning without breaking execution if intent is unknown.
"""

import re
import sys
import os
import argparse
from typing import Optional, Dict, Any, List, Tuple

# Ensure UTF-8 output encoding on Windows console
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Ensure module directory is in sys.path
MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
if MODULE_DIR not in sys.path:
    sys.path.insert(0, MODULE_DIR)

from intent_memory import IntentMemory, get_memory


class IntentResult:
    """Encapsulates the parsed intent, resolved CLI command, and execution safety status."""

    def __init__(
        self,
        is_matched: bool,
        intent_id: str,
        action_name: str,
        command: Optional[str],
        raw_text: str,
        confidence: float = 1.0,
        warning: Optional[str] = None,
        auto_submit: bool = False
    ):
        self.is_matched = is_matched
        self.intent_id = intent_id
        self.action_name = action_name
        self.command = command
        self.raw_text = raw_text
        self.confidence = confidence
        self.warning = warning
        self.auto_submit = auto_submit

    @property
    def executable_command(self) -> str:
        """Returns the resolved CLI command or a safe echo fallback if unknown."""
        if self.command:
            return self.command
        clean_text = self.raw_text.replace('"', '\\"').replace("'", "")
        return f"echo [SAFE FALLBACK] Unknown intent: \"{clean_text}\""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_matched": self.is_matched,
            "intent_id": self.intent_id,
            "action_name": self.action_name,
            "command": self.command,
            "executable_command": self.executable_command,
            "raw_text": self.raw_text,
            "confidence": self.confidence,
            "warning": self.warning,
            "auto_submit": self.auto_submit
        }

    def summary(self) -> str:
        if self.is_matched:
            return f"[INTENT: {self.intent_id}] \"{self.raw_text}\" -> `{self.command}`"
        return f"[INTENT: UNKNOWN] \"{self.raw_text}\" -> {self.warning}"


import string


def clean_intent_text(text: str) -> str:
    """Strips punctuation, normalizes whitespace, and lowercases text for intent matching."""
    if not text:
        return ""
    t = text.lower().strip()
    for ch in string.punctuation + "–—…“”‘’«».,!?":
        t = t.replace(ch, " ")
    return " ".join(t.split())


class IntentRule:
    """Individual intent mapping rule with regex patterns in Thai and English."""

    def __init__(
        self,
        intent_id: str,
        action_name: str,
        command: str,
        patterns: List[str]
    ):
        self.intent_id = intent_id
        self.action_name = action_name
        self.command = command
        self.compiled_patterns = [
            re.compile(p, re.IGNORECASE | re.UNICODE) for p in patterns
        ]

    def match(self, text: str) -> Optional[float]:
        """Checks if text matches any rule pattern. Returns confidence score or None."""
        clean = clean_intent_text(text)
        for p in self.compiled_patterns:
            if p.search(clean):
                return 1.0
        return None


class IntentParser:
    """
    High-performance rule-based intent parser for bilingual (Thai/English) speech input.
    Supports relaxed pattern matching, fuzzy/partial keywords, and safe fallbacks.
    """

    def __init__(self, memory: Optional[IntentMemory] = None):
        self.memory = memory or get_memory()
        self.rules: List[IntentRule] = self._build_rules()

    def _build_rules(self) -> List[IntentRule]:
        return [
            # 0. Gemini Live Multimodal Co-pilot Wake ("เจมิไนมาช่วยหน่อย" / "gemini help")
            IntentRule(
                intent_id="GEMINI_LIVE_WAKE",
                action_name="Gemini Live Co-pilot Wake",
                command="python gemini_live_client.py",
                patterns=[
                    r"(เจมิไน.*ช่วย|เจมิไน.*มาช่วย|gemini.*help|gemini.*come|เจมิไน)",
                ]
            ),
            # 0b. Session Termination & Standby Return (Strict exact matching: ["พอแล้ว", "ขอบคุณมาก", "พอแค่นี้", "stop"])
            IntentRule(
                intent_id="SESSION_STANDBY_DISMISS",
                action_name="Session Standby Dismiss",
                command="echo [SESSION DISMISSED -> STANDBY]",
                patterns=[
                    r"^(พอแล้ว|ขอบคุณมาก|พอแค่นี้|stop)$",
                ]
            ),
            # 1. Open Browser (Relaxed matching: catches "But browser", "Ất browser", "browser", "chrome")
            IntentRule(
                intent_id="OPEN_BROWSER",
                action_name="Open Browser",
                command="start chrome",
                patterns=[
                    r"(เปิด.*บราวเซอร์|เปิด.*เบราว์เซอร์|เปิด.*เบราเซอร์|เปิด.*บราวเซอ|เปิด.*chrome|เปิด.*โครม)",
                    r".*(browser|chrome).*",
                    r"\b(browser|chrome)\b",
                    r"\b(open|start|launch)\s*(browser|chrome|web|internet)\b",
                    r"(เปิด.*เว็บ|เปิด.*เน็ต|เปิด.*กูเกิล)"
                ]
            ),
            # 2. Git Status (Relaxed matching)
            IntentRule(
                intent_id="GIT_STATUS",
                action_name="Check Git Status",
                command="git status --short",
                patterns=[
                    r"\b(git\s*status|check\s*git|git\s*state|git\s*stat)\b",
                    r"(เช็ค.*git|ดู.*git|ตรวจ.*git|สถานะ.*git|git.*status|กิต.*สถานะ|เช็ค.*กิต)",
                    r".*git\s*status.*",
                    r".*status.*git.*",
                    r"\bgit\s*st\b",
                    r"(เช็ค|ตรวจ|ดู|เช็คสถานะ|ดูสถานะ)\s*(git|กิต|สถานะ)"
                ]
            ),
            # 3. Open Project (Relaxed matching)
            IntentRule(
                intent_id="OPEN_PROJECT",
                action_name="Open Project Folder",
                command="explorer .",
                patterns=[
                    r"(เปิด.*โปรเจกต์|เปิด.*โปรเจค|เปิด.*โฟลเดอร์|เปิด.*ไดเรกทอรี|เปิด.*หน้าต่างไฟล์|เปิด.*ที่เก็บไฟล์)",
                    r"\b(open\s*project|open\s*folder|open\s*directory|open\s*explorer|show\s*folder)\b",
                    r".*(project|explorer|folder).*",
                    r"\b(project|folder)\b"
                ]
            ),
            # 4. Open Terminal (Relaxed matching)
            IntentRule(
                intent_id="OPEN_TERMINAL",
                action_name="Open Terminal",
                command="start cmd",
                patterns=[
                    r"(เปิด.*เทอร์มินัล|เปิด.*คอมมานด์|เปิด.*cmd|เปิด.*พรอมต์|เปิด.*terminal)",
                    r"\b(open\s*terminal|start\s*terminal|open\s*cmd|start\s*cmd|open\s*shell)\b",
                    r".*terminal.*",
                    r"\bcmd\b"
                ]
            ),
            # 5. List Files (Relaxed matching)
            IntentRule(
                intent_id="LIST_FILES",
                action_name="List Files",
                command="dir /b",
                patterns=[
                    r"(แสดง.*ไฟล์|ดู.*รายชื่อไฟล์|ดู.*ไฟล์ทั้งหมด|ลิสต์.*ไฟล์|ดู.*ไฟล์)",
                    r"\b(list\s*files|show\s*files|dir\s*list|list\s*file)\b",
                    r"\bdir\b"
                ]
            ),
            # 6. Git Pull
            IntentRule(
                intent_id="GIT_PULL",
                action_name="Git Pull",
                command="git pull",
                patterns=[
                    r"\b(git\s*pull|pull\s*code)\b",
                    r"(ดึง.*โค้ด|อัปเดต.*โค้ด|กิต.*พูล|git.*pull)"
                ]
            ),
            # 7. Git Diff
            IntentRule(
                intent_id="GIT_DIFF",
                action_name="Git Diff",
                command="git diff --stat",
                patterns=[
                    r"\b(git\s*diff|show\s*diff)\b",
                    r"(ดู.*diff|ตรวจ.*โค้ด|git.*diff)"
                ]
            ),
            # 8. WhoAmI
            IntentRule(
                intent_id="WHOAMI",
                action_name="System WhoAmI",
                command="whoami",
                patterns=[
                    r"\b(whoami|who\s*am\s*i)\b",
                    r"(ใคร.*กำลังใช้เครื่อง|ผู้ใช้.*ปัจจุบัน|ชื่อ.*ผู้ใช้)"
                ]
            ),
            # 9. Clear Screen
            IntentRule(
                intent_id="CLEAR_SCREEN",
                action_name="Clear Screen",
                command="cls",
                patterns=[
                    r"\b(clear\s*screen|cls)\b",
                    r"(ล้าง.*หน้าจอ|เคลียร์.*หน้าจอ)"
                ]
            )
        ]

    def parse(self, text: str) -> IntentResult:
        """
        Parses text and matches against user rules first, then static rules.
        Returns IntentResult with resolved command or safe fallback warning.
        """
        if not text or not text.strip():
            return IntentResult(
                is_matched=False,
                intent_id="EMPTY_INPUT",
                action_name="Empty Input",
                command=None,
                raw_text="",
                confidence=0.0,
                warning="Voice input was empty or silent",
                auto_submit=False
            )

        clean_text = clean_intent_text(text)

        # 1. PRIORITY CHECK: Consult user_rules.json first
        mem_rule = self.memory.get_mapped_command(clean_text)
        if mem_rule:
            return IntentResult(
                is_matched=True,
                intent_id="USER_LEARNED",
                action_name=mem_rule.get("action_name", f"Run: {mem_rule['command']}"),
                command=mem_rule["command"],
                raw_text=text.strip(),
                confidence=1.0,
                warning=None,
                auto_submit=mem_rule.get("auto_submit", True)
            )

        # 2. Static Intent Rules
        for rule in self.rules:
            conf = rule.match(clean_text)
            if conf is not None:
                return IntentResult(
                    is_matched=True,
                    intent_id=rule.intent_id,
                    action_name=rule.action_name,
                    command=rule.command,
                    raw_text=text.strip(),
                    confidence=conf,
                    warning=None,
                    auto_submit=False  # Newly detected / unconfirmed: requires prompt!
                )

        # 3. Safe fallback for unknown intents
        return IntentResult(
            is_matched=False,
            intent_id="UNKNOWN_INTENT",
            action_name="Unknown Intent",
            command=None,
            raw_text=text.strip(),
            confidence=0.0,
            warning=f"No matching command found for: '{text.strip()}'",
            auto_submit=False
        )


def run_intent_parser_tests() -> bool:
    """Automated unit test suite verifying all mandatory and boundary intent cases."""
    print("=" * 65)
    print(" INTENT PARSER: AUTOMATED UNIT TEST SUITE")
    print("=" * 65)

    parser = IntentParser()

    test_cases: List[Tuple[str, str, str]] = [
        # (Input text, Expected intent_id, Expected CLI command)
        # Browser intents (including Whisper misrecognition / relaxed cases)
        ("But browser", "OPEN_BROWSER", "start chrome"),
        ("browser", "OPEN_BROWSER", "start chrome"),
        ("เปิดบราวเซอร์", "OPEN_BROWSER", "start chrome"),
        ("Ất, browser", "OPEN_BROWSER", "start chrome"),
        ("เปิดเบราว์เซอร์", "OPEN_BROWSER", "start chrome"),
        ("open browser", "OPEN_BROWSER", "start chrome"),
        ("chrome", "OPEN_BROWSER", "start chrome"),
        ("เปิดโครม", "OPEN_BROWSER", "start chrome"),
        ("launch chrome", "OPEN_BROWSER", "start chrome"),

        ("เช็คสถานะ git", "GIT_STATUS", "git status --short"),
        ("git status", "GIT_STATUS", "git status --short"),
        ("ดูสถานะ git", "GIT_STATUS", "git status --short"),
        ("check git", "GIT_STATUS", "git status --short"),

        ("เปิดโปรเจกต์", "OPEN_PROJECT", "explorer ."),
        ("open project", "OPEN_PROJECT", "explorer ."),
        ("เปิดโฟลเดอร์", "OPEN_PROJECT", "explorer ."),
        ("open folder", "OPEN_PROJECT", "explorer ."),

        ("เปิดเทอร์มินัล", "OPEN_TERMINAL", "start cmd"),
        ("open terminal", "OPEN_TERMINAL", "start cmd"),
        ("whoami", "WHOAMI", "whoami"),
        ("แสดงไฟล์", "LIST_FILES", "dir /b"),
    ]

    passed_count = 0
    for input_text, expected_id, expected_cmd in test_cases:
        res = parser.parse(input_text)
        status = "OK" if (res.is_matched and res.intent_id == expected_id and res.command == expected_cmd) else "FAIL"
        print(f"[{status}] '{input_text}' -> ID: {res.intent_id} | Cmd: `{res.command}`")
        assert res.is_matched, f"Expected matched for '{input_text}'"
        assert res.intent_id == expected_id, f"Expected {expected_id}, got {res.intent_id}"
        assert res.command == expected_cmd, f"Expected {expected_cmd}, got {res.command}"
        passed_count += 1

    # Safe Fallback Test Case
    print("\n[Testing Safe Fallback on Unknown Intent]...")
    unknown_inputs = [
        "ชงกาแฟให้หน่อย",
        "play some jazz music",
        "xyz unknown random phrase 999"
    ]
    for unk in unknown_inputs:
        res = parser.parse(unk)
        print(f"[OK] Unknown Input: \"{unk}\"")
        print(f"     -> Matched: {res.is_matched} | Warning: {res.warning}")
        print(f"     -> Safe Executable: `{res.executable_command}`")
        assert not res.is_matched, f"Expected unmatched for unknown input '{unk}'"
        assert res.command is None, "Command must be None for unknown intent"
        assert res.warning is not None, "Warning must be present for unknown intent"
        assert res.executable_command.startswith("echo [SAFE FALLBACK]"), "Safe fallback command missing"
        passed_count += 1

    # Standby Dismiss Strict Exact Matching & Non-matching verification
    print("\n[Testing Strict Standby Dismiss Exact Matching vs Question Words]...")
    dismiss_cases = [
        ("พอแล้ว", True),
        ("ขอบคุณมาก", True),
        ("พอแค่นี้", True),
        ("stop", True),
        ("อธิบายหน่อย...", False),
        ("เป็นยังไง", False),
        ("ช่วยอธิบายหน่อย", False),
        ("หยุดก่อน", False),
    ]
    for text_in, should_dismiss in dismiss_cases:
        r = parser.parse(text_in)
        is_dismiss = (r.intent_id == "SESSION_STANDBY_DISMISS")
        status = "OK" if is_dismiss == should_dismiss else "FAIL"
        print(f"[{status}] '{text_in}' -> is_dismiss={is_dismiss} (expected {should_dismiss})")
        assert is_dismiss == should_dismiss, f"Standby dismiss mismatch for '{text_in}'"
        passed_count += 1

    # Adaptive Memory Priority & Auto-Submit Verification
    print("\n[Testing Adaptive Memory Priority & Auto-Submit Flow]...")
    import tempfile
    test_rules_path = os.path.join(tempfile.gettempdir(), "test_user_rules_parser.json")
    if os.path.exists(test_rules_path):
        os.remove(test_rules_path)

    mem = IntentMemory(filepath=test_rules_path)
    parser_with_mem = IntentParser(memory=mem)

    # 1. Unlearned phrase triggers confirmation (auto_submit=False)
    test_phrase = "เปิดโปรแกรมสปอติฟาย"
    res1 = parser_with_mem.parse(test_phrase)
    print(f"      Step 1 (Unlearned):   '{test_phrase}' -> Matched={res1.is_matched} | auto_submit={res1.auto_submit}")
    assert res1.auto_submit is False, "Unlearned phrase must require confirmation (auto_submit=False)"

    # 2. Simulate User selecting [A] (Learn phrase to memory with auto_submit=True)
    mem.save_rule(phrase=test_phrase, command="start spotify", auto_submit=True, action_name="Launch Spotify")

    # 3. Subsequent call bypasses confirmation and executes directly
    res2 = parser_with_mem.parse(test_phrase)
    print(f"      Step 2 (Subsequent):  '{test_phrase}' -> Intent={res2.intent_id} | Cmd=`{res2.command}` | auto_submit={res2.auto_submit}")
    assert res2.is_matched is True, "Subsequent call must match"
    assert res2.intent_id == "USER_LEARNED", f"Expected USER_LEARNED, got {res2.intent_id}"
    assert res2.command == "start spotify", f"Expected 'start spotify', got {res2.command}"
    assert res2.auto_submit is True, "Subsequent call must bypass confirmation (auto_submit=True)"
    passed_count += 2

    if os.path.exists(test_rules_path):
        os.remove(test_rules_path)

    print("\n" + "=" * 65)
    print(f" ALL {passed_count} INTENT PARSER TESTS PASSED (0 ERRORS)")
    print("=" * 65)
    return True


def main():
    parser_cli = argparse.ArgumentParser(description="Windows Edge - Rule-Based Natural Language Intent Parser")
    parser_cli.add_argument("--test", action="store_true", help="Run automated unit test suite and exit")
    parser_cli.add_argument("--text", type=str, default=None, help="Input natural language phrase to parse")
    args = parser_cli.parse_args()

    if args.test or not args.text:
        success = run_intent_parser_tests()
        sys.exit(0 if success else 1)

    parser = IntentParser()
    result = parser.parse(args.text)
    print(result.summary())
    print(f"Resolved Command: {result.executable_command}")


if __name__ == "__main__":
    main()
