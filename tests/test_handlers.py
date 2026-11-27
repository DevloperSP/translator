"""End-to-end mocked Telegram handler tests for forwarded & recorded flows."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.config import Settings
from bot.handlers import (
    BotServices,
    handle_audio_message,
    handle_callback,
    lang_command,
    start_command,
)
from bot.services.audio import run_ffmpeg
from bot.services.stt import TranscriptionResult
from bot.state import JobStore, UserPrefsStore


def _make_settings() -> Settings:
    return Settings(
        telegram_bot_token="123:abc",
        openai_api_key="sk-test",
        replicate_api_token="r8-test",
    )


class _FakeMessage:
    def __init__(
        self,
        *,
        message_id: int = 10,
        chat_id: int = 100,
        voice=None,
        forward_origin=None,
        reply_to_message=None,
    ):
        self.message_id = message_id
        self.chat_id = chat_id
        self.voice = voice
        self.audio = None
        self.video_note = None
        self.forward_origin = forward_origin
        self.reply_to_message = reply_to_message
        self.replies: list[dict] = []
        self.voice_replies: list[dict] = []
        self.edits: list[dict] = []
        self.deleted = False

    async def reply_text(self, text, **kwargs):
        msg = _FakeMessage(
            message_id=self.message_id + len(self.replies) + 1,
            chat_id=self.chat_id,
            reply_to_message=self,
        )
        self.replies.append({"text": text, "kwargs": kwargs, "message": msg})
        return msg

    async def reply_voice(self, voice, **kwargs):
        msg = _FakeMessage(
            message_id=self.message_id + len(self.voice_replies) + 100,
            chat_id=self.chat_id,
            reply_to_message=self,
        )
        self.voice_replies.append({"voice": voice, "kwargs": kwargs, "message": msg})
        return msg

    async def edit_text(self, text, **kwargs):
        self.edits.append({"text": text, "kwargs": kwargs})
        return self

    async def edit_reply_markup(self, **kwargs):
        self.edits.append({"markup": kwargs.get("reply_markup")})
        return self

    async def delete(self):
        self.deleted = True


class _FakeBot:
    def __init__(self, source_audio_bytes: bytes):
        self.source_audio_bytes = source_audio_bytes
        self.chat_actions = []

    async def send_chat_action(self, chat_id: int, action: str):
        self.chat_actions.append((chat_id, action))

    async def get_file(self, _file_id: str):
        payload = self.source_audio_bytes

        class _TGFile:
            async def download_to_drive(self, custom_path: Path):
                custom_path.write_bytes(payload)

        return _TGFile()


async def _make_services(tmp_path: Path):
    src_ogg = tmp_path / "src_voice.ogg"
    clone_wav = tmp_path / "clone_chunk.wav"
    await run_ffmpeg(
        ["-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:a", "libopus", str(src_ogg)]
    )
    await run_ffmpeg(
        ["-f", "lavfi", "-i", "sine=frequency=660:duration=1", "-ar", "24000", str(clone_wav)]
    )

    stt = SimpleNamespace(
        transcribe=AsyncMock(
            return_value=TranscriptionResult(text="How are you doing?", detected_language="english")
        )
    )
    translator = SimpleNamespace(
        translate=AsyncMock(return_value="आप कैसे हैं?")
    )

    async def fake_synthesize(*, text, language_code, speaker_wav_path, work_dir):
        out = work_dir / "out.wav"
        out.write_bytes(clone_wav.read_bytes())
        return [out]

    voice_clone = SimpleNamespace(synthesize_chunks=AsyncMock(side_effect=fake_synthesize))

    services = BotServices(
        settings=_make_settings(),
        stt=stt,  # type: ignore[arg-type]
        translator=translator,  # type: ignore[arg-type]
        voice_clone=voice_clone,  # type: ignore[arg-type]
        jobs=JobStore(),
        prefs=UserPrefsStore(),
    )
    bot = _FakeBot(src_ogg.read_bytes())
    context = SimpleNamespace(application=SimpleNamespace(bot_data={"services": services}), bot=bot)
    return services, context


async def test_forwarded_voice_text_then_one_tap_same_voice(tmp_path: Path):
    services, context = await _make_services(tmp_path)
    user = SimpleNamespace(id=42)
    fwd_msg = _FakeMessage(
        voice=SimpleNamespace(file_id="f_fwd", duration=6, file_size=5000),
        forward_origin=SimpleNamespace(type="user"),
    )

    await handle_audio_message(
        SimpleNamespace(effective_message=fwd_msg, effective_user=user),  # type: ignore[arg-type]
        context,  # type: ignore[arg-type]
    )
    assert len(fwd_msg.replies) == 1
    prompt_msg = fwd_msg.replies[0]["message"]
    assert "Forwarded voice detected" in fwd_msg.replies[0]["text"]

    job_id = next(iter(services.jobs._jobs))
    # Tap Hindi -> text translation first
    cb1 = SimpleNamespace(
        data=f"tr:{job_id}:hi",
        from_user=user,
        message=prompt_msg,
        answer=AsyncMock(),
    )
    await handle_callback(SimpleNamespace(callback_query=cb1), context)  # type: ignore[arg-type]

    assert services.stt.transcribe.await_count == 1
    assert services.translator.translate.await_count == 1
    assert services.voice_clone.synthesize_chunks.await_count == 0
    assert "आप कैसे हैं?" in prompt_msg.edits[-1]["text"]

    # Now tap "Hear in Same Voice" (cl:<job_id>:hi) -> skips STT & Translate!
    cb2 = SimpleNamespace(
        data=f"cl:{job_id}:hi",
        from_user=user,
        message=prompt_msg,
        answer=AsyncMock(),
    )
    await handle_callback(SimpleNamespace(callback_query=cb2), context)  # type: ignore[arg-type]

    assert services.stt.transcribe.await_count == 1
    assert services.translator.translate.await_count == 1
    assert services.voice_clone.synthesize_chunks.await_count == 1
    assert len(fwd_msg.voice_replies) == 1
    assert fwd_msg.voice_replies[0]["kwargs"]["filename"] == "translated_hi.ogg"


async def test_recorded_voice_defaults_to_same_voice_and_rejects_other_user(tmp_path: Path):
    services, context = await _make_services(tmp_path)
    owner = SimpleNamespace(id=7)
    stranger = SimpleNamespace(id=999)
    rec_msg = _FakeMessage(
        voice=SimpleNamespace(file_id="f_rec", duration=9, file_size=7000),
        forward_origin=None,
    )

    await handle_audio_message(
        SimpleNamespace(effective_message=rec_msg, effective_user=owner),  # type: ignore[arg-type]
        context,  # type: ignore[arg-type]
    )
    prompt_msg = rec_msg.replies[0]["message"]
    assert "Voice note received" in rec_msg.replies[0]["text"]
    job_id = next(iter(services.jobs._jobs))

    # Stranger cannot tap owner's buttons
    stranger_cb = SimpleNamespace(
        data=f"tr:{job_id}:de",
        from_user=stranger,
        message=prompt_msg,
        answer=AsyncMock(),
    )
    await handle_callback(SimpleNamespace(callback_query=stranger_cb), context)  # type: ignore[arg-type]
    stranger_cb.answer.assert_awaited_once_with(
        "Only the sender of this voice note can control these buttons.", show_alert=True
    )

    # Owner taps German -> directly transcribes, translates, and clones voice
    owner_cb = SimpleNamespace(
        data=f"tr:{job_id}:de",
        from_user=owner,
        message=prompt_msg,
        answer=AsyncMock(),
    )
    await handle_callback(SimpleNamespace(callback_query=owner_cb), context)  # type: ignore[arg-type]

    assert services.voice_clone.synthesize_chunks.await_count == 1
    assert len(rec_msg.voice_replies) == 1
    assert rec_msg.voice_replies[0]["kwargs"]["filename"] == "translated_de.ogg"
    assert prompt_msg.deleted is True


async def test_start_and_lang_commands_and_size_limit(tmp_path: Path):
    services, context = await _make_services(tmp_path)
    user = SimpleNamespace(id=55)
    msg = _FakeMessage()

    await start_command(
        SimpleNamespace(effective_message=msg, effective_user=user),  # type: ignore[arg-type]
        context,  # type: ignore[arg-type]
    )
    assert "Welcome to Voice Translator" in msg.replies[0]["text"]

    await lang_command(
        SimpleNamespace(effective_message=msg, effective_user=user),  # type: ignore[arg-type]
        context,  # type: ignore[arg-type]
    )
    lang_msg = msg.replies[1]["message"]
    cb = SimpleNamespace(
        data="pf:ar",
        from_user=user,
        message=lang_msg,
        answer=AsyncMock(),
    )
    await handle_callback(SimpleNamespace(callback_query=cb), context)  # type: ignore[arg-type]
    assert services.prefs.get_preferred_language(55) == "ar"

    # Oversized file rejected cleanly
    big_msg = _FakeMessage(
        voice=SimpleNamespace(file_id="big", duration=999, file_size=50 * 1024 * 1024)
    )
    await handle_audio_message(
        SimpleNamespace(effective_message=big_msg, effective_user=user),  # type: ignore[arg-type]
        context,  # type: ignore[arg-type]
    )
    assert "too large" in big_msg.replies[0]["text"]


async def test_mode_toggle_more_languages_again_and_app_builder(tmp_path: Path, monkeypatch):
    from bot.config import load_settings
    from bot.main import build_application

    services, context = await _make_services(tmp_path)
    user = SimpleNamespace(id=88)
    rec_msg = _FakeMessage(
        voice=SimpleNamespace(file_id="f_rec2", duration=6, file_size=4000),
        forward_origin=None,
    )
    await handle_audio_message(
        SimpleNamespace(effective_message=rec_msg, effective_user=user),  # type: ignore[arg-type]
        context,  # type: ignore[arg-type]
    )
    prompt_msg = rec_msg.replies[0]["message"]
    job_id = next(iter(services.jobs._jobs))

    # Toggle mode from clone -> text
    await handle_callback(
        SimpleNamespace(
            callback_query=SimpleNamespace(
                data=f"md:{job_id}:text",
                from_user=user,
                message=prompt_msg,
                answer=AsyncMock(),
            )
        ),  # type: ignore[arg-type]
        context,  # type: ignore[arg-type]
    )
    assert services.jobs.get(job_id).mode == "text"  # type: ignore[union-attr]

    # Expand full language list
    await handle_callback(
        SimpleNamespace(
            callback_query=SimpleNamespace(
                data=f"mr:{job_id}:1",
                from_user=user,
                message=prompt_msg,
                answer=AsyncMock(),
            )
        ),  # type: ignore[arg-type]
        context,  # type: ignore[arg-type]
    )
    assert services.jobs.get(job_id).show_all_languages is True  # type: ignore[union-attr]

    # Click "Again" button -> spawns a fresh language picker
    await handle_callback(
        SimpleNamespace(
            callback_query=SimpleNamespace(
                data=f"ag:{job_id}",
                from_user=user,
                message=prompt_msg,
                answer=AsyncMock(),
            )
        ),  # type: ignore[arg-type]
        context,  # type: ignore[arg-type]
    )
    assert len(rec_msg.replies) == 2

    # Smoke-test Settings and Application builder
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:test-token")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("REPLICATE_API_TOKEN", "r8-test")
    monkeypatch.setenv("WEBHOOK_URL", "https://example.herokuapp.com/")
    settings = load_settings(require_secrets=True)
    assert settings.webhook_url == "https://example.herokuapp.com"
    assert len(settings.effective_webhook_secret) == 32
    app = build_application(settings)
    assert "services" in app.bot_data

