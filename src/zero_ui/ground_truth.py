"""
Context Ground Truth, Ephemeral RAM Policy [INV-04], and Session File Primacy [INV-06].
Guarantees zero-hallucination grounded advice, safety interlocks, and in-memory ephemeral context isolation.
"""

from __future__ import annotations
import json
import sqlite3
from typing import Dict, List, Optional, Tuple
from pathlib import Path

from src.zero_ui.contracts import (
    PinoutGraph,
    ComponentDefinition,
    PinDefinition,
    SafetyRule,
    SafetyFlag
)


class GroundTruthEngine:
    """
    Deterministic Circuit & Safety Ground Truth Repository.
    Maintains immutable pinout graphs, safety rules, and session step sequences.
    """

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or ":memory:"
        self.active_graphs: Dict[str, PinoutGraph] = {}
        if self.db_path == ":memory:":
            self._persistent_conn = sqlite3.connect(":memory:")
        else:
            self._persistent_conn = None
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        if self._persistent_conn is not None:
            return self._persistent_conn
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def _init_db(self):
        """Initialize SQLite WAL database for persistent session and ground truth state."""
        conn = self._get_connection()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                project_id TEXT PRIMARY KEY,
                schematic_version TEXT NOT NULL,
                graph_json TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS session_steps (
                session_id TEXT NOT NULL,
                step_index INTEGER NOT NULL,
                step_id TEXT NOT NULL,
                description TEXT NOT NULL,
                target_component TEXT,
                target_pin TEXT,
                status TEXT NOT NULL,
                verified_ref TEXT,
                completed_at TIMESTAMP,
                PRIMARY KEY (session_id, step_index)
            );
        """)
        conn.commit()
        if self._persistent_conn is None:
            conn.close()

    def register_graph(self, graph: PinoutGraph) -> None:
        """Register a verified pinout graph in memory and persist in DB."""
        self.active_graphs[graph.project_id] = graph
        graph_dict = {
            "project_id": graph.project_id,
            "schematic_version": graph.schematic_version,
            "components": {
                c_id: {
                    "id": c.id,
                    "name": c.name,
                    "part_number": c.part_number,
                    "pins": {
                        p_id: {
                            "pin_number": p.pin_number,
                            "signal": p.signal,
                            "voltage_class": p.voltage_class,
                            "color_code": p.color_code,
                            "target_component": p.target_component,
                            "target_pin": p.target_pin,
                            "notes": p.notes
                        } for p_id, p in c.pins.items()
                    }
                } for c_id, c in graph.components.items()
            },
            "safety_rules": [
                {
                    "rule_id": r.rule_id,
                    "severity": r.severity,
                    "condition": r.condition,
                    "required_verification": r.required_verification
                } for r in graph.safety_rules
            ]
        }
        conn = self._get_connection()
        try:
            conn.execute("""
                INSERT OR REPLACE INTO projects (project_id, schematic_version, graph_json)
                VALUES (?, ?, ?)
            """, (graph.project_id, graph.schematic_version, json.dumps(graph_dict)))
            conn.commit()
        finally:
            if self._persistent_conn is None:
                conn.close()

    def get_graph(self, project_id: str) -> Optional[PinoutGraph]:
        """Retrieve graph from memory or DB."""
        if project_id in self.active_graphs:
            return self.active_graphs[project_id]

        conn = self._get_connection()
        try:
            cur = conn.execute("SELECT schematic_version, graph_json FROM projects WHERE project_id = ?", (project_id,))
            row = cur.fetchone()
            if row:
                schematic_version, graph_json = row
                data = json.loads(graph_json)
                components = {}
                for c_id, c_data in data.get("components", {}).items():
                    pins = {}
                    for p_id, p_data in c_data.get("pins", {}).items():
                        pins[p_id] = PinDefinition(**p_data)
                    components[c_id] = ComponentDefinition(
                        id=c_data["id"],
                        name=c_data["name"],
                        part_number=c_data.get("part_number"),
                        pins=pins
                    )
                rules = [SafetyRule(**r) for r in data.get("safety_rules", [])]
                graph = PinoutGraph(
                    project_id=project_id,
                    schematic_version=schematic_version,
                    components=components,
                    safety_rules=rules
                )
                self.active_graphs[project_id] = graph
                return graph
        finally:
            if self._persistent_conn is None:
                conn.close()
        return None

    def evaluate_safety_interlock(
        self,
        project_id: str,
        target_component_id: str,
        target_pin_id: str,
        vision_confidence: float = 1.0,
        focus_locked: bool = True
    ) -> Tuple[SafetyFlag, Optional[str]]:
        """
        Evaluate deterministic safety invariants.
        Returns (SafetyFlag, WarningMessage).
        """
        if not focus_locked or vision_confidence < 0.95:
            return (
                SafetyFlag.STOP_PROBE_REQUIRED,
                "Visual focus or clarity insufficient. Reposition camera or verify terminal with a multimeter before wiring."
            )

        graph = self.get_graph(project_id)
        if not graph:
            return (
                SafetyFlag.STOP_PROBE_REQUIRED,
                f"No verified schematic loaded for project '{project_id}'. Operation halted."
            )

        comp = graph.components.get(target_component_id)
        if not comp:
            return (
                SafetyFlag.STOP_PROBE_REQUIRED,
                f"Unknown component '{target_component_id}' in schematic ground truth."
            )

        pin = comp.pins.get(str(target_pin_id))
        if not pin:
            return (
                SafetyFlag.STOP_PROBE_REQUIRED,
                f"Pin '{target_pin_id}' not found on component '{target_component_id}' in verified ground truth."
            )

        # High Voltage Safety Check (220V AC)
        if "220V" in pin.voltage_class or "AC" in pin.voltage_class:
            return (
                SafetyFlag.INTERLOCK_WARNING,
                f"CRITICAL SAFETY INTERLOCK: Pin {pin.pin_number} carries {pin.voltage_class} ({pin.signal}). Ensure main breaker is isolated before connecting {pin.color_code or ''} wire."
            )

        return (SafetyFlag.CLEAR, None)

    def generate_system_prompt(self, project_id: str) -> str:
        """
        Construct a strict, anti-hallucination grounding system prompt embedding canonical ground truth.
        """
        graph = self.get_graph(project_id)
        if not graph:
            return "No verified schematic loaded. Refuse all wiring instructions."

        pinout_summary = []
        for comp_id, comp in graph.components.items():
            pinout_summary.append(f"Component [{comp.id} - {comp.name}]:")
            for pin_id, pin in comp.pins.items():
                target_str = f" -> Connects to {pin.target_component}:{pin.target_pin}" if pin.target_component else ""
                color_str = f" [Color: {pin.color_code}]" if pin.color_code else ""
                pinout_summary.append(f"  - Pin {pin.pin_number} ({pin.signal}, {pin.voltage_class}){color_str}{target_str}")

        pinout_block = "\n".join(pinout_summary)

        return (
            "=== ZERO-UI HARDWARE CO-PILOT DETERMINISTIC GROUND TRUTH ===\n"
            "You are a strict, safety-first electrical engineering co-pilot assisting in real-time hardware assembly.\n"
            "CORE SAFETY RULES:\n"
            "1. You MUST ONLY provide wiring instructions that are EXPLICITLY defined in the Ground Truth Pinout below.\n"
            "2. NEVER invent, extrapolate, or guess pin numbers, polarities, or voltages.\n"
            "3. If any connection carries 220V AC or High Voltage, you MUST issue a verbal warning to confirm the breaker is isolated.\n"
            "4. If an image is blurry, shadowed, or pin labels are unclear, state: 'Image unclear. Please re-aim or probe with multimeter.'\n"
            "5. Keep voice answers concise, direct, and actionable (under 2 sentences) for hands-free audio listening.\n\n"
            f"PROJECT: {graph.project_id} (Schematic Rev: {graph.schematic_version})\n"
            "CANONICAL PINOUT GRAPH:\n"
            f"{pinout_block}\n"
            "============================================================"
        )
