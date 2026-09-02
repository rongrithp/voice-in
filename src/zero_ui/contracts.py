"""
Zero-UI Hardware & Desktop System (Project Gemini) Data Contracts & Protocol Definitions.
Strict typing and serialization for Sensor-to-Actuator pipelines.
"""

from __future__ import annotations
import enum
import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field, asdict
import json


# ---------------------------------------------------------------------------
# Identity & Entitlement Contract [INV-AUTH]
# ---------------------------------------------------------------------------

class EntitlementTier(str, enum.Enum):
    """User subscription/license tier."""
    TRIAL = "TRIAL"        # 30-day full access from first login
    LIFETIME = "LIFETIME"  # Unlocked via purchase
    EXPIRED = "EXPIRED"    # Trial ended; access halted until purchase


_TRIAL_DURATION_SEC: int = 30 * 24 * 3600  # 30 days


@dataclass
class UserIdentity:
    """
    Google-authenticated user identity and license status.
    Passed in `ClientHello.identity` after OAuth sign-in on the client.
    """
    email: str
    tier: EntitlementTier = EntitlementTier.TRIAL
    trial_start_epoch_sec: int = field(default_factory=lambda: int(time.time()))
    api_key: Optional[str] = None  # BYOK Gemini API key (user-supplied)

    def is_active(self) -> bool:
        """Returns True if the user has active access (non-expired)."""
        if self.tier == EntitlementTier.LIFETIME:
            return True
        if self.tier == EntitlementTier.EXPIRED:
            return False
        # TRIAL: check wall-clock elapsed vs 30-day window
        elapsed = int(time.time()) - self.trial_start_epoch_sec
        return elapsed < _TRIAL_DURATION_SEC

    def days_remaining(self) -> int:
        """Days left in trial. Returns 0 for LIFETIME (unlimited) and -1 for EXPIRED."""
        if self.tier == EntitlementTier.LIFETIME:
            return 0  # Unlimited — not applicable
        if self.tier == EntitlementTier.EXPIRED:
            return -1
        elapsed = int(time.time()) - self.trial_start_epoch_sec
        remaining_sec = _TRIAL_DURATION_SEC - elapsed
        return max(0, remaining_sec // (24 * 3600))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "email": self.email,
            "tier": self.tier.value,
            "trial_start_epoch_sec": self.trial_start_epoch_sec,
            "api_key": self.api_key,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "UserIdentity":
        return cls(
            email=data["email"],
            tier=EntitlementTier(data.get("tier", EntitlementTier.TRIAL.value)),
            trial_start_epoch_sec=int(data.get("trial_start_epoch_sec", int(time.time()))),
            api_key=data.get("api_key"),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_json(cls, json_str: str) -> "UserIdentity":
        return cls.from_dict(json.loads(json_str))


class ClientMode(str, enum.Enum):
    EDGE_FIELD = "EDGE_FIELD"
    PC_STATION = "PC_STATION"


class TriggerSource(str, enum.Enum):
    BT_MEDIA_BUTTON = "BT_MEDIA_BUTTON"
    FOOT_SWITCH = "FOOT_SWITCH"
    VOICE_KEYWORD = "VOICE_KEYWORD"
    PC_HOTKEY = "PC_HOTKEY"
    FLOATING_SHUTTER_BUTTON = "FLOATING_SHUTTER_BUTTON"


class LockScreenIndicatorStatus(str, enum.Enum):
    READY = "READY"
    LISTENING = "LISTENING"
    PROCESSING = "PROCESSING"
    STREAMING = "STREAMING"
    CONNECTING = "CONNECTING"
    THINKING = "THINKING"
    MUTED = "MUTED"


class AndroidOngoingNotificationState(str, enum.Enum):
    """Machine states for Android persistent Foreground Service status bar indicator."""
    READY = "READY"
    CONNECTING = "CONNECTING"
    THINKING = "THINKING"
    MUTED = "MUTED"


class ThumbnailOverlayState(str, enum.Enum):
    """Lifecycle states for Android transient floating image thumbnail overlay [INV-12]."""
    HIDDEN = "HIDDEN"
    THUMBNAIL_VISIBLE = "THUMBNAIL_VISIBLE"
    EXPANDED = "EXPANDED"


class SafetyFlag(str, enum.Enum):
    CLEAR = "CLEAR"
    INTERLOCK_WARNING = "INTERLOCK_WARNING"
    STOP_PROBE_REQUIRED = "STOP_PROBE_REQUIRED"


class StepStatus(str, enum.Enum):
    IDLE = "IDLE"
    IN_PROGRESS = "IN_PROGRESS"
    AWAITING_PHYSICAL_CONFIRMATION = "AWAITING_PHYSICAL_CONFIRMATION"
    COMPLETED = "COMPLETED"
    SAFETY_HALTED = "SAFETY_HALTED"


@dataclass
class ClientCapabilities:
    camera_pdaf: bool = True
    max_image_resolution: List[int] = field(default_factory=lambda: [3840, 2160])
    audio_codec: str = "audio/pcm;rate=16000;channels=1"
    voice_output_supported: bool = True


@dataclass
class ClientHello:
    client_id: str
    client_mode: ClientMode
    capabilities: ClientCapabilities = field(default_factory=ClientCapabilities)
    auth_token: Optional[str] = None
    identity: Optional[UserIdentity] = None  # [INV-AUTH] Google OAuth identity + entitlement tier
    type: str = "CLIENT_HELLO"

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["client_mode"] = self.client_mode.value if isinstance(self.client_mode, ClientMode) else self.client_mode
        if self.identity is not None:
            data["identity"] = self.identity.to_dict()
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ClientHello":
        caps_data = data.get("capabilities", {})
        capabilities = ClientCapabilities(**caps_data) if isinstance(caps_data, dict) else caps_data
        mode = ClientMode(data.get("client_mode", ClientMode.EDGE_FIELD.value))
        identity_data = data.get("identity")
        identity = UserIdentity.from_dict(identity_data) if isinstance(identity_data, dict) else None
        return cls(
            client_id=data["client_id"],
            client_mode=mode,
            capabilities=capabilities,
            auth_token=data.get("auth_token"),
            identity=identity,
            type=data.get("type", "CLIENT_HELLO")
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_json(cls, json_str: str) -> "ClientHello":
        return cls.from_dict(json.loads(json_str))


@dataclass
class CaptureTriggerEvent:
    trigger_source: TriggerSource
    timestamp_ns: int
    action: str = "CAPTURE_SNAPSHOT_AND_LISTEN"
    context_hint: Optional[str] = None
    type: str = "CAPTURE_TRIGGER"

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["trigger_source"] = self.trigger_source.value if isinstance(self.trigger_source, TriggerSource) else self.trigger_source
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CaptureTriggerEvent":
        trigger = TriggerSource(data["trigger_source"])
        return cls(
            trigger_source=trigger,
            timestamp_ns=data["timestamp_ns"],
            action=data.get("action", "CAPTURE_SNAPSHOT_AND_LISTEN"),
            context_hint=data.get("context_hint"),
            type=data.get("type", "CAPTURE_TRIGGER")
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_json(cls, json_str: str) -> "CaptureTriggerEvent":
        return cls.from_dict(json.loads(json_str))


@dataclass
class ImagePayload:
    format: str = "image/jpeg"
    encoding: str = "base64"
    width: int = 3840
    height: int = 2160
    data: str = ""  # Base64 encoded JPEG or binary ref


@dataclass
class AudioPayload:
    format: str = "audio/pcm;rate=16000;channels=1;bits=16"
    encoding: str = "base64"
    data: str = ""  # Base64 encoded PCM buffer
    text_transcript: Optional[str] = None


@dataclass
class GpsCoordinates:
    latitude: float
    longitude: float
    altitude: Optional[float] = None
    accuracy_meters: Optional[float] = None


@dataclass
class MotionTelemetry:
    acceleration_x: Optional[float] = None
    acceleration_y: Optional[float] = None
    acceleration_z: Optional[float] = None
    user_activity: Optional[str] = None  # e.g., "STATIONARY", "WALKING", "RUNNING", "IN_VEHICLE"


@dataclass
class DeviceHealthTelemetry:
    battery_level: Optional[float] = None  # 0.0 to 1.0 (e.g. 0.85 = 85%)
    is_charging: Optional[bool] = None
    thermal_status: Optional[str] = None  # "NORMAL", "THROTTLED", "CRITICAL"


@dataclass
class TelemetryPayload:
    focus_locked: bool = True
    lux_level: Optional[float] = 400.0
    gps: Optional[GpsCoordinates] = None
    motion: Optional[MotionTelemetry] = None
    device_health: Optional[DeviceHealthTelemetry] = None
    permissions_granted: Dict[str, bool] = field(
        default_factory=lambda: {"location": False, "motion": False, "camera": True, "microphone": True}
    )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TelemetryPayload":
        gps_data = data.get("gps")
        gps = GpsCoordinates(**gps_data) if isinstance(gps_data, dict) else gps_data

        motion_data = data.get("motion")
        motion = MotionTelemetry(**motion_data) if isinstance(motion_data, dict) else motion_data

        health_data = data.get("device_health")
        device_health = DeviceHealthTelemetry(**health_data) if isinstance(health_data, dict) else health_data

        return cls(
            focus_locked=data.get("focus_locked", True),
            lux_level=data.get("lux_level", 400.0),
            gps=gps,
            motion=motion,
            device_health=device_health,
            permissions_granted=data.get("permissions_granted", {"location": False, "motion": False, "camera": True, "microphone": True})
        )


@dataclass
class AttachedDocumentPayload:
    file_name: str
    mime_type: str
    size_bytes: int
    content_b64_or_text: str
    description: Optional[str] = None
    priority_rank: int = 1  # [INV-06] Session File Primacy Guard (1 = Highest Primacy)
    type: str = "ATTACHED_DOCUMENT"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AttachedDocumentPayload":
        return cls(
            file_name=data["file_name"],
            mime_type=data["mime_type"],
            size_bytes=data["size_bytes"],
            content_b64_or_text=data["content_b64_or_text"],
            description=data.get("description"),
            priority_rank=data.get("priority_rank", 1),
            type=data.get("type", "ATTACHED_DOCUMENT")
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_json(cls, json_str: str) -> "AttachedDocumentPayload":
        return cls.from_dict(json.loads(json_str))


@dataclass
class AbortFrame:
    session_id: str
    sequence_id: int
    reason: str = "LOCAL_INTENT_MATCHED"  # Emitted to cancel cloud processing upon on-device intent match (<50ms)
    timestamp_ns: Optional[int] = None
    type: str = "ABORT_FRAME"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AbortFrame":
        return cls(
            session_id=data["session_id"],
            sequence_id=data["sequence_id"],
            reason=data.get("reason", "LOCAL_INTENT_MATCHED"),
            timestamp_ns=data.get("timestamp_ns"),
            type=data.get("type", "ABORT_FRAME")
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_json(cls, json_str: str) -> "AbortFrame":
        return cls.from_dict(json.loads(json_str))


@dataclass
class AudioConfig:
    """
    2-Stage Timeout Policy & Audio Ingestion Configuration.
    - Stage 1 (Turn-taking): wait turn_silence_timeout_sec before sealing current speech turn.
    - Stage 2 (Dormant screensaver): idle duration before dropping client FSM to STANDBY_DORMANT.
    """
    turn_silence_timeout_sec: float = 10.0   # Turn-taking: wait before sealing current speech turn
    session_idle_timeout_sec: float = 60.0   # Dormant screensaver: idle duration before dropping to STANDBY_DORMANT

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AudioConfig":
        turn_sec = data.get("turn_silence_timeout_sec")
        if turn_sec is None:
            turn_sec = data.get("turn_silence_sec", data.get("rms_silence_timeout_sec", 10.0))
        idle_sec = data.get("session_idle_timeout_sec")
        if idle_sec is None:
            idle_sec = data.get("session_idle_sec", 60.0)
        return cls(
            turn_silence_timeout_sec=float(turn_sec),
            session_idle_timeout_sec=float(idle_sec)
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_json(cls, json_str: str) -> "AudioConfig":
        return cls.from_dict(json.loads(json_str))


@dataclass
class PersonaConfig:
    """
    Configurable Persona Settings.
    Base acoustic invariants (zero tables, zero markdown, acoustic-friendly phrasing) remain strictly locked.
    """
    style: str = "EXPERT_THINKING_OUT_LOUD"
    custom_system_instruction: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PersonaConfig":
        return cls(
            style=data.get("style", "EXPERT_THINKING_OUT_LOUD"),
            custom_system_instruction=data.get("custom_system_instruction")
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_json(cls, json_str: str) -> "PersonaConfig":
        return cls.from_dict(json.loads(json_str))


@dataclass
class UserPersonalizationConfig:
    persona_name: str = "Default"
    tone_directive: str = "Concise and direct"
    preferred_voice: str = "Neural2-C"
    enable_live_subtitles: bool = True
    custom_system_instructions: Optional[str] = None
    language_code: str = "th-TH"
    response_verbosity: str = "CONCISE"  # "ULTRA_CONCISE" | "CONCISE" | "DETAILED"
    type: str = "PERSONALIZATION_CONFIG"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "UserPersonalizationConfig":
        return cls(
            persona_name=data.get("persona_name", "Default"),
            tone_directive=data.get("tone_directive", "Concise and direct"),
            preferred_voice=data.get("preferred_voice", "Neural2-C"),
            enable_live_subtitles=data.get("enable_live_subtitles", True),
            custom_system_instructions=data.get("custom_system_instructions"),
            language_code=data.get("language_code", "th-TH"),
            response_verbosity=data.get("response_verbosity", "CONCISE"),
            type=data.get("type", "PERSONALIZATION_CONFIG")
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_json(cls, json_str: str) -> "UserPersonalizationConfig":
        return cls.from_dict(json.loads(json_str))


@dataclass
class WakeWordConfig:
    """
    Dynamic wake-word configuration schema.
    Prohibits hardcoded wake-word strings in the audio loop.
    """
    primary_word: str = "gemini"
    aliases: List[str] = field(default_factory=lambda: ["hey gemini", "ok gemini"])
    sensitivity: float = 0.5
    model_path: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WakeWordConfig":
        return cls(
            primary_word=data.get("primary_word", "gemini"),
            aliases=list(data.get("aliases", ["hey gemini", "ok gemini"])),
            sensitivity=float(data.get("sensitivity", 0.5)),
            model_path=data.get("model_path")
        )

    def matches(self, text: str) -> bool:
        """Check if transcribed or detected text matches primary wake-word or aliases."""
        clean = text.lower().strip()
        all_words = [self.primary_word.lower()] + [a.lower() for a in self.aliases]
        return any(w in clean for w in all_words)


@dataclass
class EndStreamFrame:
    """
    End of stream / teardown frame sent when dynamic background noise floor triggers silence timeout (e.g. >2.0s).
    Flushes buffers and transitions client FSM back to STANDBY_DORMANT.
    """
    session_id: str
    reason: str = "RMS_SILENCE_TIMEOUT"
    timestamp_ns: int = field(default_factory=time.time_ns)
    type: str = "END_STREAM"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EndStreamFrame":
        return cls(
            session_id=data["session_id"],
            reason=data.get("reason", "RMS_SILENCE_TIMEOUT"),
            timestamp_ns=data.get("timestamp_ns", time.time_ns()),
            type=data.get("type", "END_STREAM")
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_json(cls, json_str: str) -> "EndStreamFrame":
        return cls.from_dict(json.loads(json_str))


@dataclass
class QuickDropPayload:
    """
    Transient Quick-Drop Box payload (Alt+Space on PC / ACTION_SEND on Android).
    Ingests text/URLs straight into active WebSocket session buffer without retaining UI.
    """
    content: str
    source: str = "PC_QUICK_DROP"  # "PC_QUICK_DROP" or "ANDROID_ACTION_SEND"
    timestamp_ns: int = field(default_factory=time.time_ns)
    type: str = "QUICK_DROP"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "QuickDropPayload":
        return cls(
            content=data.get("content", ""),
            source=data.get("source", "PC_QUICK_DROP"),
            timestamp_ns=data.get("timestamp_ns", time.time_ns()),
            type=data.get("type", "QUICK_DROP")
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_json(cls, json_str: str) -> "QuickDropPayload":
        return cls.from_dict(json.loads(json_str))


DEFAULT_PC_STATION_HOTKEYS: Dict[str, str] = {
    "f13": "talk_to_cursor",
    "f14": "read_selected_text",
    "f15": "read_below_text",
    "f16": "toggle_audio_playback",
    "f17": "capture_display_1",
    "f18": "capture_display_2",
    "f19": "capture_display_3",
    "f20": "f20_display_selector",   # Interactive display selector overlay (replaces stream_ultrawide_live)
    "alt+space": "quick_drop"
}


@dataclass
class UserRuntimeConfig:
    """
    Decoupled runtime configuration loaded from external config.json or environment variables.
    """
    credentials_json_path: Optional[str] = None
    model_name: str = "gemini-2.5-flash"
    provider_type: str = "gemini"  # "gemini" | "open_source" | "local" | "openai"
    openai_base_url: Optional[str] = None  # e.g., "http://localhost:8000/v1"
    openai_api_key: Optional[str] = None
    noise_gate_rms_threshold: float = 0.015
    client_playback_speed: float = 1.0  # 0.75x to 1.5x
    vad_silence_timeout_sec: float = 3.0
    rms_silence_timeout_sec: float = 5.0
    thumbnail_dismiss_timeout_sec: float = 4.0
    session_idle_timeout_sec: float = 90.0
    cost_per_million_input_tokens_thb: float = 2.50
    cost_per_million_output_tokens_thb: float = 10.00
    talk_to_cursor_hotkey: str = "F13"
    read_selection_hotkey: str = "Ctrl+Shift+R"
    audio: AudioConfig = field(default_factory=AudioConfig)
    persona: PersonaConfig = field(default_factory=PersonaConfig)
    turn_silence_timeout_sec: float = 10.0
    session_idle_timeout_sec: float = 60.0
    wake_word: WakeWordConfig = field(default_factory=WakeWordConfig)
    pc_station_hotkeys: Dict[str, str] = field(default_factory=lambda: dict(DEFAULT_PC_STATION_HOTKEYS))

    def __post_init__(self):
        if self.audio:
            if self.turn_silence_timeout_sec == 10.0 and self.audio.turn_silence_timeout_sec != 10.0:
                self.turn_silence_timeout_sec = self.audio.turn_silence_timeout_sec
            elif self.turn_silence_timeout_sec != 10.0 and self.audio.turn_silence_timeout_sec == 10.0:
                self.audio.turn_silence_timeout_sec = self.turn_silence_timeout_sec

            if self.session_idle_timeout_sec == 60.0 and self.audio.session_idle_timeout_sec != 60.0:
                self.session_idle_timeout_sec = self.audio.session_idle_timeout_sec
            elif self.session_idle_timeout_sec != 60.0 and self.audio.session_idle_timeout_sec == 60.0:
                self.audio.session_idle_timeout_sec = self.session_idle_timeout_sec

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["wake_word"] = self.wake_word.to_dict()
        data["audio"] = self.audio.to_dict()
        data["persona"] = self.persona.to_dict()
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "UserRuntimeConfig":
        raw_hotkeys = data.get("pc_station_hotkeys")
        merged_hotkeys = dict(DEFAULT_PC_STATION_HOTKEYS)
        if isinstance(raw_hotkeys, dict):
            merged_hotkeys.update(raw_hotkeys)

        wake_data = data.get("wake_word", {})
        if isinstance(wake_data, dict):
            wake_word_cfg = WakeWordConfig.from_dict(wake_data)
        elif isinstance(wake_data, WakeWordConfig):
            wake_word_cfg = wake_data
        else:
            wake_word_cfg = WakeWordConfig()

        audio_data = data.get("audio", {})
        if isinstance(audio_data, dict):
            if "turn_silence_timeout_sec" in data and "turn_silence_timeout_sec" not in audio_data:
                audio_data["turn_silence_timeout_sec"] = data["turn_silence_timeout_sec"]
            if "session_idle_timeout_sec" in data and "session_idle_timeout_sec" not in audio_data:
                audio_data["session_idle_timeout_sec"] = data["session_idle_timeout_sec"]
            audio_cfg = AudioConfig.from_dict(audio_data)
        elif isinstance(audio_data, AudioConfig):
            audio_cfg = audio_data
        else:
            audio_cfg = AudioConfig(
                turn_silence_timeout_sec=float(data.get("turn_silence_timeout_sec", 10.0)),
                session_idle_timeout_sec=float(data.get("session_idle_timeout_sec", 60.0))
            )

        persona_data = data.get("persona", {})
        if isinstance(persona_data, dict):
            if "persona_style" in data and "style" not in persona_data:
                persona_data["style"] = data["persona_style"]
            if "custom_system_instruction" in data and "custom_system_instruction" not in persona_data:
                persona_data["custom_system_instruction"] = data["custom_system_instruction"]
            persona_cfg = PersonaConfig.from_dict(persona_data)
        elif isinstance(persona_data, PersonaConfig):
            persona_cfg = persona_data
        else:
            persona_cfg = PersonaConfig(
                style=data.get("persona_style", "EXPERT_THINKING_OUT_LOUD"),
                custom_system_instruction=data.get("custom_system_instruction")
            )

        return cls(
            credentials_json_path=data.get("credentials_json_path"),
            model_name=data.get("model_name", "gemini-2.5-flash"),
            provider_type=data.get("provider_type", "gemini"),
            openai_base_url=data.get("openai_base_url"),
            openai_api_key=data.get("openai_api_key"),
            noise_gate_rms_threshold=float(data.get("noise_gate_rms_threshold", 0.015)),
            client_playback_speed=float(data.get("client_playback_speed", 1.0)),
            vad_silence_timeout_sec=float(data.get("vad_silence_timeout_sec", 3.0)),
            rms_silence_timeout_sec=float(data.get("rms_silence_timeout_sec", 5.0)),
            thumbnail_dismiss_timeout_sec=float(data.get("thumbnail_dismiss_timeout_sec", 4.0)),
            session_idle_timeout_sec=audio_cfg.session_idle_timeout_sec,
            cost_per_million_input_tokens_thb=float(data.get("cost_per_million_input_tokens_thb", 2.50)),
            cost_per_million_output_tokens_thb=float(data.get("cost_per_million_output_tokens_thb", 10.00)),
            talk_to_cursor_hotkey=data.get("talk_to_cursor_hotkey", "F13"),
            read_selection_hotkey=data.get("read_selection_hotkey", "Ctrl+Shift+R"),
            audio=audio_cfg,
            persona=persona_cfg,
            turn_silence_timeout_sec=audio_cfg.turn_silence_timeout_sec,
            wake_word=wake_word_cfg,
            pc_station_hotkeys=merged_hotkeys
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_json(cls, json_str: str) -> "UserRuntimeConfig":
        return cls.from_dict(json.loads(json_str))

    @classmethod
    def load_from_file_or_env(cls, config_path: Optional[str] = None) -> "UserRuntimeConfig":
        import os
        base_data: Dict[str, Any] = {}
        if config_path and os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                base_data = json.load(f)
        elif os.path.exists("config.json"):
            with open("config.json", "r", encoding="utf-8") as f:
                base_data = json.load(f)

        # Environment variables override file configs
        if "VOICE_IN_CREDENTIALS_PATH" in os.environ:
            base_data["credentials_json_path"] = os.environ["VOICE_IN_CREDENTIALS_PATH"]
        if "VOICE_IN_MODEL_NAME" in os.environ:
            base_data["model_name"] = os.environ["VOICE_IN_MODEL_NAME"]
        if "VOICE_IN_PROVIDER_TYPE" in os.environ:
            base_data["provider_type"] = os.environ["VOICE_IN_PROVIDER_TYPE"]
        if "VOICE_IN_OPENAI_BASE_URL" in os.environ:
            base_data["openai_base_url"] = os.environ["VOICE_IN_OPENAI_BASE_URL"]
        if "VOICE_IN_OPENAI_API_KEY" in os.environ:
            base_data["openai_api_key"] = os.environ["VOICE_IN_OPENAI_API_KEY"]
        if "VOICE_IN_NOISE_GATE_RMS" in os.environ:
            base_data["noise_gate_rms_threshold"] = float(os.environ["VOICE_IN_NOISE_GATE_RMS"])
        if "VOICE_IN_PLAYBACK_SPEED" in os.environ:
            base_data["client_playback_speed"] = float(os.environ["VOICE_IN_PLAYBACK_SPEED"])
        if "VOICE_IN_VAD_SILENCE_TIMEOUT" in os.environ:
            base_data["vad_silence_timeout_sec"] = float(os.environ["VOICE_IN_VAD_SILENCE_TIMEOUT"])
        if "VOICE_IN_RMS_SILENCE_TIMEOUT_SEC" in os.environ:
            base_data["rms_silence_timeout_sec"] = float(os.environ["VOICE_IN_RMS_SILENCE_TIMEOUT_SEC"])
        if "VOICE_IN_TURN_SILENCE_SEC" in os.environ:
            if "audio" not in base_data or not isinstance(base_data["audio"], dict):
                base_data["audio"] = {}
            base_data["audio"]["turn_silence_timeout_sec"] = float(os.environ["VOICE_IN_TURN_SILENCE_SEC"])
            base_data["turn_silence_timeout_sec"] = float(os.environ["VOICE_IN_TURN_SILENCE_SEC"])
        if "VOICE_IN_SESSION_IDLE_SEC" in os.environ:
            if "audio" not in base_data or not isinstance(base_data["audio"], dict):
                base_data["audio"] = {}
            base_data["audio"]["session_idle_timeout_sec"] = float(os.environ["VOICE_IN_SESSION_IDLE_SEC"])
            base_data["session_idle_timeout_sec"] = float(os.environ["VOICE_IN_SESSION_IDLE_SEC"])
        if "VOICE_IN_PERSONA_STYLE" in os.environ:
            if "persona" not in base_data or not isinstance(base_data["persona"], dict):
                base_data["persona"] = {}
            base_data["persona"]["style"] = os.environ["VOICE_IN_PERSONA_STYLE"]
            base_data["persona_style"] = os.environ["VOICE_IN_PERSONA_STYLE"]
        if "VOICE_IN_THUMBNAIL_DISMISS_TIMEOUT" in os.environ:
            base_data["thumbnail_dismiss_timeout_sec"] = float(os.environ["VOICE_IN_THUMBNAIL_DISMISS_TIMEOUT"])
        if "VOICE_IN_SESSION_IDLE_TIMEOUT" in os.environ:
            base_data["session_idle_timeout_sec"] = float(os.environ["VOICE_IN_SESSION_IDLE_TIMEOUT"])
        if "VOICE_IN_TALK_TO_CURSOR_HOTKEY" in os.environ:
            base_data["talk_to_cursor_hotkey"] = os.environ["VOICE_IN_TALK_TO_CURSOR_HOTKEY"]
        if "VOICE_IN_READ_SELECTION_HOTKEY" in os.environ:
            base_data["read_selection_hotkey"] = os.environ["VOICE_IN_READ_SELECTION_HOTKEY"]
        if "VOICE_IN_WAKE_WORD_PRIMARY" in os.environ:
            if "wake_word" not in base_data:
                base_data["wake_word"] = {}
            base_data["wake_word"]["primary_word"] = os.environ["VOICE_IN_WAKE_WORD_PRIMARY"]
        if "VOICE_IN_WAKE_WORD_ALIASES" in os.environ:
            if "wake_word" not in base_data:
                base_data["wake_word"] = {}
            base_data["wake_word"]["aliases"] = [a.strip() for a in os.environ["VOICE_IN_WAKE_WORD_ALIASES"].split(",") if a.strip()]

        return cls.from_dict(base_data)



@dataclass
class SensorPayload:
    session_id: str
    sequence_id: int
    image: ImagePayload
    audio_query: AudioPayload
    telemetry: TelemetryPayload = field(default_factory=TelemetryPayload)
    attached_documents: List[AttachedDocumentPayload] = field(default_factory=list)
    type: str = "SENSOR_PAYLOAD"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SensorPayload":
        img = ImagePayload(**data["image"]) if isinstance(data["image"], dict) else data["image"]
        aud = AudioPayload(**data["audio_query"]) if isinstance(data["audio_query"], dict) else data["audio_query"]
        telem_data = data.get("telemetry", {})
        if isinstance(telem_data, dict):
            telem = TelemetryPayload.from_dict(telem_data)
        elif isinstance(telem_data, TelemetryPayload):
            telem = telem_data
        else:
            telem = TelemetryPayload()
        docs_data = data.get("attached_documents", [])
        docs = [AttachedDocumentPayload.from_dict(d) if isinstance(d, dict) else d for d in docs_data]
        return cls(
            session_id=data["session_id"],
            sequence_id=data["sequence_id"],
            image=img,
            audio_query=aud,
            telemetry=telem,
            attached_documents=docs,
            type=data.get("type", "SENSOR_PAYLOAD")
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_json(cls, json_str: str) -> "SensorPayload":
        return cls.from_dict(json.loads(json_str))


@dataclass
class ServerAudioStreamChunk:
    session_id: str
    chunk_index: int
    is_final: bool
    audio_format: str = "audio/pcm;rate=24000;channels=1"
    data: str = ""  # Base64 encoded audio or PCM
    safety_flag: SafetyFlag = SafetyFlag.CLEAR
    text_transcript: Optional[str] = None
    subtitle_token: Optional[str] = None  # [INV-07] Synchronized text token for live subtitles
    type: str = "SERVER_AUDIO_CHUNK"

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["safety_flag"] = self.safety_flag.value if isinstance(self.safety_flag, SafetyFlag) else self.safety_flag
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ServerAudioStreamChunk":
        flag = SafetyFlag(data.get("safety_flag", SafetyFlag.CLEAR.value))
        return cls(
            session_id=data["session_id"],
            chunk_index=data["chunk_index"],
            is_final=data["is_final"],
            audio_format=data.get("audio_format", "audio/pcm;rate=24000;channels=1"),
            data=data.get("data", ""),
            safety_flag=flag,
            text_transcript=data.get("text_transcript"),
            subtitle_token=data.get("subtitle_token"),
            type=data.get("type", "SERVER_AUDIO_CHUNK")
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_json(cls, json_str: str) -> "ServerAudioStreamChunk":
        return cls.from_dict(json.loads(json_str))


@dataclass
class StateUpdateEvent:
    current_step_id: str
    step_description: str
    status: StepStatus
    verified_ground_truth_ref: Optional[str] = None
    safety_warning: Optional[str] = None
    type: str = "STATE_UPDATE"

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value if isinstance(self.status, StepStatus) else self.status
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StateUpdateEvent":
        st = StepStatus(data["status"])
        return cls(
            current_step_id=data["current_step_id"],
            step_description=data["step_description"],
            status=st,
            verified_ground_truth_ref=data.get("verified_ground_truth_ref"),
            safety_warning=data.get("safety_warning"),
            type=data.get("type", "STATE_UPDATE")
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_json(cls, json_str: str) -> "StateUpdateEvent":
        return cls.from_dict(json.loads(json_str))


# --- Ground Truth Data Model ---

@dataclass
class PinDefinition:
    pin_number: str
    signal: str
    voltage_class: str  # e.g., "220V_AC", "3-32V_DC", "GND", "12V_DC"
    color_code: Optional[str] = None
    target_component: Optional[str] = None
    target_pin: Optional[str] = None
    notes: Optional[str] = None


@dataclass
class ComponentDefinition:
    id: str
    name: str
    part_number: Optional[str] = None
    pins: Dict[str, PinDefinition] = field(default_factory=dict)


@dataclass
class SafetyRule:
    rule_id: str
    severity: str  # "CRITICAL_FATAL", "HIGH_EQUIPMENT_RISK", "CAUTION"
    condition: str
    required_verification: str  # e.g. "CONFIRM_BREAKER_OFF_VOICE_ACK"


@dataclass
class PinoutGraph:
    project_id: str
    schematic_version: str
    components: Dict[str, ComponentDefinition] = field(default_factory=dict)
    safety_rules: List[SafetyRule] = field(default_factory=list)

    def is_high_voltage(self, component_id: str, pin_number: str) -> bool:
        comp = self.components.get(component_id)
        if not comp:
            return False
        pin = comp.pins.get(str(pin_number))
        if not pin:
            return False
        return "220V" in pin.voltage_class or "AC" in pin.voltage_class or "HV" in pin.voltage_class

    def get_connection_ground_truth(self, component_id: str, pin_number: str) -> Optional[PinDefinition]:
        comp = self.components.get(component_id)
        if not comp:
            return None
        return comp.pins.get(str(pin_number))
