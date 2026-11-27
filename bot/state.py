"""In-memory bounded TTL job and user preference stores."""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Literal

TranslationMode = Literal["text", "clone"]


@dataclass
class VoiceJob:
    """Represents a pending or cached voice/audio message waiting for actions."""

    job_id: str
    chat_id: int
    user_id: int
    file_id: str
    file_name: str
    duration_seconds: int
    file_size_bytes: int | None
    is_forwarded: bool
    mode: TranslationMode
    show_all_languages: bool = False
    source_text: str | None = None
    source_language: str | None = None
    translations: dict[str, str] = field(default_factory=dict)
    created_at: float = field(default_factory=time.monotonic)
    updated_at: float = field(default_factory=time.monotonic)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def touch(self) -> None:
        self.updated_at = time.monotonic()


class JobStore:
    """Bounded TTL cache for active voice translation jobs (Heroku RAM safe)."""

    def __init__(self, *, ttl_seconds: int = 1800, max_jobs: int = 250) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_jobs = max_jobs
        self._jobs: dict[str, VoiceJob] = {}

    def _prune(self, now: float | None = None) -> None:
        current = now if now is not None else time.monotonic()
        expired = [
            jid
            for jid, job in self._jobs.items()
            if (current - job.updated_at) > self.ttl_seconds
        ]
        for jid in expired:
            self._jobs.pop(jid, None)

        if len(self._jobs) > self.max_jobs:
            # Evict oldest updated jobs first
            ordered = sorted(self._jobs.items(), key=lambda kv: kv[1].updated_at)
            excess = len(self._jobs) - self.max_jobs
            for jid, _ in ordered[:excess]:
                self._jobs.pop(jid, None)

    def create(
        self,
        *,
        chat_id: int,
        user_id: int,
        file_id: str,
        file_name: str,
        duration_seconds: int,
        file_size_bytes: int | None,
        is_forwarded: bool,
    ) -> VoiceJob:
        self._prune()
        job_id = uuid.uuid4().hex[:10]
        default_mode: TranslationMode = "text" if is_forwarded else "clone"
        job = VoiceJob(
            job_id=job_id,
            chat_id=chat_id,
            user_id=user_id,
            file_id=file_id,
            file_name=file_name,
            duration_seconds=duration_seconds,
            file_size_bytes=file_size_bytes,
            is_forwarded=is_forwarded,
            mode=default_mode,
        )
        self._jobs[job_id] = job
        self._prune()
        return job

    def get(self, job_id: str) -> VoiceJob | None:
        self._prune()
        job = self._jobs.get(job_id)
        if job is not None:
            job.touch()
        return job

    def __len__(self) -> int:
        self._prune()
        return len(self._jobs)


class UserPrefsStore:
    """Stores per-user quick-default target language preferences."""

    def __init__(self, max_users: int = 5000) -> None:
        self.max_users = max_users
        self._preferred_lang: dict[int, str] = {}

    def get_preferred_language(self, user_id: int) -> str | None:
        return self._preferred_lang.get(user_id)

    def set_preferred_language(self, user_id: int, lang_code: str | None) -> None:
        if lang_code is None:
            self._preferred_lang.pop(user_id, None)
            return
        if len(self._preferred_lang) >= self.max_users and user_id not in self._preferred_lang:
            oldest_key = next(iter(self._preferred_lang))
            self._preferred_lang.pop(oldest_key, None)
        self._preferred_lang[user_id] = lang_code
