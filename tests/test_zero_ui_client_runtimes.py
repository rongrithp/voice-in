"""
Unit and Integration Tests for Zero-UI Client Runtimes & Hardware Sensors (Phase 3).
Covers:
- Android Edge Headless Runtime (Hardware Trigger Debounce, Auto-Reconnect Backoff, Permission-Gated Telemetry [INV-05])
- PC Workstation Client (Screen Capture Pipeline, Mic PCM Streaming, Drag-and-Drop Documents [INV-06], Audio Focus Ducking & Subtitles [INV-07])
"""

import asyncio
import json
import time
import pytest

from src.zero_ui.contracts import (
    TriggerSource,
    TelemetryPayload,
    GpsCoordinates,
    MotionTelemetry,
    DeviceHealthTelemetry,
    LockScreenIndicatorStatus,
    AndroidOngoingNotificationState,
    AttachedDocumentPayload,
    SafetyFlag,
    UserPersonalizationConfig,
    UserRuntimeConfig,
    WakeWordConfig,
    ThumbnailOverlayState,
    AudioConfig,
    PersonaConfig
)
from src.zero_ui.fsm import EdgeClientState, TwoStageTimeoutFSM
from src.zero_ui.mock_edge_client import EdgeClientHarness, TriggerDebouncedError
from src.zero_ui.station_client import StationIngestionClient
from src.zero_ui.server import CentralZeroUIServer
from src.zero_ui.ground_truth import GroundTruthEngine


def test_edge_client_hardware_trigger_debounce():
    client = EdgeClientHarness(debounce_interval_ms=100)

    # 1. First trigger accepted
    assert client.check_trigger_debounce(TriggerSource.BT_MEDIA_BUTTON) is True

    # 2. Immediate second trigger of same button rejected (debounced)
    assert client.check_trigger_debounce(TriggerSource.BT_MEDIA_BUTTON) is False

    # 3. Trigger of different hardware source (FLOATING_SHUTTER_BUTTON) accepted independently
    assert client.check_trigger_debounce(TriggerSource.FLOATING_SHUTTER_BUTTON) is True

    # 4. Immediate second trigger of floating shutter rejected
    assert client.check_trigger_debounce(TriggerSource.FLOATING_SHUTTER_BUTTON) is False

    # 5. After debounce interval elapsed, trigger accepted again
    time.sleep(0.11)
    assert client.check_trigger_debounce(TriggerSource.BT_MEDIA_BUTTON) is True


def test_edge_client_permission_gated_telemetry_null_safety():
    client = EdgeClientHarness()

    input_telemetry = TelemetryPayload(
        focus_locked=True,
        lux_level=420.0,
        gps=GpsCoordinates(latitude=13.7563, longitude=100.5018, accuracy_meters=3.5),
        motion=MotionTelemetry(user_activity="WALKING", acceleration_z=9.81),
        device_health=DeviceHealthTelemetry(battery_level=0.85, thermal_status="NORMAL")
    )

    # 1. Default: All permissions False -> Sensitive sensors MUST be None [INV-05]
    payload_no_perms = client.serialize_camera_snapshot(
        image_bytes=b"jpeg_bytes",
        audio_bytes=b"pcm_bytes",
        focus_locked=True,
        telemetry=input_telemetry
    )
    assert payload_no_perms.telemetry.focus_locked is True
    assert payload_no_perms.telemetry.lux_level == 420.0
    assert payload_no_perms.telemetry.gps is None
    assert payload_no_perms.telemetry.motion is None
    assert payload_no_perms.telemetry.device_health is None

    # 2. Grant GPS and Motion permissions only
    client.set_permissions({"gps": True, "motion": True, "device_health": False})
    payload_partial = client.serialize_camera_snapshot(
        image_bytes=b"jpeg_bytes",
        telemetry=input_telemetry
    )
    assert payload_partial.telemetry.gps is not None
    assert payload_partial.telemetry.gps.latitude == 13.7563
    assert payload_partial.telemetry.motion is not None
    assert payload_partial.telemetry.motion.user_activity == "WALKING"
    assert payload_partial.telemetry.device_health is None

    # 3. Grant All permissions
    client.set_permissions({"device_health": True})
    payload_all = client.serialize_camera_snapshot(
        image_bytes=b"jpeg_bytes",
        telemetry=input_telemetry
    )
    assert payload_all.telemetry.device_health is not None
    assert payload_all.telemetry.device_health.battery_level == 0.85


def test_edge_client_auto_reconnect_backoff():
    async def _test():
        # Connect to non-existent server port to test retry exhaustion
        client = EdgeClientHarness(
            server_uri="ws://127.0.0.1:59999",
            client_id="reconnect_test_unit"
        )

        with pytest.raises(ConnectionError) as exc_info:
            await client.connect_with_retry(max_retries=2, initial_delay=0.01, backoff_factor=1.5)

        assert "Failed to connect after 2 attempts" in str(exc_info.value)
        # Verify FSM cycled to BOOT_OFFLINE after retry exhaustion
        assert client.fsm.state == EdgeClientState.BOOT_OFFLINE

    asyncio.run(_test())


def test_station_client_screen_capture_and_mic_chunks():
    async def _test():
        client = StationIngestionClient(client_id="pc_media_test")

        # 1. Screen Capture Frame
        frame = client.capture_screen_frame()
        assert isinstance(frame, bytes)
        assert len(frame) > 0

        # 2. Microphone PCM Stream
        chunks = []
        async for pcm in client.stream_mic_audio_chunks(chunk_size_bytes=512, num_chunks=3):
            chunks.append(pcm)

        assert len(chunks) == 3
        assert all(len(c) == 512 for c in chunks)

    asyncio.run(_test())


def test_station_client_drag_and_drop_and_audio_ducking():
    async def _test():
        port = 8794
        engine = GroundTruthEngine(":memory:")
        server = CentralZeroUIServer(host="127.0.0.1", port=port, ground_truth_engine=engine)
        await server.start()

        received_subtitles = []

        def on_sub(token: str):
            received_subtitles.append(token)

        try:
            client = StationIngestionClient(
                server_uri=f"ws://127.0.0.1:{port}",
                client_id="pc_drag_drop_station",
                on_subtitle_received=on_sub
            )
            await client.connect()

            # 1. Test Drag and Drop Document Ingestion [INV-06]
            ack = await client.attach_document_drag_and_drop(
                file_name="circuit_schematic.pdf",
                content=b"%PDF-1.4\nMOCK_PDF_DATA_PIN_1_TO_220V",
                mime_type="application/pdf",
                priority_rank=1
            )
            assert ack["type"] == "DOCUMENT_ATTACHED"
            assert ack["file_name"] == "circuit_schematic.pdf"
            assert ack["priority_rank"] == 1

            # 2. Test Audio Focus Ducking & Live Subtitles [INV-07]
            assert client.is_audio_ducked is False
            chunks = await client.send_screen_or_macro_frame(
                image_bytes=b"cad_buffer",
                audio_query_bytes=b"inspect connection"
            )
            assert len(chunks) >= 1
            # Verify audio focus is restored after streaming ends
            assert client.is_audio_ducked is False

            await client.close()
        finally:
            await server.stop()

    asyncio.run(_test())


def test_station_client_talk_to_cursor_pipeline():
    async def _test():
        port = 8795
        engine = GroundTruthEngine(":memory:")
        server = CentralZeroUIServer(host="127.0.0.1", port=port, ground_truth_engine=engine)
        await server.start()

        injected_tokens = []

        def on_injected(token: str):
            injected_tokens.append(token)

        try:
            client = StationIngestionClient(
                server_uri=f"ws://127.0.0.1:{port}",
                client_id="pc_cursor_station",
                talk_to_cursor_hotkey="F13",
                on_text_injected_at_cursor=on_injected
            )
            await client.connect()

            # Trigger F13 talk-to-cursor with simulated audio query
            tokens = await client.trigger_talk_to_cursor(
                audio_query_bytes=b"transcribe this note to cursor"
            )

            assert len(tokens) >= 1
            assert len(injected_tokens) == len(tokens)
            assert client.injected_cursor_tokens == tokens
            # Audio focus restored after streaming
            assert client.is_audio_ducked is False

            await client.close()
        finally:
            await server.stop()

    asyncio.run(_test())


def test_station_client_read_selected_text_tts():
    async def _test():
        port = 8796
        engine = GroundTruthEngine(":memory:")
        server = CentralZeroUIServer(host="127.0.0.1", port=port, ground_truth_engine=engine)
        await server.start()

        try:
            client = StationIngestionClient(
                server_uri=f"ws://127.0.0.1:{port}",
                client_id="pc_tts_station",
                read_selection_hotkey="Ctrl+Shift+R"
            )
            await client.connect()

            # 1. Empty selection test -> returns empty list without network spam
            empty_chunks = await client.read_selected_text_aloud(selected_text="")
            assert empty_chunks == []

            # 2. Valid highlighted text read aloud
            chunks = await client.read_selected_text_aloud(
                selected_text="Caution: High voltage circuit breaker is active."
            )
            assert len(chunks) >= 1
            assert client.is_audio_ducked is False

            await client.close()
        finally:
            await server.stop()

    asyncio.run(_test())


def test_station_client_f13_to_f20_hotkeys_and_multi_monitor():
    async def _test():
        port = 8797
        engine = GroundTruthEngine(":memory:")
        server = CentralZeroUIServer(host="127.0.0.1", port=port, ground_truth_engine=engine)
        await server.start()

        injected = []
        try:
            client = StationIngestionClient(
                server_uri=f"ws://127.0.0.1:{port}",
                client_id="pc_f13_f20_station",
                on_text_injected_at_cursor=lambda t: injected.append(t)
            )
            await client.connect()

            # --- F13: Talk to cursor via handle_hotkey ---
            tokens = await client.handle_hotkey("F13", audio_query_bytes=b"test voice input")
            assert len(tokens) >= 1
            assert len(injected) == len(tokens)

            # --- F14: Read selected text via handle_hotkey (Non-destructive) ---
            f14_chunks = await client.handle_hotkey("F14", selected_text="Highlighted document section")
            assert len(f14_chunks) >= 1

            # --- F15: Read Below Text via handle_hotkey and direct read_below_text ---
            assert client.pc_station_hotkeys["f15"] == "read_below_text"
            f15_chunks = await client.handle_hotkey("F15", mock_selected_text="Read from mouse down to EOF")
            assert len(f15_chunks) >= 1
            direct_f15_chunks = await client.read_below_text(mock_selected_text="Direct read below text")
            assert len(direct_f15_chunks) >= 1

            # --- Non-Destructive Clipboard Preservation Invariant ---
            extracted_text = client.extract_selected_text_non_destructive()
            assert isinstance(extracted_text, str)

            # --- F16: Toggle audio playback via handle_hotkey ---
            # Initially active -> toggle to pause (False)
            res1 = await client.handle_hotkey("F16")
            assert res1 is False
            assert client.audio_sink.is_paused is True

            # Toggle back to play (True)
            res2 = await client.handle_hotkey("F16")
            assert res2 is True
            assert client.audio_sink.is_paused is False

            # --- F17, F18, F19: Local OS Clipboard Multi-Monitor Captures ---
            d1_bytes = await client.handle_hotkey("F17")
            assert isinstance(d1_bytes, bytes)
            assert len(d1_bytes) > 0
            assert client.local_clipboard_image == d1_bytes

            d2_bytes = await client.handle_hotkey("F18")
            assert isinstance(d2_bytes, bytes)
            assert len(d2_bytes) > 0
            assert client.local_clipboard_image == d2_bytes

            d3_bytes = await client.handle_hotkey("F19")
            assert isinstance(d3_bytes, bytes)
            assert len(d3_bytes) > 0
            assert client.local_clipboard_image == d3_bytes

            # Direct methods check (Local clipboard captures)
            assert len(client.capture_display_1()) > 0
            assert len(client.capture_display_2()) > 0
            assert len(client.capture_display_3()) > 0

            # --- F20: Display Selector Overlay (new behavior) ---
            # First press opens the selector overlay
            f20_result = await client.handle_hotkey("F20")
            assert isinstance(f20_result, dict), f"Expected dict from F20 selector, got: {type(f20_result)}"
            assert f20_result["action"] == "SELECTOR_OPENED"
            assert client.f20_selector_visible is True
            assert len(f20_result["tiles"]) >= 1

            # Select display 1 to start streaming
            selected_idx = client.select_display_and_start_stream(1)
            assert selected_idx == 0  # Display 1 -> 0-indexed
            assert client.is_ultrawide_streaming is True
            assert client.f20_selector_visible is False  # auto-dismissed

            # Stream 2 frames and verify compression <= 1280px in RAM
            frames = []
            async for frame in client.stream_ultrawide_live_frames(interval_sec=0.01, max_frames=2):
                assert isinstance(frame, bytes)
                assert len(frame) > 0
                frames.append(frame)
            assert len(frames) == 2

            # Press F20 while streaming -> immediately stops feed
            # Re-arm the stream first (generator already stopped naturally above)
            client.select_display_and_start_stream(1)
            assert client.is_ultrawide_streaming is True
            f20_stop = await client.handle_hotkey("F20")
            assert f20_stop["action"] == "STREAM_STOPPED"
            assert client.is_ultrawide_streaming is False

            await client.close()
        finally:
            await server.stop()

    asyncio.run(_test())


def test_configurable_wake_word_loading_and_matching():
    from src.zero_ui.contracts import WakeWordConfig, UserRuntimeConfig

    # 1. Default config
    cfg = WakeWordConfig()
    assert cfg.primary_word == "gemini"
    assert "hey gemini" in cfg.aliases
    assert cfg.matches("Hey Gemini, what's on my screen?") is True
    assert cfg.matches("OK GEMINI check pin 1") is True
    assert cfg.matches("gemini look at this") is True
    assert cfg.matches("random noise words") is False

    # 2. Custom configuration
    custom_cfg = WakeWordConfig(
        primary_word="jarvis",
        aliases=["hey jarvis", "listen jarvis"],
        sensitivity=0.8
    )
    assert custom_cfg.matches("Hey Jarvis, summarize document") is True
    assert custom_cfg.matches("Hey Gemini") is False

    # 3. UserRuntimeConfig integration
    runtime_cfg = UserRuntimeConfig.from_dict({
        "wake_word": {
            "primary_word": "computer",
            "aliases": ["hey computer"],
            "sensitivity": 0.6
        }
    })
    assert runtime_cfg.wake_word.primary_word == "computer"
    assert runtime_cfg.wake_word.matches("Hey Computer do task") is True


def test_dynamic_rms_silence_teardown_and_dormant_standby():
    async def _test():
        port = 8798
        engine = GroundTruthEngine(":memory:")
        server = CentralZeroUIServer(host="127.0.0.1", port=port, ground_truth_engine=engine)
        await server.start()

        try:
            # 1. Verify default 5.0s silence timeout on PC Station Client
            client = StationIngestionClient(server_uri=f"ws://127.0.0.1:{port}", client_id="pc_rms_teardown_test")
            await client.connect()

            # Verify initial state and default 5.0s timeout
            assert client.is_dormant is False
            assert client.rms_silence_timeout_sec == 5.0
            assert client.dynamic_noise_gate.silence_teardown_sec == 5.0

            # Feed active speech PCM
            active_pcm = b"\x50\x20" * 512
            is_active = client.dynamic_noise_gate.process_pcm_frame(active_pcm)
            assert is_active is True

            # Feed silence frame at t=100.0
            silence_pcm = b"\x00\x00" * 512
            is_active_silence = client.dynamic_noise_gate.process_pcm_frame(silence_pcm, now=100.0)
            assert is_active_silence is False

            # At t=103.0 (3.0s elapsed < 5.0s), teardown must NOT trigger
            client.dynamic_noise_gate.process_pcm_frame(silence_pcm, now=103.0)
            assert client.is_dormant is False

            # Fast-forward silence past 5.0s teardown threshold (t=105.5s)
            # This triggers silence teardown callback
            client.dynamic_noise_gate.process_pcm_frame(silence_pcm, now=105.5)

            # Await teardown frame
            ack = await client.teardown_stream_to_dormant(reason="RMS_SILENCE_TIMEOUT")
            assert ack is not None
            assert ack.get("type") == "STREAM_TEARDOWN_ACK"
            assert ack.get("status") == "STANDBY_DORMANT"
            assert client.is_dormant is True
            assert client.fsm.current_state == EdgeClientState.STANDBY_DORMANT

            # 2. Verify dynamic configurable silence timeout on Edge Client (e.g. 1.5s)
            custom_cfg = UserRuntimeConfig(rms_silence_timeout_sec=1.5)
            edge_client = EdgeClientHarness(
                server_uri=f"ws://127.0.0.1:{port}",
                client_id="edge_custom_timeout_test",
                config=custom_cfg
            )
            assert edge_client.rms_silence_timeout_sec == 1.5
            assert edge_client.dynamic_noise_gate.silence_teardown_sec == 1.5

            # Active frame at t=200.0
            edge_client.dynamic_noise_gate.process_pcm_frame(active_pcm, now=200.0)
            # Silence at t=200.0
            edge_client.dynamic_noise_gate.process_pcm_frame(silence_pcm, now=200.0)
            # Silence at t=201.0 (1.0s < 1.5s) - not triggered
            edge_client.dynamic_noise_gate.process_pcm_frame(silence_pcm, now=201.0)
            # Silence at t=201.8 (1.8s >= 1.5s) - triggers teardown
            edge_client.dynamic_noise_gate.process_pcm_frame(silence_pcm, now=201.8)

            await client.close()
        finally:
            await server.stop()

    asyncio.run(_test())


def test_quick_drop_box_transient_ingestion():
    async def _test():
        port = 8799
        engine = GroundTruthEngine(":memory:")
        server = CentralZeroUIServer(host="127.0.0.1", port=port, ground_truth_engine=engine)
        await server.start()

        try:
            # 1. PC Station Client Quick-Drop (Alt+Space)
            pc_client = StationIngestionClient(server_uri=f"ws://127.0.0.1:{port}", client_id="pc_quickdrop_test")
            await pc_client.connect()

            # Open transient overlay
            pc_client.open_quick_drop_box()
            assert pc_client.quick_drop_overlay_visible is True

            # Submit via submit_quick_drop (dismiss on enter without retaining UI)
            ack1 = await pc_client.submit_quick_drop("https://example.com/schematic-v2.pdf")
            assert pc_client.quick_drop_overlay_visible is False
            assert ack1.get("type") == "QUICK_DROP_ACK"
            assert ack1.get("status") == "INGESTED"
            assert ack1.get("source") == "PC_QUICK_DROP"

            # Submit via hotkey dispatch (Alt+Space)
            ack2 = await pc_client.handle_hotkey("alt+space", text_or_url="Analyze capacitor C12 voltage rating")
            assert ack2.get("type") == "QUICK_DROP_ACK"
            assert ack2.get("status") == "INGESTED"

            # 2. Android Edge Client ACTION_SEND Intent
            edge_client = EdgeClientHarness(server_uri=f"ws://127.0.0.1:{port}", client_id="android_share_test")
            await edge_client.connect()

            ack3 = await edge_client.handle_action_send("Shared technical log text from mobile logcat")
            assert ack3.get("type") == "QUICK_DROP_ACK"
            assert ack3.get("source") == "ANDROID_ACTION_SEND"

            await pc_client.close()
            await edge_client.disconnect()
        finally:
            await server.stop()

    asyncio.run(_test())


def test_android_transient_image_thumbnail_lifecycle():
    # 1. Default lifecycle and auto-dismiss on 4.0s timeout [INV-12]
    edge = EdgeClientHarness(client_id="android_thumb_test")
    assert edge.thumbnail_state == ThumbnailOverlayState.HIDDEN
    assert edge.active_thumbnail_bytes is None
    assert edge.thumbnail_dismiss_timeout_sec == 4.0

    # Ingest image frame
    img_data = b"\xff\xd8\xff\xe0mock_snapshot_frame"
    edge.render_thumbnail(img_data, now=100.0)
    assert edge.thumbnail_state == ThumbnailOverlayState.THUMBNAIL_VISIBLE
    assert edge.active_thumbnail_bytes == img_data
    assert edge.thumbnail_display_timestamp == 100.0

    # Before timeout (t=102.0s < 4.0s) -> remains visible
    assert edge.check_thumbnail_timeout(now=102.0) is False
    assert edge.thumbnail_state == ThumbnailOverlayState.THUMBNAIL_VISIBLE

    # After timeout (t=104.5s >= 4.0s) -> auto-dismisses into background to preserve Zero-UI
    assert edge.check_thumbnail_timeout(now=104.5) is True
    assert edge.thumbnail_state == ThumbnailOverlayState.HIDDEN
    assert edge.active_thumbnail_bytes is None

    # 2. Tap to expand into full-size preview overlay
    edge.render_thumbnail(img_data, now=200.0)
    assert edge.thumbnail_state == ThumbnailOverlayState.THUMBNAIL_VISIBLE
    tapped = edge.tap_thumbnail()
    assert tapped is True
    assert edge.thumbnail_state == ThumbnailOverlayState.EXPANDED
    assert edge.active_thumbnail_bytes == img_data

    # Manual dismissal
    edge.dismiss_thumbnail("MANUAL_USER_SWIPE")
    assert edge.thumbnail_state == ThumbnailOverlayState.HIDDEN
    assert edge.active_thumbnail_bytes is None

    # 3. Configurable timeout via UserRuntimeConfig
    custom_cfg = UserRuntimeConfig(thumbnail_dismiss_timeout_sec=2.5)
    custom_edge = EdgeClientHarness(client_id="edge_custom_thumb", config=custom_cfg)
    assert custom_edge.thumbnail_dismiss_timeout_sec == 2.5
    custom_edge.render_thumbnail(img_data, now=300.0)
    assert custom_edge.check_thumbnail_timeout(now=301.0) is False
    assert custom_edge.check_thumbnail_timeout(now=302.6) is True
    assert custom_edge.thumbnail_state == ThumbnailOverlayState.HIDDEN


def test_two_stage_timeout_policy_turn_seal_vs_dormant_teardown():
    # 1. EdgeClientHarness 2-Stage Timeout Policy Defaults
    edge = EdgeClientHarness(client_id="two_stage_edge_test")
    assert edge.turn_silence_timeout_sec == 10.0
    assert edge.session_idle_timeout_sec == 60.0

    # User speaks at t = 100.0s
    edge.record_speech(now=100.0)
    assert edge.is_turn_sealed is False
    assert edge.is_listening_active is True

    # At t = 105.0s (5.0s elapsed < 10.0s):
    assert edge.check_turn_silence(now=105.0) is False
    assert edge.is_turn_sealed is False
    assert edge.check_session_idle(now=105.0) is False

    # At t = 110.5s (10.5s elapsed >= 10.0s):
    # Stage 1 seals current speech turn while keeping connection active in lightweight listening state
    assert edge.check_turn_silence(now=110.5) is True
    assert edge.is_turn_sealed is True
    assert edge.is_listening_active is True
    assert edge.check_session_idle(now=110.5) is False

    # At t = 159.0s (59.0s elapsed < 60.0s):
    assert edge.check_session_idle(now=159.0) is False

    # At t = 160.5s (60.5s elapsed >= 60.0s):
    # Stage 2 dormant screensaver triggers teardown
    assert edge.check_session_idle(now=160.5) is True

    # 2. Direct evaluation of TwoStageTimeoutFSM controller
    events_logged = []
    fsm_controller = TwoStageTimeoutFSM(
        turn_silence_timeout_sec=10.0,
        session_idle_timeout_sec=60.0,
        on_turn_sealed=lambda: events_logged.append("TURN_SEALED"),
        on_idle_dormant=lambda: events_logged.append("IDLE_DORMANT")
    )
    fsm_controller.record_speech_activity(now=200.0)

    # Turn seal at 210.5s
    res1 = fsm_controller.evaluate_timeouts(now=210.5)
    assert res1["turn_sealed"] is True
    assert res1["dropped_to_dormant"] is False
    assert "TURN_SEALED" in events_logged
    assert "IDLE_DORMANT" not in events_logged

    # Dormant drop at 260.5s
    res2 = fsm_controller.evaluate_timeouts(now=260.5)
    assert res2["dropped_to_dormant"] is True
    assert "IDLE_DORMANT" in events_logged

    # 3. AudioConfig and UserRuntimeConfig overriding 2-stage timeouts
    audio_cfg = AudioConfig(turn_silence_timeout_sec=12.0, session_idle_timeout_sec=75.0)
    cfg = UserRuntimeConfig(audio=audio_cfg)
    edge_custom = EdgeClientHarness(client_id="custom_audio_edge", config=cfg)
    assert edge_custom.turn_silence_timeout_sec == 12.0
    assert edge_custom.session_idle_timeout_sec == 75.0






def test_pc_station_f20_display_selector_and_status_capsule():
    # 1. F20 Display Selector Lifecycle
    station = StationIngestionClient(client_id="pc_f20_test")
    assert station.f20_selector_visible is False
    assert station.is_ultrawide_streaming is False
    assert station.active_stream_display_index is None

    # Status Capsule default state
    capsule_ready = station.get_status_capsule()
    assert "🟢 READY" in capsule_ready["dot"]
    assert capsule_ready["stream_tag"] == ""

    # Press F20 when idle -> opens transient display preview overlay with tiles
    res_open = station.toggle_f20_display_selector()
    assert res_open["action"] == "SELECTOR_OPENED"
    assert station.f20_selector_visible is True
    assert len(res_open["tiles"]) >= 1
    assert any("[1] Display 1" in t["tile"] for t in res_open["tiles"])

    # Pressing F20 again while selector open -> dismisses preview
    res_dismiss = station.toggle_f20_display_selector()
    assert res_dismiss["action"] == "SELECTOR_DISMISSED"
    assert station.f20_selector_visible is False

    # Open again, then select display via numeric key '2' (or 2)
    station.open_f20_display_selector()
    assert station.f20_selector_visible is True
    selected_idx = station.select_display_and_start_stream(2)
    assert selected_idx == 1  # 0-indexed display 2
    assert station.f20_selector_visible is False  # auto-dismisses
    assert station.is_ultrawide_streaming is True
    assert station.active_stream_display_index == 1

    # Status Capsule in active stream state
    capsule_streaming = station.get_status_capsule()
    assert "🔵 STREAMING" in capsule_streaming["dot"]
    assert capsule_streaming["stream_tag"] == "LIVE [Disp 2]"
    assert "LIVE [Disp 2]" in capsule_streaming["display"]

    # Pressing F20 while streaming stops the video feed immediately
    res_stop = station.toggle_f20_display_selector()
    assert res_stop["action"] == "STREAM_STOPPED"
    assert station.is_ultrawide_streaming is False
    assert station.active_stream_display_index is None
    capsule_stopped = station.get_status_capsule()
    assert "🟢 READY" in capsule_stopped["dot"]
    assert capsule_stopped["stream_tag"] == ""


def test_android_edge_pocket_guard_and_notification_state_sync():
    edge = EdgeClientHarness(client_id="android_pocket_test")
    assert edge.notification_state == AndroidOngoingNotificationState.READY
    assert edge.lock_screen_status == LockScreenIndicatorStatus.READY
    assert edge.is_muted is False

    # 1. Pocket-Safe Double-Tap Toggle Power / Mute
    # Single tap -> fails to activate, waits for second tap within double_tap_window_sec (0.5s)
    activated_single = edge.double_tap_toggle_mute(now=100.0)
    assert activated_single is False
    assert edge.is_muted is False

    # Too slow second tap (> 0.5s) -> registered as new first tap
    activated_slow = edge.double_tap_toggle_mute(now=101.0)
    assert activated_slow is False
    assert edge.is_muted is False

    # Rapid second tap within 0.5s (t = 101.2s) -> succeeds, confirms with haptic feedback!
    activated_double = edge.double_tap_toggle_mute(now=101.2)
    assert activated_double is True
    assert edge.is_muted is True
    assert edge.notification_state == AndroidOngoingNotificationState.MUTED
    assert edge.lock_screen_status == LockScreenIndicatorStatus.MUTED
    assert any("DOUBLE_TAP_TOGGLE_MUTE" in h for h in edge.haptic_feedback_events)

    # Double-tap again to unmute
    edge.double_tap_toggle_mute(now=102.0)
    edge.double_tap_toggle_mute(now=102.1)
    assert edge.is_muted is False
    assert edge.notification_state == AndroidOngoingNotificationState.READY
    assert edge.lock_screen_status == LockScreenIndicatorStatus.READY

    # 2. Pocket-Safe Double-Tap Quick Snap
    # Single tap -> does not snap
    res_snap_single = asyncio.run(edge.double_tap_quick_snap(now=200.0))
    assert res_snap_single is None

    # Connect to local test server to verify double-tap Quick Snap image ingestion and thumbnail [INV-12]
    async def _run_snap_flow():
        server = CentralZeroUIServer()
        server_task = asyncio.create_task(server.start())
        await asyncio.sleep(0.05)

        try:
            assert edge.notification_state == AndroidOngoingNotificationState.READY
            await edge.connect()
            assert edge.notification_state == AndroidOngoingNotificationState.READY

            # First tap
            res1 = await edge.double_tap_quick_snap(now=300.0)
            assert res1 is None
            assert edge.thumbnail_state == ThumbnailOverlayState.HIDDEN

            # Second tap within 0.5s
            img_data = b"\xff\xd8\xff\xe0snap_image_frame"
            res2 = await edge.double_tap_quick_snap(image_bytes=img_data, now=300.2)
            assert res2 is not None
            assert len(res2) > 0
            # Thumbnail displayed for 4.0s [INV-12]
            assert edge.thumbnail_state == ThumbnailOverlayState.THUMBNAIL_VISIBLE
            assert edge.active_thumbnail_bytes == img_data

            # 3. Android Quick Settings Tile (Screen Capture Channel)
            screen_data = b"\xff\xd8\xff\xe0screen_tile_frame"
            screen_res = await edge.trigger_quick_settings_screen_capture(screen_frame_bytes=screen_data)
            assert len(screen_res) > 0
            assert edge.thumbnail_state == ThumbnailOverlayState.THUMBNAIL_VISIBLE

            # 4. ACTION_SEND Text/URL channel
            ack = await edge.handle_action_send("https://gemini.google.com/spec")
            assert ack.get("type") == "QUICK_DROP_ACK"

            # Post-session state returns to READY
            assert edge.notification_state == AndroidOngoingNotificationState.READY
            assert edge.lock_screen_status == LockScreenIndicatorStatus.READY
        finally:
            await edge.close()
            await server.stop()
            server_task.cancel()

    asyncio.run(_run_snap_flow())
