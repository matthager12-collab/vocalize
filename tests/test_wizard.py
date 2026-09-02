import io
import sys
from types import SimpleNamespace

import click
import pytest
from click.testing import CliRunner

from vocalize import auth, wizard
from vocalize.cli import main
from vocalize.config import DEFAULT_MODEL, load_config_file, resolve_settings
from vocalize.exceptions import (
    ConfigChangedError,
    ConfigError,
    MissingAPIKeyError,
    TTSRequestError,
)

UP = "\x1b[A"
DOWN = "\x1b[B"
ENTER = "\r"

VOICES = [{"id": "abc123", "name": "Rachel"}, {"id": "def456", "name": "Josh"}]


class FakeStdin:
    """Just enough stdin for the wizard's TTY check."""

    def __init__(self, tty=True):
        self._tty = tty

    def isatty(self):
        return self._tty


class FakeTTY(io.StringIO):
    """A /dev/tty stand-in that stays readable after the wizard closes it."""

    def __init__(self):
        super().__init__()
        self.closed_by_wizard = False

    def close(self):
        self.closed_by_wizard = True


class Keyboard:
    """A scripted keyboard: each getchar() call returns the next key."""

    def __init__(self, keys):
        self._keys = list(keys)
        self.pressed = []

    def __call__(self, *args, **kwargs):
        if not self._keys:
            raise AssertionError("the wizard asked for more keys than the test scripted")
        key = self._keys.pop(0)
        self.pressed.append(key)
        return key


def _setup(
    monkeypatch,
    tmp_path,
    keys,
    *,
    voices=None,
    prompts=(),
    confirm=True,
    api_key="fake-key",
    patch_ui=True,
    front_door=False,
):
    """Point the wizard at a throwaway config file and a scripted keyboard.

    patch_ui=True aims the wizard's UI stream at sys.stdout, so capsys sees
    the frames; the tty-seam tests pass False and drive the real factory.

    front_door=False stubs out the "no API key — set one up?" question and
    fakes resolve_api_key, so these tests only exercise the three steps;
    the front-door tests pass True and drive the real key resolution.

    `confirm` may be a bool, or a callable taking the prompt's label — the
    front door and the final write both go through _confirm.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    for var in ("VOCALIZE_VOICE", "VOCALIZE_MODEL", "VOCALIZE_SPEED"):
        monkeypatch.delenv(var, raising=False)
    # Pin the viewport so rendering never depends on the runner's terminal.
    monkeypatch.setenv("LINES", "40")

    monkeypatch.setattr(sys, "stdin", FakeStdin(tty=True))
    if patch_ui:
        # Resolved lazily: sys.stdout is whatever capsys has installed.
        monkeypatch.setattr(wizard, "_open_ui_stream", lambda: (sys.stdout, False))
    keyboard = Keyboard(keys)
    monkeypatch.setattr(click, "getchar", keyboard)

    answers = list(prompts)

    def fake_ask(ui, label, **kwargs):
        if not answers:
            raise AssertionError(f"unexpected prompt: {label}")
        return answers.pop(0)

    monkeypatch.setattr(wizard, "_ask", fake_ask)

    confirms = []

    def fake_confirm(ui, label, **kwargs):
        confirms.append(label)
        return confirm(label) if callable(confirm) else confirm

    monkeypatch.setattr(wizard, "_confirm", fake_confirm)

    if front_door:
        monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    elif api_key is None:
        monkeypatch.setattr(wizard, "_offer_key_setup", lambda ui: None)

        def no_key(*args, **kwargs):
            raise MissingAPIKeyError()

        monkeypatch.setattr(wizard, "resolve_api_key", no_key)
    else:
        monkeypatch.setattr(wizard, "_offer_key_setup", lambda ui: None)
        monkeypatch.setattr(wizard, "resolve_api_key", lambda *args, **kwargs: api_key)

    client = object()
    monkeypatch.setattr(wizard, "build_client", lambda key: client)
    monkeypatch.setattr(wizard, "list_voices", lambda c: list(VOICES if voices is None else voices))

    synth_calls = []

    def fake_synthesize(c, text, settings):
        synth_calls.append((text, settings))
        return b"fake-mp3-bytes"

    monkeypatch.setattr(wizard, "synthesize", fake_synthesize)
    monkeypatch.setattr(wizard, "save", lambda audio, path: path)
    played = []
    monkeypatch.setattr(wizard, "play", lambda path: played.append(path))

    return SimpleNamespace(
        keyboard=keyboard,
        confirms=confirms,
        synth_calls=synth_calls,
        played=played,
        path=tmp_path / "config" / "vocalize" / "config.toml",
    )


def test_happy_path_writes_all_three_keys(monkeypatch, tmp_path, capsys):
    # voice: down to Rachel; model: down to flash; speed: down four rows to 0.9
    keys = [DOWN, ENTER, DOWN, ENTER, DOWN, DOWN, DOWN, DOWN, ENTER]
    ctx = _setup(monkeypatch, tmp_path, keys)

    wizard.run_wizard()

    assert ctx.path.read_text() == (
        'voice = "abc123"\n'
        'model = "eleven_flash_v2_5"\n'
        "speed = 0.9\n"
    )
    assert "Step 1 of 3 — Voice" in capsys.readouterr().out


def test_q_on_the_first_step_writes_nothing(monkeypatch, tmp_path, capsys):
    ctx = _setup(monkeypatch, tmp_path, ["q"])

    wizard.run_wizard()

    assert not ctx.path.exists()
    assert "Cancelled — nothing changed." in capsys.readouterr().out


def test_preview_plays_the_highlighted_voice_without_advancing(monkeypatch, tmp_path, capsys):
    ctx = _setup(monkeypatch, tmp_path, [DOWN, "p", "q"])

    wizard.run_wizard()

    assert [text for text, _settings in ctx.synth_calls] == [wizard.PREVIEW_TEXT]
    assert ctx.synth_calls[0][1].voice_id == "abc123"
    assert ctx.played == [wizard.PREVIEW_PATH]

    out = capsys.readouterr().out
    assert "Previewed abc123." in out
    assert "Step 2 of 3" not in out  # p must not move the wizard along
    assert not ctx.path.exists()


def test_manual_speed_reprompts_until_it_is_in_range(monkeypatch, tmp_path, capsys):
    # keep the voice, up to "keep current" for the model, then type a speed
    keys = [ENTER, UP, ENTER, "m"]
    ctx = _setup(monkeypatch, tmp_path, keys, prompts=["5", "0.9"])

    wizard.run_wizard()

    out = capsys.readouterr().out
    assert "must be between 0.7 and 1.2" in out
    # "keep current" wrote nothing for voice/model, so only speed lands
    assert ctx.path.read_text() == "speed = 0.9\n"


def test_unknown_keys_in_an_existing_file_survive(monkeypatch, tmp_path):
    ctx = _setup(monkeypatch, tmp_path, [DOWN, ENTER, UP, ENTER, ENTER])
    ctx.path.parent.mkdir(parents=True)
    ctx.path.write_text('voice = "old-voice"\nnotes = "keep me"\n')

    wizard.run_wizard()

    assert ctx.path.read_text() == 'voice = "abc123"\nnotes = "keep me"\n'


def test_keyless_mode_falls_back_to_manual_entry(monkeypatch, tmp_path, capsys):
    # m types a voice by hand; the rest of the wizard carries on normally
    keys = ["m", DOWN, ENTER, DOWN, DOWN, DOWN, DOWN, ENTER]
    ctx = _setup(monkeypatch, tmp_path, keys, prompts=["manual-voice"], api_key=None)

    wizard.run_wizard()

    assert "No voice list" in capsys.readouterr().out
    assert ctx.path.read_text() == (
        'voice = "manual-voice"\n'
        'model = "eleven_flash_v2_5"\n'
        "speed = 0.9\n"
    )


def test_a_missing_key_is_offered_a_setup_and_the_new_one_is_used(
    monkeypatch, tmp_path, capsys, fake_keychain
):
    # down to Rachel, up to "keep current" for the model, keep the speed
    ctx = _setup(monkeypatch, tmp_path, [DOWN, ENTER, UP, ENTER, ENTER], front_door=True)
    validated = []
    monkeypatch.setattr("vocalize.auth.validate_key", validated.append)
    monkeypatch.setattr(wizard, "prompt_for_key", lambda: "typed-key")

    wizard.run_wizard()

    assert "No API key found. Set one up now?" in ctx.confirms
    assert validated == ["typed-key"]  # validated before it was stored
    assert fake_keychain[(auth.SERVICE, auth.USERNAME)] == "typed-key"
    # The stored key is what fetched the list, so there's no degradation note
    out = capsys.readouterr().out
    assert "No voice list" not in out
    assert "abc123  Rachel" in out
    assert ctx.path.read_text() == 'voice = "abc123"\n'


def test_declining_the_key_setup_keeps_the_keyless_degradation(
    monkeypatch, tmp_path, capsys, fake_keychain
):
    def never(*args, **kwargs):
        raise AssertionError("declining must not ask for a key")

    keys = ["m", DOWN, ENTER, DOWN, DOWN, DOWN, DOWN, ENTER]
    ctx = _setup(
        monkeypatch, tmp_path, keys,
        prompts=["manual-voice"],
        front_door=True,
        confirm=lambda label: "Set one up now" not in label,
    )
    monkeypatch.setattr(wizard, "prompt_for_key", never)

    wizard.run_wizard()

    assert fake_keychain == {}
    assert "No voice list" in capsys.readouterr().out
    assert ctx.path.read_text() == (
        'voice = "manual-voice"\n'
        'model = "eleven_flash_v2_5"\n'
        "speed = 0.9\n"
    )


def test_a_failed_key_setup_names_the_reason_on_the_voice_frame(
    monkeypatch, tmp_path, capsys, fake_keychain
):
    def reject(key):
        raise TTSRequestError("401 unauthorized")

    # m types a voice by hand, up to "keep current" for the model, keep speed
    ctx = _setup(monkeypatch, tmp_path, ["m", UP, ENTER, ENTER],
                 prompts=["manual-voice"], front_door=True)
    monkeypatch.setattr(wizard, "prompt_for_key", lambda: "typed-key")
    monkeypatch.setattr("vocalize.auth.validate_key", reject)

    wizard.run_wizard()

    captured = capsys.readouterr()
    # The stderr line is erased from the screen by the next frame's clear,
    # so the reason has to survive on the frame itself.
    assert "No voice list (key setup failed: 401 unauthorized)" in captured.out
    assert "could not store that key — 401 unauthorized" in captured.err
    assert fake_keychain == {}
    assert ctx.path.read_text() == 'voice = "manual-voice"\n'


def test_non_tty_stdin_is_a_clean_error(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    runner = CliRunner()

    result = runner.invoke(main, ["config"])

    assert result.exit_code != 0
    assert isinstance(result.exception, ConfigError)
    assert "interactive terminal" in str(result.exception)
    assert not (tmp_path / "config" / "vocalize" / "config.toml").exists()


def test_declining_the_confirm_writes_nothing(monkeypatch, tmp_path, capsys):
    keys = [DOWN, ENTER, DOWN, ENTER, DOWN, DOWN, DOWN, DOWN, ENTER]
    ctx = _setup(monkeypatch, tmp_path, keys, confirm=False)

    wizard.run_wizard()

    assert not ctx.path.exists()
    assert "Cancelled — nothing changed." in capsys.readouterr().out


def test_an_unusable_current_value_does_not_block_the_wizard(monkeypatch, tmp_path, capsys):
    keys = [ENTER, UP, ENTER, DOWN, DOWN, DOWN, DOWN, ENTER]
    ctx = _setup(monkeypatch, tmp_path, keys)
    ctx.path.parent.mkdir(parents=True)
    ctx.path.write_text("speed = 5.0\n")

    wizard.run_wizard()

    assert "ignoring an unusable current setting" in capsys.readouterr().err
    assert ctx.path.read_text() == "speed = 0.9\n"


def test_unset_removes_an_existing_speed_key(monkeypatch, tmp_path):
    # keep voice, up to "keep current" for the model, up to "unset" for speed
    ctx = _setup(monkeypatch, tmp_path, [ENTER, UP, ENTER, UP, UP, UP, ENTER])
    ctx.path.parent.mkdir(parents=True)
    ctx.path.write_text("speed = 0.9\n")

    wizard.run_wizard()

    assert ctx.path.read_text() == ""
    assert load_config_file() == {}


def test_quotes_and_backslashes_in_a_manual_value_round_trip(monkeypatch, tmp_path):
    weird = r'say "hi"\now'
    ctx = _setup(monkeypatch, tmp_path, ["m", UP, ENTER, ENTER], prompts=[weird])

    wizard.run_wizard()

    assert ctx.path.read_text() == 'voice = "say \\"hi\\"\\\\now"\n'
    assert load_config_file()["voice"] == weird


def test_control_characters_in_a_manual_value_round_trip(monkeypatch, tmp_path):
    weird = "Bad\nNews"
    ctx = _setup(monkeypatch, tmp_path, ["m", UP, ENTER, ENTER], prompts=[weird])

    wizard.run_wizard()

    assert ctx.path.read_text() == 'voice = "Bad\\nNews"\n'
    assert load_config_file()["voice"] == weird


def test_wizards_choice_removes_the_shadowing_provider_table_key(monkeypatch, tmp_path, capsys):
    # voice: down to abc123; model: up to "keep current"; speed: keep current
    ctx = _setup(monkeypatch, tmp_path, [DOWN, ENTER, UP, ENTER, ENTER])
    ctx.path.parent.mkdir(parents=True)
    ctx.path.write_text(
        'voice = "old-voice"\n'
        "\n[providers.elevenlabs]\n"
        'voice = "table-voice"\n'
        "monthly_chars = 1000000\n"
    )

    wizard.run_wizard()

    assert ctx.path.read_text() == (
        'voice = "abc123"\n'
        "\n[providers.elevenlabs]\n"
        "monthly_chars = 1000000\n"
    )
    data = load_config_file()
    assert "voice" not in data["providers"]["elevenlabs"]
    assert data["providers"]["elevenlabs"]["monthly_chars"] == 1000000
    assert resolve_settings().voice_id == "abc123"
    assert (
        "[providers.elevenlabs] voice removed — the wizard's choice now applies"
        in capsys.readouterr().out
    )


def test_a_table_in_the_config_file_is_refused_not_flattened(monkeypatch, tmp_path):
    ctx = _setup(monkeypatch, tmp_path, [])  # must fail before the first keypress
    ctx.path.parent.mkdir(parents=True)
    original = 'voice = "keep-me"\ntags = ["a", "b"]\n\n[extras]\nx = 1\n'
    ctx.path.write_text(original)

    with pytest.raises(ConfigError, match="edit that file by hand"):
        wizard.run_wizard()

    assert ctx.path.read_text() == original


@pytest.mark.parametrize("key", ["\x1b", ""], ids=["escape", "eof"])
def test_escape_and_eof_both_cancel(monkeypatch, tmp_path, capsys, key):
    ctx = _setup(monkeypatch, tmp_path, [key])

    wizard.run_wizard()

    assert "Cancelled — nothing changed." in capsys.readouterr().out
    assert not ctx.path.exists()


def test_a_long_voice_list_is_windowed_onto_the_cursor(monkeypatch, tmp_path, capsys):
    voices = [{"id": f"v{n:02d}", "name": f"Voice {n:02d}"} for n in range(20)]
    _setup(monkeypatch, tmp_path, [DOWN] * 10 + ["q"], voices=voices)
    monkeypatch.setenv("LINES", "12")  # 12 - 8 chrome lines = a 4-row window

    wizard.run_wizard()

    last_frame = capsys.readouterr().out.split("Step 1 of 3 — Voice")[-1]
    assert "> v09" in last_frame  # the cursor is always on screen
    assert last_frame.count("…") == 2  # truncated at both ends
    assert "v00" not in last_frame
    assert "v15" not in last_frame


def test_keep_current_names_the_file_value_not_the_env_var(monkeypatch, tmp_path, capsys):
    ctx = _setup(monkeypatch, tmp_path, [ENTER, UP, ENTER, ENTER])
    ctx.path.parent.mkdir(parents=True)
    ctx.path.write_text('voice = "from-the-file"\n')
    monkeypatch.setenv("VOCALIZE_VOICE", "from-the-env")

    wizard.run_wizard()

    out = capsys.readouterr().out
    # Keeping writes nothing, so the row must name what the file holds
    assert "keep current (from-the-file)" in out
    assert "keep current (from-the-env)" not in out
    assert "voice → unchanged (from-the-file)" in out
    assert ctx.path.read_text() == 'voice = "from-the-file"\n'


def _piped(monkeypatch):
    """stdout relayed down a pipe (`op run`), with /dev/tty still openable."""
    tty = FakeTTY()
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(wizard, "_open_tty", lambda: tty)
    assert not sys.stdout.isatty()
    return tty


def test_piped_stdout_paints_to_dev_tty(monkeypatch, tmp_path):
    keys = [DOWN, ENTER, DOWN, ENTER, DOWN, DOWN, DOWN, DOWN, ENTER]
    ctx = _setup(monkeypatch, tmp_path, keys, patch_ui=False)
    tty = _piped(monkeypatch)

    wizard.run_wizard()  # not refused: the keyboard and /dev/tty are both there

    painted = tty.getvalue()
    assert "\x1b[2J\x1b[H" in painted  # our own clear; click.clear() would no-op
    assert "Step 1 of 3 — Voice" in painted
    assert "Step 2 of 3 — Model" in painted
    assert "Step 3 of 3 — Speed" in painted
    assert wizard.VOICE_HOTKEYS in painted
    assert "About to write:" in painted
    # None of it went down the pipe, and the stream we opened got closed
    assert "Step 1 of 3 — Voice" not in sys.stdout.getvalue()
    assert tty.closed_by_wizard
    assert ctx.path.read_text() == (
        'voice = "abc123"\n'
        'model = "eleven_flash_v2_5"\n'
        "speed = 0.9\n"
    )


def test_headless_still_refused(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(sys, "stdin", FakeStdin(tty=False))
    opened = []
    monkeypatch.setattr(wizard, "_open_tty", lambda: opened.append(True))

    with pytest.raises(ConfigError, match="interactive terminal"):
        wizard.run_wizard()

    assert opened == []  # a dead stdin is decided on its own, before any tty
    assert not (tmp_path / "config" / "vocalize" / "config.toml").exists()


def test_confirmation_line_reaches_stdout_too(monkeypatch, tmp_path):
    keys = [DOWN, ENTER, DOWN, ENTER, DOWN, DOWN, DOWN, DOWN, ENTER]
    ctx = _setup(monkeypatch, tmp_path, keys, patch_ui=False)
    tty = _piped(monkeypatch)

    wizard.run_wizard()

    assert f"Wrote {ctx.path}" in tty.getvalue()
    # Wrappers and logs only capture stdout, so the outcome has to land there
    assert sys.stdout.getvalue().strip() == f"Wrote {ctx.path}"


def test_chain_array_round_trips_byte_for_byte(monkeypatch, tmp_path):
    # keep voice, up to "keep current" for the model, keep speed
    ctx = _setup(monkeypatch, tmp_path, [ENTER, UP, ENTER, ENTER])
    ctx.path.parent.mkdir(parents=True)
    original = 'chain = ["elevenlabs", "google"]\n'
    ctx.path.write_text(original)

    wizard.run_wizard()

    assert ctx.path.read_text() == original


def test_providers_table_round_trips_with_blank_line_and_key_order(monkeypatch, tmp_path):
    ctx = _setup(monkeypatch, tmp_path, [ENTER, UP, ENTER, ENTER])
    ctx.path.parent.mkdir(parents=True)
    original = (
        "\n[providers.polly]\n"
        'region = "us-east-1"\n'
        'profile = "default"\n'
        "monthly_chars = 1000000\n"
    )
    ctx.path.write_text(original)

    wizard.run_wizard()

    assert ctx.path.read_text() == original


def test_render_config_text_puts_flat_keys_before_provider_tables():
    # providers listed first in the dict on purpose: the renderer must not
    # just echo insertion order — flat keys always come first, because a
    # bare key after a [section] header would parse into that section.
    data = {
        "providers": {
            "google": {"language": "en-US", "monthly_chars": 1000000},
            "say": {"voice": "Samantha"},
        },
        "voice": "keep-me",
        "chain": ["elevenlabs", "google", "say"],
    }

    text = wizard._render_config_text(data)

    assert text == (
        'voice = "keep-me"\n'
        'chain = ["elevenlabs", "google", "say"]\n'
        "\n[providers.google]\n"
        'language = "en-US"\n'
        "monthly_chars = 1000000\n"
        "\n[providers.say]\n"
        'voice = "Samantha"\n'
    )


def test_nested_table_inside_a_provider_is_refused_not_flattened(monkeypatch, tmp_path):
    ctx = _setup(monkeypatch, tmp_path, [])  # must fail before the first keypress
    ctx.path.parent.mkdir(parents=True)
    original = '[providers.google]\nlanguage = "en-US"\n\n[providers.google.extra]\nx = 1\n'
    ctx.path.write_text(original)

    with pytest.raises(ConfigError, match="edit that file by hand"):
        wizard.run_wizard()

    assert ctx.path.read_text() == original


def test_an_extras_table_is_still_refused_alongside_chain_and_providers(monkeypatch, tmp_path):
    ctx = _setup(monkeypatch, tmp_path, [])  # must fail before the first keypress
    ctx.path.parent.mkdir(parents=True)
    original = (
        'chain = ["elevenlabs", "say"]\n\n'
        '[providers.google]\nlanguage = "en-US"\n\n'
        "[extras]\nx = 1\n"
    )
    ctx.path.write_text(original)

    with pytest.raises(ConfigError, match="edit that file by hand"):
        wizard.run_wizard()

    assert ctx.path.read_text() == original


def test_a_list_containing_a_table_is_refused(monkeypatch, tmp_path):
    # an arbitrary key, not "chain" — that one has its own dedicated
    # provider-name validation in config.py and raises before this does
    ctx = _setup(monkeypatch, tmp_path, [])  # must fail before the first keypress
    ctx.path.parent.mkdir(parents=True)
    original = 'tags = [{name = "x"}]\n'
    ctx.path.write_text(original)

    with pytest.raises(ConfigError, match="edit that file by hand"):
        wizard.run_wizard()

    assert ctx.path.read_text() == original


def test_step_titles_are_labelled_elevenlabs(monkeypatch, tmp_path, capsys):
    keys = [DOWN, ENTER, DOWN, ENTER, DOWN, DOWN, DOWN, DOWN, ENTER]
    _setup(monkeypatch, tmp_path, keys)

    wizard.run_wizard()

    out = capsys.readouterr().out
    assert "Step 1 of 3 — Voice (ElevenLabs)" in out
    assert "Step 2 of 3 — Model (ElevenLabs)" in out
    assert "Step 3 of 3 — Speed (ElevenLabs)" in out


def test_the_current_voice_starts_under_the_cursor(monkeypatch, tmp_path, capsys):
    ctx = _setup(monkeypatch, tmp_path, [ENTER, ENTER, ENTER])
    monkeypatch.setenv("VOCALIZE_VOICE", "def456")  # after _setup, which clears it

    wizard.run_wizard()

    assert "> def456  Josh  (current)" in capsys.readouterr().out
    # Enter on the pre-selected rows re-affirms those values verbatim
    assert ctx.path.read_text() == (
        'voice = "def456"\n'
        f'model = "{DEFAULT_MODEL}"\n'
    )


def test_stt_table_round_trips_byte_for_byte(monkeypatch, tmp_path):
    """A 0.10.0 config has an [stt] table, and every writer meets it.

    The renderer had no way to write a table other than [providers.*], so
    a file with [stt] in it could not be rewritten at all: the wizard and
    `vocalize chain` both refused it as "edit that file by hand".
    """
    ctx = _setup(monkeypatch, tmp_path, [ENTER, UP, ENTER, ENTER])
    ctx.path.parent.mkdir(parents=True)
    original = (
        'chain = ["elevenlabs", "google"]\n'
        "\n[stt]\n"
        'model = "small.en"\n'
        "cleanup = false\n"
    )
    ctx.path.write_text(original)

    wizard.run_wizard()

    assert ctx.path.read_text() == original


def test_the_wizard_refuses_to_write_over_a_file_that_changed_while_it_asked(
    monkeypatch, tmp_path
):
    """DEC-005: three questions is plenty of time for another writer."""
    changed = 'voice = "from-another-writer"\n'

    def confirm_and_change_the_file(label):
        # The wizard's last question, so the file moves between the read
        # it started from and the write it is about to make.
        ctx.path.write_text(changed, encoding="utf-8")
        return True

    ctx = _setup(
        monkeypatch,
        tmp_path,
        [ENTER, UP, ENTER, ENTER],
        confirm=confirm_and_change_the_file,
    )
    ctx.path.parent.mkdir(parents=True)
    ctx.path.write_text('chain = ["elevenlabs", "google"]\n', encoding="utf-8")

    with pytest.raises(ConfigChangedError):
        wizard.run_wizard()

    assert ctx.path.read_text(encoding="utf-8") == changed
