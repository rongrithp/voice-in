import enum
import logging
import threading
from typing import Callable, Optional
from PIL import Image, ImageDraw

try:
    import pystray
    PYSTRAY_AVAILABLE = True
except ImportError:
    PYSTRAY_AVAILABLE = False

logger = logging.getLogger("TrayManager")

class DaemonStatus(enum.Enum):
    READY = "Ready"       # 🟢 Idle / Ready
    ACTIVE = "Active"     # 🔴 STT Recording or TTS Playing
    ERROR = "Error"       # ⚠️ Error state

STATUS_COLORS = {
    DaemonStatus.READY: (46, 204, 113, 255),    # Vibrant Green
    DaemonStatus.ACTIVE: (231, 76, 60, 255),    # Vibrant Red
    DaemonStatus.ERROR: (241, 196, 15, 255),    # Amber Yellow
}

STATUS_ICONS = {
    DaemonStatus.READY: "🟢",
    DaemonStatus.ACTIVE: "🔴",
    DaemonStatus.ERROR: "⚠️",
}

def create_status_image(status: DaemonStatus, size: int = 64) -> Image.Image:
    """Generates a high-DPI circular status badge icon with prominent active streaming indicator."""
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    if status == DaemonStatus.ACTIVE:
        # High-visibility Active Streaming / Data Transmission Badge (Bright Crimson Red with Glow Ring)
        # Outer vibrant warning glow ring
        draw.ellipse((1, 1, size - 1, size - 1), fill=(231, 76, 60, 90), outline=(255, 60, 60, 255), width=2)
        # Main red recording core
        draw.ellipse((10, 10, size - 10, size - 10), fill=(231, 76, 60, 255), outline=(255, 255, 255, 230), width=2)
        # Center high-contrast bright white recording dot
        draw.ellipse((22, 22, size - 22, size - 22), fill=(255, 255, 255, 240))
    elif status == DaemonStatus.ERROR:
        # Amber Warning Badge
        draw.ellipse((2, 2, size - 2, size - 2), fill=(30, 30, 30, 220), outline=(241, 196, 15, 200), width=2)
        draw.ellipse((12, 12, size - 12, size - 12), fill=(241, 196, 15, 255))
        draw.rectangle((size//2 - 2, 20, size//2 + 2, 38), fill=(20, 20, 20, 255))
        draw.ellipse((size//2 - 2, 42, size//2 + 2, 46), fill=(20, 20, 20, 255))
    else:
        # Idle / Ready State (Sleek Dark Circle with Emerald Green Ring)
        draw.ellipse((2, 2, size - 2, size - 2), fill=(24, 28, 36, 230), outline=(52, 152, 219, 160), width=2)
        draw.ellipse((14, 14, size - 14, size - 14), fill=(46, 204, 113, 255), outline=(255, 255, 255, 60), width=1)
        # Subtle specular highlight
        draw.ellipse((18, 18, size - 28, size - 28), fill=(255, 255, 255, 120))

    return image

SPEED_OPTIONS = [0.75, 1.0, 1.25, 1.5, 1.75, 2.0]

FEMALE_VOICES = {
    "th-TH-Neural2-C": "Google Neural2-C (สตูดิโอ)",
    "th-TH-Standard-A": "Google Standard-A (คลาสสิก)",
}

class TrayManager:
    """
    System Tray Manager powered by pystray & Pillow.
    Maintains persistent status icon in Windows taskbar and manages user context actions,
    including real-time usage & cost monitor, female voice selection, speed control, daemon reload, and emergency unmute.
    """

    def __init__(
        self,
        on_open_dashboard: Optional[Callable[[], None]] = None,
        on_reload: Optional[Callable[[], None]] = None,
        on_emergency_unmute: Optional[Callable[[], None]] = None,
        on_speed_change: Optional[Callable[[float], None]] = None,
        on_voice_change: Optional[Callable[[str], None]] = None,
        on_reset_usage: Optional[Callable[[], None]] = None,
        on_read_down: Optional[Callable[[], None]] = None,
        on_live_toggle: Optional[Callable[[], None]] = None,
        on_windows_local_tts: Optional[Callable[[], None]] = None,
        on_exit: Optional[Callable[[], None]] = None,
        is_live_active_callback: Optional[Callable[[], bool]] = None,
        app_title: str = "Voice Hub Daemon",
        current_speed: float = 1.0,
        current_voice: str = "th-TH-Neural2-C",
        usage_tracker: Optional[object] = None
    ):
        self.on_open_dashboard = on_open_dashboard
        self.on_reload = on_reload
        self.on_emergency_unmute = on_emergency_unmute
        self.on_speed_change = on_speed_change
        self.on_voice_change = on_voice_change
        self.on_reset_usage = on_reset_usage
        self.on_read_down = on_read_down
        self.on_live_toggle = on_live_toggle
        self.on_windows_local_tts = on_windows_local_tts
        self.on_exit = on_exit
        self.is_live_active_callback = is_live_active_callback
        self.app_title = app_title
        self.current_speed = current_speed
        self.current_voice = current_voice
        self.usage_tracker = usage_tracker
        self.status = DaemonStatus.READY
        self.icon: Optional[object] = None
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None

    def notify(self, title: str, message: str):
        """Sends a desktop balloon notification via system tray icon."""
        if self.icon and PYSTRAY_AVAILABLE and hasattr(self.icon, "notify"):
            try:
                self.icon.notify(message, title)
            except Exception as e:
                logger.debug(f"[Tray Notify Notice] {e}")

    def _create_usage_menu(self):
        """Creates dynamic real-time usage and cost monitor submenu."""
        if self.usage_tracker and hasattr(self.usage_tracker, "get_current_month_summary"):
            summary = self.usage_tracker.get_current_month_summary()
            month_key = summary.get("month", "Current")
            total_thb = summary.get("total_cost_thb", 0.0)
            stt_min = summary.get("stt_min", 0.0)
            stt_thb = summary.get("stt_cost_thb", 0.0)
            tts_chars = summary.get("tts_chars", 0)
            tts_thb = summary.get("tts_cost_thb", 0.0)
        else:
            month_key = "Current"
            total_thb = 0.0
            stt_min = 0.0
            stt_thb = 0.0
            tts_chars = 0
            tts_thb = 0.0

        def _handle_open_file(icon, item):
            logger.info("[Tray] User requested to open usage stats folder/file.")
            try:
                import os
                if self.usage_tracker and hasattr(self.usage_tracker, "storage_path"):
                    path = self.usage_tracker.storage_path
                    if path.exists():
                        os.startfile(str(path))
                    else:
                        os.startfile(str(path.parent))
                else:
                    data_dir = os.path.abspath("data")
                    os.makedirs(data_dir, exist_ok=True)
                    os.startfile(data_dir)
            except Exception as e:
                logger.error(f"[Tray Error] Failed to open stats location: {e}")

        def _handle_reset(icon, item):
            logger.info("[Tray] User requested usage statistics reset.")
            if self.on_reset_usage:
                self.on_reset_usage()
            elif self.usage_tracker:
                self.usage_tracker.reset_stats()
            if self.icon and PYSTRAY_AVAILABLE:
                try:
                    self.icon.menu = self._build_menu()
                except Exception as e:
                    logger.debug(f"[Tray Menu Refresh Notice] {e}")

        return pystray.Menu(
            pystray.MenuItem(f"📊 Usage ({month_key}): ฿{total_thb:.2f} THB", lambda icon, item: None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(f"🎙️ STT: {stt_min:.1f} min (฿{stt_thb:.2f})", lambda icon, item: None, enabled=False),
            pystray.MenuItem(f"🔊 TTS: {tts_chars:,} chars (฿{tts_thb:.2f})", lambda icon, item: None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("📂 Open Usage Stats File", _handle_open_file),
            pystray.MenuItem("🔄 Reset Current Month", _handle_reset)
        )

    def _create_voice_menu(self):
        """Creates dynamic female-only voice selection submenu with radio checkmarks."""
        def set_voice_action(voice_id):
            def _handler(icon, item):
                self.current_voice = voice_id
                logger.info(f"[Tray] Voice selection changed to '{voice_id}'")
                if self.on_voice_change:
                    self.on_voice_change(voice_id)
                # Refresh menu checkmark
                if self.icon and PYSTRAY_AVAILABLE:
                    try:
                        self.icon.menu = self._build_menu()
                    except Exception as e:
                        logger.debug(f"[Tray Menu Refresh Notice] {e}")
            return _handler

        def is_checked(voice_id):
            return lambda item: self.current_voice == voice_id

        items = [
            pystray.MenuItem(
                label,
                set_voice_action(v_id),
                checked=is_checked(v_id),
                radio=True
            )
            for v_id, label in FEMALE_VOICES.items()
        ]
        return pystray.Menu(*items)

    def _create_speed_menu(self):
        """Creates dynamic speech speed selection submenu with radio checkmarks."""
        def set_speed_action(speed):
            def _handler(icon, item):
                self.current_speed = speed
                logger.info(f"[Tray] Speed selection changed to {speed:.2f}x")
                if self.on_speed_change:
                    self.on_speed_change(speed)
                # Refresh menu checkmark
                if self.icon and PYSTRAY_AVAILABLE:
                    try:
                        self.icon.menu = self._build_menu()
                    except Exception as e:
                        logger.debug(f"[Tray Menu Refresh Notice] {e}")
            return _handler

        def is_checked(speed):
            return lambda item: abs(self.current_speed - speed) < 1e-4

        items = [
            pystray.MenuItem(
                f"{s:.2f}x" if s != 1.0 else "1.00x (Normal)",
                set_speed_action(s),
                checked=is_checked(s),
                radio=True
            )
            for s in SPEED_OPTIONS
        ]
        return pystray.Menu(*items)

    def _build_menu(self):
        if not PYSTRAY_AVAILABLE:
            return None

        status_text = f"{STATUS_ICONS.get(self.status, '')} Status: {self.status.value}"
        
        is_live_active = False
        if self.is_live_active_callback:
            try:
                is_live_active = bool(self.is_live_active_callback())
            except Exception:
                pass

        live_copilot_label = f"🤖 Gemini Live Co-pilot (F20) [{'🟢 ON' if is_live_active else 'OFF'}]"

        return pystray.Menu(
            pystray.MenuItem("⚙️ Settings & Dashboard", self._handle_open_dashboard, default=True),
            pystray.MenuItem(status_text, lambda icon, item: None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("📖 TTS Read Down (F16)", self._handle_read_down),
            pystray.MenuItem(live_copilot_label, self._handle_live_toggle),
            pystray.MenuItem("🔊 Windows Local TTS (F21)", self._handle_windows_local_tts),
            pystray.MenuItem("📊 Usage & Cost", self._create_usage_menu()),
            pystray.MenuItem("🎙️ Voice (Female)", self._create_voice_menu()),
            pystray.MenuItem("⚡ Speech Speed", self._create_speed_menu()),
            pystray.MenuItem("🔄 Reload / Restart Daemon", self._handle_reload),
            pystray.MenuItem("🔊 Emergency Unmute", self._handle_emergency_unmute),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("❌ Exit", self._handle_exit)
        )

    def _handle_open_dashboard(self, icon, item):
        logger.info("[Tray] User requested Settings & Dashboard window.")
        if self.on_open_dashboard:
            threading.Thread(target=self.on_open_dashboard, daemon=True).start()

    def _handle_read_down(self, icon, item):
        logger.info("[Tray] User requested TTS Read Down (F16).")
        if self.on_read_down:
            threading.Thread(target=self.on_read_down, daemon=True).start()

    def _handle_live_toggle(self, icon, item):
        logger.info("[Tray] User requested Gemini Live Co-pilot toggle.")
        if self.on_live_toggle:
            threading.Thread(target=self.on_live_toggle, daemon=True).start()
        if self.icon and PYSTRAY_AVAILABLE:
            try:
                self.icon.menu = self._build_menu()
            except Exception as e:
                logger.debug(f"[Tray Menu Refresh Notice] {e}")

    def _handle_windows_local_tts(self, icon, item):
        logger.info("[Tray] User requested Windows Local Native TTS (F21).")
        if self.on_windows_local_tts:
            threading.Thread(target=self.on_windows_local_tts, daemon=True).start()

    def _handle_reload(self, icon, item):
        logger.info("[Tray] Reload requested by user.")
        if self.on_reload:
            threading.Thread(target=self.on_reload, daemon=True).start()

    def _handle_emergency_unmute(self, icon, item):
        logger.info("[Tray] Emergency unmute requested by user.")
        if self.on_emergency_unmute:
            threading.Thread(target=self.on_emergency_unmute, daemon=True).start()

    def _handle_exit(self, icon, item):
        logger.info("[Tray] Exit requested by user.")
        if self.on_exit:
            self.on_exit()
        self.stop()

    def update_status(self, status: DaemonStatus, tooltip: Optional[str] = None):
        """Updates tray icon state and tooltip text dynamically."""
        with self._lock:
            self.status = status
            if self.icon and PYSTRAY_AVAILABLE:
                try:
                    self.icon.icon = create_status_image(status)
                    self.icon.title = tooltip or f"{self.app_title} - {status.value}"
                    self.icon.menu = self._build_menu()
                except Exception as e:
                    logger.error(f"[Tray Error] Failed to update status: {e}")

    def start(self):
        """Starts the tray icon in a non-blocking background thread or detached mode."""
        if not PYSTRAY_AVAILABLE:
            logger.warning("[Tray] pystray not available. Tray icon will not be displayed.")
            return

        try:
            initial_image = create_status_image(self.status)
            self.icon = pystray.Icon(
                name="VoiceHubDaemon",
                icon=initial_image,
                title=f"{self.app_title} - {self.status.value}",
                menu=self._build_menu()
            )

            # Prefer run_detached() if available, otherwise launch via daemon worker thread
            if hasattr(self.icon, "run_detached"):
                self.icon.run_detached()
                logger.info("[Tray] System tray icon running (detached).")
            else:
                self._thread = threading.Thread(target=self.icon.run, daemon=True, name="TrayIconThread")
                self._thread.start()
                logger.info("[Tray] System tray icon running (worker thread).")
        except Exception as e:
            logger.error(f"[Tray Error] Failed to start tray icon: {e}")

    def stop(self):
        """Stops the system tray icon."""
        with self._lock:
            if self.icon and PYSTRAY_AVAILABLE:
                try:
                    self.icon.stop()
                    self.icon = None
                except Exception as e:
                    logger.error(f"[Tray Error] Failed to stop icon: {e}")
