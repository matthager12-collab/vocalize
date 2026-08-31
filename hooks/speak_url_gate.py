#!/usr/bin/env python3
"""Path resolution and URL safety classification for the `/speak` slash
command's web mode. Stdlib only — this runs as a plain subprocess from a
Claude Code slash command, not as part of the vocalize package.

Two subcommands:

    python3 hooks/speak_url_gate.py resolve <path>
        Expands `~` and resolves symlinks, then prints the absolute path.
        Used before reading a local file the command was pointed at.

    python3 hooks/speak_url_gate.py check <url>
        Classifies a URL before any web fetch happens, so the slash command
        can decide whether to fetch it outright, ask the user to confirm
        first, or refuse. Prints exactly one line describing the verdict:

            scheme=<s> host=<h> port=<p-or-empty> userinfo=<yes|no> \
kind=<public|confirm|refuse> reason=<slug> querylen=<n>

        `check` always exits 0 once it has printed that line — callers are
        expected to read the line, not the process exit code. This is
        deliberate: a hostile URL crashing the gate script should never be
        mistaken for the gate script waving the URL through.

The classification is deliberately conservative and mirrors the kind of
SSRF-prevention checks a browser or an HTTP client would apply: local/
private/link-local addresses, cloud metadata endpoints, credential-bearing
URLs, non-http(s) schemes, and known link shorteners are all blocked or
require confirmation rather than being fetched silently.
"""

from __future__ import annotations

import ipaddress
import os
import re
import sys
import urllib.parse

ALLOWED_SCHEMES = {"http", "https"}

METADATA_HOSTS = {"metadata", "metadata.google.internal"}

SHORTENER_HOSTS = {
    "bit.ly",
    "t.co",
    "tinyurl.com",
    "goo.gl",
    "ow.ly",
    "buff.ly",
    "is.gd",
    "rebrand.ly",
    "cutt.ly",
    "rb.gy",
    "lnkd.in",
    "shorturl.at",
    "tiny.cc",
}

FILE_EXTENSIONS = {
    "pdf",
    "zip",
    "png",
    "jpg",
    "jpeg",
    "gif",
    "webp",
    "svg",
    "mp4",
    "mov",
    "avi",
    "mkv",
    "mp3",
    "wav",
    "dmg",
    "pkg",
    "exe",
    "msi",
    "tar",
    "gz",
    "tgz",
    "bz2",
    "7z",
    "iso",
    "doc",
    "docx",
    "xls",
    "xlsx",
    "ppt",
    "pptx",
}

# Suffixes (and the bare name) that mark a hostname as private/local even
# though it isn't an IP literal — mDNS, Tailscale MagicDNS, RFC 8375, etc.
PRIVATE_NAME_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".ts.net")

LONG_QUERY_THRESHOLD = 100

_HEX_PART = re.compile(r"0[xX][0-9a-fA-F]+")
_OCTAL_PART = re.compile(r"0[0-7]+")
_DECIMAL_PART = re.compile(r"[1-9][0-9]*")


def _parse_ipv4_part(part: str) -> int | None:
    """One dot-separated part of an inet_aton-style IPv4 literal.

    Accepts decimal, 0x-prefixed hex, or 0-prefixed octal — matching what
    browsers still parse in a bare address bar, not the strict
    dotted-decimal-only rules `ipaddress` enforces.
    """
    if not part:
        return None
    if _HEX_PART.fullmatch(part):
        return int(part, 16)
    if part == "0":
        return 0
    if _OCTAL_PART.fullmatch(part):
        return int(part, 8)
    if _DECIMAL_PART.fullmatch(part):
        return int(part, 10)
    return None


def _parse_browser_ipv4(host: str) -> ipaddress.IPv4Address | None:
    """inet_aton semantics: 1-4 parts, the last one filling whatever bits
    the earlier parts didn't claim. '0x7f.0.0.1' and '2130706433' both mean
    127.0.0.1; this is what lets a URL smuggle a blocked IP past a naive
    string-based hostname check.
    """
    raw_parts = host.split(".")
    if not 1 <= len(raw_parts) <= 4:
        return None

    values = [_parse_ipv4_part(p) for p in raw_parts]
    if any(v is None for v in values):
        return None

    for v in values[:-1]:
        if v > 0xFF:
            return None

    n = len(values)
    last = values[-1]
    if n == 1:
        if last > 0xFFFFFFFF:
            return None
        total = last
    elif n == 2:
        if last > 0xFFFFFF:
            return None
        total = (values[0] << 24) | last
    elif n == 3:
        if last > 0xFFFF:
            return None
        total = (values[0] << 24) | (values[1] << 16) | last
    else:
        if last > 0xFF:
            return None
        total = (values[0] << 24) | (values[1] << 16) | (values[2] << 8) | last

    try:
        return ipaddress.IPv4Address(total)
    except ValueError:
        return None


def _parse_ip_literal(host: str):
    """An IPv4Address/IPv6Address for `host`, or None if it isn't one.

    Tries the standard library's strict parser first (covers normal
    dotted-decimal IPv4 and bracketed IPv6), then falls back to the
    browser-compatible inet_aton parser for hex/octal/integer IPv4 forms.
    """
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    if ":" not in host:
        return _parse_browser_ipv4(host)
    return None


def classify_url(url: str) -> dict:
    """Classify `url` for the /speak web-fetch gate.

    Never raises — any internal failure is reported as
    kind=refuse reason=unparseable, same as a URL this function
    deliberately rejects.
    """
    try:
        return _classify_url(url)
    except Exception:  # noqa: BLE001 — the gate must always print a verdict; a crash could read as a pass
        return {
            "scheme": "",
            "host": "",
            "port": "",
            "userinfo": False,
            "kind": "refuse",
            "reason": "unparseable",
            "querylen": 0,
        }


# After percent-decoding, an ASCII host may contain only these characters
# (colon for bare IPv6 forms; urlsplit has already stripped the brackets).
# Everything else — spaces, "=", slashes — is refused, which also keeps
# attacker-chosen bytes out of the verdict line this script prints.
_HOST_CHARSET_RE = re.compile(r"[a-z0-9._:-]+")


def _classify_url(url: str) -> dict:
    parts = urllib.parse.urlsplit(url)
    scheme = (parts.scheme or "").lower()
    query = parts.query or ""
    querylen = len(query)
    hostname = parts.hostname  # already lowercased by urlsplit
    userinfo = bool(parts.username or parts.password)
    port = parts.port  # may raise ValueError -> caught by classify_url

    # Browsers percent-decode the host before matching it; do the same, or
    # every host rule below is bypassable by %-encoding one character
    # (%62it.ly is bit.ly). A host that is still encoded after one decode
    # (%2562...) is refused outright rather than guessed at.
    double_encoded = False
    if hostname and "%" in hostname:
        hostname = urllib.parse.unquote(hostname).lower()
        double_encoded = "%" in hostname

    result = {
        "scheme": scheme,
        "host": hostname or "",
        "port": port if port is not None else "",
        "userinfo": userinfo,
        "querylen": querylen,
    }

    def refuse(reason: str) -> dict:
        result["kind"] = "refuse"
        result["reason"] = reason
        return result

    def confirm(reason: str) -> dict:
        result["kind"] = "confirm"
        result["reason"] = reason
        return result

    if scheme not in ALLOWED_SCHEMES:
        return refuse("scheme")

    if userinfo:
        return refuse("userinfo")

    if not hostname:
        return refuse("no-host")

    labels = hostname.split(".")
    if any(ord(c) > 127 for c in hostname) or any(lbl.startswith("xn--") for lbl in labels):
        result["host"] = ""  # keep raw attacker bytes out of the verdict line
        return refuse("non-ascii-host")

    if double_encoded:
        result["host"] = ""
        return refuse("host-encoding")

    if not _HOST_CHARSET_RE.fullmatch(hostname):
        result["host"] = ""
        return refuse("host-charset")

    if hostname in METADATA_HOSTS:
        return refuse("metadata")

    if hostname in SHORTENER_HOSTS:
        return refuse("shortener")

    last_segment = parts.path.rsplit("/", 1)[-1]
    if "." in last_segment:
        ext = last_segment.rsplit(".", 1)[-1].lower()
        if ext in FILE_EXTENSIONS:
            return refuse("file-url")

    ip = _parse_ip_literal(hostname)
    if ip is not None:
        # Loopback first: ::1 is also inside IPv6's reserved block, and it
        # must stay confirmable (dev servers), not hard-blocked.
        if ip.is_loopback:
            return confirm("private-ip")
        if ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return refuse("ip-blocked")
        if ip.is_private:
            return confirm("private-ip")
        return confirm("ip-literal")

    final_label = labels[-1]
    if (
        hostname == "localhost"
        or hostname.endswith(PRIVATE_NAME_SUFFIXES)
        or not final_label.isalpha()
        # A dotless name can only resolve via a search suffix, mDNS, or a
        # VPN resolver — by definition an internal host, never public DNS.
        or "." not in hostname
    ):
        return confirm("private-name")

    if port is not None:
        return confirm("port")

    if scheme == "http":
        return confirm("http")

    if querylen > LONG_QUERY_THRESHOLD:
        return confirm("long-query")

    result["kind"] = "public"
    result["reason"] = "none"
    return result


def format_verdict_line(info: dict) -> str:
    return (
        f"scheme={info['scheme']} host={info['host']} port={info['port']} "
        f"userinfo={'yes' if info['userinfo'] else 'no'} kind={info['kind']} "
        f"reason={info['reason']} querylen={info['querylen']}"
    )


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv

    if not argv:
        print("usage: speak_url_gate.py {resolve|check} <arg>", file=sys.stderr)
        return 2

    command, *rest = argv

    if command == "resolve":
        if not rest:
            print("usage: speak_url_gate.py resolve <path>", file=sys.stderr)
            return 2
        print(os.path.realpath(os.path.expanduser(rest[0])))
        return 0

    if command == "check":
        if not rest:
            print("usage: speak_url_gate.py check <url>", file=sys.stderr)
            return 2
        print(format_verdict_line(classify_url(rest[0])))
        return 0

    print(f"unknown command: {command!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
