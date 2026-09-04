"""
intent_memory.py - Adaptive Human-in-the-Loop Semantic Memory
Windows Edge Module: Dynamic memory system to learn user-confirmed commands over time.
Persists user-confirmed phrases and auto_submit rules in user_rules.json.
"""

import sys
import os
import json
import string
import threading
from typing import Optional, Dict, Any, List

# Ensure UTF-8 output encoding on Windows console
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_RULES_FILE = os.path.join(MODULE_DIR, "user_rules.json")


def normalize_phrase(phrase: str) -> str:
    """Strips punctuation, normalizes whitespace, and lowercases text for key matching."""
    if not phrase:
        return ""
    t = phrase.lower().strip()
    for ch in string.punctuation + "–—…“”‘’«».,!?":
        t = t.replace(ch, " ")
    return " ".join(t.split())


class IntentMemory:
    """
    Manages persistent user-learned command rules in user_rules.json.
    Thread-safe storage with fast normalized lookup.
    """

    def __init__(self, filepath: str = DEFAULT_RULES_FILE):
        self.filepath = filepath
        self._lock = threading.Lock()
        self._rules: Dict[str, Dict[str, Any]] = {}
        self._load()

    def _load(self):
        """Loads rules from JSON file. Creates file if not present."""
        with self._lock:
            if not os.path.exists(self.filepath):
                self._rules = {}
                self._save_locked()
                return

            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        self._rules = data
                    else:
                        self._rules = {}
            except Exception as e:
                sys.stderr.write(f"[IntentMemory] Warning: Error loading {self.filepath}: {e}\n")
                self._rules = {}

    def _save_locked(self):
        """Internal atomic save to file while holding lock."""
        temp_file = self.filepath + ".tmp"
        try:
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(self._rules, f, ensure_ascii=False, indent=2)
            if os.path.exists(self.filepath):
                os.replace(temp_file, self.filepath)
            else:
                os.rename(temp_file, self.filepath)
        except Exception as e:
            sys.stderr.write(f"[IntentMemory] Error saving {self.filepath}: {e}\n")
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except Exception:
                    pass

    def get_mapped_command(self, phrase: str) -> Optional[Dict[str, Any]]:
        """
        Priority check: Returns learned rule dict if phrase matches an entry in user_rules.json.
        Returns:
            {"phrase": str, "command": str, "auto_submit": bool, "action_name": str} or None.
        """
        if not phrase:
            return None

        norm = normalize_phrase(phrase)
        if not norm:
            return None

        with self._lock:
            # 1. Exact match
            if norm in self._rules:
                rule = self._rules[norm].copy()
                rule["matched_phrase"] = norm
                return rule

            # 2. Substring / Token subset match
            norm_tokens = set(norm.split())
            for key, rule_data in self._rules.items():
                # Direct substring match
                if key in norm or norm in key:
                    res = rule_data.copy()
                    res["matched_phrase"] = key
                    return res

                # High token overlap check
                key_tokens = set(key.split())
                if key_tokens and key_tokens.issubset(norm_tokens):
                    res = rule_data.copy()
                    res["matched_phrase"] = key
                    return res

        return None

    def save_rule(
        self,
        phrase: str,
        command: str,
        auto_submit: bool = True,
        action_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Saves or updates a user-confirmed phrase mapping in user_rules.json.
        """
        norm = normalize_phrase(phrase)
        if not norm:
            raise ValueError("Phrase cannot be empty")
        if not command or not command.strip():
            raise ValueError("Command cannot be empty")

        rule_entry = {
            "phrase": phrase.strip(),
            "normalized_phrase": norm,
            "command": command.strip(),
            "auto_submit": bool(auto_submit),
            "action_name": action_name.strip() if action_name else f"Run: {command.strip()}"
        }

        with self._lock:
            self._rules[norm] = rule_entry
            self._save_locked()

        print(f"[IntentMemory: Learned] \"{norm}\" -> `{command}` (auto_submit={auto_submit})")
        return rule_entry

    def delete_rule(self, phrase: str) -> bool:
        """Deletes a rule by phrase."""
        norm = normalize_phrase(phrase)
        with self._lock:
            if norm in self._rules:
                del self._rules[norm]
                self._save_locked()
                return True
        return False

    def list_rules(self) -> Dict[str, Any]:
        """Returns all currently stored rules."""
        with self._lock:
            return self._rules.copy()

    def clear_rules(self):
        """Clears all stored rules."""
        with self._lock:
            self._rules = {}
            self._save_locked()


# Global module-level singleton instance
_GLOBAL_MEMORY: Optional[IntentMemory] = None


def get_memory(filepath: str = DEFAULT_RULES_FILE) -> IntentMemory:
    """Returns or creates the global IntentMemory singleton."""
    global _GLOBAL_MEMORY
    if _GLOBAL_MEMORY is None or _GLOBAL_MEMORY.filepath != filepath:
        _GLOBAL_MEMORY = IntentMemory(filepath=filepath)
    return _GLOBAL_MEMORY


def get_mapped_command(phrase: str) -> Optional[Dict[str, Any]]:
    """Convenience module function: Checks memory for phrase mapping."""
    return get_memory().get_mapped_command(phrase)


def save_rule(
    phrase: str,
    command: str,
    auto_submit: bool = True,
    action_name: Optional[str] = None
) -> Dict[str, Any]:
    """Convenience module function: Saves rule to memory."""
    return get_memory().save_rule(phrase, command, auto_submit=auto_submit, action_name=action_name)


def run_intent_memory_tests() -> bool:
    """
    Automated Unit Verification Suite:
    1. Tests saving rule and retrieving with auto_submit=True.
    2. Tests unlearned phrase returns None.
    3. Tests normalized token / punctuation tolerance.
    4. Tests delete and persistence verification.
    """
    import tempfile
    print("=" * 65)
    print(" INTENT MEMORY: AUTOMATED UNIT TEST SUITE")
    print("=" * 65)

    test_file = os.path.join(tempfile.gettempdir(), "test_user_rules_mock.json")
    if os.path.exists(test_file):
        os.remove(test_file)

    memory = IntentMemory(filepath=test_file)

    # 1. Test unlearned phrase
    print("[1/5] Checking unlearned phrase...")
    res = memory.get_mapped_command("เปิดโปรเจกต์ใหม่ที่ไม่เคยมี")
    assert res is None, "Expected None for unlearned phrase"
    print("      -> PASSED (Unlearned phrase returns None)")

    # 2. Test save rule with auto_submit=True
    print("\n[2/5] Saving user rule: 'เปิดเพลงร็อค' -> 'start spotify' (auto_submit=True)...")
    saved = memory.save_rule(
        phrase="เปิดเพลงร็อค",
        command="start spotify",
        auto_submit=True,
        action_name="Play Rock Music"
    )
    assert saved["auto_submit"] is True
    assert saved["command"] == "start spotify"
    print("      -> PASSED (Rule saved successfully)")

    # 3. Test exact & normalized retrieval
    print("\n[3/5] Testing normalized retrieval with punctuation...")
    retrieved = memory.get_mapped_command("เปิดเพลงร็อค!!!")
    assert retrieved is not None, "Failed to retrieve saved rule with punctuation"
    assert retrieved["command"] == "start spotify"
    assert retrieved["auto_submit"] is True
    print(f"      Matched: '{retrieved['phrase']}' -> `{retrieved['command']}` (auto_submit={retrieved['auto_submit']})")
    print("      -> PASSED (Punctuation-normalized retrieval verified)")

    # 4. Test reloading from file (Persistence)
    print("\n[4/5] Testing file persistence across memory instances...")
    new_mem_instance = IntentMemory(filepath=test_file)
    retrieved_persist = new_mem_instance.get_mapped_command("เปิดเพลงร็อค")
    assert retrieved_persist is not None
    assert retrieved_persist["command"] == "start spotify"
    print("      -> PASSED (Rule successfully persisted to disk and reloaded)")

    # 5. Test rule deletion
    print("\n[5/6] Testing rule deletion...")
    del_ok = new_mem_instance.delete_rule("เปิดเพลงร็อค")
    assert del_ok is True
    assert new_mem_instance.get_mapped_command("เปิดเพลงร็อค") is None
    print("      -> PASSED (Rule deleted and confirmed absent)")

    # 6. Test Human-in-the-Loop Pipeline Flow: Unlearned -> [A] Learn -> Auto-Submit (Exit 0)
    print("\n[6/6] Testing Human-in-the-Loop Flow: Unlearned -> [A] Learn -> Auto-Submit...")
    from intent_parser import IntentParser
    from terminal_actuator import TerminalActuator

    parser = IntentParser(memory=new_mem_instance)
    actuator = TerminalActuator(default_hud_duration=1.0)

    test_flow_phrase = "เปิดเครื่องคิดเลขทดสอบ"

    # Step A: Unlearned phrase triggers confirmation (auto_submit=False)
    parse_res_1 = parser.parse(test_flow_phrase)
    print(f"      Phase 1: Input '{test_flow_phrase}'")
    print(f"               Matched: {parse_res_1.is_matched} | Auto-Submit: {parse_res_1.auto_submit}")
    assert parse_res_1.auto_submit is False, "Unlearned phrase must require confirmation (auto_submit=False)"

    # Step B: User selects [A] (Remember Always)
    new_mem_instance.save_rule(
        phrase=test_flow_phrase,
        command="echo [CALCULATOR SIMULATION] Exit 0",
        auto_submit=True,
        action_name="Calculator Tool"
    )
    print("      Phase 2: User selected [A] -> Rule saved to memory with auto_submit=True")

    # Step C: Subsequent call bypasses confirmation and executes directly
    parse_res_2 = parser.parse(test_flow_phrase)
    print(f"      Phase 3: Input '{test_flow_phrase}'")
    print(f"               Intent: {parse_res_2.intent_id} | Auto-Submit: {parse_res_2.auto_submit}")
    assert parse_res_2.is_matched is True, "Subsequent call must match learned rule"
    assert parse_res_2.auto_submit is True, "Subsequent call must bypass confirmation (auto_submit=True)"

    # Step D: Direct OS execution without prompt
    exec_res = actuator.execute_sync(parse_res_2.command)
    assert exec_res.exit_code == 0, f"Expected Exit 0, got {exec_res.exit_code}"
    print(f"      Direct Actuator: {exec_res.summary(max_lines=1)}")
    print("      -> PASSED (End-to-End confirmation bypass and execution Exit 0 verified)")

    if os.path.exists(test_file):
        os.remove(test_file)

    print("\n" + "=" * 65)
    print(" ALL INTENT MEMORY & PIPELINE TESTS PASSED (0 ERRORS)")
    print("=" * 65)
    return True


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Intent Memory Subsystem")
    parser.add_argument("--test", action="store_true", help="Run memory unit test suite and exit")
    parser.add_argument("--list", action="store_true", help="List all stored user rules")
    parser.add_argument("--clear", action="store_true", help="Clear all stored rules")
    args = parser.parse_args()

    if args.test:
        ok = run_intent_memory_tests()
        sys.exit(0 if ok else 1)
    elif args.list:
        mem = get_memory()
        rules = mem.list_rules()
        print(json.dumps(rules, ensure_ascii=False, indent=2))
        sys.exit(0)
    elif args.clear:
        get_memory().clear_rules()
        print("[IntentMemory] Cleared all user rules.")
        sys.exit(0)
    else:
        ok = run_intent_memory_tests()
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
