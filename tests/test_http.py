import io
import urllib.error
from pathlib import Path
from urllib.request import Request

import pytest

from vocalize.exceptions import ProviderContentError, ProviderTransientError
from vocalize.providers import _http


class _FakeResponse:
    def __init__(self, status, body):
        self.status = status
        self._body = io.BytesIO(body)

    def read(self, size=-1):
        return self._body.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _seam(monkeypatch, result):
    """Replace the one urllib call. Returns the list of Requests it saw."""
    seen = []

    def fake_urlopen(req, timeout=None):
        seen.append((req, timeout))
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(_http, "urlopen", fake_urlopen)
    return seen


def test_a_successful_request_returns_status_and_body(monkeypatch):
    _seam(monkeypatch, _FakeResponse(200, b"audio-bytes"))

    assert _http.request("GET", "https://x.test/v1", headers={}) == (200, b"audio-bytes")


def test_the_request_carries_the_method_headers_and_body(monkeypatch):
    seen = _seam(monkeypatch, _FakeResponse(200, b"ok"))

    _http.request(
        "POST",
        "https://x.test/v1",
        headers={"Authorization": "Bearer secret", "Content-Type": "application/json"},
        body=b'{"a":1}',
        timeout=5.0,
    )

    req, timeout = seen[0]
    assert req.get_method() == "POST"
    assert req.full_url == "https://x.test/v1"
    assert req.get_header("Authorization") == "Bearer secret"
    assert req.get_header("Content-type") == "application/json"
    assert req.data == b'{"a":1}'
    assert timeout == 5.0


def test_an_http_error_status_is_a_response_not_an_exception(monkeypatch):
    error = urllib.error.HTTPError(
        "https://x.test/v1", 429, "Too Many Requests", {}, io.BytesIO(b'{"error":"slow down"}')
    )
    _seam(monkeypatch, error)

    status, body = _http.request("POST", "https://x.test/v1", headers={})

    # Adapters need the status and the body to classify the failure.
    assert status == 429
    assert body == b'{"error":"slow down"}'


def test_a_network_failure_is_transient(monkeypatch):
    _seam(monkeypatch, urllib.error.URLError("connection refused"))

    with pytest.raises(ProviderTransientError, match="network error: URLError"):
        _http.request("POST", "https://x.test/v1", headers={})


def test_a_timeout_is_transient(monkeypatch):
    _seam(monkeypatch, TimeoutError("timed out"))

    with pytest.raises(ProviderTransientError, match="network error: TimeoutError"):
        _http.request("POST", "https://x.test/v1", headers={})


def test_an_oversized_response_is_refused_rather_than_buffered(monkeypatch):
    _seam(monkeypatch, _FakeResponse(200, b"x" * 5000))

    with pytest.raises(ProviderTransientError, match="exceeded 50 MB"):
        _http.request("GET", "https://x.test/v1", headers={}, max_bytes=1000)


def test_an_oversized_error_body_is_truncated(monkeypatch):
    error = urllib.error.HTTPError(
        "https://x.test/v1", 500, "Server Error", {}, io.BytesIO(b"y" * 5000)
    )
    _seam(monkeypatch, error)

    status, body = _http.request("POST", "https://x.test/v1", headers={}, max_bytes=100)

    assert status == 500
    assert len(body) == 100


def test_no_provider_module_disables_tls_verification():
    # The one security property a code change could silently drop.
    for path in sorted(Path(__file__).resolve().parent.parent.glob("vocalize/providers/*.py")):
        source = path.read_text(encoding="utf-8")
        assert "_create_unverified_context" not in source, path
        assert "verify=False" not in source, path
        assert "CERT_NONE" not in source, path


@pytest.mark.parametrize(
    "url",
    ["file:///etc/passwd", "http://x.test/v1", "ftp://x.test/v1", "HTTPS://x.test/v1"],
)
def test_only_https_urls_are_ever_opened(monkeypatch, url):
    seen = _seam(monkeypatch, _FakeResponse(200, b"ok"))

    with pytest.raises(ProviderContentError, match="non-HTTPS"):
        _http.request("GET", url, headers={})

    assert seen == []  # nothing was opened


def test_the_no_redirects_handler_is_installed_in_the_opener():
    # Finding (1): request() only checks https on the literal URL it is
    # given; if the opener ever followed a redirect, stock urlopen would
    # copy Authorization / X-goog-api-key onto whatever URL the response
    # named (even http://). This is a static guarantee that the handler
    # refusing redirects is actually wired into the opener used to send
    # every request, not just a helper class that nothing installs.
    assert any(isinstance(h, _http._NoRedirects) for h in _http._opener.handlers)


def test_the_no_redirects_handler_refuses_a_redirect():
    # Direct test of the handler itself: given a 302 with a Location header,
    # it must raise rather than build a request to the new URL. This is
    # what stops Authorization from ever being copied onto redirect targets.
    handler = _http._NoRedirects()
    req = Request("https://provider.test/v1")

    with pytest.raises(urllib.error.HTTPError) as excinfo:
        handler.redirect_request(
            req, None, 302, "Found", {"Location": "http://attacker.test/steal"}, "http://attacker.test/steal"
        )

    assert excinfo.value.code == 302


def test_a_redirect_response_becomes_a_transient_error_not_a_3xx_tuple(monkeypatch):
    # Finding (1), the request()-level half: a redirect refused by the
    # opener surfaces to urlopen's caller as an HTTPError with a 3xx code.
    # Old request() would return that straight through as a (3xx, body)
    # tuple — a shape none of the adapters' _classify() functions expect,
    # so callers must never see a bare 3xx as if it were a normal response.
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(req.full_url)
        raise urllib.error.HTTPError(
            req.full_url, 302, "redirect refused (302)", {"Location": "http://other.test/"}, io.BytesIO(b"")
        )

    monkeypatch.setattr(_http, "urlopen", fake_urlopen)

    with pytest.raises(ProviderTransientError, match="unexpected redirect"):
        _http.request("GET", "https://provider.test/v1", headers={})

    # And the redirect target itself was never contacted.
    assert calls == ["https://provider.test/v1"]


def test_an_incomplete_read_is_transient_not_a_crash(monkeypatch):
    # Finding (4): http.client.HTTPException (IncompleteRead's base) is not
    # an OSError subclass, so the old except tuple let a truncated body
    # escape request() as a raw, untyped exception instead of falling
    # through the provider chain like any other network wobble.
    import http.client

    class _TruncatedResponse:
        status = 200

        def read(self, size=-1):
            raise http.client.IncompleteRead(b"x")

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    _seam(monkeypatch, _TruncatedResponse())

    with pytest.raises(ProviderTransientError, match="network error: IncompleteRead"):
        _http.request("GET", "https://x.test/v1", headers={})


def test_errors_are_attributed_to_the_calling_provider(monkeypatch):
    # Finding (8): request() used to hardcode "http" as the provider name
    # on every exception it raised, so the chain printed "http: ..." for
    # every adapter instead of naming the one that actually failed.
    _seam(monkeypatch, urllib.error.URLError("connection refused"))

    with pytest.raises(ProviderTransientError) as excinfo:
        _http.request("GET", "https://x.test/v1", headers={}, provider="openai")

    assert excinfo.value.provider == "openai"
