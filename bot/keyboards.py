"""Inline keyboards and callback data helpers."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from bot.languages import ALL_LANGUAGES, FEATURED_LANGUAGES, Language
from bot.state import VoiceJob


def make_translate_callback(job_id: str, lang_code: str) -> str:
    return f"tr:{job_id}:{lang_code}"


def make_mode_callback(job_id: str, mode: str) -> str:
    return f"md:{job_id}:{mode}"


def make_more_callback(job_id: str, show_all: bool) -> str:
    return f"mr:{job_id}:{'1' if show_all else '0'}"


def make_clone_now_callback(job_id: str, lang_code: str) -> str:
    return f"cl:{job_id}:{lang_code}"


def make_again_callback(job_id: str) -> str:
    return f"ag:{job_id}"


def make_pref_callback(value: str) -> str:
    return f"pf:{value}"


def build_job_keyboard(job: VoiceJob) -> InlineKeyboardMarkup:
    """Build language selection + mode toggle keyboard for a voice job."""
    rows: list[list[InlineKeyboardButton]] = []

    if job.show_all_languages:
        langs = ALL_LANGUAGES
        cols = 3
    else:
        langs = FEATURED_LANGUAGES
        cols = 2

    for idx in range(0, len(langs), cols):
        row = [
            InlineKeyboardButton(
                lang.button_label,
                callback_data=make_translate_callback(job.job_id, lang.code),
            )
            for lang in langs[idx : idx + cols]
        ]
        rows.append(row)

    if job.mode == "clone":
        mode_button = InlineKeyboardButton(
            "🔊 Output: Same Voice (tap for Text Only)",
            callback_data=make_mode_callback(job.job_id, "text"),
        )
    else:
        mode_button = InlineKeyboardButton(
            "📝 Output: Text Only (tap for Same Voice)",
            callback_data=make_mode_callback(job.job_id, "clone"),
        )
    rows.append([mode_button])

    more_button = (
        InlineKeyboardButton("⬆️ Main 4 Languages", callback_data=make_more_callback(job.job_id, False))
        if job.show_all_languages
        else InlineKeyboardButton(
            f"🌐 More Languages ({len(ALL_LANGUAGES) - len(FEATURED_LANGUAGES)}+)",
            callback_data=make_more_callback(job.job_id, True),
        )
    )
    rows.append([more_button])

    return InlineKeyboardMarkup(rows)


def build_post_text_keyboard(job_id: str, target: Language) -> InlineKeyboardMarkup:
    """Buttons shown under a text translation so user can clone voice in 1 tap."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"🔊 Hear in Same Voice ({target.flag} {target.name})",
                    callback_data=make_clone_now_callback(job_id, target.code),
                )
            ],
            [
                InlineKeyboardButton(
                    "🔄 Another Language",
                    callback_data=make_again_callback(job_id),
                )
            ],
        ]
    )


def build_post_voice_keyboard(job_id: str) -> InlineKeyboardMarkup:
    """Button shown under a voice-cloned translation to translate into more languages."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🔄 Translate to Another Language",
                    callback_data=make_again_callback(job_id),
                )
            ]
        ]
    )


def build_preferences_keyboard(current_code: str | None) -> InlineKeyboardMarkup:
    """Keyboard for `/lang` quick-default preference selection."""
    rows: list[list[InlineKeyboardButton]] = []
    for idx in range(0, len(FEATURED_LANGUAGES), 2):
        row = []
        for lang in FEATURED_LANGUAGES[idx : idx + 2]:
            prefix = "✅ " if current_code == lang.code else ""
            row.append(
                InlineKeyboardButton(
                    f"{prefix}{lang.button_label}",
                    callback_data=make_pref_callback(lang.code),
                )
            )
        rows.append(row)

    ask_prefix = "✅ " if current_code is None else ""
    rows.append(
        [
            InlineKeyboardButton(
                f"{ask_prefix}❓ Ask Every Time (Default)",
                callback_data=make_pref_callback("ask"),
            )
        ]
    )
    return InlineKeyboardMarkup(rows)
