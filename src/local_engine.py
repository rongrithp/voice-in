import numpy as np
from faster_whisper import WhisperModel
import config

class LocalWhisperEngine:
    """
    Local Voice-to-Text Transcriber powered by faster-whisper.
    Optimized for Intel NUC CPU using INT8 quantization and 8 execution threads.
    """

    def __init__(self, model_size: str = config.MODEL_SIZE):
        self.model_size = model_size
        print(f"[LocalWhisperEngine] Initializing faster-whisper model '{model_size}'...", flush=True)
        print(f"  - Device: {config.DEVICE}")
        print(f"  - Compute Type: {config.COMPUTE_TYPE}")
        print(f"  - CPU Threads: {config.CPU_THREADS}")

        self.model = WhisperModel(
            model_size,
            device=config.DEVICE,
            compute_type=config.COMPUTE_TYPE,
            cpu_threads=config.CPU_THREADS,
            local_files_only=getattr(config, "LOCAL_FILES_ONLY", True)
        )
        print(f"[LocalWhisperEngine] Model '{model_size}' loaded successfully!", flush=True)

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
                initial_prompt=config.INITIAL_PROMPT,
                beam_size=config.BEAM_SIZE,
                vad_filter=config.VAD_FILTER,
                no_speech_threshold=config.NO_SPEECH_THRESHOLD,
                condition_on_previous_text=config.CONDITION_ON_PREVIOUS_TEXT,
                repetition_penalty=config.REPETITION_PENALTY
            )

            text = " ".join([segment.text.strip() for segment in segments if segment.text])
            return text.strip()
        except Exception as e:
            print(f"[LocalWhisperEngine Error]: {e}", flush=True)
            return ""
