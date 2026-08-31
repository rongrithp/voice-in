import time
import threading
import pytest
from unittest.mock import MagicMock
from src.app import HotkeyActionHandler

def test_hotkey_single_click():
    single_event = threading.Event()
    single_mock = MagicMock(side_effect=lambda: single_event.set())
    double_mock = MagicMock()

    handler = HotkeyActionHandler(
        on_single_click=single_mock,
        on_double_click=double_mock,
        threshold=0.03,
        debounce_interval=0.008
    )

    handler.handle_press()
    handler.handle_release()
    assert single_event.wait(timeout=0.2) is True

    single_mock.assert_called_once()
    double_mock.assert_not_called()

def test_hotkey_repeat_debounce_suppressed():
    single_event = threading.Event()
    single_mock = MagicMock(side_effect=lambda: single_event.set())
    double_mock = MagicMock()

    handler = HotkeyActionHandler(
        on_single_click=single_mock,
        on_double_click=double_mock,
        threshold=0.03,
        debounce_interval=0.012
    )

    # First press
    handler.handle_press()
    handler.handle_release()
    # Key bounce event within 2ms (< 12ms debounce)
    time.sleep(0.002)
    handler.handle_press()

    assert single_event.wait(timeout=0.2) is True

    # Should register as ONE single click, NOT a double click!
    single_mock.assert_called_once()
    double_mock.assert_not_called()

def test_hotkey_held_down_repeat_events_suppressed():
    single_event = threading.Event()
    single_mock = MagicMock(side_effect=lambda: single_event.set())
    double_mock = MagicMock()

    handler = HotkeyActionHandler(
        on_single_click=single_mock,
        on_double_click=double_mock,
        threshold=0.03,
        debounce_interval=0.01
    )

    # Key pressed down and held
    handler.handle_press()

    # Repeated Windows key down events arrive while key is held down
    for _ in range(3):
        time.sleep(0.002)
        handler.handle_press()

    assert single_event.wait(timeout=0.2) is True

    # Only one single click triggered, zero double clicks
    single_mock.assert_called_once()
    double_mock.assert_not_called()

    # Finally released
    handler.handle_release()

def test_hotkey_double_click():
    double_event = threading.Event()
    single_mock = MagicMock()
    double_mock = MagicMock(side_effect=lambda: double_event.set())

    handler = HotkeyActionHandler(
        on_single_click=single_mock,
        on_double_click=double_mock,
        threshold=0.06,
        debounce_interval=0.01
    )

    # First press & release
    handler.handle_press()
    handler.handle_release()
    time.sleep(0.015) # Above debounce interval, within double-click threshold

    # Second press & release
    handler.handle_press()
    handler.handle_release()

    assert double_event.wait(timeout=0.2) is True

    single_mock.assert_not_called()
    double_mock.assert_called_once()

def test_hotkey_cancel():
    single_mock = MagicMock()
    double_mock = MagicMock()

    handler = HotkeyActionHandler(
        on_single_click=single_mock,
        on_double_click=double_mock,
        threshold=0.03,
        debounce_interval=0.01
    )

    handler.handle_press()
    handler.handle_release()
    handler.cancel()
    time.sleep(0.05)

    single_mock.assert_not_called()
    double_mock.assert_not_called()
