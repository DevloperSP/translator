"""Application bootstrap supporting Heroku worker polling and webhook modes."""

from __future__ import annotations

import asyncio
import logging
import os
import signal

from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from bot.config import Settings, load_settings
from bot.handlers import (
    BotServices,
    handle_audio_message,
    handle_callback,
    help_command,
    lang_command,
    start_command,
)
from bot.services.stt import OpenAISTTService
from bot.services.translator import OpenAITranslationService
from bot.services.voice_clone import ReplicateVoiceCloneService
from bot.state import JobStore, UserPrefsStore

logger = logging.getLogger(__name__)


def build_application(settings: Settings) -> Application:
    """Create and configure the python-telegram-bot Application."""

    async def post_shutdown(app: Application) -> None:
        services: BotServices | None = app.bot_data.get("services")
        if services is not None:
            await services.stt.aclose()
            await services.translator.aclose()

    app = (
        ApplicationBuilder()
        .token(settings.telegram_bot_token)
        .concurrent_updates(True)
        .post_shutdown(post_shutdown)
        .build()
    )

    app.bot_data["services"] = BotServices(
        settings=settings,
        stt=OpenAISTTService(settings.openai_api_key, model=settings.openai_stt_model),
        translator=OpenAITranslationService(
            settings.openai_api_key, model=settings.openai_translate_model
        ),
        voice_clone=ReplicateVoiceCloneService(
            settings.replicate_api_token,
            model=settings.replicate_xtts_model,
            cleanup_voice=settings.xtts_cleanup_voice,
            max_chunk_chars=settings.max_tts_chunk_chars,
        ),
        jobs=JobStore(),
        prefs=UserPrefsStore(),
    )

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("lang", lang_command))
    app.add_handler(
        MessageHandler(
            filters.VOICE | filters.AUDIO | filters.VIDEO_NOTE,
            handle_audio_message,
        )
    )
    app.add_handler(CallbackQueryHandler(handle_callback))
    return app


async def _run_polling_with_health_server(app: Application, port: int) -> None:
    """Fallback when scaled as a Heroku `web` dyno without WEBHOOK_URL set (avoids R10)."""

    async def handle_health(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            await reader.read(1024)
            body = b"OK - Telegram Voice Translator Bot running in polling mode\n"
            response = (
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: text/plain; charset=utf-8\r\n"
                + f"Content-Length: {len(body)}\r\n".encode("ascii")
                + b"Connection: close\r\n\r\n"
                + body
            )
            writer.write(response)
            await writer.drain()
        finally:
            writer.close()

    server = await asyncio.start_server(handle_health, host="0.0.0.0", port=port)
    logger.warning(
        "Running on Heroku web dyno without WEBHOOK_URL: started health endpoint on port %s "
        "alongside polling. For production, either scale `worker=1 web=0` or set WEBHOOK_URL.",
        port,
    )

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    async with server:
        await app.initialize()
        await app.start()
        assert app.updater is not None
        await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        try:
            await stop_event.wait()
        finally:
            await app.updater.stop()
            await app.stop()
            await app.shutdown()


def main() -> None:
    """Entrypoint for local development and Heroku worker/web dynos."""
    settings = load_settings(require_secrets=True)
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    application = build_application(settings)

    if settings.webhook_url:
        full_webhook_url = f"{settings.webhook_url}/{settings.webhook_path}"
        logger.info("Starting bot in webhook mode at %s (port=%s)", full_webhook_url, settings.port)
        application.run_webhook(
            listen="0.0.0.0",
            port=settings.port,
            url_path=settings.webhook_path,
            webhook_url=full_webhook_url,
            secret_token=settings.effective_webhook_secret,
            allowed_updates=Update.ALL_TYPES,
        )
        return

    dyno_name = os.getenv("DYNO", "")
    if dyno_name.startswith("web."):
        try:
            asyncio.run(_run_polling_with_health_server(application, settings.port))
        except KeyboardInterrupt:
            pass
        return

    logger.info("Starting bot in long-polling mode...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
