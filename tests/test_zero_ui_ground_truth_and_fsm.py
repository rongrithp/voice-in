"""
Safety Guard, Ephemeral In-Memory Boundary, and FSM Transitions.
"""

import pytest
from src.zero_ui.contracts import (
    PinoutGraph,
    ComponentDefinition,
    PinDefinition,
    SafetyRule,
    SafetyFlag
)
from src.zero_ui.ground_truth import GroundTruthEngine
from src.zero_ui.fsm import (
    ServerSessionFSM,
    ServerSessionState,
    EdgeClientFSM,
    EdgeClientState
)


@pytest.fixture
def sample_graph():
    return PinoutGraph(
        project_id="curing_cabinet",
        schematic_version="1.0",
        components={
            "SSR_01": ComponentDefinition(
                id="SSR_01",
                name="Solid State Relay",
                pins={
                    "1": PinDefinition(pin_number="1", signal="AC_HOT_IN", voltage_class="220V_AC", color_code="BROWN"),
                    "2": PinDefinition(pin_number="2", signal="AC_LOAD_OUT", voltage_class="220V_AC", color_code="BLACK"),
                    "3": PinDefinition(pin_number="3", signal="DC_POS", voltage_class="5V_DC", color_code="RED", target_component="ESP32", target_pin="GPIO_18"),
                    "4": PinDefinition(pin_number="4", signal="DC_NEG", voltage_class="GND", color_code="BLUE")
                }
            )
        },
        safety_rules=[
            SafetyRule(
                rule_id="RULE_MAIN_BREAKER",
                severity="CRITICAL_FATAL",
                condition="220V wiring",
                required_verification="CONFIRM_BREAKER_OFF"
            )
        ]
    )


def test_ground_truth_db_persistence(sample_graph):
    engine = GroundTruthEngine(":memory:")
    engine.register_graph(sample_graph)

    restored = engine.get_graph("curing_cabinet")
    assert restored is not None
    assert restored.project_id == "curing_cabinet"
    assert "SSR_01" in restored.components
    assert restored.components["SSR_01"].pins["1"].voltage_class == "220V_AC"


def test_safety_interlock_evaluator(sample_graph):
    engine = GroundTruthEngine(":memory:")
    engine.register_graph(sample_graph)

    # 1. 220V AC terminal test -> Must return INTERLOCK_WARNING
    flag, warn = engine.evaluate_safety_interlock("curing_cabinet", "SSR_01", "1", vision_confidence=1.0, focus_locked=True)
    assert flag == SafetyFlag.INTERLOCK_WARNING
    assert "220V" in warn
    assert "breaker is isolated" in warn

    # 2. Low voltage terminal test -> Must return CLEAR
    flag, warn = engine.evaluate_safety_interlock("curing_cabinet", "SSR_01", "3", vision_confidence=1.0, focus_locked=True)
    assert flag == SafetyFlag.CLEAR
    assert warn is None

    # 3. Blurry/low confidence image test -> Must return STOP_PROBE_REQUIRED
    flag, warn = engine.evaluate_safety_interlock("curing_cabinet", "SSR_01", "3", vision_confidence=0.7, focus_locked=False)
    assert flag == SafetyFlag.STOP_PROBE_REQUIRED
    assert "multimeter" in warn

    # 4. Unknown pin test -> Must return STOP_PROBE_REQUIRED
    flag, warn = engine.evaluate_safety_interlock("curing_cabinet", "SSR_01", "99")
    assert flag == SafetyFlag.STOP_PROBE_REQUIRED


def test_ground_truth_system_prompt(sample_graph):
    engine = GroundTruthEngine(":memory:")
    engine.register_graph(sample_graph)

    prompt = engine.generate_system_prompt("curing_cabinet")
    assert "ZERO-UI HARDWARE CO-PILOT DETERMINISTIC GROUND TRUTH" in prompt
    assert "SSR_01" in prompt
    assert "AC_HOT_IN" in prompt
    assert "220V_AC" in prompt


def test_server_session_fsm():
    transitions = []

    def log_change(old_s, new_s):
        transitions.append((old_s, new_s))

    fsm = ServerSessionFSM("sess_test", on_state_change=log_change)
    assert fsm.state == ServerSessionState.SESSION_INITIALIZED

    # Valid path: Initialized -> Armed -> Ingesting -> Evaluating -> Streaming -> Ack -> Armed
    assert fsm.transition_to(ServerSessionState.STANDBY_ARMED, "Loaded schematic") is True
    assert fsm.transition_to(ServerSessionState.INGESTING_SENSORY, "Received trigger") is True
    assert fsm.transition_to(ServerSessionState.SAFETY_GROUND_TRUTH_EVAL, "Sensory ingested") is True
    assert fsm.transition_to(ServerSessionState.STREAMING_AUDIO_RESPONSE, "Safety clear") is True
    assert fsm.transition_to(ServerSessionState.AWAITING_PHYSICAL_ACK, "Audio done") is True
    assert fsm.transition_to(ServerSessionState.STANDBY_ARMED, "User confirmed") is True

    assert len(transitions) == 6

    # Invalid transition check
    assert fsm.transition_to(ServerSessionState.STREAMING_AUDIO_RESPONSE, "Invalid leap") is False


def test_edge_client_fsm():
    edge_fsm = EdgeClientFSM("android_01")
    assert edge_fsm.state == EdgeClientState.BOOT_OFFLINE

    assert edge_fsm.transition_to(EdgeClientState.CONNECTING_CLOUD) is True
    assert edge_fsm.transition_to(EdgeClientState.CONNECTED_READY) is True
    assert edge_fsm.transition_to(EdgeClientState.CAPTURING) is True
    assert edge_fsm.transition_to(EdgeClientState.BUFFERING_AND_UPLOADING) is True
    assert edge_fsm.transition_to(EdgeClientState.STREAMING_PLAYBACK) is True
    assert edge_fsm.transition_to(EdgeClientState.CONNECTED_READY) is True

    # Simulate network drop during capture
    assert edge_fsm.transition_to(EdgeClientState.CAPTURING) is True
    assert edge_fsm.transition_to(EdgeClientState.OFFLINE_RETRY_QUEUE, "Network dropped") is True
    assert edge_fsm.transition_to(EdgeClientState.CONNECTING_CLOUD, "Reconnecting to Cloud Gateway") is True
