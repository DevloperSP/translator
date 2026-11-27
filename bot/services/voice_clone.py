"""Same-voice cloning TTS service backed by Replicate XTTS-v2."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import httpx
import replicate

from bot.config import DEFAULT_XTTS_MODEL
from bot.services.text_chunker import split_text_for_tts

logger = logging.getLogger(__name__)


class VoiceCloneError(RuntimeError):
    """Raised when Replicate XTTS-v2 voice cloning fails."""


class ReplicateVoiceCloneService:
    """Synthesizes translated speech in the user's own voice using Replicate XTTS-v2."""

    def __init__(
        self,
        api_token: str,
        *,
        model: str = DEFAULT_XTTS_MODEL,
        cleanup_voice: bool = True,
        max_chunk_chars: int = 240,
        client: Any | None = None,
    ) -> None:
        self.model = model
        self.cleanup_voice = cleanup_voice
        self.max_chunk_chars = max_chunk_chars
        self.client = client or replicate.Client(
            api_token=api_token,
            timeout=httpx.Timeout(120.0, connect=15.0),
        )

    async def _extract_audio_bytes(self, output: Any) -> bytes:
        """Normalize Replicate output (`FileOutput`, `list`, `str` URL, or `bytes`)."""
        item = output[0] if isinstance(output, (list, tuple)) and output else output

        if isinstance(item, (bytes, bytearray)):
            return bytes(item)

        if hasattr(item, "aread"):
            return bytes(await item.aread())

        if hasattr(item, "read"):
            return bytes(item.read())

        url = getattr(item, "url", None) or (item if isinstance(item, str) else None)
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            async with httpx.AsyncClient(timeout=60.0) as http:
                resp = await http.get(url)
                resp.raise_for_status()
                return resp.content

        raise VoiceCloneError(f"Unexpected Replicate XTTS output type: {type(output)!r}")

    async def synthesize_chunks(
        self,
        *,
        text: str,
        language_code: str,
        speaker_wav_path: Path,
        work_dir: Path,
    ) -> list[Path]:
        """Clone speaker voice from `speaker_wav_path` and speak `text` in `language_code`."""
        chunks = split_text_for_tts(text, max_chars=self.max_chunk_chars)
        if not chunks:
            raise VoiceCloneError("Cannot synthesize empty translated text.")

        uploaded_file: Any | None = None
        speaker_input: Any = speaker_wav_path

        try:
            # Upload speaker reference once and reuse its URL across all chunks.
            if hasattr(self.client, "files") and hasattr(self.client.files, "async_create"):
                with speaker_wav_path.open("rb") as f:
                    uploaded_file = await self.client.files.async_create(f)
                urls = getattr(uploaded_file, "urls", None) or {}
                if isinstance(urls, dict) and urls.get("get"):
                    speaker_input = urls["get"]

            output_paths: list[Path] = []
            for idx, chunk in enumerate(chunks):
                output = await self.client.async_run(
                    self.model,
                    input={
                        "text": chunk,
                        "speaker": speaker_input,
                        "language": language_code,
                        "cleanup_voice": self.cleanup_voice,
                    },
                )
                audio_bytes = await self._extract_audio_bytes(output)
                if not audio_bytes:
                    raise VoiceCloneError(f"Replicate returned empty audio for chunk {idx}.")
                chunk_path = work_dir / f"tts_chunk_{idx:03d}.wav"
                chunk_path.write_bytes(audio_bytes)
                output_paths.append(chunk_path)

            return output_paths
        except VoiceCloneError:
            raise
        except Exception as exc:
            raise VoiceCloneError(f"Replicate XTTS-v2 error: {exc}") from exc
        finally:
            if uploaded_file is not None and getattr(uploaded_file, "id", None):
                try:
                    await self.client.files.async_delete(uploaded_file.id)
                except Exception as cleanup_exc:
                    logger.debug(
                        "Ignoring Replicate temp file cleanup error for %s: %s",
                        uploaded_file.id,
                        cleanup_exc,
                    )
