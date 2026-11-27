"""Real FFmpeg integration tests for audio preparation and OGG Opus output."""

import wave
from pathlib import Path

from bot.services.audio import (
    finalize_telegram_voice,
    prepare_speaker_reference,
    prepare_stt_audio,
    run_ffmpeg,
)


async def test_ffmpeg_stt_speaker_trim_and_multi_chunk_ogg(tmp_path: Path):
    source_ogg = tmp_path / "input.ogg"
    # Generate a real 12-second synthetic tone OGG Opus file
    await run_ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=12",
            "-c:a",
            "libopus",
            str(source_ogg),
        ]
    )
    assert source_ogg.exists() and source_ogg.stat().st_size > 0

    stt_mp3 = await prepare_stt_audio(source_ogg, tmp_path / "stt.mp3")
    assert stt_mp3.exists() and stt_mp3.stat().st_size > 0

    speaker_wav = await prepare_speaker_reference(
        source_ogg, tmp_path / "speaker.wav", max_seconds=5
    )
    assert speaker_wav.exists()
    with wave.open(str(speaker_wav), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getframerate() == 22050
        duration = wf.getnframes() / float(wf.getframerate())
        assert 4.5 <= duration <= 5.2

    # Single chunk OGG
    single_ogg = await finalize_telegram_voice([speaker_wav], tmp_path / "single.ogg")
    assert single_ogg.exists() and single_ogg.stat().st_size > 0

    # Multi-chunk concatenation OGG
    multi_ogg = await finalize_telegram_voice(
        [speaker_wav, speaker_wav], tmp_path / "multi.ogg"
    )
    assert multi_ogg.exists()
    assert multi_ogg.stat().st_size > single_ogg.stat().st_size
