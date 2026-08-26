import sys
from types import SimpleNamespace

import click
import pytest
from click.testing import CliRunner

from vocalize import wizard
from vocalize.cli import main
from vocalize.config import DEFAULT_MODEL, load_config_file
from vocalize.exceptions import ConfigError, MissingAPIKeyError

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


def _setup(monkeypatch, tmp_path, keys, *, voices=None, prompts=(), confirm=True, api_key="fake-key"):
    """Point the wizard at a throwaway config file and a scripted keyboard."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    for var in ("VOCALIZE_VOICE", "VOCALIZE_MODEL", "VOCALIZE_SPEED"):
        monkeypatch.delenv(var, raising=False)
    # Pin the viewport so rendering never depends on the runner's terminal.
    monkeypatch.setenv("LINES", "40")

    monkeypatch.setattr(sys, "stdin", FakeStdin(tty=True))
    monkeypatch.setattr(click, "clear", lambda: None)
    keyboard = Keyboard(keys)
    monkeypatch.setattr(click, "getchar", keyboard)

    answers = list(prompts)

    def fake_prompt(text, **kwargs):
        if not answers:
            raise AssertionError(f"unexpected prompt: {text}")
        return answers.pop(0)

    monkeypatch.setattr(click, "prompt", fake_prompt)
    monkeypatch.setattr(click, "confirm", lambda *args, **kwargs: confirm)

    if api_key is None:

        def no_key(*args, **kwargs):
            raise MissingAPIKeyError()

        monkeypatch.setattr(wizard, "resolve_api_key", no_key)
    else:
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
