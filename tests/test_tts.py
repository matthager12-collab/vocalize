import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from vocalize import tts
from vocalize.config import Settings
from vocalize.exceptions import ProviderTransientError, TTSRequestError
from vocalize.tts import _cache_key, build_client, get_usage, list_voices, synthesize


class FakeTTSNamespace:
    def __init__(self, chunks=(b"fake", b"-audio"), raise_error=None):
        self._chunks = chunks
        self._raise_error = raise_error
        self.calls = []

    def convert(self, **kwargs):
        self.calls.append(kwargs)
        if self._raise_error:
            raise self._raise_error
        return iter(self._chunks)


class FakeVoicesNamespace:
    def __init__(self, voices):
        self._voices = voices

    def search(self, **_kwargs):
        return SimpleNamespace(voices=self._voices)


class FakeUserNamespace:
    def __init__(self, tier="free", used=1000, limit=10000, resets_at=1700000000, raise_error=None):
        self._tier = tier
        self._used = used
        self._limit = limit
        self._resets_at = resets_at
        self._raise_error = raise_error
        self.subscription = SimpleNamespace(get=self._get)

    def _get(self):
        if self._raise_error:
            raise self._raise_error
        return SimpleNamespace(
            tier=self._tier,
            character_count=self._used,
            character_limit=self._limit,
            next_character_count_reset_unix=self._resets_at,
        )


class FakeClient:
    def __init__(self, chunks=(b"fake", b"-audio"), raise_error=None, voices=None):
        self.text_to_speech = FakeTTSNamespace(chunks=chunks, raise_error=raise_error)
        self.voices = FakeVoicesNamespace(voices or [])


def test_synthesize_returns_joined_audio_bytes(tmp_path):
    client = FakeClient(chunks=(b"hello", b"-world"))
    settings = Settings(voice_id="v1", model_id="m1")

    audio = synthesize(client, "hi", settings, cache_dir=tmp_path)

    assert audio == b"hello-world"
    assert client.text_to_speech.calls[0]["text"] == "hi"
    assert client.text_to_speech.calls[0]["voice_id"] == "v1"


def test_synthesize_uses_cache_on_second_call(tmp_path):
    client = FakeClient(chunks=(b"only-once",))
    settings = Settings(voice_id="v1", model_id="m1")

    first = synthesize(client, "cache me", settings, cache_dir=tmp_path)
    second = synthesize(client, "cache me", settings, cache_dir=tmp_path)

    assert first == second == b"only-once"
    # the underlying API should only have been hit once
    assert len(client.text_to_speech.calls) == 1


def test_unreadable_cache_entry_falls_back_to_a_fresh_call(tmp_path):
    client = FakeClient(chunks=(b"fresh",))
    settings = Settings(voice_id="v1", model_id="m1")

    synthesize(client, "hi", settings, cache_dir=tmp_path)

    # A directory in the entry's place still reports exists(), but
    # read_bytes() raises OSError.
    entry = next(tmp_path.glob("*.mp3"))
    entry.unlink()
    entry.mkdir()

    assert synthesize(client, "hi", settings, cache_dir=tmp_path) == b"fresh"
    assert len(client.text_to_speech.calls) == 2


def test_unwritable_cache_dir_still_returns_the_audio(tmp_path, monkeypatch):
    client = FakeClient(chunks=(b"paid-for",))
    settings = Settings()

    def deny(*args, **kwargs):
        raise PermissionError("read-only")

    monkeypatch.setattr(Path, "mkdir", deny)

    assert synthesize(client, "hi", settings, cache_dir=tmp_path / "nope") == b"paid-for"


def test_synthesize_rejects_empty_text(tmp_path):
    client = FakeClient()
    settings = Settings()

    with pytest.raises(TTSRequestError, match="empty"):
        synthesize(client, "   ", settings, cache_dir=tmp_path)


def test_an_empty_body_is_transient_so_the_chain_can_fall_through(tmp_path):
    # A 200 with no bytes is a wobble, not a verdict: as a bare
    # TTSRequestError it aborted the whole run instead of moving on.
    client = FakeClient(chunks=())
    settings = Settings()

    with pytest.raises(ProviderTransientError, match="returned no audio"):
        synthesize(client, "hello", settings, cache_dir=tmp_path)


def test_synthesize_wraps_sdk_errors(tmp_path):
    client = FakeClient(raise_error=RuntimeError("rate limited"))
    settings = Settings()

    with pytest.raises(TTSRequestError, match="rate limited"):
        synthesize(client, "hello", settings, cache_dir=tmp_path)


def test_list_voices_returns_id_and_name():
    fake_voice = SimpleNamespace(voice_id="abc123", name="Rachel")
    client = FakeClient(voices=[fake_voice])

    result = list_voices(client)

    assert result == [{"id": "abc123", "name": "Rachel"}]


class _PagedVoices:
    """`voices.search` over `pages`; the token is the index of the next one."""

    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def search(self, **kwargs):
        self.calls.append(kwargs)
        index = int(kwargs.get("next_page_token") or 0)
        more = index + 1 < len(self.pages)
        return SimpleNamespace(
            voices=self.pages[index],
            has_more=more,
            next_page_token=str(index + 1) if more else None,
        )


def test_list_voices_walks_every_page():
    """The API pages at ten by default and says `has_more`; an account with
    more voices than one page used to show only the first page."""
    pages = [[SimpleNamespace(voice_id=f"v{i}", name=f"Voice {i}")] for i in range(3)]
    client = SimpleNamespace(voices=_PagedVoices(pages))

    result = list_voices(client)

    assert [voice["id"] for voice in result] == ["v0", "v1", "v2"]
    assert [call.get("next_page_token") for call in client.voices.calls] == [None, "1", "2"]
    assert {call.get("page_size") for call in client.voices.calls} == {tts.VOICE_PAGE_SIZE}


def test_list_voices_stops_at_the_page_ceiling_when_the_api_always_says_more():
    calls = []

    class _Endless:
        @staticmethod
        def search(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                voices=[SimpleNamespace(voice_id="v", name="V")],
                has_more=True,
                next_page_token="again",
            )

    result = list_voices(SimpleNamespace(voices=_Endless()))

    assert len(calls) == tts.MAX_VOICE_PAGES == 20
    assert len(result) == tts.MAX_VOICE_PAGES


def test_speed_is_passed_through_as_voice_settings(tmp_path):
    client = FakeClient()
    settings = Settings(voice_id="v1", model_id="m1", speed=1.1)

    synthesize(client, "hi", settings, cache_dir=tmp_path)

    assert client.text_to_speech.calls[0]["voice_settings"].speed == 1.1


def test_unset_speed_sends_no_voice_settings_kwarg(tmp_path):
    client = FakeClient()
    settings = Settings(voice_id="v1", model_id="m1")

    synthesize(client, "hi", settings, cache_dir=tmp_path)

    assert "voice_settings" not in client.text_to_speech.calls[0]


def test_cache_key_differs_by_speed():
    unset = Settings(voice_id="v1", model_id="m1")
    faster = Settings(voice_id="v1", model_id="m1", speed=1.1)

    assert _cache_key("hi", unset) != _cache_key("hi", faster)


def test_cache_key_is_unchanged_when_speed_is_unset():
    # Pins the pre-speed payload scheme so caches written by older
    # versions keep hitting.
    settings = Settings(voice_id="v1", model_id="m1", output_format="f1")
    old_payload = f"{settings.voice_id}|{settings.model_id}|{settings.output_format}|hi"

    assert _cache_key("hi", settings) == hashlib.sha256(old_payload.encode("utf-8")).hexdigest()


def test_get_usage_returns_tier_used_limit_and_reset():
    client = SimpleNamespace(
        user=FakeUserNamespace(tier="creator", used=4200, limit=100000, resets_at=1735689600)
    )

    result = get_usage(client)

    assert result == {"tier": "creator", "used": 4200, "limit": 100000, "resets_at": 1735689600}


def test_get_usage_passes_through_a_missing_reset_time():
    client = SimpleNamespace(user=FakeUserNamespace(resets_at=None))

    result = get_usage(client)

    assert result["resets_at"] is None


def test_get_usage_wraps_sdk_errors():
    client = SimpleNamespace(user=FakeUserNamespace(raise_error=RuntimeError("unauthorized")))

    with pytest.raises(TTSRequestError, match="unauthorized"):
        get_usage(client)


# --- the SDK client's transport -----------------------------------------


def _serve(handler_class):
    """One loopback HTTP server on a random port, on a daemon thread."""
    import http.server
    import threading

    server = http.server.HTTPServer(("127.0.0.1", 0), handler_class)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_the_sdk_client_never_follows_a_redirect_with_the_key(monkeypatch, tmp_path):
    """APP-SECRETS / APP-TLS: the real SDK client, pointed at a loopback
    server that answers the voices request with a 302 to a *different*
    origin over plain http. The second origin must never hear from us —
    stock httpx follows the redirect and re-sends `xi-api-key` to whoever
    answers there, which is the leak `_http._NoRedirects` already closes
    for the urllib providers.
    """
    import http.server

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    for variable in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy", "ELEVENLABS_API_KEY"):
        monkeypatch.delenv(variable, raising=False)
    key = "sk-canary-redirect-0123456789abcdef"
    elsewhere_saw = []

    class Elsewhere(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            elsewhere_saw.append((self.path, dict(self.headers)))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"voices": [], "has_more": false}')

        def log_message(self, *_args):
            pass

    elsewhere = _serve(Elsewhere)
    target = f"http://127.0.0.1:{elsewhere.server_address[1]}"

    class Origin(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", target + self.path)
            self.end_headers()

        def log_message(self, *_args):
            pass

    origin = _serve(Origin)
    base_url = f"http://127.0.0.1:{origin.server_address[1]}"

    import elevenlabs.client as sdk

    real = sdk.ElevenLabs
    # `build_client` takes the key alone; the base URL is injected here so
    # the client under test is otherwise exactly the one production builds.
    monkeypatch.setattr(sdk, "ElevenLabs", lambda **kwargs: real(base_url=base_url, **kwargs))
    try:
        client = build_client(key)
        try:
            list_voices(client)
        except TTSRequestError as exc:
            refusal = str(exc)
        else:
            refusal = None
    finally:
        origin.shutdown()
        origin.server_close()
        elsewhere.shutdown()
        elsewhere.server_close()

    leaked = [headers.get("xi-api-key") for _path, headers in elsewhere_saw]
    assert elsewhere_saw == [], f"the redirect was followed; the key went with it: {leaked}"
    assert refusal is not None, "a redirect is refused, never served"
    assert key not in refusal


# --- the key never rides out on someone else's error text ----------------


def test_build_client_stashes_the_key_for_the_scrub():
    """`_safe` reads the key off the client. Pinned against the real SDK so
    a version that refused the attribute fails here, not silently in the
    error path where the miss is invisible."""
    client = build_client("sk-canary-stash-0123456789abcdef")

    assert client._vocalize_key == "sk-canary-stash-0123456789abcdef"


def test_get_usage_does_not_echo_a_key_the_sdk_quoted_back():
    """APP-SECRETS: `cli.usage` prints this message straight to the terminal
    and never calls `auth.scrub` — the scrub has to happen here."""
    key = "sk-canary-usage-0123456789abcdef"
    client = SimpleNamespace(
        user=FakeUserNamespace(raise_error=RuntimeError(f"401: rejected key {key}")),
        _vocalize_key=key,
    )

    with pytest.raises(TTSRequestError) as caught:
        get_usage(client)

    assert key not in str(caught.value)
    assert "[key]" in str(caught.value)


def test_list_voices_does_not_echo_a_key_the_api_quoted_back(monkeypatch, tmp_path):
    """APP-SECRETS, end to end through the real SDK: an API that quotes the
    submitted key in its error body puts it in `ApiError.__str__`, and
    `vocalize voices` prints that whole line to stderr. The reviewer's own
    repro, minus the CLI."""
    import http.server
    import json

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    for variable in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy", "ELEVENLABS_API_KEY"):
        monkeypatch.delenv(variable, raising=False)
    key = "sk-canary-echo-0123456789abcdef"

    class Echoing(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps(
                {"detail": {"status": "weird", "message": f"rejected key {key}"}}
            ).encode()
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = _serve(Echoing)
    base_url = f"http://127.0.0.1:{server.server_address[1]}"

    import elevenlabs.client as sdk

    real = sdk.ElevenLabs
    monkeypatch.setattr(sdk, "ElevenLabs", lambda **kwargs: real(base_url=base_url, **kwargs))
    try:
        with pytest.raises(TTSRequestError) as caught:
            list_voices(build_client(key))
    finally:
        server.shutdown()
        server.server_close()

    message = str(caught.value)
    assert key not in message, message
    assert "[key]" in message
