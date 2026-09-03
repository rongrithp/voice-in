import pytest
from src.zero_ui.input_intent_detector import InputIntentDetector
import time

def test_spatial_cluster_trigger():
    detector = InputIntentDetector(loiter_radius=80, loiter_time=0.8)
    
    # Simulate loitering within 80px radius
    detector.register_move(100, 100, time.time() - 0.75)
    detector.register_move(120, 110, time.time() - 0.4)
    detector.register_move(140, 100, time.time())
    
    assert detector.check_loiter_trigger() is True

    # Simulate traversal (displacement > 80px in short time)
    detector.clear_buffer()
    detector.register_move(100, 100, time.time() - 0.75)
    detector.register_move(250, 110, time.time() - 0.4)
    detector.register_move(400, 100, time.time())
    
    assert detector.check_loiter_trigger() is False

def test_drag_to_highlight_trigger():
    detector = InputIntentDetector(drag_threshold=50)
    
    # Mouse down
    detector.register_mouse_down(100, 100)
    
    # Mouse up with sufficient drag
    result = detector.register_mouse_up(200, 200)
    assert result is not None
    assert result['type'] == 'drag'
    assert result['bbox'] == (100, 100, 200, 200)
    
    # Mouse up with insufficient drag
    detector.register_mouse_down(100, 100)
    result2 = detector.register_mouse_up(110, 110)
    assert result2 is None

def test_click_trigger():
    detector = InputIntentDetector()
    
    # Single click
    detector.register_mouse_down(100, 100)
    result = detector.register_mouse_up(100, 100)
    assert result is None  # Handled separately or as click
    
    click_result = detector.register_click(100, 100)
    assert click_result['type'] == 'click'
    assert click_result['point'] == (100, 100)
