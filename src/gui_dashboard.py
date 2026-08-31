import logging
import math
import os
import threading
import time
from typing import Callable, Optional, Any
from PIL import Image

try:
    import customtkinter as ctk
    CTK_AVAILABLE = True
except ImportError:
    ctk = None
    CTK_AVAILABLE = False

import mss
import numpy as np
import sounddevice as sd
import config

logger = logging.getLogger("DashboardGUI")

# Appearance and Color Palette (Modern Dark Cyber Theme)
ACCENT_BLUE = "#1f6aa5"
ACCENT_HOVER = "#144870"
DARK_BG = "#1a1a1a"
CARD_BG = "#242424"
ACTIVE_GREEN = "#2ecc71"
ALERT_RED = "#e74c3c"
WARNING_YELLOW = "#f1c40f"
TEXT_MUTED = "#888888"


class DashboardGUI:
    """
    Lightweight CustomTkinter Dashboard GUI (Settings + Monitoring HUD)
    with thread-safe minimize-to-tray lifecycle management.
    """

    def __init__(
        self,
        app_ref: Optional[Any] = None,
        on_target_monitor_change: Optional[Callable[[int], None]] = None,
        on_rms_threshold_change: Optional[Callable[[float], None]] = None,
        on_barge_in_threshold_change: Optional[Callable[[float], None]] = None,
        on_vad_silence_change: Optional[Callable[[int], None]] = None,
        on_stt_engine_change: Optional[Callable[[str], None]] = None,
        on_tts_voice_change: Optional[Callable[[str], None]] = None,
        on_tts_speed_change: Optional[Callable[[float], None]] = None,
        on_live_toggle: Optional[Callable[[], None]] = None,
        on_emergency_unmute: Optional[Callable[[], None]] = None,
        on_reset_usage: Optional[Callable[[], None]] = None,
        on_exit: Optional[Callable[[], None]] = None,
        is_live_active_cb: Optional[Callable[[], bool]] = None,
        usage_tracker_ref: Optional[Any] = None,
    ):
        self.app_ref = app_ref
        self.on_target_monitor_change = on_target_monitor_change
        self.on_rms_threshold_change = on_rms_threshold_change
        self.on_barge_in_threshold_change = on_barge_in_threshold_change
        self.on_vad_silence_change = on_vad_silence_change
        self.on_stt_engine_change = on_stt_engine_change
        self.on_tts_voice_change = on_tts_voice_change
        self.on_tts_speed_change = on_tts_speed_change
        self.on_live_toggle = on_live_toggle
        self.on_emergency_unmute = on_emergency_unmute
        self.on_reset_usage = on_reset_usage
        self.on_exit = on_exit
        self.is_live_active_cb = is_live_active_cb
        self.usage_tracker = usage_tracker_ref

        self.root: Optional[Any] = None
        self._is_visible = False
        self._is_running = False
        self._lock = threading.Lock()
        self._gui_thread: Optional[threading.Thread] = None
        self._mic_monitor_thread: Optional[threading.Thread] = None

        # Current UI State Values
        self.selected_monitor = getattr(config, "DEFAULT_TARGET_MONITOR", getattr(config, "GEMINI_LIVE_TARGET_MONITOR", 1))
        self.current_rms_threshold = getattr(config, "RMS_THRESHOLD", 250.0)
        self.current_barge_in_rms = getattr(config, "GEMINI_LIVE_RMS_THRESHOLD", 2500.0)
        self.current_vad_silence_ms = getattr(config, "VAD_SILENCE_MS", 280)
        self.current_stt_engine = getattr(config, "STT_ENGINE", "gcp")
        self.current_tts_voice = getattr(config, "TTS_VOICE", "th-TH-Neural2-C")
        self.current_tts_speed = getattr(config, "TTS_SPEAKING_RATE", 1.0)
        self.current_audio_level = 0.0

        # UI Element References
        self._status_label = None
        self._live_btn = None
        self._preview_label = None
        self._preview_image_ref = None
        self._prev_status_lbl = None
        self._mon_dropdown = None
        self._monitor_cards: dict[int, dict] = {}
        self._vu_bar = None
        self._vu_val_label = None
        self._threshold_slider = None
        self._threshold_label = None
        self._barge_in_slider = None
        self._barge_in_label = None
        self._usage_total_label = None
        self._usage_stt_label = None
        self._usage_tts_label = None
        self._stt_dropdown = None
        self._voice_dropdown = None
        self._speed_dropdown = None
        self._vad_val_lbl = None
        self._vad_slider = None

        self._preview_job = None
        self._metrics_job = None

    @property
    def is_visible(self) -> bool:
        return self._is_visible

    def start_in_thread(self):
        """Starts the GUI in its own non-blocking thread."""
        if not CTK_AVAILABLE:
            logger.warning("[DashboardGUI] CustomTkinter is not available. Running headless.")
            return

        if self._gui_thread and self._gui_thread.is_alive():
            return

        self._gui_thread = threading.Thread(target=self._run_gui_loop, name="DashboardGUIThread", daemon=True)
        self._gui_thread.start()

    def _run_gui_loop(self):
        """Initializes CustomTkinter root and starts mainloop."""
        try:
            ctk.set_appearance_mode("Dark")
            ctk.set_default_color_theme("blue")

            self.root = ctk.CTk()
            self.root.title("Voice Operating Hub - Dashboard & Settings")
            self.root.geometry("960x700")
            self.root.minsize(860, 620)
            self.root.configure(fg_color=DARK_BG)

            # Override window close button (X) to hide/minimize to tray
            self.root.protocol("WM_DELETE_WINDOW", self.hide)

            try:
                from src.screen_capture import log_detected_monitors
                log_detected_monitors()
            except Exception:
                pass

            self._build_ui()

            # Start initially hidden / withdrawn (minimize-to-tray by default)
            self.root.withdraw()
            self._is_visible = False
            self._is_running = True

            # Start background UI update loops & mic level meter
            self._schedule_periodic_updates()
            self._start_mic_meter_monitor()

            logger.info("[DashboardGUI] CustomTkinter event loop active.")
            self.root.mainloop()
        except Exception as e:
            logger.error(f"[DashboardGUI Error] GUI loop encountered error: {e}", exc_info=True)
        finally:
            self._is_running = False
            self._is_visible = False

    def _build_ui(self):
        """Constructs modern Cyber Dark HUD layout."""
        if not self.root:
            return

        # Top Header Bar
        header_frame = ctk.CTkFrame(self.root, fg_color=CARD_BG, corner_radius=10)
        header_frame.pack(fill="x", padx=16, pady=(14, 8))

        title_lbl = ctk.CTkLabel(
            header_frame,
            text="🎙️ VOICE OPERATING HUB",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color="#ffffff"
        )
        title_lbl.pack(side="left", padx=16, pady=12)

        self._status_label = ctk.CTkLabel(
            header_frame,
            text="● STATUS: READY",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=ACTIVE_GREEN
        )
        self._status_label.pack(side="left", padx=16, pady=12)

        self._live_btn = ctk.CTkButton(
            header_frame,
            text="🤖 Gemini Live (F20) [OFF]",
            fg_color="#333333",
            hover_color="#444444",
            command=self._on_click_live_toggle,
            width=200
        )
        self._live_btn.pack(side="right", padx=16, pady=12)

        # Tabview for HUD & Settings
        tabview = ctk.CTkTabview(self.root, fg_color=DARK_BG)
        tabview.pack(fill="both", expand=True, padx=16, pady=6)

        tab_hud = tabview.add("🖥️ Monitoring HUD")
        tab_settings = tabview.add("⚙️ Engine & Settings")
        tab_hotkeys = tabview.add("⌨️ Hotkey Layout")

        # -------------------------------------------------------------
        # TAB 1: Monitoring HUD (Preview + Audio Meters + Usage Stats)
        # -------------------------------------------------------------
        hud_container = ctk.CTkFrame(tab_hud, fg_color="transparent")
        hud_container.pack(fill="both", expand=True, padx=6, pady=6)
        hud_container.grid_columnconfigure(0, weight=3)
        hud_container.grid_columnconfigure(1, weight=2)
        hud_container.grid_rowconfigure(0, weight=1)

        # Left Column: Screen Capture Target Multi-Monitor HUD
        preview_card = ctk.CTkFrame(hud_container, fg_color=CARD_BG, corner_radius=10)
        preview_card.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=0)

        prev_header = ctk.CTkFrame(preview_card, fg_color="transparent")
        prev_header.pack(fill="x", padx=12, pady=(10, 4))

        prev_title = ctk.CTkLabel(
            prev_header,
            text="📺 Screen Capture Target",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="#ffffff"
        )
        prev_title.pack(side="left")

        self._prev_status_lbl = ctk.CTkLabel(
            prev_header,
            text=f"Active: Monitor {self.selected_monitor}",
            text_color=ACTIVE_GREEN,
            font=ctk.CTkFont(size=12, weight="bold")
        )
        self._prev_status_lbl.pack(side="right")

        # Scrollable container for all physical monitor live thumbnail cards
        monitors_scroll = ctk.CTkScrollableFrame(preview_card, fg_color="transparent", orientation="vertical")
        monitors_scroll.pack(fill="both", expand=True, padx=8, pady=(4, 6))

        try:
            from src.screen_capture import get_physical_monitors
            detected_mons = get_physical_monitors()
        except Exception:
            detected_mons = []

        if not detected_mons:
            detected_mons = [
                {"index": 1, "name": "Monitor 1 (Primary)", "width": 1920, "height": 1080},
                {"index": 2, "name": "Monitor 2", "width": 1920, "height": 1080},
                {"index": 3, "name": "Monitor 3", "width": 1920, "height": 1080},
            ]

        self._monitor_cards = {}
        for mon in detected_mons:
            idx = mon["index"]
            is_active = (idx == self.selected_monitor)

            card_frame = ctk.CTkFrame(
                monitors_scroll,
                fg_color="#1a1a1a",
                corner_radius=8,
                border_width=2,
                border_color=ACTIVE_GREEN if is_active else "#333333"
            )
            card_frame.pack(fill="x", padx=2, pady=4)

            card_inner = ctk.CTkFrame(card_frame, fg_color="transparent")
            card_inner.pack(fill="x", padx=8, pady=8)

            # Left side: Live downscaled thumbnail (180x100)
            thumb_lbl = ctk.CTkLabel(
                card_inner,
                text="Capturing...",
                fg_color="#111111",
                corner_radius=6,
                width=180,
                height=100
            )
            thumb_lbl.pack(side="left", padx=(0, 10))

            # Right side: Monitor info & selection button
            info_frame = ctk.CTkFrame(card_inner, fg_color="transparent")
            info_frame.pack(side="left", fill="both", expand=True)

            mon_name_lbl = ctk.CTkLabel(
                info_frame,
                text=mon["name"],
                font=ctk.CTkFont(size=13, weight="bold"),
                text_color="#ffffff"
            )
            mon_name_lbl.pack(anchor="w", pady=(2, 1))

            res_lbl = ctk.CTkLabel(
                info_frame,
                text=f"Resolution: {mon['width']}x{mon['height']}",
                font=ctk.CTkFont(size=11),
                text_color=TEXT_MUTED
            )
            res_lbl.pack(anchor="w", pady=(0, 6))

            sel_btn = ctk.CTkButton(
                info_frame,
                text="✓ ACTIVE TARGET" if is_active else f"Select Monitor {idx}",
                fg_color=ACTIVE_GREEN if is_active else "#333333",
                hover_color=ACCENT_HOVER if is_active else "#444444",
                height=28,
                width=140,
                command=lambda i=idx: self._on_select_monitor(f"Monitor {i}")
            )
            sel_btn.pack(anchor="w")

            self._monitor_cards[idx] = {
                "frame": card_frame,
                "thumb_lbl": thumb_lbl,
                "select_btn": sel_btn,
                "name": mon["name"],
                "image_ref": None
            }

        # Keep legacy reference for backward compatibility
        if 1 in self._monitor_cards:
            self._preview_label = self._monitor_cards[1]["thumb_lbl"]

        # Preview action footer
        prev_actions = ctk.CTkFrame(preview_card, fg_color="transparent")
        prev_actions.pack(fill="x", padx=12, pady=(0, 8))

        snap_btn = ctk.CTkButton(
            prev_actions,
            text="📸 Capture Active Monitor to Clipboard",
            command=self._on_capture_screen_clipboard,
            width=240
        )
        snap_btn.pack(side="left")

        # Right Column: Audio Meters & Usage Stats
        right_col = ctk.CTkFrame(hud_container, fg_color="transparent")
        right_col.grid(row=0, column=1, sticky="nsew", padx=(8, 0), pady=0)
        right_col.grid_rowconfigure(0, weight=1)
        right_col.grid_rowconfigure(1, weight=1)
        right_col.grid_columnconfigure(0, weight=1)

        # Audio VU Meter Card
        audio_card = ctk.CTkFrame(right_col, fg_color=CARD_BG, corner_radius=10)
        audio_card.grid(row=0, column=0, sticky="nsew", pady=(0, 8))

        audio_title = ctk.CTkLabel(
            audio_card,
            text="🎙️ Audio Input & VU Meter",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="#ffffff"
        )
        audio_title.pack(anchor="w", padx=14, pady=(10, 4))

        # Real-time Progress / VU Bar
        self._vu_bar = ctk.CTkProgressBar(audio_card, orientation="horizontal", height=16)
        self._vu_bar.pack(fill="x", padx=14, pady=6)
        self._vu_bar.set(0.0)

        self._vu_val_label = ctk.CTkLabel(
            audio_card,
            text="Level: 0 RMS (Silence)",
            font=ctk.CTkFont(size=12),
            text_color=TEXT_MUTED
        )
        self._vu_val_label.pack(anchor="w", padx=14, pady=(0, 8))

        # RMS Threshold / Sensitivity Slider (Standard STT)
        sens_label_frame = ctk.CTkFrame(audio_card, fg_color="transparent")
        sens_label_frame.pack(fill="x", padx=14, pady=(4, 0))

        sens_title = ctk.CTkLabel(sens_label_frame, text="Mic Sensitivity Threshold (STT RMS):", font=ctk.CTkFont(size=12))
        sens_title.pack(side="left")

        self._threshold_label = ctk.CTkLabel(
            sens_label_frame,
            text=f"{int(self.current_rms_threshold)}",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=ACTIVE_GREEN
        )
        self._threshold_label.pack(side="right")

        self._threshold_slider = ctk.CTkSlider(
            audio_card,
            from_=50,
            to=1000,
            number_of_steps=95,
            command=self._on_slider_threshold_change
        )
        self._threshold_slider.set(self.current_rms_threshold)
        self._threshold_slider.pack(fill="x", padx=14, pady=(2, 6))

        # Barge-in RMS Noise Gate Slider (Gemini Live Co-pilot)
        barge_label_frame = ctk.CTkFrame(audio_card, fg_color="transparent")
        barge_label_frame.pack(fill="x", padx=14, pady=(4, 0))

        barge_title = ctk.CTkLabel(barge_label_frame, text="Barge-in RMS Threshold (Live):", font=ctk.CTkFont(size=12))
        barge_title.pack(side="left")

        self._barge_in_label = ctk.CTkLabel(
            barge_label_frame,
            text=f"{int(self.current_barge_in_rms)}",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=ACTIVE_GREEN
        )
        self._barge_in_label.pack(side="right")

        self._barge_in_slider = ctk.CTkSlider(
            audio_card,
            from_=500,
            to=8000,
            number_of_steps=150,
            command=self._on_slider_barge_in_threshold_change
        )
        self._barge_in_slider.set(self.current_barge_in_rms)
        self._barge_in_slider.pack(fill="x", padx=14, pady=(2, 8))

        # Usage & Cost Card
        usage_card = ctk.CTkFrame(right_col, fg_color=CARD_BG, corner_radius=10)
        usage_card.grid(row=1, column=0, sticky="nsew", pady=(8, 0))

        usage_title = ctk.CTkLabel(
            usage_card,
            text="📊 Monthly Usage & Cost",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="#ffffff"
        )
        usage_title.pack(anchor="w", padx=14, pady=(10, 4))

        self._usage_total_label = ctk.CTkLabel(
            usage_card,
            text="Total Cost: ฿0.00 THB",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color=ACTIVE_GREEN
        )
        self._usage_total_label.pack(anchor="w", padx=14, pady=2)

        self._usage_stt_label = ctk.CTkLabel(
            usage_card,
            text="• STT Speech: 0.0 min (฿0.00)",
            font=ctk.CTkFont(size=12),
            text_color="#cccccc"
        )
        self._usage_stt_label.pack(anchor="w", padx=14, pady=1)

        self._usage_tts_label = ctk.CTkLabel(
            usage_card,
            text="• TTS Output: 0 chars (฿0.00)",
            font=ctk.CTkFont(size=12),
            text_color="#cccccc"
        )
        self._usage_tts_label.pack(anchor="w", padx=14, pady=1)

        usage_btns = ctk.CTkFrame(usage_card, fg_color="transparent")
        usage_btns.pack(fill="x", padx=14, pady=(8, 10))

        open_file_btn = ctk.CTkButton(
            usage_btns,
            text="📂 Open Stats",
            command=self._on_open_stats_file,
            width=110
        )
        open_file_btn.pack(side="left", padx=(0, 6))

        reset_btn = ctk.CTkButton(
            usage_btns,
            text="🔄 Reset Month",
            fg_color="#444444",
            hover_color="#555555",
            command=self._on_reset_usage_clicked,
            width=110
        )
        reset_btn.pack(side="left")

        # -------------------------------------------------------------
        # TAB 2: Engine & Speech Settings
        # -------------------------------------------------------------
        settings_container = ctk.CTkScrollableFrame(tab_settings, fg_color="transparent")
        settings_container.pack(fill="both", expand=True, padx=6, pady=6)

        # STT Engine Selection
        stt_box = ctk.CTkFrame(settings_container, fg_color=CARD_BG, corner_radius=10)
        stt_box.pack(fill="x", pady=6, padx=8)

        stt_lbl = ctk.CTkLabel(stt_box, text="Primary Speech-to-Text Engine:", font=ctk.CTkFont(size=13, weight="bold"))
        stt_lbl.pack(anchor="w", padx=14, pady=(10, 4))

        self._stt_dropdown = ctk.CTkOptionMenu(
            stt_box,
            values=[
                "Google Cloud Streaming (gRPC)",
                "Gemini 2.5 Flash API",
                "Local faster-whisper (Offline)"
            ],
            command=self._on_select_stt_engine,
            width=320
        )
        engine_map_rev = {
            "gcp": "Google Cloud Streaming (gRPC)",
            "gemini-2.5-flash": "Gemini 2.5 Flash API",
            "local": "Local faster-whisper (Offline)"
        }
        self._stt_dropdown.set(engine_map_rev.get(self.current_stt_engine, "Google Cloud Streaming (gRPC)"))
        self._stt_dropdown.pack(anchor="w", padx=14, pady=(0, 10))

        # TTS Voice Selection
        tts_box = ctk.CTkFrame(settings_container, fg_color=CARD_BG, corner_radius=10)
        tts_box.pack(fill="x", pady=6, padx=8)

        tts_lbl = ctk.CTkLabel(tts_box, text="TTS Female Voice:", font=ctk.CTkFont(size=13, weight="bold"))
        tts_lbl.pack(anchor="w", padx=14, pady=(10, 4))

        self._voice_dropdown = ctk.CTkOptionMenu(
            tts_box,
            values=[
                "th-TH-Neural2-C (Studio Female)",
                "th-TH-Standard-A (Classic Female)"
            ],
            command=self._on_select_tts_voice,
            width=320
        )
        voice_map_rev = {
            "th-TH-Neural2-C": "th-TH-Neural2-C (Studio Female)",
            "th-TH-Standard-A": "th-TH-Standard-A (Classic Female)"
        }
        self._voice_dropdown.set(voice_map_rev.get(self.current_tts_voice, "th-TH-Neural2-C (Studio Female)"))
        self._voice_dropdown.pack(anchor="w", padx=14, pady=(0, 10))

        # TTS Speech Speed
        speed_box = ctk.CTkFrame(settings_container, fg_color=CARD_BG, corner_radius=10)
        speed_box.pack(fill="x", pady=6, padx=8)

        speed_lbl = ctk.CTkLabel(speed_box, text="TTS Speech Rate (Speed):", font=ctk.CTkFont(size=13, weight="bold"))
        speed_lbl.pack(anchor="w", padx=14, pady=(10, 4))

        self._speed_dropdown = ctk.CTkOptionMenu(
            speed_box,
            values=["0.75x", "1.00x (Normal)", "1.25x", "1.50x", "1.75x", "2.00x"],
            command=self._on_select_tts_speed,
            width=200
        )
        cur_speed_str = f"{self.current_tts_speed:.2f}x" if self.current_tts_speed != 1.0 else "1.00x (Normal)"
        self._speed_dropdown.set(cur_speed_str)
        self._speed_dropdown.pack(anchor="w", padx=14, pady=(0, 10))

        # VAD Silence Cutoff Window
        vad_box = ctk.CTkFrame(settings_container, fg_color=CARD_BG, corner_radius=10)
        vad_box.pack(fill="x", pady=6, padx=8)

        vad_header = ctk.CTkFrame(vad_box, fg_color="transparent")
        vad_header.pack(fill="x", padx=14, pady=(10, 4))

        vad_title = ctk.CTkLabel(vad_header, text="VAD Silence Cutoff Window:", font=ctk.CTkFont(size=13, weight="bold"))
        vad_title.pack(side="left")

        self._vad_val_lbl = ctk.CTkLabel(
            vad_header,
            text=f"{self.current_vad_silence_ms} ms",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=ACTIVE_GREEN
        )
        self._vad_val_lbl.pack(side="right")

        self._vad_slider = ctk.CTkSlider(
            vad_box,
            from_=150,
            to=1000,
            number_of_steps=17,
            command=self._on_slider_vad_change
        )
        self._vad_slider.set(self.current_vad_silence_ms)
        self._vad_slider.pack(fill="x", padx=14, pady=(0, 12))

        # -------------------------------------------------------------
        # TAB 3: Dedicated Hotkey Layout
        # -------------------------------------------------------------
        hotkey_frame = ctk.CTkFrame(tab_hotkeys, fg_color=CARD_BG, corner_radius=10)
        hotkey_frame.pack(fill="both", expand=True, padx=12, pady=12)

        hk_title = ctk.CTkLabel(
            hotkey_frame,
            text="⌨️ Dedicated Hotkey Layout (F13 - F20)",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color="#ffffff"
        )
        hk_title.pack(anchor="w", padx=16, pady=(14, 10))

        hotkeys_info = [
            ("F13", "Speak-to-Cursor (Streaming Speech-to-Text Dictation)"),
            ("F14", "TTS Read Selected Text (Read already selected content)"),
            ("F15", "TTS Read Downwards (Select from cursor to bottom & read)"),
            ("F16", "TTS Read Down (Cloud) - Select from cursor to bottom & read"),
            ("F17", "Capture Monitor 1 to Clipboard"),
            ("F18", "Capture Monitor 2 to Clipboard"),
            ("F19", "Capture Monitor 3 to Clipboard"),
            ("F20", "Gemini Multimodal Live Co-pilot (Vision + Voice Full-Duplex)"),
            ("F21", "Windows Local Native TTS (Offline 100% SAPI5 / OneCore)"),
        ]

        for key, desc in hotkeys_info:
            row = ctk.CTkFrame(hotkey_frame, fg_color="transparent")
            row.pack(fill="x", padx=16, pady=4)
            badge = ctk.CTkLabel(
                row,
                text=f" {key} ",
                fg_color=ACCENT_BLUE,
                corner_radius=6,
                font=ctk.CTkFont(size=12, weight="bold"),
                text_color="#ffffff",
                width=50
            )
            badge.pack(side="left", padx=(0, 12))
            desc_lbl = ctk.CTkLabel(row, text=desc, font=ctk.CTkFont(size=13))
            desc_lbl.pack(side="left")

        # -------------------------------------------------------------
        # Bottom Action Bar (Minimize, Unmute, Quit)
        # -------------------------------------------------------------
        footer_frame = ctk.CTkFrame(self.root, fg_color=CARD_BG, corner_radius=10)
        footer_frame.pack(fill="x", padx=16, pady=(6, 14))

        min_btn = ctk.CTkButton(
            footer_frame,
            text="🔽 Minimize to Tray",
            command=self.hide,
            fg_color=ACCENT_BLUE,
            hover_color=ACCENT_HOVER,
            width=160
        )
        min_btn.pack(side="left", padx=14, pady=10)

        unmute_btn = ctk.CTkButton(
            footer_frame,
            text="🔊 Emergency Unmute",
            command=self._on_emergency_unmute_clicked,
            fg_color="#444444",
            hover_color="#555555",
            width=160
        )
        unmute_btn.pack(side="left", padx=6, pady=10)

        quit_btn = ctk.CTkButton(
            footer_frame,
            text="❌ Quit Voice Hub",
            command=self._on_quit_clicked,
            fg_color="#771111",
            hover_color="#991111",
            width=140
        )
        quit_btn.pack(side="right", padx=14, pady=10)

    # -----------------------------------------------------------------
    # UI Interaction Callbacks & Dynamic Config Sync
    # -----------------------------------------------------------------

    def _get_monitor_options(self):
        """Returns list of detected display monitors."""
        try:
            with mss.MSS() as sct:
                num_mons = len(sct.monitors) - 1
                return [f"Monitor {i}" for i in range(1, max(2, num_mons + 1))]
        except Exception:
            return ["Monitor 1", "Monitor 2", "Monitor 3"]

    def _on_select_monitor(self, choice: str | int):
        try:
            if isinstance(choice, int):
                mon_idx = choice
            else:
                mon_idx = int(str(choice).replace("Monitor", "").strip())
            self.selected_monitor = mon_idx
            config.GEMINI_LIVE_TARGET_MONITOR = mon_idx
            logger.info(f"[DashboardGUI] Target monitor changed to {mon_idx}")
            self._update_monitor_cards_selection()
            if self._prev_status_lbl:
                self._prev_status_lbl.configure(text=f"Active: Monitor {mon_idx}")
            if self.on_target_monitor_change:
                self.on_target_monitor_change(mon_idx)
        except Exception as e:
            logger.error(f"[DashboardGUI Error] Failed to change monitor: {e}")

    def _update_monitor_cards_selection(self):
        """Refreshes visual border and button highlight for all monitor cards."""
        for mon_idx, card_info in list(self._monitor_cards.items()):
            is_active = (mon_idx == self.selected_monitor)
            if "frame" in card_info and card_info["frame"]:
                card_info["frame"].configure(border_color=ACTIVE_GREEN if is_active else "#333333")
            if "select_btn" in card_info and card_info["select_btn"]:
                card_info["select_btn"].configure(
                    text="✓ ACTIVE TARGET" if is_active else f"Select Monitor {mon_idx}",
                    fg_color=ACTIVE_GREEN if is_active else "#333333",
                    hover_color=ACCENT_HOVER if is_active else "#444444"
                )

    def _on_slider_threshold_change(self, value: float):
        self.current_rms_threshold = float(value)
        config.RMS_THRESHOLD = self.current_rms_threshold
        if self._threshold_label:
            self._threshold_label.configure(text=f"{int(value)}")
        if self.on_rms_threshold_change:
            self.on_rms_threshold_change(self.current_rms_threshold)

    def _on_slider_barge_in_threshold_change(self, value: float):
        self.current_barge_in_rms = float(value)
        config.GEMINI_LIVE_RMS_THRESHOLD = self.current_barge_in_rms
        if self._barge_in_label:
            self._barge_in_label.configure(text=f"{int(value)}")
        if self.on_barge_in_threshold_change:
            self.on_barge_in_threshold_change(self.current_barge_in_rms)

    def _on_slider_vad_change(self, value: float):
        self.current_vad_silence_ms = int(value)
        config.VAD_SILENCE_MS = self.current_vad_silence_ms
        if self._vad_val_lbl:
            self._vad_val_lbl.configure(text=f"{self.current_vad_silence_ms} ms")
        if self.on_vad_silence_change:
            self.on_vad_silence_change(self.current_vad_silence_ms)

    def _on_select_stt_engine(self, choice: str):
        engine_map = {
            "Google Cloud Streaming (gRPC)": "gcp",
            "Gemini 2.5 Flash API": "gemini-2.5-flash",
            "Local faster-whisper (Offline)": "local"
        }
        engine_key = engine_map.get(choice, "gcp")
        self.current_stt_engine = engine_key
        config.STT_ENGINE = engine_key
        logger.info(f"[DashboardGUI] STT engine changed to '{engine_key}'")
        if self.on_stt_engine_change:
            self.on_stt_engine_change(engine_key)

    def _on_select_tts_voice(self, choice: str):
        voice_key = "th-TH-Neural2-C" if "Neural2-C" in choice else "th-TH-Standard-A"
        self.current_tts_voice = voice_key
        config.TTS_VOICE = voice_key
        config.GCP_TTS_VOICE = voice_key
        logger.info(f"[DashboardGUI] TTS voice changed to '{voice_key}'")
        if self.on_tts_voice_change:
            self.on_tts_voice_change(voice_key)

    def _on_select_tts_speed(self, choice: str):
        try:
            speed_val = float(choice.split("x")[0])
            self.current_tts_speed = speed_val
            config.TTS_SPEAKING_RATE = speed_val
            logger.info(f"[DashboardGUI] TTS speed changed to {speed_val}x")
            if self.on_tts_speed_change:
                self.on_tts_speed_change(speed_val)
        except Exception as e:
            logger.error(f"[DashboardGUI Error] Failed to parse speed: {e}")

    def _on_click_live_toggle(self):
        logger.info("[DashboardGUI] Gemini Live Co-pilot toggle clicked.")
        if self.on_live_toggle:
            self.on_live_toggle()

    def _on_capture_screen_clipboard(self):
        try:
            from src.screen_capture import capture_monitor_to_clipboard
            capture_monitor_to_clipboard(self.selected_monitor)
        except Exception as e:
            logger.error(f"[DashboardGUI Error] Clipboard capture failed: {e}")

    def _on_emergency_unmute_clicked(self):
        logger.info("[DashboardGUI] Emergency unmute clicked.")
        if self.on_emergency_unmute:
            self.on_emergency_unmute()

    def _on_open_stats_file(self):
        try:
            if self.usage_tracker and hasattr(self.usage_tracker, "storage_path"):
                p = self.usage_tracker.storage_path
                if p.exists():
                    os.startfile(str(p))
                else:
                    os.startfile(str(p.parent))
            else:
                data_dir = os.path.abspath("data")
                os.makedirs(data_dir, exist_ok=True)
                os.startfile(data_dir)
        except Exception as e:
            logger.error(f"[DashboardGUI Error] Failed to open stats file: {e}")

    def _on_reset_usage_clicked(self):
        logger.info("[DashboardGUI] Reset usage statistics clicked.")
        if self.on_reset_usage:
            self.on_reset_usage()
        elif self.usage_tracker:
            self.usage_tracker.reset_stats()
        self._update_usage_metrics()

    def _on_quit_clicked(self):
        logger.info("[DashboardGUI] User initiated full application quit.")
        if self.on_exit:
            self.on_exit()
        self.destroy()

    # -----------------------------------------------------------------
    # Periodic Updaters & Audio VU Monitor
    # -----------------------------------------------------------------

    def _schedule_periodic_updates(self):
        if not self.root or not self._is_running:
            return
        self._update_preview()
        self._update_usage_metrics()

    def _update_preview(self):
        """Captures lightweight downscaled preview thumbnails for all physical monitors when visible."""
        if not self.root or not self._is_running:
            return

        if self._is_visible and self._monitor_cards:
            try:
                from src.screen_capture import grab_monitor_thumbnail
                for mon_idx, card_info in list(self._monitor_cards.items()):
                    img = grab_monitor_thumbnail(mon_idx, max_width=180, max_height=100)
                    if img is not None and ctk:
                        ctk_img = ctk.CTkImage(light_image=img, dark_image=img, size=(img.width, img.height))
                        if "thumb_lbl" in card_info and card_info["thumb_lbl"]:
                            card_info["thumb_lbl"].configure(image=ctk_img, text="")
                        card_info["image_ref"] = ctk_img
                
                self._update_monitor_cards_selection()
            except Exception as e:
                logger.debug(f"[DashboardGUI Preview Notice] {e}")

        # Schedule next preview tick (1.0s interval when visible to minimize CPU usage)
        interval_ms = 1000 if self._is_visible else 3000
        self._preview_job = self.root.after(interval_ms, self._update_preview)

    def _update_usage_metrics(self):
        """Refreshes usage and cost cards from UsageTracker."""
        if not self.root or not self._is_running:
            return

        try:
            if self.usage_tracker and hasattr(self.usage_tracker, "get_current_month_summary"):
                summary = self.usage_tracker.get_current_month_summary()
                month = summary.get("month", "Current")
                total_thb = summary.get("total_cost_thb", 0.0)
                stt_min = summary.get("stt_min", 0.0)
                stt_thb = summary.get("stt_cost_thb", 0.0)
                tts_chars = summary.get("tts_chars", 0)
                tts_thb = summary.get("tts_cost_thb", 0.0)

                if self._usage_total_label:
                    self._usage_total_label.configure(text=f"Total Cost ({month}): ฿{total_thb:.2f} THB")
                if self._usage_stt_label:
                    self._usage_stt_label.configure(text=f"• STT Speech: {stt_min:.1f} min (฿{stt_thb:.2f})")
                if self._usage_tts_label:
                    self._usage_tts_label.configure(text=f"• TTS Output: {tts_chars:,} chars (฿{tts_thb:.2f})")

            # Update Live Button State
            if self.is_live_active_cb and self._live_btn:
                is_live = bool(self.is_live_active_cb())
                btn_txt = f"🤖 Gemini Live (F20) [{'🟢 ON' if is_live else 'OFF'}]"
                btn_col = "#2a7a3a" if is_live else "#333333"
                self._live_btn.configure(text=btn_txt, fg_color=btn_col)
        except Exception as e:
            logger.debug(f"[DashboardGUI Metrics Notice] {e}")

        # Schedule next metrics refresh (every 2s)
        self._metrics_job = self.root.after(2000, self._update_usage_metrics)

    def _start_mic_meter_monitor(self):
        """Starts background thread measuring ambient mic levels when dashboard is open."""
        def _mic_loop():
            from src.audio import calculate_rms
            while self._is_running:
                if self._is_visible:
                    # Check if app is not already actively recording (to avoid device contention)
                    is_app_recording = False
                    if self.app_ref:
                        is_app_recording = getattr(self.app_ref, "is_streaming", False) or \
                                          getattr(getattr(self.app_ref, "live_copilot", None), "is_running", False)

                    if not is_app_recording:
                        try:
                            # Sample small 50ms audio chunk
                            samples = int(16000 * 0.05)
                            rec = sd.rec(samples, samplerate=16000, channels=1, dtype='int16', blocking=True)
                            rms = calculate_rms(rec.flatten())
                            self.update_audio_level(rms)
                        except Exception:
                            time.sleep(0.1)
                    else:
                        time.sleep(0.1)
                else:
                    time.sleep(0.3)

        self._mic_monitor_thread = threading.Thread(target=_mic_loop, name="DashboardMicMonitor", daemon=True)
        self._mic_monitor_thread.start()

    # -----------------------------------------------------------------
    # Public Thread-Safe Control Methods
    # -----------------------------------------------------------------

    def show(self):
        """Restores and brings the Dashboard GUI to front."""
        if not self.root:
            self.start_in_thread()
            time.sleep(0.1)

        def _do_show():
            try:
                self.root.deiconify()
                self.root.lift()
                self.root.focus_force()
                self.root.state("normal")
                self._is_visible = True
                logger.info("[DashboardGUI] Window restored and displayed.")
            except Exception as e:
                logger.error(f"[DashboardGUI Error] Failed to show window: {e}")

        if self.root:
            self.root.after(0, _do_show)

    def hide(self):
        """Hides the window to system tray without terminating background services."""
        if not self.root:
            return

        def _do_hide():
            try:
                self.root.withdraw()
                self._is_visible = False
                logger.info("[DashboardGUI] Window minimized to system tray.")
            except Exception as e:
                logger.error(f"[DashboardGUI Error] Failed to hide window: {e}")

        self.root.after(0, _do_hide)

    def update_audio_level(self, rms_value: float):
        """Updates the live audio VU meter progress bar in real-time."""
        if not self.root or not self._is_visible or not self._vu_bar:
            return

        # Normalize RMS (0 to 1000 RMS normalized to 0.0 to 1.0)
        norm_level = min(1.0, max(0.0, rms_value / 1000.0))
        is_above = rms_value >= self.current_rms_threshold

        def _do_update():
            try:
                self._vu_bar.set(norm_level)
                bar_color = ALERT_RED if is_above else ACTIVE_GREEN
                self._vu_bar.configure(progress_color=bar_color)
                
                status_txt = f"Level: {int(rms_value)} RMS ({'Speaking' if is_above else 'Silence'})"
                if self._vu_val_label:
                    self._vu_val_label.configure(text=status_txt)
            except Exception:
                pass

        try:
            self.root.after(0, _do_update)
        except Exception:
            pass

    def update_status(self, text: str, is_active: bool = False):
        """Updates the main header status badge."""
        if not self.root or not self._status_label:
            return

        def _do_update():
            try:
                color = ACTIVE_GREEN if not is_active else ALERT_RED
                self._status_label.configure(text=f"● STATUS: {text.upper()}", text_color=color)
            except Exception:
                pass

        try:
            self.root.after(0, _do_update)
        except Exception:
            pass

    def destroy(self):
        """Closes and releases the GUI window."""
        if not self.root:
            return

        def _do_destroy():
            try:
                if self._preview_job:
                    self.root.after_cancel(self._preview_job)
                if self._metrics_job:
                    self.root.after_cancel(self._metrics_job)
                self.root.destroy()
                self.root = None
                self._is_running = False
                self._is_visible = False
            except Exception as e:
                logger.debug(f"[DashboardGUI Destroy Notice] {e}")

        try:
            self.root.after(0, _do_destroy)
        except Exception:
            pass
