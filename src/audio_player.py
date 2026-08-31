import io
import time
import enum
import queue
import logging
import threading
from typing import Optional, Callable

logger = logging.getLogger("AudioPlayer")

class PlaybackState(enum.Enum):
    STOPPED = "STOPPED"
    PLAYING = "PLAYING"
    PAUSED = "PAUSED"

class AudioPlayer:
    """
    Non-blocking background audio player state machine.
    Uses pygame.mixer to support play, pause, resume, continuous queued chunk streaming,
    and immediate stop from in-memory buffers.
    """

    def __init__(self, lazy_init: bool = True):
        self._state = PlaybackState.STOPPED
        self._lock = threading.RLock()
        self._monitor_thread: Optional[threading.Thread] = None
        self._stop_monitor_event = threading.Event()
        self._on_finished: Optional[Callable[[], None]] = None
        self._chunk_queue: queue.Queue = queue.Queue()
        self._queue_finished = True
        self._initialized = False
        if not lazy_init:
            self._init_mixer()

    def _init_mixer(self):
        """Safely initialize pygame.mixer."""
        try:
            import pygame
            if not pygame.mixer.get_init():
                pygame.mixer.init()
            self._initialized = True
        except Exception as e:
            logger.warning(f"[AudioPlayer] Failed to initialize pygame mixer: {e}")
            self._initialized = False

    @property
    def state(self) -> PlaybackState:
        with self._lock:
            return self._state

    def is_playing(self) -> bool:
        return self.state == PlaybackState.PLAYING

    def is_paused(self) -> bool:
        return self.state == PlaybackState.PAUSED

    def is_stopped(self) -> bool:
        return self.state == PlaybackState.STOPPED

    def set_on_finished_callback(self, callback: Optional[Callable[[], None]]):
        self._on_finished = callback

    def play(self, audio_bytes: bytes) -> bool:
        """
        Loads in-memory audio bytes and starts single-track playback in non-blocking mode.
        """
        return self.start_queue_playback(audio_bytes, is_last=True)

    def start_queue_playback(self, first_chunk_bytes: bytes, is_last: bool = False) -> bool:
        """
        Starts queue-driven playback with the first synthesized chunk immediately (< 800ms TTFA).
        Subsequent chunks can be loaded dynamically via enqueue_chunk().
        """
        if not first_chunk_bytes:
            logger.warning("[AudioPlayer] Empty audio bytes provided to start playback.")
            return False

        if not self._initialized:
            self._init_mixer()

        with self._lock:
            # Stop any existing playback and flush queue
            self._stop_internal_unlocked()
            self._queue_finished = is_last

            try:
                import pygame
                audio_stream = io.BytesIO(first_chunk_bytes)
                pygame.mixer.music.load(audio_stream)
                pygame.mixer.music.play()
                self._state = PlaybackState.PLAYING
                logger.info(f"[AudioPlayer] Started queue playback with initial chunk ({len(first_chunk_bytes)}B, is_last={is_last}).")

                # Start watcher thread for track/queue completion
                self._stop_monitor_event.clear()
                self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True, name="AudioPlayerMonitor")
                self._monitor_thread.start()
                return True
            except Exception as e:
                logger.error(f"[AudioPlayer Error] Failed to play audio: {e}")
                self._state = PlaybackState.STOPPED
                return False

    def _clear_queue(self):
        """Drains and discards all pending audio chunks in queue."""
        while not self._chunk_queue.empty():
            try:
                self._chunk_queue.get_nowait()
            except Exception:
                break

    def enqueue_chunk(self, chunk_bytes: bytes, is_last: bool = False):
        """
        Enqueues a pre-fetched synthesized audio chunk into the active playback stream.
        Discards chunks if playback has been stopped or canceled.
        """
        if not chunk_bytes:
            if is_last:
                with self._lock:
                    self._queue_finished = True
            return

        with self._lock:
            # If player was stopped or canceled, discard chunk to prevent unwanted resumption
            if self._state not in (PlaybackState.PLAYING, PlaybackState.PAUSED) or self._stop_monitor_event.is_set():
                logger.debug("[AudioPlayer] Discarding enqueued chunk because playback is STOPPED/CANCELED.")
                return

            if is_last:
                self._queue_finished = True

            self._chunk_queue.put(chunk_bytes)
            logger.info(f"[AudioPlayer] Enqueued audio chunk ({len(chunk_bytes)}B, queue_size={self._chunk_queue.qsize()}, is_last={is_last}).")

    def pause(self) -> bool:
        """Pauses active playback."""
        with self._lock:
            if self._state == PlaybackState.PLAYING:
                try:
                    import pygame
                    pygame.mixer.music.pause()
                    self._state = PlaybackState.PAUSED
                    logger.info("[AudioPlayer] Playback PAUSED.")
                    return True
                except Exception as e:
                    logger.error(f"[AudioPlayer Error] Pause failed: {e}")
            return False

    def resume(self) -> bool:
        """Resumes paused playback."""
        with self._lock:
            if self._state == PlaybackState.PAUSED:
                try:
                    import pygame
                    pygame.mixer.music.unpause()
                    self._state = PlaybackState.PLAYING
                    logger.info("[AudioPlayer] Playback RESUMED (State: PLAYING).")
                    return True
                except Exception as e:
                    logger.error(f"[AudioPlayer Error] Resume failed: {e}")
            return False

    def unpause(self) -> bool:
        """Alias for resume()."""
        return self.resume()

    def get_state(self) -> PlaybackState:
        """Returns the current playback state."""
        return self.state

    def toggle_play_pause(self) -> PlaybackState:
        """Toggles between Playing and Paused."""
        with self._lock:
            current = self._state
            if current == PlaybackState.PLAYING:
                self.pause()
                return PlaybackState.PAUSED
            elif current == PlaybackState.PAUSED:
                self.resume()
                return PlaybackState.PLAYING
            return current

    toggle_pause_resume = toggle_play_pause

    def stop(self) -> bool:
        """Immediately stops playback, flushes queued chunks, and resets state to STOPPED."""
        with self._lock:
            return self._stop_internal_unlocked()

    def _stop_internal_unlocked(self) -> bool:
        self._stop_monitor_event.set()
        self._queue_finished = True
        self._clear_queue()
        try:
            import pygame
            if pygame.mixer.get_init():
                pygame.mixer.music.stop()
        except Exception:
            pass
        self._state = PlaybackState.STOPPED
        logger.info("[AudioPlayer] Playback STOPPED & queue cleared.")
        return True

    def _monitor_loop(self):
        """Monitors continuous queue playback and transitions between audio chunks seamlessly."""
        import pygame
        while not self._stop_monitor_event.is_set():
            time.sleep(0.03)
            with self._lock:
                if self._state != PlaybackState.PLAYING and self._state != PlaybackState.PAUSED:
                    break
                try:
                    # If current chunk finished playing and not paused
                    if self._state == PlaybackState.PLAYING and not pygame.mixer.music.get_busy():
                        next_chunk = None
                        try:
                            next_chunk = self._chunk_queue.get_nowait()
                        except queue.Empty:
                            pass

                        if next_chunk:
                            audio_stream = io.BytesIO(next_chunk)
                            pygame.mixer.music.load(audio_stream)
                            pygame.mixer.music.play()
                            logger.info(f"[AudioPlayer] Seamlessly playing next queued chunk ({len(next_chunk)}B, remaining={self._chunk_queue.qsize()}).")
                            continue
                        elif not self._queue_finished:
                            # Waiting for pre-fetching worker to synthesize the next chunk
                            continue
                        else:
                            # Entire queue and speech finished
                            self._state = PlaybackState.STOPPED
                            logger.info("[AudioPlayer] All queued audio playback completed naturally.")
                            callback = self._on_finished
                            if callback:
                                threading.Thread(target=callback, daemon=True).start()
                            break
                except Exception as ex:
                    logger.debug(f"[AudioPlayer Monitor Notice]: {ex}")
                    break
