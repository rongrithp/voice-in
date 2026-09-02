"""
Monthly Usage & Cost Accounting Ledger for Zero-UI Real-Time Multimodal Personal Co-pilot.
Persists token metrics and aggregates estimated costs in Thai Baht (THB) non-blockingly to data/usage_ledger.json.
"""

from __future__ import annotations
import os
import json
import time
import threading
import logging
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Dict, Any, List, Optional

logger = logging.getLogger("zero_ui.ledger")

# Default Pricing Parameters (THB per 1M tokens)
DEFAULT_INPUT_RATE_THB_PER_M = 2.50
DEFAULT_OUTPUT_RATE_THB_PER_M = 10.00


@dataclass
class UsageRecord:
    timestamp_iso: str
    session_id: str
    client_id: str
    input_tokens: int
    output_tokens: int
    estimated_cost_thb: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "UsageRecord":
        return cls(
            timestamp_iso=data["timestamp_iso"],
            session_id=data["session_id"],
            client_id=data.get("client_id", "unknown"),
            input_tokens=int(data.get("input_tokens", 0)),
            output_tokens=int(data.get("output_tokens", 0)),
            estimated_cost_thb=float(data.get("estimated_cost_thb", 0.0))
        )


class UsageLedger:
    """
    Non-blocking, thread-safe Monthly Usage & Cost Ledger.
    """

    def __init__(
        self,
        ledger_path: str = "data/usage_ledger.json",
        input_rate_thb_per_m: float = DEFAULT_INPUT_RATE_THB_PER_M,
        output_rate_thb_per_m: float = DEFAULT_OUTPUT_RATE_THB_PER_M
    ):
        self.ledger_path = ledger_path
        self.input_rate = input_rate_thb_per_m
        self.output_rate = output_rate_thb_per_m
        self._lock = threading.Lock()
        self._ensure_storage()

    def _ensure_storage(self) -> None:
        dirname = os.path.dirname(self.ledger_path)
        if dirname and not os.path.exists(dirname):
            os.makedirs(dirname, exist_ok=True)
        if not os.path.exists(self.ledger_path):
            with self._lock:
                with open(self.ledger_path, "w", encoding="utf-8") as f:
                    json.dump({"records": []}, f, indent=2)

    def calculate_cost_thb(self, input_tokens: int, output_tokens: int) -> float:
        input_cost = (input_tokens / 1_000_000.0) * self.input_rate
        output_cost = (output_tokens / 1_000_000.0) * self.output_rate
        return round(input_cost + output_cost, 6)

    def record_usage(
        self,
        session_id: str,
        client_id: str,
        input_tokens: int,
        output_tokens: int,
        cost_in_thb: Optional[float] = None
    ) -> UsageRecord:
        """
        Appends usage record thread-safely to disk.
        """
        if cost_in_thb is None:
            cost_in_thb = self.calculate_cost_thb(input_tokens, output_tokens)

        now_iso = datetime.now().isoformat()
        record = UsageRecord(
            timestamp_iso=now_iso,
            session_id=session_id,
            client_id=client_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_thb=cost_in_thb
        )

        with self._lock:
            try:
                data: Dict[str, Any] = {"records": []}
                if os.path.exists(self.ledger_path):
                    with open(self.ledger_path, "r", encoding="utf-8") as f:
                        data = json.load(f)

                data.setdefault("records", []).append(record.to_dict())

                with open(self.ledger_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
            except Exception as e:
                logger.error(f"Failed to persist usage record: {e}")

        return record

    def get_monthly_summary(self, month_str: Optional[str] = None) -> Dict[str, Any]:
        """
        Aggregates usage for a specific month (format: 'YYYY-MM', defaults to current month).
        """
        if not month_str:
            month_str = datetime.now().strftime("%Y-%m")

        with self._lock:
            records = self._read_all_records()

        month_records = [r for r in records if r.timestamp_iso.startswith(month_str)]

        total_input = sum(r.input_tokens for r in month_records)
        total_output = sum(r.output_tokens for r in month_records)
        total_cost = sum(r.estimated_cost_thb for r in month_records)

        return {
            "month": month_str,
            "session_count": len(month_records),
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "total_tokens": total_input + total_output,
            "total_cost_thb": round(total_cost, 4),
            "currency": "THB"
        }

    def _read_all_records(self) -> List[UsageRecord]:
        if not os.path.exists(self.ledger_path):
            return []
        try:
            with open(self.ledger_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return [UsageRecord.from_dict(d) for d in data.get("records", [])]
        except Exception as e:
            logger.error(f"Failed reading ledger records: {e}")
            return []
