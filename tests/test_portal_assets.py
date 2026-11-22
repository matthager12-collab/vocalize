"""The shipped portal page, against the constraints it is built under.

Four of these are gate checks rather than preferences, and each one fails
silently in a browser rather than loudly in the suite, which is why they are
pinned here:

    * the served HTML carries no `<script>` body — the CSP refuses inline
      script, so a page that grew one would simply stop working
    * neither asset names an external resource — the portal has to work with
      the machine's network cable pulled, and the CSP refuses one anyway
    * both are served from the package with the security headers, as
      themselves and not as `portal.py`'s placeholder
    * both are inside the wheel, which on this project means "not ignored by
      git" (see the gitignore test)

Two behavioural constraints are pinned by reading the script as text. That
is a blunt instrument, and deliberate: both are rules about what the page
must *never* do, and the cheapest way to keep a later change from doing it is a
check that fails the moment the words appear a second time.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import unicodedata
from pathlib import Path

import pytest

import vocalize
from vocalize.portal import SECURITY_HEADERS, Portal

REPO = Path(__file__).resolve().parent.parent
ASSETS = Path(vocalize.__file__).resolve().parent / "assets"
HTML = ASSETS / "portal.html"
JS = ASSETS / "portal.js"
HARNESS = Path(__file__).with_name("portal_page_harness.js")

PORT = 45998
ORIGIN = f"127.0.0.1:{PORT}"

_SCRIPT = re.compile(r"<script\b([^>]*)>(.*?)</script\s*>", re.IGNORECASE | re.DOTALL)
_ABSOLUTE_URL = re.compile(r"https?:", re.IGNORECASE)
_PROTOCOL_RELATIVE = re.compile(r"""(?:src|href)\s*=\s*["']//""", re.IGNORECASE)
_INLINE_HANDLER = re.compile(r"""\son[a-z]+\s*=\s*["']""", re.IGNORECASE)


@pytest.fixture
def portal():
    """An unbound portal with a known port, for driving `route()` directly."""
    made = Portal(probe_timeout=0.2)
    made.port = PORT
    return made


def _serve(portal: Portal, path: str):
    return portal.route("GET", path, {"Host": ORIGIN})


# --- what the browser is handed ---------------------------------------


@pytest.mark.parametrize(
    ("path", "asset", "content_type"),
    [
        ("/", HTML, "text/html; charset=utf-8"),
        ("/portal.js", JS, "application/javascript; charset=utf-8"),
    ],
)
def test_the_asset_is_served_as_itself_with_the_security_headers(
    portal, path, asset, content_type
):
    """Byte-for-byte the file — not `portal.py`'s not-built-yet placeholder."""
    status, headers, body = _serve(portal, path)

    assert status == 200
    assert headers["Content-Type"] == content_type
    for name, value in SECURITY_HEADERS.items():
        assert headers[name] == value
    assert body == asset.read_bytes()


def test_the_served_page_has_exactly_one_script_and_it_has_no_body(portal):
    """The CSP has no `'unsafe-inline'` for script, so an inline one is dead."""
    _status, _headers, body = _serve(portal, "/")
    scripts = _SCRIPT.findall(body.decode("utf-8"))

    assert len(scripts) == 1
    attributes, contents = scripts[0]
    assert contents.strip() == ""
    assert 'src="/portal.js"' in attributes


def test_the_served_page_has_no_inline_event_handlers(portal):
    """`onclick="…"` is inline script by another name, and the CSP knows it."""
    _status, _headers, body = _serve(portal, "/")
    page = body.decode("utf-8")

    assert not _INLINE_HANDLER.search(page)
    assert "javascript:" not in page.lower()


def test_the_page_styles_itself_under_the_shipped_policy(portal):
    """The stylesheet is in the document, and the CSP has to allow it.

    `default-src 'self'` does not cover an inline `<style>`: "self" is a
    source for a stylesheet the page *fetches*, and there is no third asset
    file to fetch one from. So the policy names `style-src` explicitly, and
    if either half of that pairing changes without the other the page is
    served unstyled with nothing but a console warning to say so.
    """
    _status, headers, body = _serve(portal, "/")

    assert "<style>" in body.decode("utf-8")
    assert "style-src 'self' 'unsafe-inline'" in headers["Content-Security-Policy"]


@pytest.mark.parametrize("asset", [HTML, JS], ids=lambda p: p.name)
def test_no_asset_names_an_external_resource(asset):
    """It must work with the network cable pulled: no CDN, no font, no image."""
    text = asset.read_text(encoding="utf-8")

    assert not _ABSOLUTE_URL.search(text)
    assert not _PROTOCOL_RELATIVE.search(text)
    assert "@import" not in text


@pytest.mark.parametrize("asset", [HTML, JS], ids=lambda p: p.name)
def test_no_asset_carries_a_control_character(asset):
    """A NUL once sat in the script where a space belonged.

    Three reviewers found it independently; it made `grep` and `file(1)`
    call the shipped page binary. Newline and tab are the only control
    characters a text asset has a use for.
    """
    text = asset.read_text(encoding="utf-8")
    stray = sorted({c for c in text if unicodedata.category(c) == "Cc" and c not in "\n\t"})

    assert stray == [], [hex(ord(c)) for c in stray]


# --- the two rules the page must never break --------------------------


def test_the_script_exchanges_the_one_time_code_in_exactly_one_place():
    """Every failed exchange counts toward the five that close the portal.

    A retry — on failure, on reload, on focus, in a `catch` — therefore
    bricks the user's portal in five goes. One mention of the route is the
    cheapest thing that fails when a second call appears, and the fragment
    has to be stripped before that one call so a reload cannot resend a
    spent code.
    """
    script = JS.read_text(encoding="utf-8")

    assert script.count("/api/session") == 1, "the session exchange happens once, on load"
    assert "replaceState" in script, "the #code fragment is stripped before the exchange"


def test_the_script_builds_the_page_without_a_raw_html_sink():
    """Everything the page shows is somebody's text, and some of it is hostile.

    A readiness `detail`, a setting's value, a 400's message, `config_error`
    and `config_path` all come from a config file, an exception or a probe,
    and the page renders them into the cards and the banners. They are put
    there as text nodes — there is no raw-markup sink in this file — so a
    `<img onerror=…>` in a hand-edited voice name is displayed, not run.
    The CSP would refuse the script anyway; this is the layer that means it
    never gets that far.
    """
    script = JS.read_text(encoding="utf-8")
    page = HTML.read_text(encoding="utf-8")

    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert sink not in script
        assert sink not in page
    assert "new Function" not in script
    assert "console." not in script  # nothing about a session belongs in a log


def test_every_other_request_goes_through_the_one_helper():
    """Two `fetch` calls: the exchange, and `api()`, which adds the token.

    The gate asks that every request carry the session token header. Code
    that calls `fetch` itself is how that stops being true, so there is no
    room in this file for a third call — everything uses `api()`.
    """
    script = JS.read_text(encoding="utf-8")

    assert script.count("fetch(") == 2
    assert "X-Vocalize-Token" in script
    assert "?token=" not in script  # the server refuses a token in a URL


# --- packaging --------------------------------------------------------


def test_the_assets_ship_in_the_wheel():
    """Which on this project means: inside the package, and not git-ignored.

    Hatchling's default file selection for the wheel drops anything the
    VCS ignores. That is how `vocalize/assets/cues/*.wav` came to need an
    un-ignore line of its own in 0.10.2 — `*.wav` had quietly kept the cue
    sounds out of the built wheel while every test passed. Nothing in
    `.gitignore` matches `.html` or `.js` today; this test is here for the
    day something does.
    """
    package = Path(vocalize.__file__).resolve().parent
    for asset in (HTML, JS):
        assert asset.is_file()
        assert asset.is_relative_to(package)

    for asset in (HTML, JS):
        try:
            done = subprocess.run(
                ["git", "check-ignore", "-q", str(asset)],
                cwd=REPO,
                capture_output=True,
                check=False,
            )
        except OSError:  # no git on this machine
            pytest.skip("git is not available")
        if done.returncode == 128:  # not a checkout — an unpacked sdist, say
            pytest.skip("not a git checkout")
        assert done.returncode == 1, f"{asset.name} is git-ignored, so the wheel loses it"


# --- the page drawn from the schema --------------------------------------


def _renderer_names(script: str) -> set[str]:
    """The keys of the `RENDERERS` map, read from the script's text."""
    start = script.index("var RENDERERS = {")
    end = script.index("\n};", start)
    return set(re.findall(r"^  ([a-z_]+): function", script[start:end], re.MULTILINE))


def test_the_renderer_map_covers_every_control_kind_the_registry_can_produce():
    """A kind with no renderer would fall back to a plain text field.

    That fallback keeps the page working, but a registry kind landing in it
    means a setting lost its real control, so the map has to cover every
    entry of `settings_registry.CONTROLS`, not only the kinds in use today.
    The harness `kinds` scenario checks the same against the running map.
    """
    from vocalize import settings_registry

    script = JS.read_text(encoding="utf-8")

    assert set(settings_registry.CONTROLS) <= _renderer_names(script)
    assert {s.control for s in settings_registry.SETTINGS} <= _renderer_names(script)


def test_the_page_ships_no_list_of_settings_or_choices_of_its_own():
    """Every key, label and choice comes from `/api/schema` and `/api/state`.

    A copy in the page is the drift the old model and cleanup lists were;
    a new setting must appear with no page change.
    """
    from vocalize import settings_registry
    from vocalize.local import whisper_manifest

    script = JS.read_text(encoding="utf-8")

    assert script.count('"/api/schema"') == 1
    assert "datalist" not in script
    assert 'el("option")' in script
    assert "item.value = value" in script
    assert "item.textContent = text" in script
    # An option's text is a text node, never the `label` attribute.
    assert not re.search(r"\.label\s*=(?!=)", script)
    for model in whisper_manifest.MODELS:
        assert model not in script
    for setting in settings_registry.SETTINGS:
        assert f'"{setting.key}"' not in script, setting.key


def test_the_readiness_list_keeps_its_role_under_list_style_none():
    """Safari drops list semantics with the markers; `role="list"` puts them back."""
    page = HTML.read_text(encoding="utf-8")

    assert re.search(r'<ol id="rows"[^>]*\brole="list"', page)


def test_the_header_names_the_page_address_for_what_it_is():
    """127.0.0.1 and a port is this page's address, not the name of a Mac."""
    page = HTML.read_text(encoding="utf-8")

    assert "<dt>Machine</dt>" not in page
    assert re.search(r'<dt>This page&rsquo;s local address</dt><dd id="page-address">', page)


def test_the_save_message_can_take_the_focus():
    """After a save the Save button turns off; the message it leaves takes focus."""
    page = HTML.read_text(encoding="utf-8")

    assert re.search(r'<div id="save-note" class="banner mild" tabindex="-1" hidden>', page)


def test_the_script_names_no_setting_key_part():
    """Labels for a setting's values come from the data, not from the page."""
    script = JS.read_text(encoding="utf-8")

    assert not re.search(r"transcribe\.model|stt\.[a-z_]+|providers\.", script)


def test_status_changes_are_announced_through_a_live_region():
    page = HTML.read_text(encoding="utf-8")

    assert re.search(r'<p id="status" role="status" aria-live="polite">', page)
    assert re.search(r'<div id="alert" class="banner" role="alert" hidden>', page)


# --- the page, driven without a browser --------------------------------


SCENARIOS = [
    "every_setting",
    "kinds",
    "round_trip",
    "reset",
    "errors",
    "conflict",
    "actions",
    "unknown",
    "notices",
    "race",
    "fatal",
    "lists",
    "empty_choice",
    "empty_choice_help",
    "save_message",
    "save_message_retired",
    "overrides",
    "keyboard",
    "devices",
    "devices_unexplained",
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_the_page_behaves_when_driven(scenario, tmp_path):
    """`portal.js` under node, over a stub DOM and a hand-answered `fetch`.

    The fixture is the real `settings_registry.schema()` plus the actions
    the harness declares from the API contract. The scenarios: every
    setting gets exactly one control under its group; the renderer map
    covers `CONTROLS`; a change of each control kind saves exactly that key
    with a value of the right type; reset sends null; a 400 shows its error
    by its control and keeps the edits; a 409 offers a reload and discards
    nothing; every action gets one button and asks first when it must; an
    unknown control kind still renders; notices and restart needs are
    shown; an older poll never overwrites a newer save. One per process,
    because the page keeps module state. Skipped where node is missing —
    the page has no other runtime.
    """
    import json

    from vocalize import settings_registry

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")

    schema = tmp_path / "schema.json"
    schema.write_text(json.dumps(settings_registry.schema()), encoding="utf-8")
    controls = tmp_path / "controls.json"
    controls.write_text(json.dumps(list(settings_registry.CONTROLS)), encoding="utf-8")

    done = subprocess.run(
        [node, str(HARNESS), str(JS), scenario, str(schema), str(controls)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert done.returncode == 0, done.stderr or done.stdout


def test_a_config_write_can_only_go_out_with_a_fingerprint():
    """One write route, written from one place, which carries the fingerprint.

    `saveSettings` is also what refuses to re-send after a 409 — the refusal
    that stops a write nobody saw from being overwritten.
    """
    script = JS.read_text(encoding="utf-8")

    assert script.count('"/api/settings"') == 1
    assert 'api("POST", "/api/settings", { changes: changes, fingerprint: fingerprint })' in script


def test_portal_has_only_local_processing_controls():
    script = JS.read_text(encoding="utf-8")
    html = HTML.read_text(encoding="utf-8")
    for removed in ("/api/auth/", "anthropic", "claude-cli", "elevenlabs", "monthly_chars"):
        assert removed not in script
    for route in ("/api/chain", "/api/provider", "/api/stt", "/api/voices", "/api/local"):
        assert route not in script, f"{route} was removed from the server"
    for word in ("openai", "polly", "api key", "usage", "budget", "summar"):
        assert word not in script.lower()
        assert word not in html.lower()
    assert 'role="tab"' not in html
    assert 'id="tab-keys"' not in html
    assert 'id="tab-usage"' not in html
