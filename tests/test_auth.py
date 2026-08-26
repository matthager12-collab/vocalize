import pytest
from click.testing import CliRunner

from vocalize import auth
from vocalize.cli import main
from vocalize.config import _load_dotenv_if_present
from vocalize.exceptions import AuthError, TTSRequestError

# Bound at import time on purpose: conftest's autouse fixture replaces the
# module attribute, so this reference is the only way to reach the real one.
real_load_dotenv = _load_dotenv_if_present

ENTRY = (auth.SERVICE, auth.USERNAME)
SECRET = "sk_supersecret1234567890"


@pytest.fixture
def no_env_key(monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)


def _fake_tts(monkeypatch, *, failure=None):
    """Swap the real ElevenLabs client out of the validation path."""
    seen = []

    def build_client(key):
        seen.append(key)
        return object()

    def list_voices(client):
        if failure is not None:
            raise TTSRequestError(failure)
        return [{"id": "abc123", "name": "Rachel"}]

    monkeypatch.setattr("vocalize.tts.build_client", build_client)
    monkeypatch.setattr("vocalize.tts.list_voices", list_voices)
    return seen


class _BrokenKeyring:
    """A backend that fails the way a locked or absent keychain does."""

    def get_password(self, service, username):
        raise RuntimeError("no recommended backend")

    def set_password(self, service, username, password):
        raise RuntimeError("no recommended backend")

    def delete_password(self, service, username):
        raise RuntimeError("no recommended backend")


class _FakeReadFailure:
    """Delete appears to work; the read-back that would confirm it doesn't."""

    def __init__(self, store):
        self._store = store

    def get_password(self, service, username):
        raise RuntimeError("keychain locked")

    def delete_password(self, service, username):
        from keyring.errors import PasswordDeleteError

        raise PasswordDeleteError("failed to delete password")


def _break_backend(monkeypatch):
    monkeypatch.setattr(auth, "_backend", lambda: _BrokenKeyring())


def test_store_read_delete_round_trip(fake_keychain):
    assert auth.stored_key() is None

    auth.store_key(SECRET)

    assert fake_keychain[ENTRY] == SECRET
    assert auth.stored_key() == SECRET

    auth.delete_key()

    assert ENTRY not in fake_keychain
    assert auth.stored_key() is None


def test_stored_key_is_none_when_the_backend_raises(monkeypatch):
    _break_backend(monkeypatch)

    assert auth.stored_key() is None


def test_store_key_reports_a_broken_backend(monkeypatch):
    _break_backend(monkeypatch)

    with pytest.raises(AuthError, match="Could not write"):
        auth.store_key(SECRET)


def test_delete_on_a_missing_entry_is_silent(fake_keychain):
    auth.delete_key()  # must not raise

    assert fake_keychain == {}


def test_delete_key_reports_a_broken_backend(monkeypatch):
    _break_backend(monkeypatch)

    with pytest.raises(AuthError, match="Could not delete"):
        auth.delete_key()


def test_a_denied_delete_is_not_reported_as_success(fake_keychain):
    # macOS raises PasswordDeleteError for a denial too, entry intact
    fake_keychain[ENTRY] = SECRET
    fake_keychain.deny_delete = True

    with pytest.raises(AuthError, match="STILL stored"):
        auth.delete_key()

    assert fake_keychain[ENTRY] == SECRET


def test_delete_will_not_claim_success_it_cannot_verify(monkeypatch, fake_keychain):
    fake_keychain[ENTRY] = SECRET
    keyring = _FakeReadFailure(fake_keychain)
    monkeypatch.setattr(auth, "_backend", lambda: keyring)

    with pytest.raises(AuthError, match="Could not confirm"):
        auth.delete_key()


@pytest.mark.parametrize(
    "failure",
    [ModuleNotFoundError("No module named 'keyrings.nope'"), AttributeError("no such backend")],
    ids=["module-not-found", "attribute-error"],
)
def test_stored_key_survives_a_backend_selection_failure(monkeypatch, failure):
    # PYTHON_KEYRING_BACKEND naming an uninstalled module fails at import
    # time, so it never surfaces as a KeyringError.
    class _Unselectable:
        def get_password(self, service, username):
            raise failure

    monkeypatch.setattr(auth, "_backend", lambda: _Unselectable())

    assert auth.stored_key() is None


def test_probe_keychain_tells_absent_apart_from_unreadable(monkeypatch, fake_keychain):
    assert auth.probe_keychain() == ("ok", None)

    fake_keychain[ENTRY] = SECRET
    assert auth.probe_keychain() == ("ok", SECRET)

    _break_backend(monkeypatch)
    status, reason = auth.probe_keychain()
    assert status == "error"
    assert "no recommended backend" in reason


def test_key_source_reports_the_flag(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "env-key")

    assert auth.key_source("flag-key") == "flag"


def test_key_source_reports_the_environment(monkeypatch, fake_keychain):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "env-key")
    fake_keychain[ENTRY] = SECRET

    assert auth.key_source(None) == "environment"


def test_key_source_reports_the_dotenv_file(monkeypatch, tmp_path, no_env_key):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("ELEVENLABS_API_KEY=from-cwd-file\n", encoding="utf-8")
    # setenv before delenv so monkeypatch restores the var whether or not it
    # was set beforehand — the real loader writes straight to os.environ.
    monkeypatch.setenv("ELEVENLABS_API_KEY", "placeholder")
    monkeypatch.delenv("ELEVENLABS_API_KEY")
    monkeypatch.setattr("vocalize.config._load_dotenv_if_present", real_load_dotenv)

    assert auth.key_source(None) == ".env file"


def test_key_source_reports_the_keychain(fake_keychain, no_env_key):
    fake_keychain[ENTRY] = SECRET

    assert auth.key_source(None) == "keychain"


def test_key_source_reports_not_found(no_env_key):
    assert auth.key_source(None) == "not found"


def test_masked_never_shows_more_than_four_characters():
    assert auth.masked(SECRET) == "sk_s…"
    assert SECRET not in auth.masked(SECRET)


def test_login_validates_before_storing(monkeypatch, fake_keychain):
    seen = _fake_tts(monkeypatch)

    assert auth.login(SECRET) == f"Stored the API key in {auth.WHERE}."
    assert seen == [SECRET]
    assert fake_keychain[ENTRY] == SECRET


def test_login_rejects_a_key_with_control_characters(monkeypatch, fake_keychain):
    reached = []
    monkeypatch.setattr("vocalize.auth.validate_key", reached.append)
    utf16_bytes = "s\x00k\x00_\x00a\x00b\x00"

    with pytest.raises(AuthError, match="encoding"):
        auth.login(utf16_bytes)

    assert reached == []  # never got near a request
    assert fake_keychain == {}


def test_cli_login_never_echoes_a_malformed_key(fake_keychain):
    utf16_bytes = "s\x00k\x00_\x00secretpart\x00"

    result = CliRunner().invoke(main, ["auth", "login", "--stdin"], input=f"{utf16_bytes}\n")

    assert result.exit_code == 1
    assert "control characters" in result.output
    assert "secretpart" not in result.output
    assert fake_keychain == {}


def test_login_scrubs_the_key_out_of_a_wrapped_api_error(monkeypatch, fake_keychain):
    # What h11 does: the rejected header value is quoted back in full
    _fake_tts(monkeypatch, failure=f"Illegal header value b'xi-api-key: {SECRET}'")

    result = CliRunner().invoke(main, ["auth", "login"], input=f"{SECRET}\n")

    assert result.exit_code == 1
    assert SECRET not in result.output
    assert "[key]" in result.output
    assert fake_keychain == {}


def test_cli_login_stores_a_prompted_key(monkeypatch, fake_keychain):
    _fake_tts(monkeypatch)

    result = CliRunner().invoke(main, ["auth", "login"], input=f"{SECRET}\n")

    assert result.exit_code == 0, result.output
    assert fake_keychain[ENTRY] == SECRET
    assert "keychain" in result.output
    assert SECRET not in result.output


def test_cli_login_stores_nothing_when_the_key_is_rejected(monkeypatch, fake_keychain):
    _fake_tts(monkeypatch, failure="401 unauthorized")

    result = CliRunner().invoke(main, ["auth", "login"], input="bad-key\n")

    assert result.exit_code == 1
    assert "401 unauthorized" in result.output
    assert fake_keychain == {}


def test_cli_login_reads_a_piped_key(monkeypatch, fake_keychain):
    seen = _fake_tts(monkeypatch)

    result = CliRunner().invoke(main, ["auth", "login", "--stdin"], input=f"{SECRET}\n")

    assert result.exit_code == 0, result.output
    assert seen == [SECRET]  # the trailing newline is not part of the key
    assert fake_keychain[ENTRY] == SECRET


def test_cli_login_refuses_an_empty_key(fake_keychain):
    result = CliRunner().invoke(main, ["auth", "login", "--stdin"], input="\n")

    assert result.exit_code == 1
    assert "No API key given" in result.output
    assert fake_keychain == {}


def test_cli_status_masks_the_key(fake_keychain, no_env_key):
    fake_keychain[ENTRY] = SECRET

    result = CliRunner().invoke(main, ["auth", "status"])

    assert result.exit_code == 0, result.output
    assert "API key source: keychain" in result.output
    assert "Key: sk_s…" in result.output
    assert SECRET not in result.output


def test_cli_status_is_informational_when_there_is_no_key(no_env_key):
    result = CliRunner().invoke(main, ["auth", "status"])

    assert result.exit_code == 0, result.output
    assert "API key source: not found" in result.output
    assert "vocalize auth login" in result.output


def test_cli_status_admits_the_keychain_is_unreadable(monkeypatch, no_env_key):
    _break_backend(monkeypatch)

    result = CliRunner().invoke(main, ["auth", "status"])

    assert result.exit_code == 0, result.output
    assert "keychain unavailable (no recommended backend)" in result.output
    assert "Unlock your keychain" in result.output
    assert "not found" not in result.output


def test_cli_logout_removes_the_stored_key(fake_keychain):
    fake_keychain[ENTRY] = SECRET

    result = CliRunner().invoke(main, ["auth", "logout"])

    assert result.exit_code == 0, result.output
    assert fake_keychain == {}
    assert "Removed" in result.output


def test_cli_logout_does_not_claim_a_denied_removal(fake_keychain):
    fake_keychain[ENTRY] = SECRET
    fake_keychain.deny_delete = True

    result = CliRunner().invoke(main, ["auth", "logout"])

    assert result.exit_code == 1
    assert "Removed" not in result.output
    assert "STILL stored" in result.output
    assert "rotate the key" in result.output
    assert fake_keychain[ENTRY] == SECRET
