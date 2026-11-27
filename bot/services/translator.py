"""Multilingual text translation service backed by OpenAI Chat Completions."""

from __future__ import annotations

from openai import AsyncOpenAI

from bot.languages import Language


class TranslationError(RuntimeError):
    """Raised when translation fails or returns empty text."""


_SYSTEM_PROMPT = (
    "You are an expert spoken-language translator for voice messages.\n"
    "Translate the user's spoken transcript accurately into the requested target language.\n"
    "Rules:\n"
    "1. Preserve the speaker's natural conversational tone, intent, names, and numbers.\n"
    "2. Always write in the target language's standard native script "
    "(e.g., Devanagari for Hindi, Arabic script for Arabic, German orthography for German, "
    "Simplified Chinese for Chinese) — never transliterate into Latin/Roman script unless "
    "the target language itself uses Latin script.\n"
    "3. Keep natural sentence punctuation so a Text-to-Speech engine reads it smoothly.\n"
    "4. If the transcript is already in the target language, return it cleanly in that language "
    "with only minimal punctuation cleanup if needed.\n"
    "5. Output ONLY the final translated text — no quotes, no prefixes, no explanations."
)


class OpenAITranslationService:
    """Translates spoken transcripts into XTTS-compatible target language text."""

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "gpt-4o-mini",
        client: AsyncOpenAI | None = None,
    ) -> None:
        self.model = model
        self.client = client or AsyncOpenAI(api_key=api_key, timeout=45.0)

    async def translate(
        self,
        text: str,
        target: Language,
        *,
        source_language: str | None = None,
    ) -> str:
        """Translate `text` into `target` language."""
        cleaned = text.strip()
        if not cleaned:
            raise TranslationError("Cannot translate empty transcript.")

        source_hint = f"Detected source language: {source_language}\n" if source_language else ""
        user_prompt = (
            f"{source_hint}"
            f"Target language: {target.name} ({target.native_name}, code={target.code})\n"
            f"Transcript:\n{cleaned}"
        )

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                temperature=0.2,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
            )
        except Exception as exc:
            raise TranslationError(f"OpenAI translation error: {exc}") from exc

        translated = (response.choices[0].message.content or "").strip()
        if not translated:
            raise TranslationError("Translation service returned empty text.")
        return translated

    async def aclose(self) -> None:
        await self.client.close()
