import pytest
from unittest.mock import patch, MagicMock
from src.tts_engine import clean_tts_text, split_text_chunks, TTSEngine

def test_clean_tts_text_markdown_and_links():
    raw = "## หัวข้อสำคัญ [คลิกที่นี่](https://example.com) `code block` **เน้นข้อความ**"
    cleaned = clean_tts_text(raw)
    assert "หัวข้อสำคัญ" in cleaned
    assert "คลิกที่นี่" in cleaned
    assert "https://example.com" not in cleaned
    assert "##" not in cleaned
    assert "*" not in cleaned
    assert "`" not in cleaned

def test_clean_tts_text_brackets_and_spaces():
    raw = "ข้อความ [Silence] [00:00]   เว้นวรรคเยอะมาก   "
    cleaned = clean_tts_text(raw)
    assert "[Silence]" not in cleaned
    assert "  " not in cleaned
    assert cleaned == "ข้อความ เว้นวรรคเยอะมาก"

def test_clean_tts_text_empty():
    assert clean_tts_text("") == ""
    assert clean_tts_text("   ") == ""
    assert clean_tts_text(None) == ""

def test_split_text_chunks_short_text():
    short = "สวัสดีครับชาวโลก"
    chunks = split_text_chunks(short, max_chunk_len=120)
    assert len(chunks) == 1
    assert chunks[0] == short

def test_split_text_chunks_long_text():
    long_text = (
        "สวัสดีครับทุกคน วันนี้เราจะมาทดสอบระบบสังเคราะห์เสียงอ่านภาษาไทยแบบแบ่งเป็นท่อนย่อย "
        "เพื่อให้สามารถเริ่มเล่นเสียงได้ทันทีภายในแปดร้อยมิลลิวินาที โดยไม่ต้องรอการสังเคราะห์ "
        "ข้อความขนาดยาวทั้งย่อหน้าจบลงก่อน ซึ่งจะช่วยลดระยะเวลาการรอคอยได้อย่างมาก"
    )
    chunks = split_text_chunks(long_text, first_chunk_max_len=40, normal_chunk_max_len=80)
    assert len(chunks) > 1
    # First chunk is micro-chunk <= 40 chars
    assert len(chunks[0]) <= 50
    for chunk in chunks:
        assert len(chunk.strip()) > 0

def test_gcp_tts_engine_warmup():
    from src.gcp_tts_engine import GCPTTSEngine
    engine = GCPTTSEngine()
    engine.warmup()
    assert engine._client is None

def test_gcp_tts_engine():
    from src.gcp_tts_engine import GCPTTSEngine
    mock_resp = MagicMock()
    mock_resp.audio_content = b"GCP_AUDIO_BYTES"

    mock_client = MagicMock()
    mock_client.synthesize_speech.return_value = mock_resp

    with patch.dict("sys.modules", {"google.cloud.texttospeech": MagicMock()}):
        engine = GCPTTSEngine()
        engine._client = mock_client
        audio = engine.synthesize("ข้อความทดสอบ GCP")
        assert audio == b"GCP_AUDIO_BYTES"

def test_tts_engine_synthesize_with_cache():
    mock_resp = MagicMock()
    mock_resp.audio_content = b"PRIMARY_GCP_BYTES"

    with patch("src.gcp_tts_engine.GCPTTSEngine.synthesize", return_value=b"PRIMARY_GCP_BYTES") as mock_synth:
        engine = TTSEngine()
        engine.clear_cache()

        # First call -> Synthesizes via GCP
        audio1 = engine.synthesize("ทดสอบ GCP Cache")
        assert audio1 == b"PRIMARY_GCP_BYTES"
        assert mock_synth.call_count == 1

        # Second call -> Returns from In-Memory Cache
        audio2 = engine.synthesize("ทดสอบ GCP Cache")
        assert audio2 == b"PRIMARY_GCP_BYTES"
        assert mock_synth.call_count == 1

def test_tts_engine_set_voice_and_clear_cache():
    engine = TTSEngine()

    # 1. Switch to Standard-A
    engine.set_voice("th-TH-Standard-A")
    assert engine.voice_name == "th-TH-Standard-A"

    # 2. Switch to Neural2-C
    engine.set_voice("th-TH-Neural2-C")
    assert engine.voice_name == "th-TH-Neural2-C"

    # 3. Test speed and clear cache
    engine.set_speed(1.25)
    assert engine.speaking_rate == 1.25
