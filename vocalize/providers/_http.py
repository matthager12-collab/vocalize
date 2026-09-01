"""One HTTP seam for every provider that talks to a REST API.

stdlib urllib rather than requests/httpx: the adapters send one POST and
read one body, and a new hard dependency for that is not worth it. The
tests monkeypatch `vocalize.providers._http.urlopen`, so this module is
also the single place network access can be cut off.

TLS verification is urllib's default and must stay that way — there is a
test that greps this package for the usual ways people turn it off.
"""

from __future__ import annotations

import http.client
import urllib.error
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ..exceptions import ProviderContentError, ProviderTransientError

# Enough for any plausible speech response; a hostile or broken endpoint
# streaming forever must not fill the disk.
MAX_RESPONSE_BYTES = 50 * 1024 * 1024

_CHUNK = 64 * 1024


class _NoRedirects(HTTPRedirectHandler):
    """Refuse every redirect.

    Every provider endpoint is a fixed URL, so a 3xx is never legitimate.
    Left to stock urlopen, HTTPRedirectHandler re-sends Authorization /
    X-goog-api-key to whatever Location says — including a plain-http
    URL — which hands an API key to anyone who can answer on that
    address. Raising turns the redirect into an HTTPError instead, which
    request() below maps to ProviderTransientError.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, f"redirect refused ({code})", headers, fp)


# Module-level so a provider endpoint can never be opened through anything
# else, and named `urlopen` so tests' `monkeypatch.setattr(_http, "urlopen",
# ...)` seam keeps working.
_opener = build_opener(_NoRedirects())
urlopen = _opener.open


def _read_capped(response, max_bytes: int, provider: str) -> bytes:
    """Read the body, refusing to buffer more than `max_bytes`."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = response.read(_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise ProviderTransientError(provider, "response exceeded 50 MB")
        chunks.append(chunk)
    return b"".join(chunks)


def request(
    method: str,
    url: str,
    *,
    headers: dict,
    body: bytes | None = None,
    timeout: float = 30.0,
    max_bytes: int = MAX_RESPONSE_BYTES,
    provider: str = "http",
) -> tuple[int, bytes]:
    """Send one request. Returns (status, body) — including for 4xx/5xx.

    An HTTP error status is a *response*, not an exception: the adapters
    need the status and the body to classify it. Only a failure to get any
    response at all becomes an exception here.

    Headers carry API keys, so nothing in this module logs them.
    """
    if not url.startswith("https://"):
        # urlopen speaks file:// and ftp:// too. Every provider endpoint is a
        # fixed HTTPS URL, so anything else is a bug or a redirected value —
        # loud and stopping, never a "try the next provider" wobble.
        raise ProviderContentError(provider, "refusing a non-HTTPS URL")

    req = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(req, timeout=timeout) as response:
            return response.status, _read_capped(response, max_bytes, provider)
    except urllib.error.HTTPError as exc:
        if 300 <= exc.code < 400:
            # _NoRedirects turns every redirect into an HTTPError; a 3xx
            # reaching here is one of those, never a real response — the
            # opener already refused to follow it and never contacted the
            # new URL, so nothing beyond this status leaked.
            raise ProviderTransientError(
                provider, f"unexpected redirect (HTTP {exc.code}) refused"
            ) from exc
        # Read one byte past the cap so an oversized error body is truncated
        # rather than trusted.
        return exc.code, exc.read(max_bytes + 1)[:max_bytes]
    # socket.timeout has been an alias of TimeoutError since 3.10, HTTPError
    # is caught above, and http.client.HTTPException (e.g. IncompleteRead on
    # a truncated body) is not an OSError subclass, so it needs listing
    # explicitly alongside the OSError catch-all.
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as exc:
        raise ProviderTransientError(
            provider, f"network error: {exc.__class__.__name__}"
        ) from exc
