"""Configuration management for the Telegram Voice Translator bot."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass

from dotenv import load_dotenv

DEFAULT_XTTS_MODEL = (
    "lucataco/xtts-v2:"
    "684bc3855b37866c0c65add2ff39c78f3dea3f4ff103a436465326e0f438d55e"
)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw.strip())
    except ValueError as exc:
        raise ValueError(f"Environment variable {name} must be an integer, got {raw!r}") from exc


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime settings loaded from environment variables."""

    telegram_bot_token: str
    openai_api_key: str
    replicate_api_token: str
    openai_stt_model: str = "whisper-1"
    openai_translate_model: str = "gpt-4o-mini"
    replicate_xtts_model: str = DEFAULT_XTTS_MODEL
    xtts_cleanup_voice: bool = True
    max_audio_file_mb: int = 20
    speaker_ref_max_seconds: int = 15
    max_tts_chunk_chars: int = 240
    webhook_url: str = ""
    webhook_secret_token: str = ""
    port: int = 8080
    log_level: str = "INFO"

    @property
    def max_audio_file_bytes(self) -> int:
        return self.max_audio_file_mb * 1024 * 1024

    @property
    def effective_webhook_secret(self) -> str:
        """Return explicit webhook secret or deterministic valid secret derived from bot token."""
        if self.webhook_secret_token:
            return self.webhook_secret_token
        return hashlib.sha256(f"tg-webhook:{self.telegram_bot_token}".encode("utf-8")).hexdigest()[:32]

    @property
    def webhook_path(self) -> str:
        """Non-secret stable webhook path segment."""
        suffix = hashlib.sha256(self.telegram_bot_token.encode("utf-8")).hexdigest()[:12]
        return f"webhook/{suffix}"


def load_settings(*, require_secrets: bool = True) -> Settings:
    """Load settings from `.env` and OS environment variables."""
    load_dotenv()

    telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    openai_api_key = os.getenv("OPENAI_API_KEY", "").strip()
    replicate_api_token = os.getenv("REPLICATE_API_TOKEN", "").strip()

    if require_secrets:
        missing = [
            name
            for name, val in (
                ("TELEGRAM_BOT_TOKEN", telegram_bot_token),
                ("OPENAI_API_KEY", openai_api_key),
                ("REPLICATE_API_TOKEN", replicate_api_token),
            )
            if not val
        ]
        if missing:
            joined = ", ".join(missing)
            raise RuntimeError(f"Missing required environment variables: {joined}")

    return Settings(
        telegram_bot_token=telegram_bot_token,
        openai_api_key=openai_api_key,
        replicate_api_token=replicate_api_token,
        openai_stt_model=os.getenv("OPENAI_STT_MODEL", "whisper-1").strip() or "whisper-1",
        openai_translate_model=os.getenv("OPENAI_TRANSLATE_MODEL", "gpt-4o-mini").strip()
        or "gpt-4o-mini",
        replicate_xtts_model=os.getenv("REPLICATE_XTTS_MODEL", DEFAULT_XTTS_MODEL).strip()
        or DEFAULT_XTTS_MODEL,
        xtts_cleanup_voice=_env_bool("XTTS_CLEANUP_VOICE", True),
        max_audio_file_mb=_env_int("MAX_AUDIO_FILE_MB", 20),
        speaker_ref_max_seconds=_env_int("SPEAKER_REF_MAX_SECONDS", 15),
        max_tts_chunk_chars=_env_int("MAX_TTS_CHUNK_CHARS", 240),
        webhook_url=os.getenv("WEBHOOK_URL", "").strip().rstrip("/"),
        webhook_secret_token=os.getenv("WEBHOOK_SECRET_TOKEN", "").strip(),
        port=_env_int("PORT", 8080),
        log_level=(os.getenv("LOG_LEVEL", "INFO").strip() or "INFO").upper(),
    )
