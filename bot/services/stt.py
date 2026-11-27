"""Speech-to-Text service backed by OpenAI audio transcriptions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from openai import AsyncOpenAI


class TranscriptionError(RuntimeError):
    """Raised when audio transcription fails or returns empty speech."""


@dataclass(frozen=True, slots=True)
class TranscriptionResult:
    """Transcript text and optional detected language name."""

    text: str
    detected_language: str | None = None


class OpenAISTTService:
    """Transcribes audio files using OpenAI Audio Transcriptions API."""

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "whisper-1",
        client: AsyncOpenAI | None = None,
    ) -> None:
        self.model = model
        self.client = client or AsyncOpenAI(api_key=api_key, timeout=60.0)

    async def transcribe(self, audio_path: Path) -> TranscriptionResult:
        """Transcribe speech in `audio_path` and detect its source language when supported."""
        audio_bytes = audio_path.read_bytes()
        if not audio_bytes:
            raise TranscriptionError("Audio file is empty.")

        use_verbose = "whisper" in self.model.lower()
        response_format = "verbose_json" if use_verbose else "json"

        try:
            response = await self.client.audio.transcriptions.create(
                model=self.model,
                file=(audio_path.name, audio_bytes, "audio/mpeg"),
                response_format=response_format,  # type: ignore[arg-type]
            )
        except Exception as exc:
            raise TranscriptionError(f"OpenAI transcription error: {exc}") from exc

        text = (getattr(response, "text", "") or "").strip()
        if not text:
            raise TranscriptionError("Could not detect clear speech in the audio.")

        detected = getattr(response, "language", None)
        if isinstance(detected, str):
            detected = detected.strip() or None
        else:
            detected = None

        return TranscriptionResult(text=text, detected_language=detected)

    async def aclose(self) -> None:
        await self.client.close()
