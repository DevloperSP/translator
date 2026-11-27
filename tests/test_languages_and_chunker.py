"""Tests for language definitions, callback byte limits, and TTS text chunker."""

from bot.keyboards import build_job_keyboard, build_post_text_keyboard, build_preferences_keyboard
from bot.languages import ALL_LANGUAGES, FEATURED_LANGUAGES, get_language
from bot.services.text_chunker import split_text_for_tts
from bot.state import VoiceJob


def test_required_languages_present():
    codes = {lang.code for lang in ALL_LANGUAGES}
    assert {"en", "hi", "ar", "de", "zh-cn"}.issubset(codes)
    assert [lang.code for lang in FEATURED_LANGUAGES] == ["en", "hi", "ar", "de"]
    assert get_language("HI").name == "Hindi"
    assert get_language("unknown") is None


def test_all_callback_data_within_telegram_64_bytes():
    job = VoiceJob(
        job_id="a1b2c3d4e5",
        chat_id=1,
        user_id=2,
        file_id="file_123",
        file_name="voice.ogg",
        duration_seconds=10,
        file_size_bytes=12345,
        is_forwarded=False,
        mode="clone",
        show_all_languages=True,
    )
    for kb in (
        build_job_keyboard(job),
        build_post_text_keyboard(job.job_id, get_language("zh-cn")),  # type: ignore[arg-type]
        build_preferences_keyboard("hi"),
    ):
        for row in kb.inline_keyboard:
            for btn in row:
                assert btn.callback_data is not None
                assert len(btn.callback_data.encode("utf-8")) <= 64


def test_text_chunker_multilingual_sentences_and_bounds():
    hindi_text = (
        "नमस्ते दोस्तों, आज मौसम बहुत सुहावना है। "
        "हम सब मिलकर एक नया प्रोजेक्ट बना रहे हैं। "
        "क्या आप इस आवाज़ को पहचान सकते हैं?"
    )
    chunks = split_text_for_tts(hindi_text, max_chars=75)
    assert len(chunks) >= 2
    assert all(len(c) <= 75 for c in chunks)
    assert "नमस्ते" in chunks[0]
    assert "पहचान" in chunks[-1]


def test_text_chunker_handles_long_clause_and_arabic():
    arabic = "مرحباً بكم في هذا الاختبار، " * 15 + "كيف حالكم اليوم؟"
    chunks = split_text_for_tts(arabic, max_chars=80)
    assert len(chunks) > 2
    assert all(len(c) <= 80 for c in chunks)


def test_text_chunker_handles_empty_and_spaceless():
    assert split_text_for_tts("   ") == []
    cjk = "你好" * 60
    chunks = split_text_for_tts(cjk, max_chars=50)
    assert "".join(chunks) == cjk
    assert all(len(c) <= 50 for c in chunks)
