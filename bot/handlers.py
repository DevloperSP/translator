"""Telegram command, voice/audio, and callback query handlers."""

from __future__ import annotations

import html
import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path

from telegram import Message, Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import ContextTypes

from bot.config import Settings
from bot.keyboards import (
    build_job_keyboard,
    build_post_text_keyboard,
    build_post_voice_keyboard,
    build_preferences_keyboard,
)
from bot.languages import Language, get_language
from bot.services.audio import (
    AudioProcessingError,
    finalize_telegram_voice,
    prepare_speaker_reference,
    prepare_stt_audio,
)
from bot.services.stt import OpenAISTTService, TranscriptionError
from bot.services.translator import OpenAITranslationService, TranslationError
from bot.services.voice_clone import ReplicateVoiceCloneService, VoiceCloneError
from bot.state import JobStore, UserPrefsStore, VoiceJob

logger = logging.getLogger(__name__)

TELEGRAM_CAPTION_LIMIT = 1024
TELEGRAM_TEXT_LIMIT = 4096


@dataclass(slots=True)
class BotServices:
    """Container for runtime services stored in `application.bot_data`."""

    settings: Settings
    stt: OpenAISTTService
    translator: OpenAITranslationService
    voice_clone: ReplicateVoiceCloneService
    jobs: JobStore
    prefs: UserPrefsStore


def _get_services(context: ContextTypes.DEFAULT_TYPE) -> BotServices:
    return context.application.bot_data["services"]


def _format_job_prompt(job: VoiceJob) -> str:
    """Build concise prompt text above the language picker buttons."""
    duration_label = f"{job.duration_seconds}s" if job.duration_seconds > 0 else "audio"
    if job.is_forwarded:
        header = f"📩 <b>Forwarded voice detected</b> ({duration_label})."
    else:
        header = f"🎙️ <b>Voice note received</b> ({duration_label})."

    if job.mode == "clone":
        action_hint = "Choose target language — I will translate &amp; speak in the <b>same voice</b>:"
    else:
        action_hint = "Choose target language for <b>text translation</b>:"

    lines = [header, action_hint]
    if not job.is_forwarded and 0 < job.duration_seconds < 3:
        lines.append("💡 <i>Tip: 5–15s clear voice gives the best same-voice match.</i>")
    return "\n".join(lines)


def _format_translation_html(
    *,
    source_text: str,
    source_language: str | None,
    translated_text: str,
    target: Language,
    max_length: int,
) -> str:
    """Format original + translated text safely in HTML within Telegram length limits."""
    src_label = f" ({html.escape(source_language.title())})" if source_language else ""
    escaped_src = html.escape(source_text.strip())
    escaped_tgt = html.escape(translated_text.strip())
    target_header = f"{target.flag} <b>{html.escape(target.name)}:</b>"

    full = f"🗣 <b>Original{src_label}:</b>\n{escaped_src}\n\n{target_header}\n{escaped_tgt}"
    if len(full) <= max_length:
        return full

    # Fit translated text first when caption/message space is tight.
    tgt_only = f"{target_header}\n{escaped_tgt}"
    if len(tgt_only) <= max_length:
        return tgt_only

    return tgt_only[: max_length - 3] + "..."


def _extract_audio_meta(message: Message) -> tuple[str, str, int, int | None] | None:
    """Extract (file_id, safe_filename, duration_seconds, file_size) from voice/audio/video_note."""
    if message.voice:
        return (
            message.voice.file_id,
            "voice.ogg",
            int(message.voice.duration or 0),
            message.voice.file_size,
        )
    if message.audio:
        raw_name = message.audio.file_name or "audio.mp3"
        ext = Path(raw_name).suffix or ".mp3"
        return (
            message.audio.file_id,
            f"input{ext}",
            int(message.audio.duration or 0),
            message.audio.file_size,
        )
    if message.video_note:
        return (
            message.video_note.file_id,
            "video_note.mp4",
            int(message.video_note.duration or 0),
            message.video_note.file_size,
        )
    return None


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send welcome guide explaining Forwarded vs Recorded voice flows."""
    if not update.effective_message:
        return
    services = _get_services(context)
    pref_code = (
        services.prefs.get_preferred_language(update.effective_user.id)
        if update.effective_user
        else None
    )
    pref_lang = get_language(pref_code) if pref_code else None
    pref_line = (
        f"Current quick-default language: {pref_lang.flag} <b>{pref_lang.name}</b>"
        if pref_lang
        else "Current language mode: <b>Ask every time</b>"
    )

    text = (
        "👋 <b>Welcome to Voice Translator &amp; Same-Voice Bot!</b>\n\n"
        "Here is what I can do:\n"
        "1️⃣ <b>Forward a voice message</b> → I transcribe &amp; translate it to "
        "🇬🇧 English, 🇮🇳 Hindi, 🇸🇦 Arabic, 🇩🇪 German (and 13+ more languages), "
        "plus give you a 1-tap button to hear it in the same voice.\n"
        "2️⃣ <b>Record &amp; send a voice note</b> → Pick your target language and I will "
        "translate and send back a voice note <b>in your own voice</b>!\n\n"
        f"{pref_line}\n"
        "Use /lang anytime to set or clear a default language, or /help for tips."
    )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send usage tips and supported languages."""
    if not update.effective_message:
        return
    text = (
        "ℹ️ <b>How to use this bot</b>\n\n"
        "• <b>Forwarded voice/audio:</b> Defaults to fast text translation; tap "
        "<i>🔊 Hear in Same Voice</i> if you also want voice-cloned audio.\n"
        "• <b>Recorded voice note:</b> Defaults to <b>Same-Voice Translation</b> (XTTS-v2). "
        "Speak clearly for 5–15 seconds for the closest voice match.\n"
        "• <b>Toggle mode:</b> Use the button below the language list to switch between "
        "<i>Same Voice</i> and <i>Text Only</i> anytime.\n"
        "• <b>Commands:</b> /start, /lang, /help"
    )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)


async def lang_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Let user set a quick-default target language or choose 'Ask Every Time'."""
    if not update.effective_message or not update.effective_user:
        return
    services = _get_services(context)
    current = services.prefs.get_preferred_language(update.effective_user.id)
    current_lang = get_language(current) if current else None
    status = (
        f"{current_lang.flag} <b>{current_lang.name}</b>"
        if current_lang
        else "<b>Ask Every Time</b>"
    )
    await update.effective_message.reply_text(
        f"⚙️ <b>Default Language Preference</b>\nCurrent: {status}\n\n"
        "Pick a default language for automatic translation, or keep <i>Ask Every Time</i>:",
        parse_mode=ParseMode.HTML,
        reply_markup=build_preferences_keyboard(current),
    )


async def handle_audio_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming voice notes, audio files, and video notes."""
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return

    meta = _extract_audio_meta(message)
    if meta is None:
        return

    services = _get_services(context)
    file_id, file_name, duration_seconds, file_size_bytes = meta

    if file_size_bytes and file_size_bytes > services.settings.max_audio_file_bytes:
        await message.reply_text(
            f"⚠️ This audio file is too large (max {services.settings.max_audio_file_mb} MB)."
        )
        return

    is_forwarded = getattr(message, "forward_origin", None) is not None
    job = services.jobs.create(
        chat_id=message.chat_id,
        user_id=user.id,
        file_id=file_id,
        file_name=file_name,
        duration_seconds=duration_seconds,
        file_size_bytes=file_size_bytes,
        is_forwarded=is_forwarded,
    )

    pref_code = services.prefs.get_preferred_language(user.id)
    pref_lang = get_language(pref_code) if pref_code else None
    if pref_lang is not None:
        status_msg = await message.reply_text(
            f"⏳ Translating to {pref_lang.flag} <b>{html.escape(pref_lang.name)}</b>...",
            parse_mode=ParseMode.HTML,
            do_quote=True,
        )
        await _run_job_locked(
            anchor_message=message,
            status_message=status_msg,
            job=job,
            target=pref_lang,
            run_clone=(job.mode == "clone"),
            services=services,
            context=context,
        )
        return

    await message.reply_text(
        _format_job_prompt(job),
        parse_mode=ParseMode.HTML,
        reply_markup=build_job_keyboard(job),
        do_quote=True,
    )


async def _run_job_locked(
    *,
    anchor_message: Message,
    status_message: Message,
    job: VoiceJob,
    target: Language,
    run_clone: bool,
    services: BotServices,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Execute transcription, translation, and optional voice cloning under per-job lock."""
    async with job.lock:
        try:
            await _process_job(
                anchor_message=anchor_message,
                status_message=status_message,
                job=job,
                target=target,
                run_clone=run_clone,
                services=services,
                context=context,
            )
        except (TranscriptionError, TranslationError, AudioProcessingError, VoiceCloneError) as exc:
            logger.warning("Pipeline error for job %s: %s", job.job_id, exc)
            await _safe_edit_status(
                status_message,
                f"⚠️ {html.escape(str(exc))}",
                reply_markup=build_job_keyboard(job),
            )
        except Exception:
            logger.exception("Unexpected failure processing job %s", job.job_id)
            await _safe_edit_status(
                status_message,
                "⚠️ Something went wrong while processing this voice message. Please try again.",
                reply_markup=build_job_keyboard(job),
            )


async def _safe_edit_status(
    message: Message,
    text: str,
    *,
    reply_markup=None,
) -> None:
    try:
        await message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
    except Exception:
        logger.debug("Could not edit status message %s", message.message_id, exc_info=True)


async def _process_job(
    *,
    anchor_message: Message,
    status_message: Message,
    job: VoiceJob,
    target: Language,
    run_clone: bool,
    services: BotServices,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    bot = context.bot

    with tempfile.TemporaryDirectory(prefix="tg_tr_") as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)
        raw_audio_path: Path | None = None

        async def ensure_downloaded() -> Path:
            nonlocal raw_audio_path
            if raw_audio_path is not None and raw_audio_path.exists():
                return raw_audio_path
            dest = tmp_dir / job.file_name
            tg_file = await bot.get_file(job.file_id)
            await tg_file.download_to_drive(custom_path=dest)
            raw_audio_path = dest
            return dest

        # 1. Speech-to-Text (cached across languages for same voice job)
        if job.source_text is None:
            await bot.send_chat_action(chat_id=job.chat_id, action=ChatAction.TYPING)
            await _safe_edit_status(
                status_message,
                f"⏳ Transcribing audio &amp; translating to {target.flag} <b>{html.escape(target.name)}</b>...",
            )
            src_path = await ensure_downloaded()
            stt_mp3 = await prepare_stt_audio(src_path, tmp_dir / "stt_input.mp3")
            stt_result = await services.stt.transcribe(stt_mp3)
            job.source_text = stt_result.text
            job.source_language = stt_result.detected_language

        # 2. Translation (cached per target language)
        translated = job.translations.get(target.code)
        if translated is None:
            await bot.send_chat_action(chat_id=job.chat_id, action=ChatAction.TYPING)
            await _safe_edit_status(
                status_message,
                f"⏳ Translating to {target.flag} <b>{html.escape(target.name)}</b>...",
            )
            translated = await services.translator.translate(
                job.source_text,
                target,
                source_language=job.source_language,
            )
            job.translations[target.code] = translated

        # 3a. Text-only path (e.g. default for forwarded voice)
        if not run_clone:
            body = _format_translation_html(
                source_text=job.source_text,
                source_language=job.source_language,
                translated_text=translated,
                target=target,
                max_length=TELEGRAM_TEXT_LIMIT,
            )
            await _safe_edit_status(
                status_message,
                body,
                reply_markup=build_post_text_keyboard(job.job_id, target),
            )
            return

        # 3b. Same-Voice Cloning path (XTTS-v2 on Replicate)
        await bot.send_chat_action(chat_id=job.chat_id, action=ChatAction.RECORD_VOICE)
        await _safe_edit_status(
            status_message,
            f"🎙️ Generating <b>same-voice</b> audio in {target.flag} <b>{html.escape(target.name)}</b>...\n"
            "<i>(This usually takes 10–25 seconds)</i>",
        )

        src_path = await ensure_downloaded()
        speaker_wav = await prepare_speaker_reference(
            src_path,
            tmp_dir / "speaker_ref.wav",
            max_seconds=services.settings.speaker_ref_max_seconds,
        )
        chunk_paths = await services.voice_clone.synthesize_chunks(
            text=translated,
            language_code=target.code,
            speaker_wav_path=speaker_wav,
            work_dir=tmp_dir,
        )
        final_ogg = await finalize_telegram_voice(chunk_paths, tmp_dir / "translated_voice.ogg")
        voice_bytes = final_ogg.read_bytes()

        full_text = _format_translation_html(
            source_text=job.source_text,
            source_language=job.source_language,
            translated_text=translated,
            target=target,
            max_length=TELEGRAM_TEXT_LIMIT,
        )

        post_kb = build_post_voice_keyboard(job.job_id)
        if len(full_text) <= TELEGRAM_CAPTION_LIMIT:
            await anchor_message.reply_voice(
                voice=voice_bytes,
                filename=f"translated_{target.code}.ogg",
                caption=full_text,
                parse_mode=ParseMode.HTML,
                reply_markup=post_kb,
                do_quote=True,
            )
        else:
            await anchor_message.reply_voice(
                voice=voice_bytes,
                filename=f"translated_{target.code}.ogg",
                caption=f"{target.flag} <b>{html.escape(target.name)}</b> (Same Voice)",
                parse_mode=ParseMode.HTML,
                do_quote=True,
            )
            await anchor_message.reply_text(
                full_text,
                parse_mode=ParseMode.HTML,
                reply_markup=post_kb,
                do_quote=True,
            )

        try:
            await status_message.delete()
        except Exception:
            logger.debug("Could not delete temporary status message", exc_info=True)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline keyboard button taps."""
    query = update.callback_query
    if not query or not query.data:
        return

    services = _get_services(context)
    parts = query.data.split(":")
    action = parts[0]

    # Preference selection (/lang)
    if action == "pf" and len(parts) == 2:
        await query.answer()
        val = parts[1]
        user_id = query.from_user.id
        if val == "ask":
            services.prefs.set_preferred_language(user_id, None)
            chosen_label = "<b>Ask Every Time</b>"
        else:
            lang = get_language(val)
            if lang is None:
                await query.answer("Unsupported language.", show_alert=True)
                return
            services.prefs.set_preferred_language(user_id, lang.code)
            chosen_label = f"{lang.flag} <b>{html.escape(lang.name)}</b>"

        await _safe_edit_status(
            query.message,  # type: ignore[arg-type]
            f"⚙️ <b>Default Language Preference</b>\nUpdated to: {chosen_label}",
            reply_markup=build_preferences_keyboard(None if val == "ask" else val),
        )
        return

    if len(parts) < 2:
        await query.answer("Invalid action.", show_alert=True)
        return

    job_id = parts[1]
    job = services.jobs.get(job_id)
    if job is None:
        await query.answer("Session expired. Please send or forward the voice note again.", show_alert=True)
        return

    if query.from_user.id != job.user_id:
        await query.answer("Only the sender of this voice note can control these buttons.", show_alert=True)
        return

    if action == "md" and len(parts) == 3:
        new_mode = parts[2]
        if new_mode not in {"text", "clone"}:
            await query.answer("Invalid mode.")
            return
        job.mode = new_mode  # type: ignore[assignment]
        await query.answer(f"Mode set to {'Same Voice' if new_mode == 'clone' else 'Text Only'}")
        await _safe_edit_status(
            query.message,  # type: ignore[arg-type]
            _format_job_prompt(job),
            reply_markup=build_job_keyboard(job),
        )
        return

    if action == "mr" and len(parts) == 3:
        job.show_all_languages = parts[2] == "1"
        await query.answer()
        try:
            await query.edit_message_reply_markup(reply_markup=build_job_keyboard(job))
        except Exception:
            logger.debug("Failed to toggle language list markup", exc_info=True)
        return

    if action == "ag":
        await query.answer()
        anchor = query.message.reply_to_message or query.message  # type: ignore[union-attr]
        await anchor.reply_text(
            _format_job_prompt(job),
            parse_mode=ParseMode.HTML,
            reply_markup=build_job_keyboard(job),
            do_quote=True,
        )
        return

    if action in {"tr", "cl"} and len(parts) == 3:
        lang = get_language(parts[2])
        if lang is None:
            await query.answer("Unsupported language.", show_alert=True)
            return

        if job.lock.locked():
            await query.answer("Already processing this audio, please wait...", show_alert=True)
            return

        await query.answer(f"Processing {lang.name}...")
        run_clone = True if action == "cl" else (job.mode == "clone")

        anchor = query.message.reply_to_message or query.message  # type: ignore[union-attr]
        # If tapping "cl" from a text translation result, spawn a fresh status message so the text stays intact.
        if action == "cl":
            status_msg = await anchor.reply_text(
                f"🎙️ Preparing <b>same-voice</b> audio in {lang.flag} <b>{html.escape(lang.name)}</b>...",
                parse_mode=ParseMode.HTML,
                do_quote=True,
            )
        else:
            status_msg = query.message  # type: ignore[assignment]

        await _run_job_locked(
            anchor_message=anchor,
            status_message=status_msg,
            job=job,
            target=lang,
            run_clone=run_clone,
            services=services,
            context=context,
        )
        return

    await query.answer("Unsupported action.", show_alert=True)
