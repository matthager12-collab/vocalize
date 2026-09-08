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
must *never* do, and the cheapest way to keep a later tab from doing it is a
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

    A readiness `detail`, a provider `error`, `config_error` and
    `config_path` all come from a config file, an exception or a probe, and
    the page renders them into the sidebar and the banners. They are put
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

    The gate asks that every tab's requests carry the session token header.
    A tab that calls `fetch` itself is how that stops being true, so there
    is no room in this file for a third call — tabs use `portal.api`.
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


# --- the panels -------------------------------------------------------


def _js_list(script: str, opening: str) -> tuple[str, ...]:
    """The string literals of one array in the script, in order."""
    start = script.index(opening) + len(opening)
    return tuple(re.findall(r'"([^"]+)"', script[start : script.index("]", start)]))


def test_the_page_offers_the_models_the_cli_knows():
    """The model list is a copy of the manifest's, so it can drift."""
    from vocalize.local import whisper_manifest

    script = JS.read_text(encoding="utf-8")

    assert _js_list(script, "var STT_MODELS = [") == whisper_manifest.MODELS


def test_the_page_offers_the_cleanup_backends_the_config_knows():
    """Same drift for the cleanup select on the Local tab."""
    from vocalize import config

    script = JS.read_text(encoding="utf-8")

    assert _js_list(script, "var STT_CLEANUP = [") == config.STT_CLEANUP_BACKENDS


def test_the_page_asks_the_server_for_voices_and_ships_no_list_of_its_own():
    """`GET /api/voices/<name>` is where a voice list comes from — the server
    holds the keys and the page stays offline-clean. A copy of a list in the
    page is the drift the old test pinned; there is nothing left to pin.

    The options are built as properties on a created node — `.value` and
    `.textContent`, never `.label` (an attribute string) and never markup.
    `test_the_page_behaves_when_driven[voices]` proves that with two hostile
    names; this is the text check that fails the moment someone reaches for
    a shortcut. The picker is a `<select>`, not a `<datalist>`: a datalist
    filters its options by the field's current text, so a field already
    holding "af_heart" offered one voice, or none.
    """
    script = JS.read_text(encoding="utf-8")

    assert "openai: [" not in script
    assert "kokoro: [" not in script
    assert len(re.findall(r"""api\(\s*"GET",\s*"/api/voices/" \+ name\)""", script)) == 1
    assert "datalist" not in script
    assert 'el("option")' in script
    assert "item.value = value" in script
    assert "item.textContent = text" in script
    assert ".label =" not in script
    assert "String(voice.id)" in script
    assert "String(voice.name)" in script


def test_the_keys_hint_names_the_command_that_removes_a_key():
    """The page can remove a key since 0.12.0, and the hint still names the
    terminal command that does the same — it used to send the user to
    Keychain Access, a GUI that knows nothing about vocalize. There are
    four `vocalize` entries in there, named only by username slug, and a
    keychain delete that is denied shows nothing. `auth logout` reads the
    entry back and refuses to claim a removal it cannot verify, so that is
    the command the sentence has to name. Cross-checked against the CLI, so
    a renamed command fails here rather than in a user's terminal."""
    from vocalize.cli import main

    script = JS.read_text(encoding="utf-8")
    logout = main.commands["auth"].commands["logout"]

    assert "Keychain Access" not in script
    assert "vocalize auth logout --provider <name>" in script
    assert logout.name == "logout"
    assert "provider" in {param.name for param in logout.params}


def test_the_sidebar_maps_a_row_to_a_button_by_name_and_never_by_its_action():
    """`ROW_ACTIONS` is keyed by the row's name; the action is shown, not read.

    A readiness row's action is text from a probe. The one place it may
    go is a `<code>` node's text. `test_the_page_behaves_when_driven
    [sidebar]` drives it with a hostile action; this pins that the script
    has no other use for the field.
    """
    script = JS.read_text(encoding="utf-8")

    assert "var ROW_ACTIONS = {" in script
    assert script.count("row.action") == 2  # the `if`, and the <code> node's text
    assert 'el("code", "row-action", row.action)' in script
    assert "hasOwnProperty.call(ROW_ACTIONS, row.name)" in script


def test_every_tab_has_a_panel():
    """A tab with no renderer draws "Nothing here yet." and is a dead end."""
    script = JS.read_text(encoding="utf-8")
    page = HTML.read_text(encoding="utf-8")

    for name in ("chain", "providers", "keys", "usage", "local"):
        assert f'id="panel-{name}"' in page
        assert f"renderers.{name} = function" in script


def test_an_api_key_has_nowhere_to_leak_to():
    """The Keys tab handles a secret, and three sinks would spill it.

    A `<form>` with no action submits to the current URL as a GET, which
    puts the key in the address bar, in history, and in the one place this
    server refuses to read a secret from. `localStorage` outlives the tab
    and every later portal on this origin. And a key typed into a field the
    browser offers to remember is a key in the browser's password store.
    """
    script = JS.read_text(encoding="utf-8")
    page = HTML.read_text(encoding="utf-8")

    assert "<form" not in page.lower()
    assert 'createElement("form")' not in script
    assert 'el("form"' not in script
    assert "localStorage" not in script
    assert 'keyBox.type = "password"' in script
    # "off" is the value Safari and Chrome ignore on a password field.
    assert 'keyBox.autocomplete = "new-password"' in script
    assert 'autocomplete = "off"' not in script.split("function keyCard")[1].split("\n}\n")[0]


def test_the_readiness_list_keeps_its_role_under_list_style_none():
    """Safari drops list semantics with the markers; `role="list"` puts them back."""
    page = HTML.read_text(encoding="utf-8")

    assert re.search(r'<ol id="rows"[^>]*\brole="list"', page)


# --- the page, driven without a browser --------------------------------


@pytest.mark.parametrize(
    "scenario", ["race", "keys", "fatal", "lists", "voices", "keystate", "keyslots", "sidebar"]
)
def test_the_page_behaves_when_driven(scenario):
    """`portal.js` under node, over a stub DOM and a hand-answered `fetch`.

    One scenario per behaviour a reviewer found broken: a save reverting on
    screen behind an older poll, a refused key left in the field, a live
    region filled while hidden, a list stripped of its role. Three more from
    the owner's UX pass: the voice picker as a `<select>` that never fetches
    or saves on its own, the key state as the first line of every card, and
    the sidebar's buttons mapped by row name. The scenarios live in the
    harness; this runs one per process because the page keeps module state.
    Skipped where node is missing — the page has no other runtime, and a
    text check on the script could not fail for the right reason.
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")

    done = subprocess.run(
        [node, str(HARNESS), str(JS), scenario],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert done.returncode == 0, done.stderr or done.stdout


def test_a_config_write_can_only_go_out_with_a_fingerprint():
    """The three write routes go through `save`, which carries the fingerprint.

    `save` is also what refuses to re-send after a 409 — the refusal that
    stops a write nobody saw from being overwritten. A panel that called
    `api` directly for one of these would skip both.
    """
    script = JS.read_text(encoding="utf-8")

    direct = re.compile(r"""api\(\s*["']POST["'],\s*["']/api/(chain|stt|provider/)""")
    assert not direct.search(script)
    for route in ('"/api/chain"', '"/api/stt"', '"/api/provider/" + name'):
        assert script.count(route) == 1, f"{route} is written from exactly one place"
