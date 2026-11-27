"""Async FFmpeg helpers for Telegram audio normalization and conversion."""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path


class AudioProcessingError(RuntimeError):
    """Raised when FFmpeg fails or cannot be located."""


def find_ffmpeg() -> str:
    """Locate the FFmpeg binary from FFMPEG_PATH or system PATH."""
    explicit = os.getenv("FFMPEG_PATH", "").strip()
    if explicit:
        return explicit

    found = shutil.which("ffmpeg")
    if not found:
        raise AudioProcessingError(
            "FFmpeg executable not found. Install ffmpeg (or add heroku-community/apt with Aptfile)."
        )
    return found


async def run_ffmpeg(args: list[str], *, timeout_seconds: float = 90.0) -> None:
    """Execute FFmpeg asynchronously without blocking the event loop."""
    ffmpeg_bin = find_ffmpeg()
    process = await asyncio.create_subprocess_exec(
        ffmpeg_bin,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
    except asyncio.TimeoutError as exc:
        process.kill()
        await process.communicate()
        raise AudioProcessingError("FFmpeg audio conversion timed out.") from exc

    if process.returncode != 0:
        err_text = stderr.decode("utf-8", errors="replace").strip()
        raise AudioProcessingError(f"FFmpeg failed (code {process.returncode}): {err_text[:400]}")


async def prepare_stt_audio(input_path: Path, output_mp3_path: Path) -> Path:
    """Convert any Telegram voice/audio/video_note to a compact 16kHz mono MP3 for STT."""
    await run_ffmpeg(
        [
            "-i",
            str(input_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-b:a",
            "48k",
            str(output_mp3_path),
        ]
    )
    if not output_mp3_path.exists() or output_mp3_path.stat().st_size == 0:
        raise AudioProcessingError("Prepared STT audio file is empty.")
    return output_mp3_path


async def prepare_speaker_reference(
    input_path: Path,
    output_wav_path: Path,
    *,
    max_seconds: int = 15,
) -> Path:
    """Extract a clean 22.05kHz mono WAV clip (up to max_seconds) for XTTS-v2 speaker cloning."""
    clipped_seconds = max(3, max_seconds)
    await run_ffmpeg(
        [
            "-i",
            str(input_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "22050",
            "-t",
            str(clipped_seconds),
            "-c:a",
            "pcm_s16le",
            str(output_wav_path),
        ]
    )
    if not output_wav_path.exists() or output_wav_path.stat().st_size == 0:
        raise AudioProcessingError("Prepared speaker reference WAV file is empty.")
    return output_wav_path


async def finalize_telegram_voice(chunk_paths: list[Path], output_ogg_path: Path) -> Path:
    """Concatenate synthesized audio chunks (if >1) and encode as Telegram-native OGG Opus."""
    if not chunk_paths:
        raise AudioProcessingError("No synthesized audio chunks to encode.")

    if len(chunk_paths) == 1:
        await run_ffmpeg(
            [
                "-i",
                str(chunk_paths[0]),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "48000",
                "-c:a",
                "libopus",
                "-b:a",
                "48k",
                str(output_ogg_path),
            ]
        )
    else:
        args: list[str] = []
        stream_labels: list[str] = []
        for idx, path in enumerate(chunk_paths):
            args.extend(["-i", str(path)])
            stream_labels.append(f"[{idx}:a]")

        filter_expr = f"{''.join(stream_labels)}concat=n={len(chunk_paths)}:v=0:a=1[outa]"
        args.extend(
            [
                "-filter_complex",
                filter_expr,
                "-map",
                "[outa]",
                "-ac",
                "1",
                "-ar",
                "48000",
                "-c:a",
                "libopus",
                "-b:a",
                "48k",
                str(output_ogg_path),
            ]
        )
        await run_ffmpeg(args)

    if not output_ogg_path.exists() or output_ogg_path.stat().st_size == 0:
        raise AudioProcessingError("Final Telegram OGG voice file is empty.")
    return output_ogg_path
