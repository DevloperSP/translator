"""Unit tests for OpenAI STT, OpenAI Translation, and Replicate XTTS-v2 services."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from bot.languages import get_language
from bot.services.stt import OpenAISTTService, TranscriptionError
from bot.services.translator import OpenAITranslationService, TranslationError
from bot.services.voice_clone import ReplicateVoiceCloneService, VoiceCloneError


class _FakeTranscriptions:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


class _FakeChatCompletions:
    def __init__(self, text: str):
        self.text = text
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.text))]
        )


class _FakeOpenAIClient:
    def __init__(self, transcription_res=None, chat_text: str = ""):
        self.audio = SimpleNamespace(transcriptions=_FakeTranscriptions(transcription_res))
        self.chat = SimpleNamespace(completions=_FakeChatCompletions(chat_text))

    async def close(self):
        return None


async def test_stt_service_whisper_and_empty_error(tmp_path: Path):
    mp3 = tmp_path / "sample.mp3"
    mp3.write_bytes(b"fake-mp3-bytes")

    client = _FakeOpenAIClient(
        transcription_res=SimpleNamespace(text="Hello world", language="english")
    )
    stt = OpenAISTTService("sk-test", model="whisper-1", client=client)  # type: ignore[arg-type]
    res = await stt.transcribe(mp3)
    assert res.text == "Hello world"
    assert res.detected_language == "english"
    assert client.audio.transcriptions.calls[0]["response_format"] == "verbose_json"

    # Empty transcript should raise TranscriptionError
    empty_client = _FakeOpenAIClient(transcription_res=SimpleNamespace(text="   ", language=None))
    empty_stt = OpenAISTTService("sk-test", model="gpt-4o-mini-transcribe", client=empty_client)  # type: ignore[arg-type]
    with pytest.raises(TranscriptionError):
        await empty_stt.transcribe(mp3)
    assert empty_client.audio.transcriptions.calls[0]["response_format"] == "json"


async def test_translation_service():
    client = _FakeOpenAIClient(chat_text="  नमस्ते दुनिया  ")
    translator = OpenAITranslationService("sk-test", client=client)  # type: ignore[arg-type]
    hindi = get_language("hi")
    assert hindi is not None

    out = await translator.translate("Hello world", hindi, source_language="english")
    assert out == "नमस्ते दुनिया"
    messages = client.chat.completions.calls[0]["messages"]
    assert "Hindi" in messages[1]["content"]
    assert "Devanagari" in messages[0]["content"]

    with pytest.raises(TranslationError):
        await translator.translate("   ", hindi)


class _FakeFileOutput:
    def __init__(self, payload: bytes):
        self.payload = payload

    async def aread(self) -> bytes:
        return self.payload


class _FakeReplicateFiles:
    def __init__(self):
        self.created = 0
        self.deleted = []

    async def async_create(self, _file_obj):
        self.created += 1
        return SimpleNamespace(id="rep_file_123", urls={"get": "https://replicate.delivery/speaker.wav"})

    async def async_delete(self, file_id: str):
        self.deleted.append(file_id)


class _FakeReplicateClient:
    def __init__(self, *, fail_on_second: bool = False):
        self.files = _FakeReplicateFiles()
        self.calls = []
        self.fail_on_second = fail_on_second

    async def async_run(self, model: str, input: dict):
        self.calls.append((model, input))
        if self.fail_on_second and len(self.calls) == 2:
            raise RuntimeError("boom")
        return _FakeFileOutput(b"WAVDATA")


async def test_replicate_voice_clone_uploads_once_chunks_and_cleans_up(tmp_path: Path):
    speaker_wav = tmp_path / "speaker.wav"
    speaker_wav.write_bytes(b"RIFF....")

    fake_client = _FakeReplicateClient()
    service = ReplicateVoiceCloneService(
        "r8_test", max_chunk_chars=30, client=fake_client
    )

    paths = await service.synthesize_chunks(
        text="First short sentence. Second short sentence.",
        language_code="de",
        speaker_wav_path=speaker_wav,
        work_dir=tmp_path,
    )
    assert len(paths) == 2
    assert all(p.read_bytes() == b"WAVDATA" for p in paths)
    assert fake_client.files.created == 1
    assert fake_client.files.deleted == ["rep_file_123"]
    assert fake_client.calls[0][1]["speaker"] == "https://replicate.delivery/speaker.wav"
    assert fake_client.calls[0][1]["language"] == "de"
    assert fake_client.calls[0][1]["cleanup_voice"] is True


async def test_replicate_voice_clone_deletes_uploaded_file_on_error(tmp_path: Path):
    speaker_wav = tmp_path / "speaker.wav"
    speaker_wav.write_bytes(b"RIFF....")

    fake_client = _FakeReplicateClient(fail_on_second=True)
    service = ReplicateVoiceCloneService(
        "r8_test", max_chunk_chars=30, client=fake_client
    )

    with pytest.raises(VoiceCloneError):
        await service.synthesize_chunks(
            text="First short sentence. Second short sentence.",
            language_code="ar",
            speaker_wav_path=speaker_wav,
            work_dir=tmp_path,
        )
    assert fake_client.files.deleted == ["rep_file_123"]
