"""Regression: faster-whisper muss Audio mit der installierten PyAV-Version dekodieren koennen.

PyAV 19 entfernte av.open(metadata_errors=...), das faster-whisper 1.2.1 nutzt (Fehler 2026-11-03).
"""
import wave

from faster_whisper.audio import decode_audio


def test_decode_audio_mit_installiertem_pyav(tmp_path):
    datei = tmp_path / "stille.wav"
    with wave.open(str(datei), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\0\0" * 1600)
    assert len(decode_audio(str(datei))) == 1600
