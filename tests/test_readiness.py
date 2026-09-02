"""Tests for vocalize.readiness — see its module docstring for the threading
contract this file exercises: daemon threads, joined with a timeout, at most
one in-flight probe per row name, and a function that never raises.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time

import pytest
from click.testing import CliRunner

import vocalize.readiness as readiness_module
from vocalize import ledger
from vocalize.cli import main
from vocalize.readiness import Row, readiness


@pytest.fixture(autouse=True)
def _reset_readiness_registry():
    """Every test gets a clean probe registry.

    Without this, a probe registered (or a thread started) by one test
    would keep showing up in every later call to readiness() for the rest
    of the process — including the deliberately-forever-blocked probes a
    couple of tests below register on purpose.
    """
    readiness_module._PROBES.clear()
    readiness_module._inflight.clear()
    yield
    readiness_module._PROBES.clear()
    readiness_module._inflight.clear()


def _isolate_config(monkeypatch, tmp_path):
    """Point config_path() at an empty tmp_path so resolve_chain() falls
    back to the built-in default chain, not whatever the developer's real
    ~/.config/vocalize/config.toml happens to contain.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))


def test_say_row_is_always_ok():
    rows = readiness({"chain": ["say"]})
    assert rows == [Row("say", "ok", "local, no credentials needed", "")]


def test_missing_key_yields_fail_row(monkeypatch, fake_keychain):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    rows = readiness({"chain": ["elevenlabs"]})
    row = rows[0]
    assert row.name == "elevenlabs"
    assert row.state == "fail"
    assert "no API key" in row.detail
    assert "auth login" in row.action


def test_env_var_key_never_touches_keychain(monkeypatch, fake_keychain):
    """Design contract: key_source checks the env var first, so a key
    supplied via ELEVENLABS_API_KEY must never reach stored_key/the keychain.
    """
    monkeypatch.setenv("ELEVENLABS_API_KEY", "env-key-value")
    calls: list[str] = []
    original_stored_key = readiness_module.auth.stored_key

    def spy(provider="elevenlabs"):
        calls.append(provider)
        return original_stored_key(provider)

    monkeypatch.setattr("vocalize.auth.stored_key", spy)

    rows = readiness({"chain": ["elevenlabs"]})

    assert calls == []
    assert rows[0].state == "ok"
    assert rows[0].detail.startswith("key from environment")


def test_polly_row_ok_from_environment(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAEXAMPLE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    rows = readiness({"chain": ["polly"]})
    assert rows[0] == Row("polly", "ok", "credentials from environment", "")


def test_polly_row_fail_when_not_configured(monkeypatch):
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)
    monkeypatch.setattr(
        "vocalize.providers.polly._profile_in_credentials_file", lambda profile: False
    )
    rows = readiness({"chain": ["polly"]})
    row = rows[0]
    assert row.state == "fail"
    assert "AWS credentials" in row.detail


def test_kokoro_row_ok_when_installed(monkeypatch):
    monkeypatch.setattr("vocalize.providers.kokoro.installed", lambda model_dir=None: (True, ""))
    rows = readiness({"chain": ["kokoro"]})
    assert rows[0] == Row("kokoro", "ok", "installed and ready", "")


def test_kokoro_row_warn_when_not_installed(monkeypatch):
    monkeypatch.setattr(
        "vocalize.providers.kokoro.installed",
        lambda model_dir=None: (False, "not installed — run: vocalize local install"),
    )
    rows = readiness({"chain": ["kokoro"]})
    row = rows[0]
    assert row.state == "warn"
    assert row.action == "vocalize local install"


def test_budget_exhausted_yields_warn(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "env-key")
    ledger.record("elevenlabs", 1000)  # DEFAULT_CACHE_DIR is tmp_path via the autouse fixture
    file_config = {
        "chain": ["elevenlabs"],
        "providers": {"elevenlabs": {"monthly_chars": 500}},
    }
    rows = readiness(file_config)
    row = rows[0]
    assert row.state == "warn"
    assert "budget exhausted" in row.detail


def test_raising_probe_yields_warn_row():
    def boom():
        raise RuntimeError("kaboom")

    readiness_module._PROBES["boom"] = boom
    rows = readiness({"chain": ["say"]})
    row = next(r for r in rows if r.name == "boom")
    assert row.state == "warn"
    assert "RuntimeError" in row.detail


def test_raising_probe_never_leaks_exception_message(monkeypatch, tmp_path, fake_keychain):
    """A probe's exception message is untrusted (_PROBES is an open registry
    future callers register into) and must never reach Row.detail or
    `vocalize status` output — only the exception type may show.
    """
    secret = "sk-canary-0123456789abcdef"  # synthetic test value, not a real key

    def boom():
        raise RuntimeError(f"upstream said: Authorization: Bearer {secret}")

    readiness_module._PROBES["boom"] = boom
    rows = readiness({"chain": ["say"]})
    row = next(r for r in rows if r.name == "boom")
    assert secret not in row.detail
    assert "RuntimeError" in row.detail

    _isolate_config(monkeypatch, tmp_path)
    monkeypatch.setenv("ELEVENLABS_API_KEY", "env-key")
    runner = CliRunner()
    result = runner.invoke(main, ["status", "--json"])
    assert secret not in result.output


def test_blocked_probe_yields_warn_within_timeout():
    event = threading.Event()  # never set
    readiness_module._PROBES["blocked"] = event.wait

    start = time.monotonic()
    rows = readiness({"chain": ["say"]}, timeout=0.2)
    elapsed = time.monotonic() - start

    row = next(r for r in rows if r.name == "blocked")
    assert row.state == "warn"
    assert "still checking" in row.detail
    assert elapsed < 0.2 + 0.5


def test_repeated_call_reuses_thread_across_calls():
    event = threading.Event()  # never set
    readiness_module._PROBES["wedged"] = event.wait

    readiness({"chain": ["say"]}, timeout=0.05)
    count_after_first = threading.active_count()
    readiness({"chain": ["say"]}, timeout=0.05)
    count_after_second = threading.active_count()

    assert count_after_second == count_after_first


def test_registry_accepts_a_name_not_in_provider_names():
    """_PROBES is a plain name -> callable seam, not validated against
    auth.PROVIDER_NAMES (design § Readiness aggregation).
    """
    readiness_module._PROBES["totally-not-a-provider"] = (
        lambda: Row("totally-not-a-provider", "ok", "fine", "")
    )
    rows = readiness({"chain": ["say"]})
    assert any(r.name == "totally-not-a-provider" for r in rows)


def test_unknown_provider_in_chain_never_raises():
    rows = readiness({"chain": ["not-a-real-provider"]})
    row = rows[0]
    assert row.name == "not-a-real-provider"
    assert row.state == "warn"


def test_bad_vocalize_chain_env_var_degrades_to_a_row_never_raises(monkeypatch):
    """resolve_chain reads VOCALIZE_CHAIN itself and raises ConfigError on an
    unrecognized provider name — readiness() promises never to raise, so an
    unrecognized VOCALIZE_CHAIN (a typo, stale config, or a future caller
    that builds it from user input) must degrade to a row, not propagate.
    """
    monkeypatch.setenv("VOCALIZE_CHAIN", "bogus-provider")
    rows = readiness({"chain": ["say"]})
    assert len(rows) == 1
    row = rows[0]
    assert row.name == "chain"
    assert row.state == "fail"
    assert "bogus-provider" in row.detail


def test_status_json_still_valid_with_bad_vocalize_chain_env_var(monkeypatch, tmp_path):
    _isolate_config(monkeypatch, tmp_path)
    monkeypatch.setenv("VOCALIZE_CHAIN", "bogus-provider")
    runner = CliRunner()
    result = runner.invoke(main, ["status", "--json"])
    assert result.exit_code == 1
    rows = json.loads(result.output)
    assert rows and rows[0]["state"] == "fail"


def test_stale_provider_dropped_when_chain_changes(monkeypatch, fake_keychain):
    """A provider removed from the chain must not keep showing up forever,
    computed from the stale file_config it was last seen with (T-10: one
    row per chain link, for the *current* chain).
    """
    monkeypatch.setenv("ELEVENLABS_API_KEY", "env-key")
    rows = readiness({"chain": ["elevenlabs"]})
    assert [row.name for row in rows] == ["elevenlabs"]

    rows = readiness({"chain": ["say"]})
    assert [row.name for row in rows] == ["say"]


def test_stale_pruning_leaves_non_provider_names_alone():
    """Non-provider names are the design's stated test/verification seam and
    must never be pruned just because they aren't in the current chain.
    """
    readiness_module._PROBES["hand-registered"] = lambda: Row("hand-registered", "ok", "", "")
    readiness({"chain": ["say"]})
    readiness({"chain": ["elevenlabs"]})
    assert "hand-registered" in readiness_module._PROBES


def test_process_exits_promptly_with_a_probe_still_blocked():
    """Mirrors validate-exit.sh's own check: a daemon-thread probe that
    never returns must never keep the interpreter alive.
    """
    script = (
        "import threading, vocalize.readiness as r; "
        "r._PROBES['wedged-forever'] = lambda: threading.Event().wait(); "
        "r.readiness({'chain': ['say']}, timeout=0.2)"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=10, check=False
    )
    assert result.returncode == 0, result.stderr


def test_status_json_has_row_shape(monkeypatch, tmp_path, fake_keychain):
    _isolate_config(monkeypatch, tmp_path)
    monkeypatch.setenv("ELEVENLABS_API_KEY", "env-key")
    runner = CliRunner()
    result = runner.invoke(main, ["status", "--json"])
    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    assert rows
    for row in rows:
        assert set(row) == {"name", "state", "detail", "action"}


def test_status_never_prints_the_api_key_value(monkeypatch, tmp_path, fake_keychain):
    """Synthetic secret canary: status only ever reports *where* a key came
    from (key_source), never the key itself, in either output mode.
    """
    _isolate_config(monkeypatch, tmp_path)
    secret = "sk-canary-0123456789abcdef"  # synthetic test value, not a real key
    monkeypatch.setenv("ELEVENLABS_API_KEY", secret)
    runner = CliRunner()

    result_json = runner.invoke(main, ["status", "--json"])
    assert secret not in result_json.output

    result_plain = runner.invoke(main, ["status"])
    assert secret not in result_plain.output


def test_status_exit_code_zero_when_all_ok(monkeypatch, tmp_path, fake_keychain):
    _isolate_config(monkeypatch, tmp_path)
    monkeypatch.setenv("ELEVENLABS_API_KEY", "env-key")
    runner = CliRunner()
    result = runner.invoke(main, ["status"])
    assert result.exit_code == 0


def test_status_exit_code_one_when_a_row_fails(monkeypatch, tmp_path, fake_keychain):
    _isolate_config(monkeypatch, tmp_path)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    runner = CliRunner()
    result = runner.invoke(main, ["status"])
    assert result.exit_code == 1


def test_status_plain_output_names_each_provider(monkeypatch, tmp_path, fake_keychain):
    _isolate_config(monkeypatch, tmp_path)
    monkeypatch.setenv("ELEVENLABS_API_KEY", "env-key")
    runner = CliRunner()
    result = runner.invoke(main, ["status"])
    assert "elevenlabs" in result.output
    assert "say" in result.output


# --- the dictation rows (T-45) ----------------------------------------
#
# Four rows that only appear once dictation is set up, and none of which
# may launch the recorder app: a status screen (and the portal polling it)
# has to stay cheap and silent.


@pytest.fixture
def stt_machine(monkeypatch, tmp_path):
    """A machine with dictation set up. Returns knobs to break each part."""
    from vocalize import dictate
    from vocalize.local import install as install_module

    binary = install_module.recorder_binary()
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_text(
        '#!/bin/sh\nprintf "%s\\n" "Built-in Microphone" "Studio Mic"\nexit 0\n',
        encoding="utf-8",
    )
    binary.chmod(0o755)
    install_module.write_recorder_stamp()
    monkeypatch.setattr(
        install_module, "installed", lambda manifest, **kw: (True, "")
    )
    dictate.write_mic_status("authorized")
    return {"binary": binary}


def _row(rows, name):
    return next(row for row in rows if row.name == name)


def test_stt_rows_are_absent_on_a_machine_that_never_opted_in():
    rows = readiness({"chain": ["say"]})

    assert [row.name for row in rows] == ["say"]


def test_stt_rows_appear_once_dictation_is_set_up(stt_machine):
    rows = readiness({"chain": ["say"]})

    assert [row.name for row in rows][1:] == list(readiness_module.STT_ROW_NAMES)
    assert all(row.state == "ok" for row in rows)


def test_stt_rows_appear_from_the_config_table_alone(monkeypatch):
    """A configured `[stt]` table is enough, even before anything is built."""
    rows = readiness({"chain": ["say"], "stt": {"model": "base.en"}})

    assert _row(rows, "stt model").state == "fail"
    assert _row(rows, "recorder").state == "fail"


def test_stt_rows_disappear_again_when_the_table_is_removed(stt_machine, monkeypatch):
    from vocalize.local import install as install_module

    readiness({"chain": ["say"]})
    stt_machine["binary"].unlink()
    monkeypatch.setattr(install_module, "installed", lambda manifest, **kw: (False, "no"))

    rows = readiness({"chain": ["say"]})

    assert [row.name for row in rows] == ["say"]


def test_the_stt_model_row_names_what_is_on_disk(stt_machine):
    row = _row(readiness({"chain": ["say"]}), "stt model")

    assert row.state == "ok"
    assert "small.en" in row.detail


def test_the_stt_model_row_fails_with_nothing_installed(stt_machine, monkeypatch):
    from vocalize.local import install as install_module

    monkeypatch.setattr(install_module, "installed", lambda manifest, **kw: (False, "nope"))

    row = _row(readiness({"chain": ["say"]}), "stt model")

    assert row.state == "fail"
    assert row.action == "vocalize local install --stt"


def test_the_stt_recorder_row_fails_when_the_bundle_is_not_built(stt_machine):
    stt_machine["binary"].unlink()

    row = _row(readiness({"chain": ["say"], "stt": {}}), "recorder")

    assert row.state == "fail"
    assert row.action == "vocalize local install --stt"


@pytest.mark.parametrize(
    ("word", "state"),
    [("authorized", "ok"), ("denied", "fail"), ("notDetermined", "warn")],
)
def test_the_stt_microphone_row_reports_what_the_check_recorded(
    stt_machine, word, state
):
    from vocalize import dictate

    dictate.write_mic_status(word)

    assert _row(readiness({"chain": ["say"]}), "microphone").state == state


def test_the_stt_microphone_row_says_unknown_when_nothing_was_recorded(stt_machine):
    from vocalize import dictate

    dictate.mic_status_path().unlink()

    row = _row(readiness({"chain": ["say"]}), "microphone")

    assert row.state == "warn"
    assert row.action == "run: vocalize listen --check"


def test_the_stt_microphone_row_never_launches_the_recorder(stt_machine, monkeypatch):
    """The whole point of reading a file: `vocalize status` opens no app."""
    launches = []
    real_run = readiness_module.subprocess.run

    def spy(argv, **kwargs):
        launches.append(argv)
        return real_run(argv, **kwargs)

    monkeypatch.setattr(readiness_module.subprocess, "run", spy)

    readiness({"chain": ["say"]})

    for argv in launches:
        assert "/usr/bin/open" not in argv[0]
        assert argv[1:] == ["--list-devices"]  # the one permission-free call


def test_the_stt_input_device_row_reports_the_system_default(stt_machine):
    row = _row(readiness({"chain": ["say"]}), "input device")

    assert row.state == "ok"
    assert "2 available" in row.detail


def test_the_stt_input_device_row_confirms_a_configured_device(stt_machine):
    config = {"chain": ["say"], "stt": {"input_device": "Studio Mic"}}

    assert _row(readiness(config), "input device").state == "ok"


def test_the_stt_input_device_row_fails_when_the_configured_device_is_gone(stt_machine):
    config = {"chain": ["say"], "stt": {"input_device": "Unplugged Mic"}}

    row = _row(readiness(config), "input device")

    assert row.state == "fail"
    assert "list-devices" in row.action


def test_the_stt_input_device_row_fails_when_macos_sees_nothing(stt_machine):
    stt_machine["binary"].write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    stt_machine["binary"].chmod(0o755)

    row = _row(readiness({"chain": ["say"]}), "input device")

    assert row.state == "fail"
    assert "no input device" in row.detail


def test_the_stt_input_device_row_warns_when_the_recorder_cannot_be_asked(stt_machine):
    stt_machine["binary"].write_text("#!/bin/sh\nexit 3\n", encoding="utf-8")
    stt_machine["binary"].chmod(0o755)

    assert _row(readiness({"chain": ["say"]}), "input device").state == "warn"


def test_a_bad_stt_table_becomes_a_row_and_never_raises(stt_machine):
    """`readiness()` promises never to raise — an invalid device included."""
    config = {"chain": ["say"], "stt": {"input_device": "--serve"}}

    row = _row(readiness(config), "input device")

    assert row.state == "fail"
    assert "input_device" in row.action


def test_the_stt_rows_show_up_in_the_status_screen(stt_machine, monkeypatch, tmp_path):
    _isolate_config(monkeypatch, tmp_path)
    monkeypatch.setenv("VOCALIZE_CHAIN", "say")

    result = CliRunner().invoke(main, ["status", "--json"])

    names = [row["name"] for row in json.loads(result.output)]
    assert set(readiness_module.STT_ROW_NAMES) <= set(names)


# --- what the 0.10.0 release review found (DEC-014) -------------------


def test_a_stale_authorized_verdict_is_reported_with_its_age(stt_machine):
    """The grant can be revoked in System Settings and nothing tells us.

    `status` reads a cached verdict rather than launching the bundle
    (DEC-010), so the only honest thing it can do is say how old the
    answer is — and stop calling a day-old one `ok`.
    """
    from vocalize import dictate

    dictate.write_mic_status("authorized")
    old = time.time() - (readiness_module.MIC_STATUS_MAX_AGE + 3600)
    dictate.mic_status_path().write_text(f"authorized\n{old}\n", encoding="utf-8")

    row = _row(readiness({"chain": ["say"]}), "microphone")

    assert row.state == "warn"
    assert "d ago" in row.detail
    assert row.action == "run: vocalize listen --check"


def test_a_fresh_authorized_verdict_still_says_when_it_was_measured(stt_machine):
    from vocalize import dictate

    dictate.write_mic_status("authorized")

    row = _row(readiness({"chain": ["say"]}), "microphone")

    assert row.state == "ok"
    assert "as of" in row.detail


def test_concurrent_readiness_calls_never_raise(stt_machine):
    """The portal is a ThreadingHTTPServer; `readiness()` never raises.

    Two handler threads mutating `_PROBES` while a third iterated it
    raised "dictionary changed size during iteration" out of a function
    whose whole contract is that it does not.
    """
    configs = [{"chain": ["say"]}, {"chain": ["say", "kokoro"]}, {"chain": ["kokoro"]}]
    failures = []

    def poll(index):
        try:
            for _ in range(40):
                readiness(configs[index % len(configs)], timeout=1.0)
        except BaseException as exc:  # noqa: BLE001 — any raise is the failure
            failures.append(exc)

    threads = [threading.Thread(target=poll, args=(i,)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)

    assert failures == []


def test_a_running_probe_is_never_dropped_when_its_row_disappears(monkeypatch):
    """One wedged native call leaks one thread total, never one per poll.

    The portal polls with a changing config. Toggling `[stt]` out and back
    in used to pop the in-flight slot — losing the handle without stopping
    the thread — so the returning row started a second one for the same
    name.
    """
    release = threading.Event()
    started = []

    def wedged():
        started.append(1)
        release.wait(10)
        return Row("microphone", "ok", "", "")

    monkeypatch.setattr(readiness_module, "_microphone_row", wedged)
    monkeypatch.setattr(readiness_module, "stt_configured", lambda config: bool(config.get("stt")))
    on = {"chain": ["say"], "stt": {"model": "small.en"}}
    try:
        readiness(on, timeout=0.05)
        readiness({"chain": ["say"]}, timeout=0.05)  # [stt] removed
        readiness(on, timeout=0.05)  # and put back

        assert len(started) == 1
    finally:
        release.set()
        readiness_module._PROBES.pop("microphone", None)
        readiness_module._inflight.pop("microphone", None)
