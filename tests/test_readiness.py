"""Tests for vocalize.readiness — see its module docstring for the threading
contract this file exercises: daemon threads, joined with a timeout, at most
one in-flight probe per row name, and a function that never raises.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from click.testing import CliRunner

import vocalize.readiness as readiness_module
from vocalize import app as app_module
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
    say = [r for r in rows if r.name == "say"]
    assert say == [Row("say", "ok", "built into macOS, nothing to install", "")]
    # No cleanup was ever opted into, so there is no llm model row.
    assert [r for r in rows if r.name == "llm model"] == []


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


@pytest.mark.parametrize("reason", [
    "not installed — run: vocalize local install",
    "kokoro-v1.0.onnx is the wrong size — reinstall with: vocalize local install",
    "its locked runtime is missing or out of date — run `vocalize local install` again",
])
def test_kokoro_row_names_its_fix_once(monkeypatch, reason):
    """Mac Studio run: 'run: vocalize local install — vocalize local install'."""
    monkeypatch.setattr("vocalize.providers.kokoro.installed", lambda model_dir=None: (False, reason))
    row = readiness({"chain": ["kokoro"]})[0]

    rendered = f"{row.detail} — {row.action}" if row.action else row.detail
    assert rendered.count("vocalize local install") == 1
    assert row.state == "warn"


def test_kokoro_row_keeps_the_command_as_its_action(monkeypatch):
    monkeypatch.setattr(
        "vocalize.providers.kokoro.installed",
        lambda model_dir=None: (False, "not installed — run: vocalize local install"),
    )
    row = readiness({"chain": ["kokoro"]})[0]

    assert row == Row("kokoro", "warn", "not installed", "vocalize local install")


def test_raising_probe_yields_warn_row():
    def boom():
        raise RuntimeError("kaboom")

    readiness_module._PROBES["boom"] = boom
    rows = readiness({"chain": ["say"]})
    row = next(r for r in rows if r.name == "boom")
    assert row.state == "warn"
    assert "RuntimeError" in row.detail


def test_blocked_probe_yields_warn_within_timeout():
    event = threading.Event()  # released in the finally, or its thread outlives us
    readiness_module._PROBES["blocked"] = event.wait

    try:
        start = time.monotonic()
        rows = readiness({"chain": ["say"]}, timeout=0.2)
        elapsed = time.monotonic() - start

        row = next(r for r in rows if r.name == "blocked")
        assert row.state == "warn"
        assert "still checking" in row.detail
        assert elapsed < 0.2 + 0.5
    finally:
        event.set()


def test_repeated_call_reuses_thread_across_calls():
    event = threading.Event()  # released in the finally, or its thread outlives us
    readiness_module._PROBES["wedged"] = event.wait

    try:
        readiness({"chain": ["say"]}, timeout=0.05)
        count_after_first = threading.active_count()
        readiness({"chain": ["say"]}, timeout=0.05)
        count_after_second = threading.active_count()

        assert count_after_second == count_after_first
    finally:
        event.set()


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
    assert row.name == "chain"
    assert row.state == "fail"


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


def test_stale_pruning_leaves_non_provider_names_alone():
    """Non-provider names are the design's stated test/verification seam and
    must never be pruned just because they aren't in the current chain.
    """
    readiness_module._PROBES["hand-registered"] = lambda: Row("hand-registered", "ok", "", "")
    readiness({"chain": ["say"]})
    readiness({"chain": ["kokoro"]})
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


LOCAL_CLEANUP = {"chain": ["say"], "stt": {"cleanup": "local"}}


@pytest.mark.parametrize("file_config", [
    {"chain": ["say"]},
    {"chain": ["say"], "stt": {"cleanup": "off"}},
    {"chain": ["say"], "stt": {"cleanup": "claude-cli"}},  # 0.15: becomes off
    {"chain": ["say"], "stt": {"cleanup": "nonsense"}},  # never raises
])
def test_the_llm_model_row_is_absent_unless_cleanup_is_local(file_config):
    assert "llm model" not in [row.name for row in readiness(file_config)]


def test_the_llm_model_row_appears_with_local_cleanup_and_goes_again():
    assert _row(readiness(LOCAL_CLEANUP), "llm model").state == "warn"
    assert "llm model" not in [row.name for row in readiness({"chain": ["say"]})]


def test_status_exits_0_on_a_machine_that_never_opted_into_cleanup(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("VOCALIZE_CHAIN", raising=False)
    (tmp_path / "vocalize").mkdir()
    (tmp_path / "vocalize" / "config.toml").write_text('chain = ["say"]\n', encoding="utf-8")

    result = CliRunner().invoke(main, ["status"])

    assert result.exit_code == 0, result.output
    assert "llm model" not in result.output


def test_llm_model_row_when_installed(monkeypatch):
    from vocalize.local import install as install_module
    from vocalize.local import llm_manifest
    _original = install_module.installed
    def _fake_installed(manifest, **kw):
        if manifest is llm_manifest:
            return (True, "")
        return _original(manifest, **kw)
    monkeypatch.setattr(install_module, "installed", _fake_installed)
    row = _row(readiness(LOCAL_CLEANUP), "llm model")
    assert row.state == "ok"
    assert row.detail == "installed and ready"


def test_llm_model_row_ram_gate_names_measured_and_required(monkeypatch):
    from vocalize import local as local_module
    from vocalize.local import install as install_module
    monkeypatch.setattr(install_module, "installed", lambda m, **kw: (False, "not installed"))
    monkeypatch.setattr(local_module, "physical_ram_bytes", lambda: 8 * 1024**3)
    row = _row(readiness(LOCAL_CLEANUP), "llm model")
    assert row.state == "warn"
    assert "8.0 GB RAM, needs 12.0 GB" in row.detail
    assert "disable cleanup until a compatible local model is installed" in row.detail


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

    assert [row.name for row in rows if row.name != "say"] == list(readiness_module.STT_ROW_NAMES)
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


def _engines(monkeypatch, *, parakeet, whisper, supported=True):
    from vocalize.local import install as install_module
    from vocalize.local import parakeet_manifest, whisper_manifest

    def installed(manifest, **kw):
        return (parakeet if manifest is parakeet_manifest else whisper), ""

    monkeypatch.setattr(install_module, "installed", installed)
    monkeypatch.setattr(parakeet_manifest, "supported", lambda: supported)
    assert whisper_manifest.MODELS


def test_the_stt_model_row_names_both_engines(stt_machine, monkeypatch):
    _engines(monkeypatch, parakeet=True, whisper=True)
    row = _row(readiness({"chain": ["say"]}), "stt model")
    assert row.state == "ok"
    assert row.detail.startswith("Parakeet on disk; Whisper ")


def test_the_stt_model_row_says_parakeet_is_missing(stt_machine, monkeypatch):
    _engines(monkeypatch, parakeet=False, whisper=True)
    row = _row(readiness({"chain": ["say"]}), "stt model")
    assert row.state == "ok"
    assert "Parakeet not installed" in row.detail and "Whisper" in row.detail


def test_the_stt_model_row_omits_parakeet_where_it_cannot_run(stt_machine, monkeypatch):
    _engines(monkeypatch, parakeet=False, whisper=True, supported=False)
    row = _row(readiness({"chain": ["say"]}), "stt model")
    assert row.state == "ok"
    assert "Parakeet" not in row.detail


def test_the_stt_model_row_is_ok_with_parakeet_alone_under_auto(stt_machine, monkeypatch):
    # With auto, `vocalize transcribe` falls back to Parakeet, so a machine
    # with only Parakeet can dictate and transcribe.
    _engines(monkeypatch, parakeet=True, whisper=False)
    row = _row(readiness({"chain": ["say"]}), "stt model")
    assert row.state == "ok"
    assert "Parakeet on disk" in row.detail and "transcribe cannot run" not in row.detail


def test_the_stt_model_row_follows_a_parakeet_engine(stt_machine, monkeypatch):
    _engines(monkeypatch, parakeet=True, whisper=False)
    row = _row(readiness({"chain": ["say"], "stt": {"engine": "parakeet"}}), "stt model")
    assert row == readiness_module.Row("stt model", "ok", "Parakeet ready", "")


def test_the_stt_model_row_follows_a_whisper_engine(stt_machine, monkeypatch):
    _engines(monkeypatch, parakeet=True, whisper=False)
    row = _row(readiness({"chain": ["say"], "stt": {"engine": "whisper"}}), "stt model")
    assert row.state == "fail"
    assert row.detail == "no Whisper model installed"
    assert row.action == "vocalize local install --stt --engine whisper"


_RUNTIME_REASON = "its locked runtime is missing or out of date — run `x` again"


def _upgraded(monkeypatch, *, ready=(), waiting=(), supported=True):
    """Manifests in `waiting` have verified files but no locked runtime (0.15)."""
    from vocalize.local import install as install_module
    from vocalize.local import (
        kokoro_manifest,
        llm_manifest,
        parakeet_manifest,
        whisper_manifest,
    )

    names = {kokoro_manifest: "kokoro", llm_manifest: "llm",
             parakeet_manifest: "parakeet", whisper_manifest: "whisper"}

    def installed(manifest, files=None, **kw):
        name = names.get(manifest)
        if name == "whisper" and files is not None:
            entry = files[0]["name"]
            hit = [m for m in (*ready, *waiting) if m.startswith("whisper:")
                   and whisper_manifest.file_for(m.split(":", 1)[1])["name"] == entry]
            name = hit[0] if hit else "whisper:none"
        if name in ready:
            return True, ""
        if name in waiting:
            return False, _RUNTIME_REASON
        return False, "not installed — run: x"

    monkeypatch.setattr(install_module, "installed", installed)
    monkeypatch.setattr(install_module, "runtime_installed", lambda manifest: False)
    monkeypatch.setattr(parakeet_manifest, "supported", lambda: supported)


def test_an_upgraded_parakeet_says_the_model_is_installed_and_names_the_command(
        stt_machine, monkeypatch):
    _upgraded(monkeypatch, waiting=("parakeet",))
    for stt in ({}, {"engine": "parakeet"}):
        row = _row(readiness({"chain": ["say"], "stt": stt}), "stt model")
        assert "no speech-to-text model installed" not in row.detail
        assert row.detail == "Parakeet model installed; its runtime must be installed once"
        assert row.action == "vocalize local install --stt --engine parakeet"


def test_an_upgraded_whisper_names_its_model_and_the_command(stt_machine, monkeypatch):
    _upgraded(monkeypatch, waiting=("whisper:small.en",), supported=False)
    row = _row(readiness({"chain": ["say"]}), "stt model")
    assert row.detail == "Whisper model small.en installed; its runtime must be installed once"
    assert row.action == "vocalize local install --stt --engine whisper --model small.en"


def test_nothing_on_disk_still_says_no_model_installed(stt_machine, monkeypatch):
    _upgraded(monkeypatch)
    row = _row(readiness({"chain": ["say"]}), "stt model")
    assert row.detail == "no speech-to-text model installed"


def test_upgrade_commands_name_each_engine_waiting_for_its_runtime(monkeypatch):
    _upgraded(monkeypatch, waiting=("kokoro", "parakeet", "whisper:large-v3-turbo-q5_0", "llm"))
    assert readiness_module.upgrade_commands() == [
        "vocalize local install",
        "vocalize local install --stt --engine parakeet",
        "vocalize local install --stt --engine whisper",
        "vocalize local install --llm",
    ]
    line = readiness_module.upgrade_line(readiness_module.upgrade_commands())
    assert line.startswith("After upgrading from an older version, run once: ")
    assert line.endswith("Model files already on disk are checked and not downloaded again.")


def test_upgrade_commands_are_empty_on_a_fresh_or_current_machine(monkeypatch):
    _upgraded(monkeypatch)
    assert readiness_module.upgrade_commands() == []
    _upgraded(monkeypatch, ready=("kokoro", "parakeet"))
    assert readiness_module.upgrade_commands() == []


def test_status_ends_with_the_upgrade_line(monkeypatch, tmp_path):
    from click.testing import CliRunner

    from vocalize.cli import main

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("VOCALIZE_CHAIN", "say")
    _upgraded(monkeypatch, waiting=("kokoro",))
    result = CliRunner().invoke(main, ["status"])
    assert result.output.rstrip().splitlines()[-1] == readiness_module.upgrade_line(
        ["vocalize local install"])
    json_result = CliRunner().invoke(main, ["status", "--json"])
    assert "After upgrading" not in json_result.output


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


def test_wedged_probes_are_joined_against_one_shared_deadline():
    """Three blocked probes cost one timeout, not three.

    The portal polls this while a keychain dialog is up, so joining each
    probe for the full timeout in turn is paid on every poll — 4x2s with
    the default chain, and the docstring's "never blocks longer than
    `timeout`" is a promise the caller reads as a bound on the call.
    """
    blocked = threading.Event()  # released in the finally: three daemon threads
    for name in ("wedged-1", "wedged-2", "wedged-3"):
        readiness_module._PROBES[name] = blocked.wait

    try:
        start = time.monotonic()
        rows = readiness({"chain": ["say"]}, timeout=0.5)
        elapsed = time.monotonic() - start

        assert sum(r.detail == readiness_module.STILL_CHECKING for r in rows) == 3
        assert elapsed < 1.0, f"three wedged probes took {elapsed:.2f}s of a 0.5s budget"
    finally:
        blocked.set()


# --- app rows -----------------------------------------------------------


def test_app_rows_absent_when_no_bundle_is_built(monkeypatch):
    monkeypatch.setattr(app_module, "bundle_state", lambda: "not built")
    rows = readiness({"chain": ["say"]})
    assert set(readiness_module.APP_ROW_NAMES).isdisjoint(row.name for row in rows)


def test_app_rows_present_once_a_bundle_exists(monkeypatch):
    monkeypatch.setattr(app_module, "bundle_state", lambda: "current")
    monkeypatch.setattr(
        app_module,
        "status_dict",
        lambda: {
            "bundle": "current",
            "agent": "loaded",
            "accessibility": "granted",
            "hotkeys": "ok",
            "hotkey_backend": "carbon",
            "vocalize": "/opt/homebrew/bin/vocalize",
        },
    )
    rows = readiness({"chain": ["say"]})
    names = {row.name for row in rows}
    assert set(readiness_module.APP_ROW_NAMES) <= names
    assert _row(rows, "app") == Row("app", "ok", "Vocalize.app is built", "")
    assert _row(rows, "app agent") == Row("app agent", "ok", "loaded", "")
    assert _row(rows, "accessibility") == Row("accessibility", "ok", "granted", "")


@pytest.mark.parametrize(
    "bundle,expected",
    [
        ("current", Row("app", "ok", "Vocalize.app is built", "")),
        ("stale", Row("app", "warn", "stale — run: vocalize app install", "")),
        ("not built", Row("app", "fail", "not built", "vocalize app install")),
        ("some-hostile-word", Row("app", "fail", "not built", "vocalize app install")),
    ],
)
def test_app_row_state_mapping(monkeypatch, bundle, expected):
    monkeypatch.setattr(app_module, "status_dict", lambda: {"bundle": bundle})
    assert readiness_module._app_row() == expected


@pytest.mark.parametrize(
    "agent,bundle,expected",
    [
        ("loaded", "current", Row("app agent", "ok", "loaded", "")),
        (
            "not running",
            "current",
            Row("app agent", "fail", "not running", "vocalize app restart"),
        ),
        (
            "not running",
            "stale",
            Row("app agent", "fail", "not running", "vocalize app install"),
        ),
        ("unknown", "current", Row("app agent", "warn", "unknown", "")),
        ("some-hostile-word", "current", Row("app agent", "warn", "unknown", "")),
    ],
)
def test_app_agent_row_state_mapping(monkeypatch, agent, bundle, expected):
    monkeypatch.setattr(app_module, "status_dict", lambda: {"agent": agent, "bundle": bundle})
    assert readiness_module._app_agent_row() == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        ("granted", Row("accessibility", "ok", "granted", "")),
        (
            "not granted",
            Row(
                "accessibility",
                "warn",
                "not granted",
                "grant Accessibility to Vocalize in System Settings",
            ),
        ),
        ("unknown", Row("accessibility", "warn", "unknown", "")),
    ],
)
def test_accessibility_row_state_mapping(monkeypatch, value, expected):
    monkeypatch.setattr(app_module, "status_dict", lambda: {"accessibility": value})
    assert readiness_module._accessibility_row() == expected


def test_hostile_app_status_word_never_reaches_a_row_detail_unchanged(monkeypatch):
    """`app.status` is untrusted (DEC-028): a word outside the known
    vocabulary must read as "unknown" on the row, never pass through raw."""
    hostile = "<script>alert(1)</script>"
    monkeypatch.setattr(
        app_module, "status_dict", lambda: {"accessibility": hostile, "agent": hostile}
    )

    accessibility_row = readiness_module._accessibility_row()
    agent_row = readiness_module._app_agent_row()

    assert accessibility_row.detail == "unknown"
    assert hostile not in accessibility_row.detail
    assert hostile not in accessibility_row.action
    assert agent_row.detail == "unknown"
    assert hostile not in agent_row.detail
    assert hostile not in agent_row.action


# --- doctor -----------------------------------------------------------

_DOCTOR_ONLY_NAMES = (
    "cli path", "uv", "swiftc", "shebang", "hammerspoon",
    "services shortcut", "cli start-up", "app bundle", "transcripts folder",
)


@pytest.fixture
def _no_real_tools(monkeypatch, tmp_path):
    """Keep the doctor_rows() composition tests off whatever toolchain
    (and transcripts folder) happens to exist on the machine running the suite:
    no real `which`, `xcrun` or `vocalize` subprocess, and HOME pointed at
    tmp_path so the default transcripts folder is never the developer's real
    ~/Documents. Only the composition (names, order) is under test here —
    the state each probe reports for a real machine is covered
    probe-by-probe above with an explicit fake.
    """
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setenv("HOME", str(tmp_path))

    def boom(argv, **kw):
        raise FileNotFoundError(2, "No such file or directory", argv[0])

    monkeypatch.setattr(readiness_module.subprocess, "run", boom)


def test_doctor_includes_every_provider_regardless_of_chain(_no_real_tools):
    rows = readiness_module.doctor_rows({"chain": ["say"]})
    names = {row.name for row in rows}
    from vocalize.auth import PROVIDER_NAMES

    assert set(PROVIDER_NAMES) <= names


def test_doctor_includes_stt_rows_even_when_never_set_up(_no_real_tools):
    rows = readiness_module.doctor_rows({})
    names = {row.name for row in rows}
    assert set(readiness_module.STT_ROW_NAMES) <= names


def test_doctor_includes_app_rows_even_when_no_bundle_is_built(monkeypatch, _no_real_tools):
    monkeypatch.setattr(app_module, "bundle_state", lambda: "not built")
    rows = readiness_module.doctor_rows({})
    names = {row.name for row in rows}
    assert set(readiness_module.APP_ROW_NAMES) <= names


def test_doctor_appends_the_toolchain_rows_in_order(_no_real_tools):
    rows = readiness_module.doctor_rows({})
    names = [row.name for row in rows]
    tail = names[-len(_DOCTOR_ONLY_NAMES):]
    assert tail == list(_DOCTOR_ONLY_NAMES)


def test_doctor_never_raises_when_a_probe_blows_up(monkeypatch, tmp_path, _no_real_tools):
    monkeypatch.setattr(
        "vocalize.config._validate_transcribe_table",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    rows = readiness_module.doctor_rows({})
    row = _row(rows, "transcripts folder")
    assert row.state == "warn"
    assert "boom" not in row.detail  # exception message never leaks (see run_probes)


def test_cli_path_row_ok_when_a_known_candidate(monkeypatch):
    candidate = app_module.BINARY_CANDIDATES[1]  # /opt/homebrew/bin/vocalize
    monkeypatch.setattr(shutil, "which", lambda name: str(candidate))
    row = readiness_module._cli_path_row()
    assert row == Row("cli path", "ok", str(candidate), "")


def test_cli_path_row_warns_with_the_override_command_otherwise(monkeypatch, tmp_path):
    odd = tmp_path / "vocalize"
    monkeypatch.setattr(shutil, "which", lambda name: str(odd))
    row = readiness_module._cli_path_row()
    assert row.state == "warn"
    assert row.action == app_module.override_command(odd)


def test_cli_path_row_warns_when_not_on_path(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    row = readiness_module._cli_path_row()
    assert row.state == "warn"
    assert row.action == ""


def test_uv_row_ok_when_on_path(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "/opt/homebrew/bin/uv" if name == "uv" else None)
    assert readiness_module._uv_row().state == "ok"


def test_uv_row_fails_with_an_install_hint(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    row = readiness_module._uv_row()
    assert row.state == "fail"
    assert "astral.sh" in row.action


def test_swiftc_row_ok_when_xcrun_finds_it(monkeypatch):
    monkeypatch.setattr(
        readiness_module.subprocess, "run",
        lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "/usr/bin/swiftc\n", ""),
    )
    row = readiness_module._swiftc_row()
    assert row == Row("swiftc", "ok", "/usr/bin/swiftc", "")


def test_swiftc_row_fails_when_xcrun_cannot_find_it(monkeypatch):
    monkeypatch.setattr(
        readiness_module.subprocess, "run",
        lambda argv, **kw: subprocess.CompletedProcess(argv, 1, "", "error: unable to find"),
    )
    row = readiness_module._swiftc_row()
    assert row.state == "fail"
    assert row.action == "xcode-select --install"


def test_swiftc_row_fails_when_xcrun_is_missing(monkeypatch):
    def boom(argv, **kw):
        raise FileNotFoundError(2, "No such file or directory", "xcrun")

    monkeypatch.setattr(readiness_module.subprocess, "run", boom)
    row = readiness_module._swiftc_row()
    assert row.state == "fail"
    assert row.action == "xcode-select --install"


def test_shebang_row_ok_when_the_interpreter_exists(monkeypatch, tmp_path):
    interpreter = tmp_path / "python3"
    interpreter.write_text("#!/bin/sh\n")
    script = tmp_path / "vocalize"
    script.write_text(f"#!{interpreter}\n")
    monkeypatch.setattr(sys, "argv", [str(script)])
    row = readiness_module._shebang_row()
    assert row.state == "ok"
    assert row.detail == str(interpreter)


def test_shebang_row_fails_when_the_interpreter_is_gone(monkeypatch, tmp_path):
    script = tmp_path / "vocalize"
    script.write_text(f"#!{tmp_path / 'no-such-python'}\n")
    monkeypatch.setattr(sys, "argv", [str(script)])
    row = readiness_module._shebang_row()
    assert row.state == "fail"
    assert "uv tool install --reinstall vocalize-cli" in row.action


def test_shebang_row_ok_when_not_a_console_script(monkeypatch, tmp_path):
    script = tmp_path / "vocalize"
    script.write_text("import vocalize.cli\n")  # no shebang line at all
    monkeypatch.setattr(sys, "argv", [str(script)])
    assert readiness_module._shebang_row().state == "ok"


def test_shebang_row_ok_when_the_script_cannot_be_read(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", [str(tmp_path / "does-not-exist")])
    assert readiness_module._shebang_row().state == "ok"


def test_hammerspoon_row_warns_when_running(monkeypatch):
    monkeypatch.setattr(app_module, "hammerspoon_running", lambda: True)
    row = readiness_module._hammerspoon_row()
    assert row.state == "warn"
    assert "init.lua" in row.action


def test_hammerspoon_row_ok_when_not_running(monkeypatch):
    monkeypatch.setattr(app_module, "hammerspoon_running", lambda: False)
    assert readiness_module._hammerspoon_row() == Row("hammerspoon", "ok", "not running", "")


def test_services_shortcut_row_ok_when_no_conflicts(monkeypatch):
    monkeypatch.setattr(app_module, "services_shortcut_conflicts", lambda cfg: [])
    row = readiness_module._services_shortcut_row({})
    assert row == Row("services shortcut", "ok", "none assigned", "")


def test_services_shortcut_row_warns_when_conflicts_exist(monkeypatch):
    monkeypatch.setattr(
        app_module,
        "services_shortcut_conflicts",
        lambda cfg: [("cards.arda.vocalize.dictate", "Dictate with Vocalize", "ctrl+alt+cmd+d")],
    )
    row = readiness_module._services_shortcut_row({})
    assert row.state == "warn"
    assert "Dictate with Vocalize" in row.detail
    assert "ctrl+alt+cmd+d" in row.detail
    assert "System Settings" in row.action


def test_no_doctor_row_warns_about_a_quick_action_integrate_installs(
    monkeypatch, tmp_path, _no_real_tools
):
    """`vocalize integrate claude` installs "Dictate with Vocalize" on
    purpose, as an unbound Services-menu entry, so a row that warns on its
    existence never clears and tells the user to delete what this release
    ships. The real conflict is an assigned shortcut, which no probe can
    see."""
    workflow = tmp_path / "Dictate with Vocalize.workflow"
    workflow.mkdir()
    monkeypatch.setattr(app_module, "SERVICES_WORKFLOW", workflow)

    rows = readiness_module.doctor_rows({})

    assert not any("Quick Action" in row.detail for row in rows)
    assert not any(row.action == "remove it in Finder" for row in rows)


def test_cli_startup_row_ok_when_fast(monkeypatch):
    monkeypatch.setattr(
        readiness_module.subprocess, "run",
        lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "vocalize 0.13.0\n", ""),
    )
    ticks = iter([0.0, 0.05])
    monkeypatch.setattr(readiness_module.time, "monotonic", lambda: next(ticks))
    row = readiness_module._cli_startup_row()
    assert row.state == "ok"
    assert row.detail == "50 ms"


def test_cli_startup_row_warns_above_400ms(monkeypatch):
    monkeypatch.setattr(
        readiness_module.subprocess, "run",
        lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "vocalize 0.13.0\n", ""),
    )
    ticks = iter([0.0, 0.5])
    monkeypatch.setattr(readiness_module.time, "monotonic", lambda: next(ticks))
    row = readiness_module._cli_startup_row()
    assert row.state == "warn"
    assert row.detail == "500 ms"


def test_cli_startup_row_warns_on_a_nonzero_exit(monkeypatch):
    monkeypatch.setattr(
        readiness_module.subprocess, "run",
        lambda argv, **kw: subprocess.CompletedProcess(argv, 1, "", "boom"),
    )
    monkeypatch.setattr(readiness_module.time, "monotonic", lambda: 0.0)
    row = readiness_module._cli_startup_row()
    assert row.state == "warn"


def test_cli_startup_row_warns_when_vocalize_is_not_on_path(monkeypatch):
    def boom(argv, **kw):
        raise FileNotFoundError(2, "No such file or directory", "vocalize")

    monkeypatch.setattr(readiness_module.subprocess, "run", boom)
    assert readiness_module._cli_startup_row().state == "warn"


@pytest.mark.parametrize(
    "bundle,expected_state",
    [("current", "ok"), ("stale", "warn"), ("not built", "fail")],
)
def test_app_bundle_row_mirrors_the_app_row(monkeypatch, bundle, expected_state):
    monkeypatch.setattr(app_module, "status_dict", lambda: {"bundle": bundle})
    row = readiness_module._app_bundle_row()
    assert row.name == "app bundle"
    assert row.state == expected_state


def test_transcripts_folder_row_reports_not_created_yet(tmp_path, monkeypatch):
    folder = tmp_path / "Vocalize Notes"
    row = readiness_module._transcripts_folder_row({"notes": {"folder": str(folder)}})
    assert row == Row("transcripts folder", "ok", "not created yet", "")


def test_transcripts_folder_row_reads_the_transcribe_table(tmp_path):
    folder = tmp_path / "Transcripts"
    folder.mkdir()
    row = readiness_module._transcripts_folder_row({"transcribe": {"folder": str(folder)}})
    assert row == Row("transcripts folder", "ok", "0.0 MB on disk", "")


def test_transcripts_folder_row_reports_size_on_disk(tmp_path):
    folder = tmp_path / "Vocalize Notes"
    folder.mkdir()
    (folder / "one.md").write_bytes(b"x" * 1024 * 1024)
    (folder / "one.m4a").write_bytes(b"y" * 1024 * 1024)  # kept audio counts too
    row = readiness_module._transcripts_folder_row({"notes": {"folder": str(folder)}})
    assert row.state == "ok"
    assert row.detail == "2.0 MB on disk"


def test_transcripts_folder_row_warns_under_icloud_mobile_documents(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    folder = tmp_path / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "Vocalize Notes"
    row = readiness_module._transcripts_folder_row({"notes": {"folder": str(folder)}})
    assert row.state == "warn"
    assert "iCloud" in row.detail
