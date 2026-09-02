"""
Central Cloud Backend Server for Zero-UI Real-Time Multimodal Personal Co-pilot.
Provides WebSocket endpoints (ws:// and wss:// TLS) for Edge Mobile and PC Station clients.
"""

from __future__ import annotations
import asyncio
import json
import logging
import ssl
from typing import Dict, Optional, Set
import websockets
try:
    from websockets.asyncio.server import ServerConnection as WebSocketConnection
except ImportError:
    from websockets.server import WebSocketServerProtocol as WebSocketConnection

from src.zero_ui.contracts import (
    ClientHello,
    CaptureTriggerEvent,
    SensorPayload,
    ServerAudioStreamChunk,
    StateUpdateEvent,
    AbortFrame,
    AttachedDocumentPayload,
    UserPersonalizationConfig,
    UserRuntimeConfig,
    UserIdentity,
    EntitlementTier
)
from src.zero_ui.ground_truth import GroundTruthEngine
from src.zero_ui.orchestrator import SessionOrchestrator
from src.zero_ui.ledger import UsageLedger

logger = logging.getLogger("zero_ui.server")


class CentralZeroUIServer:
    """
    Central Cloud Backend WebSocket Gateway.
    Orchestrates multiple concurrent edge mobile and PC station sessions over secure WebSockets (wss://).
    """

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8765,
        db_path: str = ":memory:",
        ground_truth_engine: Optional[GroundTruthEngine] = None,
        ssl_context: Optional[ssl.SSLContext] = None,
        session_idle_timeout_sec: float = 90.0,
        ledger: Optional[UsageLedger] = None
    ):
        self.host = host
        self.port = port
        self.ground_truth = ground_truth_engine or GroundTruthEngine(db_path)
        self.ssl_context = ssl_context
        self.session_idle_timeout_sec = session_idle_timeout_sec
        self.ledger = ledger or UsageLedger()
        self.active_sessions: Dict[str, SessionOrchestrator] = {}
        self.connected_clients: Dict[str, WebSocketConnection] = {}
        self.client_last_activity: Dict[str, float] = {}
        self._server = None
        self._is_running = False
        self._watchdog_task: Optional[asyncio.Task] = None

    async def start(self):
        """Start the WebSocket server (ws:// or wss:// if ssl_context provided)."""
        scheme = "wss" if self.ssl_context else "ws"
        self._server = await websockets.serve(
            self._handle_connection,
            self.host,
            self.port,
            ssl=self.ssl_context
        )
        self._is_running = True
        self._watchdog_task = asyncio.create_task(self._session_idle_watchdog())
        logger.info(f"CentralZeroUIServer listening on {scheme}://{self.host}:{self.port}")

    async def stop(self):
        """Stop the WebSocket server."""
        self._is_running = False
        if self._watchdog_task:
            self._watchdog_task.cancel()
            try:
                await self._watchdog_task
            except asyncio.CancelledError:
                pass
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            logger.info("CentralZeroUIServer stopped.")

    async def _session_idle_watchdog(self):
        """Periodic background watchdog terminating idle WebSocket sessions (Tier 2)."""
        while self._is_running:
            try:
                await asyncio.sleep(2.0)
                import time
                now = time.time()
                for client_id, last_time in list(self.client_last_activity.items()):
                    if now - last_time > self.session_idle_timeout_sec:
                        logger.warning(f"Closing idle session for client '{client_id}' (exceeded {self.session_idle_timeout_sec}s timeout).")
                        ws = self.connected_clients.get(client_id)
                        if ws:
                            await ws.close(1000, "Session idle timeout")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in session watchdog loop: {e}")

    async def _handle_connection(self, websocket: WebSocketConnection, path: str = ""):
        """Handle incoming client WebSocket lifecycle."""
        client_id = None
        session_id = None
        orchestrator: Optional[SessionOrchestrator] = None

        try:
            async for raw_message in websocket:
                import time
                if client_id:
                    self.client_last_activity[client_id] = time.time()

                try:
                    data = json.loads(raw_message)
                except Exception as e:
                    logger.error(f"Malformed JSON frame received: {e}")
                    continue

                msg_type = data.get("type")

                # 1. Handshake: CLIENT_HELLO
                if msg_type == "CLIENT_HELLO":
                    hello = ClientHello.from_dict(data)
                    client_id = hello.client_id

                    # [INV-AUTH] Entitlement gate: reject expired identities before arming session
                    if hello.identity is not None and not hello.identity.is_active():
                        auth_err = {
                            "type": "AUTH_ERROR",
                            "code": "ENTITLEMENT_EXPIRED",
                            "tier": hello.identity.tier.value,
                            "message": "Trial period has ended. Please upgrade to continue."
                        }
                        await websocket.send(json.dumps(auth_err))
                        logger.warning(
                            f"[AUTH] Rejected expired client '{hello.client_id}' "
                            f"(tier={hello.identity.tier.value})."
                        )
                        return  # Close connection; do not arm session

                    session_id = f"sess_{client_id}"
                    self.connected_clients[client_id] = websocket
                    self.client_last_activity[client_id] = time.time()

                    project_id = data.get("project_id", "default_project")
                    orchestrator = SessionOrchestrator(
                        session_id=session_id,
                        project_id=project_id,
                        ground_truth_engine=self.ground_truth,
                        ledger=self.ledger
                    )
                    self.active_sessions[session_id] = orchestrator

                    # Forward BYOK api_key to orchestrator session context if provided
                    if hello.identity and hello.identity.api_key:
                        orchestrator.session_context = {
                            "byok_api_key": hello.identity.api_key,
                            "user_email": hello.identity.email,
                            "entitlement_tier": hello.identity.tier.value
                        }

                    ready_resp = orchestrator.handle_client_hello(hello)
                    await websocket.send(json.dumps(ready_resp))
                    logger.info(f"Client '{client_id}' connected and armed in project '{project_id}'.")
                # 2. CAPTURE_TRIGGER Event
                elif msg_type == "CAPTURE_TRIGGER":
                    trigger = CaptureTriggerEvent.from_dict(data)
                    logger.info(f"Trigger received from {client_id}: {trigger.trigger_source}")
                    ack = {
                        "type": "TRIGGER_ACK",
                        "status": "ARMED_FOR_PAYLOAD",
                        "timestamp_ns": trigger.timestamp_ns
                    }
                    await websocket.send(json.dumps(ack))

                # 3. SENSOR_PAYLOAD Event (Multimodal Ingestion)
                elif msg_type == "SENSOR_PAYLOAD":
                    if not orchestrator:
                        logger.warning("Received SENSOR_PAYLOAD before CLIENT_HELLO handshake.")
                        continue

                    payload = SensorPayload.from_dict(data)
                    target_comp = data.get("target_component_id")
                    target_pin = data.get("target_pin_id")

                    async for chunk in orchestrator.process_sensor_payload(
                        payload,
                        target_component_id=target_comp,
                        target_pin_id=target_pin
                    ):
                        await websocket.send(chunk.to_json())

                # 4. ABORT_FRAME Event (Local Intent Match Cancel <50ms)
                elif msg_type == "ABORT_FRAME":
                    abort = AbortFrame.from_dict(data)
                    logger.info(f"Abort frame received from {client_id}: {abort.reason}")
                    if orchestrator:
                        await orchestrator.handle_abort_frame(abort)
                    ack = {
                        "type": "ABORT_ACK",
                        "session_id": abort.session_id,
                        "sequence_id": abort.sequence_id,
                        "status": "CANCELLED"
                    }
                    await websocket.send(json.dumps(ack))

                # 5. ATTACH_DOCUMENT Event ([INV-06] Session File Primacy)
                elif msg_type == "ATTACH_DOCUMENT":
                    doc = AttachedDocumentPayload.from_dict(data)
                    if orchestrator:
                        orchestrator.attach_document(doc)
                    ack = {
                        "type": "DOCUMENT_ATTACHED",
                        "file_name": doc.file_name,
                        "size_bytes": doc.size_bytes,
                        "priority_rank": doc.priority_rank
                    }
                    await websocket.send(json.dumps(ack))

                # 6. SET_PERSONALIZATION Event (Decoupled Config & Persona)
                elif msg_type == "SET_PERSONALIZATION":
                    config = UserPersonalizationConfig.from_dict(data)
                    if orchestrator:
                        orchestrator.set_personalization(config)
                    ack = {
                        "type": "PERSONALIZATION_UPDATED",
                        "persona_name": config.persona_name
                    }
                    await websocket.send(json.dumps(ack))

                # 7. END_STREAM Event (RMS silence teardown)
                elif msg_type == "END_STREAM":
                    logger.info(f"EndStreamFrame received from {client_id}: {data.get('reason')}")
                    if orchestrator:
                        orchestrator.ephemeral_buffer.purge()
                    ack = {
                        "type": "STREAM_TEARDOWN_ACK",
                        "session_id": session_id,
                        "status": "STANDBY_DORMANT"
                    }
                    await websocket.send(json.dumps(ack))

                # 8. QUICK_DROP Event (Transient text/URL ingestion without UI)
                elif msg_type == "QUICK_DROP":
                    content = data.get("content", "")
                    src = data.get("source", "PC_QUICK_DROP")
                    logger.info(f"Quick drop ingested from {client_id} ({src}): {content[:60]}...")
                    if orchestrator:
                        orchestrator.dialogue_history.append({
                            "role": "user",
                            "type": "quick_drop",
                            "source": src,
                            "content": content,
                            "timestamp": time.time()
                        })
                    ack = {
                        "type": "QUICK_DROP_ACK",
                        "status": "INGESTED",
                        "source": src,
                        "length": len(content)
                    }
                    await websocket.send(json.dumps(ack))

                # 9. Unknown Message
                else:
                    logger.warning(f"Unhandled message type: {msg_type}")

        except websockets.exceptions.ConnectionClosed:
            logger.info(f"Client connection closed: {client_id}")
        finally:
            if client_id and client_id in self.connected_clients:
                del self.connected_clients[client_id]
            if session_id and session_id in self.active_sessions:
                del self.active_sessions[session_id]


# Backwards compatibility alias
ZeroUIServer = CentralZeroUIServer
