import pytest
from click.testing import CliRunner

from vocalize import auth
from vocalize.cli import main
from vocalize.config import _load_dotenv_if_present
from vocalize.exceptions import AuthError, ProviderTransientError, TTSRequestError

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


@pytest.mark.parametrize("key", ["a", "ab", "abc", "abcd", "abcdefg"])
def test_masked_hides_a_key_too_short_to_preview(key):
    """APP-SECRETS: the preview is printed by `vocalize auth status` and by
    the portal's Keys tab, which reads whatever is in the environment with no
    validation at all. A truncated paste — or a short secret from another
    tool sharing the variable name — must not come back whole."""
    assert auth.masked(key) == "…"
    assert key not in auth.masked(key)


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


# --- per-provider keychain slots ---------------------------------------------


@pytest.mark.parametrize("provider", ["elevenlabs", "openai", "google"])
def test_store_read_delete_round_trip_per_provider(fake_keychain, provider):
    entry = (auth.SERVICE, auth.PROVIDER_USERNAMES[provider])

    assert auth.stored_key(provider) is None

    auth.store_key(SECRET, provider)

    assert fake_keychain[entry] == SECRET
    assert auth.stored_key(provider) == SECRET

    auth.delete_key(provider)

    assert entry not in fake_keychain
    assert auth.stored_key(provider) is None


def test_logout_of_openai_leaves_elevenlabs_untouched(fake_keychain):
    auth.store_key(SECRET, "elevenlabs")
    auth.store_key("openai-secret-key-1234567890", "openai")

    auth.delete_key("openai")

    assert auth.stored_key("elevenlabs") == SECRET
    assert auth.stored_key("openai") is None


@pytest.mark.parametrize(
    "provider, env_var", [("openai", "OPENAI_API_KEY"), ("google", "GOOGLE_API_KEY")]
)
def test_key_source_per_provider_env_var(monkeypatch, provider, env_var):
    monkeypatch.setenv(env_var, "provider-env-key")

    assert auth.key_source(None, provider) == "environment"


def test_an_elevenlabs_outage_is_not_a_verdict_on_the_key(monkeypatch):
    """`tts.list_voices` wraps a refused connection in the parent
    TTSRequestError, which callers read as "the key is invalid". The
    provider's own validate() classifies it as transient instead, so the
    portal answers 502 rather than wiping a good key the user pasted."""
    def down(client):
        raise TTSRequestError("Could not list voices: [Errno 61] Connection refused") from OSError(61, "refused")

    monkeypatch.setattr("vocalize.tts.build_client", lambda key: object())
    monkeypatch.setattr("vocalize.tts.list_voices", down)

    with pytest.raises(ProviderTransientError) as excinfo:
        auth.validate_key("sk_" + "a" * 40, "elevenlabs")
    assert "sk_" not in str(excinfo.value)


def test_validate_key_dispatches_to_the_provider_module(monkeypatch):
    seen = []

    class _Stub:
        def validate(self, key):
            seen.append(key)

    monkeypatch.setattr("vocalize.providers.get", lambda name: _Stub())

    auth.validate_key("provider-key", "openai")

    assert seen == ["provider-key"]


# --- polly_credential_status --------------------------------------------------


def test_polly_credential_status_reports_environment(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAEXAMPLE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "shh")

    assert auth.polly_credential_status("default") == "environment"


def test_polly_credential_status_reports_the_credentials_file(monkeypatch, tmp_path):
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)
    creds = tmp_path / "credentials"
    creds.write_text("[work]\naws_access_key_id = x\naws_secret_access_key = y\n")
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(creds))

    assert auth.polly_credential_status("work") == "~/.aws/credentials [work]"


def test_polly_credential_status_reports_not_configured(monkeypatch, tmp_path):
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "missing"))

    assert auth.polly_credential_status("default") == "not configured"


# --- the macOS backend: Apple's `security` tool (DEC-035) ----------------------


@pytest.fixture
def security(tmp_path):
    """A fake `security` tool: stateful, logs argv and stdin, speaks the real
    tool's exit codes (44 = not found). `deny` makes every call fail like a
    locked keychain."""
    state = tmp_path / "keychain"
    state.mkdir()
    script = tmp_path / "security"
    script.write_text(
        "#!/bin/sh\n"
        f'STATE="{state}"\n'
        'for a in "$@"; do printf "%s\\n" "$a"; done >> "$STATE/argv"\n'
        'if [ -e "$STATE/deny" ]; then echo "security: SecKeychainSearchCopyNext: User interaction is not allowed." >&2; exit 36; fi\n'
        'case "$1" in\n'
        '  -i)\n'
        '    cat > "$STATE/stdin"\n'
        '    sed -n \'s/.*-j "\\([^"]*\\)".*/\\1/p\' "$STATE/stdin" > "$STATE/comment"\n'
        '    sed -n \'s/.*-w "\\([^"]*\\)".*/\\1/p\' "$STATE/stdin" > "$STATE/value"\n'
        '    exit 0;;\n'
        '  find-generic-password)\n'
        '    [ -s "$STATE/value" ] || exit 44\n'
        '    for a in "$@"; do [ "$a" = "-w" ] && { cat "$STATE/value"; exit 0; }; done\n'
        '    printf \'    "acct"<blob>="x"\\n    "icmt"<blob>="%s"\\n    "svce"<blob>="vocalize"\\n\' "$(cat "$STATE/comment")"\n'
        '    exit 0;;\n'
        '  delete-generic-password)\n'
        '    [ -s "$STATE/value" ] || exit 44\n'
        '    rm -f "$STATE/value" "$STATE/comment"; exit 0;;\n'
        'esac\n'
        'exit 1\n',
        encoding="utf-8",
    )
    script.chmod(0o700)
    backend = auth._SecurityKeychain(binary=str(script))
    backend.state = state
    return backend


def _argv(backend):
    path = backend.state / "argv"
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def test_security_stores_the_key_on_stdin_never_in_argv(security):
    security.set_password("vocalize", "elevenlabs-api-key", "sk-secret-canary", comment="validated 2026-09-07")

    argv = _argv(security)
    assert "-i" in argv
    assert not any("sk-secret-canary" in entry for entry in argv)
    stdin = (security.state / "stdin").read_text(encoding="utf-8")
    assert stdin.startswith('add-generic-password -a "elevenlabs-api-key" -s "vocalize" -U ')
    assert '-j "validated 2026-09-07"' in stdin and '-w "sk-secret-canary"' in stdin
    assert security.get_password("vocalize", "elevenlabs-api-key") == "sk-secret-canary"


def test_security_reads_with_dash_w_and_reports_absence_as_none(security):
    assert security.get_password("vocalize", "elevenlabs-api-key") is None
    argv = _argv(security)
    assert argv[:5] == ["find-generic-password", "-s", "vocalize", "-a", "elevenlabs-api-key"] and argv[5] == "-w"


def test_security_get_raises_on_any_other_failure(security):
    (security.state / "deny").touch()

    with pytest.raises(auth.SecurityKeychainError):
        security.get_password("vocalize", "elevenlabs-api-key")


def test_security_refuses_a_key_with_a_quote_or_backslash(security):
    for bad in ('sk-"quoted"', "sk-back\\slash"):
        with pytest.raises(auth.SecurityKeychainError):
            security.set_password("vocalize", "elevenlabs-api-key", bad)
    assert not (security.state / "stdin").exists()  # nothing reached the tool


def test_security_store_verifies_the_read_back(security, monkeypatch):
    original = security.get_password
    monkeypatch.setattr(security, "get_password", lambda service, username: "something else")

    with pytest.raises(auth.SecurityKeychainError):
        security.set_password("vocalize", "elevenlabs-api-key", "sk-key")
    monkeypatch.setattr(security, "get_password", original)


def test_security_store_deletes_the_old_item_instead_of_updating_it(security):
    """macOS pins an item to the binary that created it, and `-U` keeps that
    list. An item written by an older vocalize (keyring, another Python) is
    therefore unreadable by `security` until it is deleted and recreated —
    which is the migration `login` is documented to perform."""
    security.set_password("vocalize", "elevenlabs-api-key", "sk-old")
    (security.state / "argv").unlink()

    security.set_password("vocalize", "elevenlabs-api-key", "sk-new")

    argv = _argv(security)
    assert argv.index("delete-generic-password") < argv.index("-i")
    assert security.get_password("vocalize", "elevenlabs-api-key") == "sk-new"


def test_security_delete_speaks_password_delete_error(security):
    from keyring.errors import PasswordDeleteError

    with pytest.raises(PasswordDeleteError):
        security.delete_password("vocalize", "elevenlabs-api-key")  # 44: nothing there

    security.set_password("vocalize", "elevenlabs-api-key", "sk-key")
    security.delete_password("vocalize", "elevenlabs-api-key")
    assert security.get_password("vocalize", "elevenlabs-api-key") is None


def test_security_comment_is_read_without_the_secret(security):
    security.set_password("vocalize", "openai-api-key", "sk-key", comment="validated 2026-09-07")
    (security.state / "argv").unlink()

    assert security.comment("vocalize", "openai-api-key") == "validated 2026-09-07"
    assert "-w" not in _argv(security)


def test_security_backend_stamps_the_date_and_status_shows_it(security, monkeypatch, no_env_key):
    monkeypatch.setattr(auth, "_backend", lambda: security)
    monkeypatch.setattr(auth, "_today", lambda: "2026-09-07")

    auth.store_key("sk-el-abcdefgh1234", "elevenlabs")
    auth.store_key("sk-oa-abcdefgh1234", "openai")

    assert auth.validated_on("elevenlabs") == "2026-09-07"
    result = CliRunner().invoke(main, ["auth", "status"])
    assert result.exit_code == 0, result.output
    assert "Validated: 2026-09-07" in result.output
    assert "openai: keychain (sk-o…, validated 2026-09-07)" in result.output
    assert "sk-el-abcdefgh1234" not in result.output and "sk-oa-abcdefgh1234" not in result.output


def test_security_validated_on_is_none_without_a_stamp(fake_keychain):
    fake_keychain[("vocalize", "elevenlabs-api-key")] = "sk-x"

    assert auth.validated_on("elevenlabs") is None  # the keyring fake keeps no comment


def test_security_is_the_backend_on_macos_and_keyring_elsewhere(monkeypatch):
    # conftest replaces `_backend` for every test; the platform choice lives
    # one function down so it can be checked without touching a real keychain.
    monkeypatch.setattr(auth.sys, "platform", "darwin")
    monkeypatch.setattr(auth.os.path, "exists", lambda path: path == auth._SECURITY)
    assert isinstance(auth._default_backend(), auth._SecurityKeychain)

    monkeypatch.setattr(auth.sys, "platform", "linux")
    assert auth._default_backend().__name__ == "keyring"


# --- the Anthropic slot ----------------------------------------------------


def test_anthropic_is_a_credential_choice_and_a_slot_but_not_a_provider():
    assert auth.CREDENTIAL_CHOICES == auth.PROVIDER_NAMES + ("anthropic",)
    assert "anthropic" not in auth.PROVIDER_NAMES
    assert "anthropic" in auth.KEY_SLOTS
    assert auth.PROVIDER_USERNAMES["anthropic"] == "anthropic-api-key"
    assert auth.PROVIDER_ENV_VARS["anthropic"] == "ANTHROPIC_API_KEY"


def test_anthropic_key_reaches_the_security_tool_on_stdin_never_in_argv(security, monkeypatch):
    """T-43: the slot goes through the same door as every other key — the
    tool's stdin — so `ps` never shows it and no shell log holds it."""
    monkeypatch.setattr(auth, "_backend", lambda: security)

    auth.store_key("sk-ant-secret-canary", "anthropic")

    argv = _argv(security)
    assert not any("sk-ant-secret-canary" in entry for entry in argv)
    stdin = (security.state / "stdin").read_text(encoding="utf-8")
    assert '-a "anthropic-api-key"' in stdin and '-w "sk-ant-secret-canary"' in stdin
    assert auth.stored_key("anthropic") == "sk-ant-secret-canary"
    assert auth.validated_on("anthropic") == auth._today()


def test_a_key_longer_than_any_provider_issues_is_refused_before_any_request(monkeypatch, fake_keychain):
    called = []
    monkeypatch.setattr(auth, "validate_key", lambda *a, **k: called.append(a))

    with pytest.raises(auth.AuthError, match="longer than any provider"):
        auth.login("k" * (auth.KEY_MAX_CHARS + 1), "anthropic")

    assert called == []
    assert not fake_keychain
