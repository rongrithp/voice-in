import math
import time
from collections import deque
from typing import Optional, Dict, Any, Tuple

class InputIntentDetector:
    def __init__(self, loiter_radius: int = 80, loiter_time: float = 0.8, drag_threshold: int = 50):
        self.loiter_radius = loiter_radius
        self.loiter_time = loiter_time
        self.drag_threshold = drag_threshold
        
        self._move_buffer = deque()
        self._mouse_down_pos: Optional[Tuple[int, int]] = None
        self._last_click_time = 0.0
        self._last_click_pos: Optional[Tuple[int, int]] = None

    def register_move(self, x: int, y: int, timestamp: Optional[float] = None) -> bool:
        """
        Registers a mouse movement and returns True if a spatial cluster (loiter) is detected.
        """
        ts = timestamp if timestamp is not None else time.time()
        self._move_buffer.append((x, y, ts))
        
        # Prune old entries
        while self._move_buffer and ts - self._move_buffer[0][2] > self.loiter_time:
            self._move_buffer.popleft()
            
        return self.check_loiter_trigger()

    def check_loiter_trigger(self) -> bool:
        if not self._move_buffer:
            return False
            
        # Need at least a full loiter_time worth of data, roughly.
        # But for testability, we just check if the window is long enough.
        time_span = self._move_buffer[-1][2] - self._move_buffer[0][2]
        if time_span < self.loiter_time * 0.9:  # Allow 10% margin for timing issues
            return False
            
        xs = [pt[0] for pt in self._move_buffer]
        ys = [pt[1] for pt in self._move_buffer]
        
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        
        # Calculate max distance between any two points in the buffer
        # A simpler way is to ensure the bounding box fits within the radius * 2
        width = max_x - min_x
        height = max_y - min_y
        max_dist = math.hypot(width, height)
        
        return max_dist <= self.loiter_radius * 2

    def clear_buffer(self):
        self._move_buffer.clear()

    def register_mouse_down(self, x: int, y: int):
        self._mouse_down_pos = (x, y)

    def register_mouse_up(self, x: int, y: int) -> Optional[Dict[str, Any]]:
        if not self._mouse_down_pos:
            return None
            
        start_x, start_y = self._mouse_down_pos
        self._mouse_down_pos = None
        
        dist = math.hypot(x - start_x, y - start_y)
        
        if dist >= self.drag_threshold:
            return {
                'type': 'drag',
                'bbox': (min(start_x, x), min(start_y, y), max(start_x, x), max(start_y, y))
            }
        return None

    def register_click(self, x: int, y: int) -> Dict[str, Any]:
        """Registers a click (or double click) and returns the pinpoint focus."""
        now = time.time()
        is_double = False
        
        if self._last_click_pos:
            lx, ly = self._last_click_pos
            if now - self._last_click_time < 0.5 and math.hypot(x - lx, y - ly) < 10:
                is_double = True
                
        self._last_click_pos = (x, y)
        self._last_click_time = now
        
        return {
            'type': 'double_click' if is_double else 'click',
            'point': (x, y)
        }
