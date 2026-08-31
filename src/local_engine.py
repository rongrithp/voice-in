import logging
import os
from typing import Optional
import numpy as np
import config

logger = logging.getLogger("LocalWhisperEngine")

def resolve_device_and_compute():
    device = getattr(config, "DEVICE", "auto")
    compute_type = getattr(config, "COMPUTE_TYPE", "auto")

    if device == "auto":
        try:
            import ctranslate2
            if ctranslate2.get_cuda_device_count() > 0:
                device = "cuda"
                if compute_type == "auto":
                    compute_type = "float16"
            else:
                device = "cpu"
                if compute_type == "auto":
                    compute_type = "int8"
        except Exception:
            device = "cpu"
            if compute_type == "auto":
                compute_type = "int8"
    else:
        if compute_type == "auto":
            compute_type = "float16" if device == "cuda" else "int8"

    return device, compute_type


class LocalWhisperEngine:
    """
    Local Voice-to-Text Transcriber powered by faster-whisper.
    Zero-network latency, 100% offline INT8/FP16 quantized local execution.
    """

    def __init__(
        self,
        model_size: str = getattr(config, "MODEL_SIZE", "small"),
        device: Optional[str] = None,
        compute_type: Optional[str] = None
    ):
        self.model_size = model_size
        if device is not None and compute_type is not None:
            self.device = device
            self.compute_type = compute_type
        else:
            resolved_dev, resolved_comp = resolve_device_and_compute()
            self.device = device or resolved_dev
            self.compute_type = compute_type or resolved_comp
        self.cpu_threads = int(getattr(config, "CPU_THREADS", os.cpu_count() or 8))
        self._model = None

        logger.info(f"[LocalWhisperEngine] Configured model '{self.model_size}' on device '{self.device}' ({self.compute_type}, {self.cpu_threads} threads)")

    @property
    def model(self):
        if self._model is None:
            from faster_whisper import WhisperModel
            logger.info(f"[LocalWhisperEngine] Loading faster-whisper '{self.model_size}' into memory...")
            self._model = WhisperModel(
                self.model_size,
                device=self.device,
                compute_type=self.compute_type,
                cpu_threads=self.cpu_threads,
                local_files_only=getattr(config, "LOCAL_FILES_ONLY", False)
            )
            logger.info(f"[LocalWhisperEngine] Model '{self.model_size}' loaded successfully!")
        return self._model

    @model.setter
    def model(self, val):
        self._model = val

    def warmup(self):
        """Pre-warms the Whisper model weights and computation graph in memory."""
        try:
            logger.info("[LocalWhisperEngine] Pre-warming model computation graph...")
            dummy_audio = np.zeros(int(config.SAMPLE_RATE * 0.2), dtype=np.float32)
            _ = self.transcribe(dummy_audio)
            logger.info("[LocalWhisperEngine] Model warmup complete.")
        except Exception as e:
            logger.warning(f"[LocalWhisperEngine Warmup Warning]: {e}")

    def transcribe(self, audio_data: np.ndarray) -> str:
        """
        Transcribe float32 16kHz mono audio array using locked Thai language and initial prompt.
        """
        if audio_data is None or len(audio_data) == 0:
            return ""

        try:
            segments, info = self.model.transcribe(
                audio_data,
                language=config.LANGUAGE,
                initial_prompt=getattr(config, "INITIAL_PROMPT", ""),
                beam_size=getattr(config, "BEAM_SIZE", 1),
                vad_filter=getattr(config, "VAD_FILTER", True),
                no_speech_threshold=getattr(config, "NO_SPEECH_THRESHOLD", 0.6),
                condition_on_previous_text=getattr(config, "CONDITION_ON_PREVIOUS_TEXT", False),
                repetition_penalty=getattr(config, "REPETITION_PENALTY", 1.2)
            )

            text = " ".join([segment.text.strip() for segment in segments if segment.text])
            clean_text = text.strip()
            if clean_text:
                logger.info(f"[LocalWhisper] Transcribed: '{clean_text}'")
            return clean_text
        except Exception as e:
            logger.error(f"[LocalWhisperEngine Error]: {e}")
            return ""
