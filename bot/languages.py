"""Supported languages matching Replicate lucataco/xtts-v2 capabilities."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Language:
    """Target language metadata."""

    code: str
    name: str
    native_name: str
    flag: str
    featured: bool = False

    @property
    def button_label(self) -> str:
        return f"{self.flag} {self.name}"


ALL_LANGUAGES: tuple[Language, ...] = (
    # Primary requested languages
    Language(code="en", name="English", native_name="English", flag="🇬🇧", featured=True),
    Language(code="hi", name="Hindi", native_name="हिन्दी", flag="🇮🇳", featured=True),
    Language(code="ar", name="Arabic", native_name="العربية", flag="🇸🇦", featured=True),
    Language(code="de", name="German", native_name="Deutsch", flag="🇩🇪", featured=True),
    # Additional XTTS-v2 languages
    Language(code="es", name="Spanish", native_name="Español", flag="🇪🇸"),
    Language(code="fr", name="French", native_name="Français", flag="🇫🇷"),
    Language(code="it", name="Italian", native_name="Italiano", flag="🇮🇹"),
    Language(code="pt", name="Portuguese", native_name="Português", flag="🇵🇹"),
    Language(code="ru", name="Russian", native_name="Русский", flag="🇷🇺"),
    Language(code="tr", name="Turkish", native_name="Türkçe", flag="🇹🇷"),
    Language(code="ja", name="Japanese", native_name="日本語", flag="🇯🇵"),
    Language(code="ko", name="Korean", native_name="한국어", flag="🇰🇷"),
    Language(code="zh-cn", name="Chinese", native_name="中文", flag="🇨🇳"),
    Language(code="nl", name="Dutch", native_name="Nederlands", flag="🇳🇱"),
    Language(code="pl", name="Polish", native_name="Polski", flag="🇵🇱"),
    Language(code="cs", name="Czech", native_name="Čeština", flag="🇨🇿"),
    Language(code="hu", name="Hungarian", native_name="Magyar", flag="🇭🇺"),
)

LANGUAGE_BY_CODE: dict[str, Language] = {lang.code: lang for lang in ALL_LANGUAGES}
FEATURED_LANGUAGES: tuple[Language, ...] = tuple(lang for lang in ALL_LANGUAGES if lang.featured)


def get_language(code: str) -> Language | None:
    """Return Language object for an XTTS language code, or None if unsupported."""
    return LANGUAGE_BY_CODE.get(code.strip().lower())
