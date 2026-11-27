# Telegram Voice Translator & Same-Voice Bot

A lightweight, Heroku-ready Telegram bot that handles both **forwarded voice translation** and **recorded same-voice translation**:

1. **Forwarded voice/audio messages** → Automatically detects forwarded voice notes, transcribes speech with **OpenAI Whisper**, translates into your chosen language (**English, Hindi, Arabic, German**, and 13+ more), and attaches a **1-tap `🔊 Hear in Same Voice`** button.
2. **Recorded voice messages** → Choose the target language via inline buttons, and the bot translates your speech and sends back a Telegram voice note **spoken in your own voice** using **Replicate XTTS-v2** (`lucataco/xtts-v2`, pay-per-use).

---

## Features

- **17 XTTS-v2 Languages:** English (`en`), Hindi (`hi`), Arabic (`ar`), German (`de`), Spanish, French, Italian, Portuguese, Russian, Turkish, Japanese, Korean, Chinese (`zh-cn`), Dutch, Polish, Czech, Hungarian.
- **Native-Script Translation:** Translates Hindi into Devanagari and Arabic into Arabic script so XTTS-v2 pronounces accurately.
- **Smart Caching per Voice Message:** Switching to a 2nd or 3rd language reuses the transcript without calling STT again.
- **Automatic Cleanup:** Uses temporary directories per request and immediately deletes uploaded speaker reference files from Replicate after synthesis.
- **Heroku-Safe Footprint:** Offloads heavy AI inference to OpenAI & Replicate APIs, keeping Heroku RAM & slug size minimal, with `ffmpeg` installed via `Aptfile`.
- **Worker & Webhook Ready:** Supports `worker` long-polling out of the box and non-blocking `web` webhook mode (`concurrent_updates=True`) so long voice cloning requests never trigger Heroku's 30s `H12` timeout.

---

## Project Structure

```text
translator/
├── bot/
│   ├── main.py              # Entrypoint (polling or webhook)
│   ├── config.py            # Environment settings
│   ├── languages.py         # Supported XTTS-v2 languages
│   ├── state.py             # Bounded TTL job cache & user preferences
│   ├── keyboards.py         # Inline keyboards & callbacks
│   ├── handlers.py          # Telegram command/voice/callback handlers
│   └── services/
│       ├── audio.py         # Async FFmpeg conversion & OGG Opus output
│       ├── stt.py           # OpenAI speech-to-text
│       ├── translator.py    # OpenAI multilingual translation
│       ├── text_chunker.py  # Multilingual sentence splitting for XTTS-v2
│       └── voice_clone.py   # Replicate XTTS-v2 same-voice synthesis
├── tests/                   # Automated pytest suite
├── Aptfile                  # Installs ffmpeg on Heroku
├── Procfile                 # worker & web process definitions
├── app.json                 # Heroku buildpacks & env manifest
├── .python-version          # Python 3.14 runtime for Heroku
├── .env.example
└── requirements.txt
```

---

## Local Setup

### Prerequisites
- Python 3.12+ (tested on 3.14)
- `ffmpeg` on system `PATH`
- API keys:
  - `TELEGRAM_BOT_TOKEN` (from [@BotFather](https://t.me/BotFather))
  - `OPENAI_API_KEY` (OpenAI Whisper + GPT-4o-mini)
  - `REPLICATE_API_TOKEN` (Replicate pay-per-use XTTS-v2)

### Run Locally

```bash
python -m venv .venv
# Windows PowerShell:
.\.venv\Scripts\Activate.ps1
# macOS / Linux:
# source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env   # Fill in your keys in .env
python -m bot.main
```

Run tests:

```bash
pip install pytest pytest-asyncio
pytest -v
```

---

## Deploy to Heroku

### 1. Create Heroku App & Add Buildpacks (Order Matters!)

`heroku-community/apt` must come **before** `heroku/python` so `ffmpeg` is installed from `Aptfile`:

```bash
heroku create your-voice-translator-bot
heroku buildpacks:add --index 1 heroku-community/apt
heroku buildpacks:add --index 2 heroku/python
```

### 2. Set Required Config Vars

```bash
heroku config:set TELEGRAM_BOT_TOKEN="your-telegram-token"
heroku config:set OPENAI_API_KEY="your-openai-key"
heroku config:set REPLICATE_API_TOKEN="your-replicate-token"
```

### 3. Push Code & Choose Dyno Mode

```bash
git push heroku main
```

#### Option A: Worker Dyno (Recommended — Simple & Reliable)
```bash
heroku ps:scale web=0 worker=1
```
- Runs long-polling with zero HTTP timeout constraints.

#### Option B: Web Dyno (Webhook Mode — Auto-Wakes on Eco Dynos)
```bash
heroku config:set WEBHOOK_URL="https://your-voice-translator-bot.herokuapp.com"
heroku ps:scale worker=0 web=1
```
- Uses `python-telegram-bot` async `concurrent_updates=True`, immediately returning `200 OK` to Telegram while processing XTTS-v2 in the background (avoids Heroku `H12` 30-second timeouts).

Check logs anytime:
```bash
heroku logs --tail
```

---

## Usage Tips

- **Voice Cloning Quality:** Record **5–15 seconds** of clear speech with minimal background noise for the best same-voice clone in English, Hindi, Arabic, or German.
- **Forwarded Messages:** Default to fast text translation; tap **🔊 Hear in Same Voice** below any translated text to synthesize audio in that speaker's voice.
- **Quick Language Shortcut:** Use `/lang` inside Telegram to set a default target language or keep **Ask Every Time**.
