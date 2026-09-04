import asyncio
from typing import Callable, Any, Optional
from pynput import keyboard

class GlobalKeyboardHook:
    """
    Physical OS sensor that captures low-level keyboard events across Windows
    and dispatches asynchronously to the application loop.
    """
    def __init__(
        self,
        target_key: str,
        on_press_coro: Callable[[], Any],
        on_release_coro: Optional[Callable[[], Any]] = None,
        loop: Optional[asyncio.AbstractEventLoop] = None
    ):
        self._target_key = target_key.lower()
        self._on_press_coro = on_press_coro
        self._on_release_coro = on_release_coro
        self._loop = loop
        self._listener: Optional[keyboard.Listener] = None
        self._is_running: bool = False
        self._is_key_down: bool = False

    @property
    def is_running(self) -> bool:
        return self._is_running

    def _resolve_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop and self._loop.is_running():
            return self._loop
        return asyncio.get_event_loop()

    def _on_key_event(self, key_name: str, is_press: bool = True) -> None:
        """Immediate non-blocking callback executed from OS hook thread."""
        if key_name.lower() == self._target_key:
            coro_fn = self._on_press_coro if is_press else self._on_release_coro
            if coro_fn:
                try:
                    target_loop = self._resolve_loop()
                    asyncio.run_coroutine_threadsafe(coro_fn(), target_loop)
                except Exception:
                    pass

    def _on_native_press(self, key) -> None:
        key_name = ""
        try:
            if hasattr(key, "name"):
                key_name = key.name
            elif hasattr(key, "char") and key.char:
                key_name = key.char
        except Exception:
            return
        
        if key_name.lower() == self._target_key:
            if self._is_key_down:
                return  # Discard Windows auto-repeat
            self._is_key_down = True

        self._on_key_event(key_name, is_press=True)

    def _on_native_release(self, key) -> None:
        key_name = ""
        try:
            if hasattr(key, "name"):
                key_name = key.name
            elif hasattr(key, "char") and key.char:
                key_name = key.char
        except Exception:
            return
        
        if key_name.lower() == self._target_key:
            if not self._is_key_down:
                return
            self._is_key_down = False

        self._on_key_event(key_name, is_press=False)

    def _create_native_listener(self) -> keyboard.Listener:
        return keyboard.Listener(
            on_press=self._on_native_press,
            on_release=self._on_native_release if self._on_release_coro else None
        )

    def start(self) -> None:
        if self._is_running:
            return
        self._listener = self._create_native_listener()
        self._listener.daemon = True
        self._listener.start()
        self._is_running = True

    def stop(self) -> None:
        if not self._is_running or not self._listener:
            return
        try:
            self._listener.stop()
        except Exception:
            pass
        finally:
            self._is_running = False
            self._listener = None
