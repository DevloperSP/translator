"""Tests for JobStore and UserPrefsStore."""

from bot.state import JobStore, UserPrefsStore


def test_job_store_default_modes_and_eviction():
    store = JobStore(ttl_seconds=60, max_jobs=2)

    fwd = store.create(
        chat_id=10,
        user_id=1,
        file_id="f1",
        file_name="voice.ogg",
        duration_seconds=8,
        file_size_bytes=1000,
        is_forwarded=True,
    )
    rec = store.create(
        chat_id=10,
        user_id=1,
        file_id="f2",
        file_name="voice.ogg",
        duration_seconds=12,
        file_size_bytes=2000,
        is_forwarded=False,
    )
    assert fwd.mode == "text"
    assert rec.mode == "clone"
    assert len(store) == 2

    third = store.create(
        chat_id=10,
        user_id=1,
        file_id="f3",
        file_name="voice.ogg",
        duration_seconds=5,
        file_size_bytes=500,
        is_forwarded=False,
    )
    assert len(store) == 2
    assert store.get(third.job_id) is third


def test_job_store_ttl_expiration():
    store = JobStore(ttl_seconds=10, max_jobs=10)
    job = store.create(
        chat_id=1,
        user_id=1,
        file_id="f1",
        file_name="voice.ogg",
        duration_seconds=4,
        file_size_bytes=500,
        is_forwarded=False,
    )
    job.updated_at -= 20
    assert store.get(job.job_id) is None
    assert len(store) == 0


def test_user_prefs_store():
    prefs = UserPrefsStore(max_users=2)
    assert prefs.get_preferred_language(1) is None
    prefs.set_preferred_language(1, "hi")
    prefs.set_preferred_language(2, "de")
    prefs.set_preferred_language(3, "ar")
    assert prefs.get_preferred_language(1) is None
    assert prefs.get_preferred_language(2) == "de"
    prefs.set_preferred_language(2, None)
    assert prefs.get_preferred_language(2) is None
