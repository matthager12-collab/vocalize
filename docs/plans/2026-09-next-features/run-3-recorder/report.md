# Run 3 report: recorder bundle, build-at-install, `listen --check`

Branch `next-features`. Contracts: [design.md](../design.md) § Recorder contract, § Terminal primitive. Decisions: [decisions.md](../decisions.md) DEC-001, DEC-010.

## Tasks

| Task | Status | Notes |
|---|---|---|
| T-30 | done | `vocalize/recorder/VocalizeRecorder.swift` + `Info.plist.in`. `--out/--stop/--max/--device/--check/--list-devices`, plus `--status-file` added in the review pass (DEC-010a). Exit codes 0/1/2/3/4/5 as contracted. `rec.pid` written by the recorder and removed on every exit path, signals included |
| T-31 | done | Build-at-install in `vocalize/local/install.py`: `xcrun swiftc -O`, bundle assembly, `codesign -s - --force --options runtime`, staged build swapped in as one rename, stamp covering source + plist + the built binary's sha256. Missing `swiftc` and an unaccepted licence are diagnosed separately |
| T-32 | done | `vocalize listen --check` and `--list-devices` in `vocalize/cli.py`. `--check` launches the bundle through LaunchServices and reads its status file; `--list-devices` stays a direct exec |

## Review pass (2026-09-02)

Three independent reviews of the shipped run-3 code produced 21 findings (many duplicated across reviewers). All were fixed except the two recorded below under *Deferred*. The decisions they forced are DEC-010 a-e.

**The one that mattered.** `listen --check` was exec'ing the bundle's binary as a child of the shell, so TCC answered for the *responsible* process - the terminal. Confirmed live on the reference Mac, same binary, same second:

```
direct exec                  -> authorized      (the terminal's grant)
open -W -n -a <bundle>       -> notDetermined   (the bundle's grant)
```

The command whose only job is to report the recorder's permission was reporting somebody else's. It now launches the bundle the way run 4's `dictate` will, and reads back a `status:`/`device:`/`exit:`/`note:` file, because `open` relays neither stdout nor the child's exit status.

**Also found on this machine, and worth run 4 knowing:** the bundle sitting in the cache `bin/` directory at the start of this pass was a leftover *test fake* - a three-line shell script printing a hard-coded `authorized`, unsigned - which `local status` and `listen --check` both reported as a built recorder. The stamp matched the shipped source, so `build_recorder` returned "current" and never looked at the binary. That is now impossible: the stamp records the binary's sha256 and a mismatch forces a rebuild.

## Security gate

Negative tests, by id:

| Test | Guards |
|---|---|
| `test_recorder_build.py::test_the_source_never_prints_what_it_records` | audio never reaches stdout |
| `::test_check_mode_never_asks_for_permission` | `--check` reports, never prompts |
| `::test_check_mode_exits_before_anything_opens_the_microphone` | no recording path reachable from `--check` |
| `::test_the_signature_turns_the_hardened_runtime_on` | `DYLD_INSERT_LIBRARIES` cannot borrow the microphone grant |
| `::test_a_swapped_binary_under_a_valid_stamp_is_rebuilt` | a replaced binary is not trusted on an old stamp |
| `::test_a_failed_signature_leaves_the_granted_bundle_untouched` | a failed build never leaves a bundle whose signature does not validate |
| `::test_the_recorder_takes_its_pid_file_with_it_when_it_is_killed` | `rec.pid` gone on SIGTERM/SIGINT/SIGHUP |
| `::test_the_tap_and_the_stop_are_serialised_on_the_output_file` | no data race between the audio thread and the stop |
| `::test_an_empty_device_name_means_the_system_default` | an empty `[stt] input_device` cannot be looked up as a device name |
| `::test_the_compile_argv_is_a_list_naming_only_our_own_paths` | no shell, no caller-supplied path in the build |
| `test_listen_check.py::test_the_check_asks_the_bundle_not_the_terminal` | the TCC identity measured is the bundle's |
| `::test_the_check_passes_the_recorder_nothing_but_the_flag_and_its_status_file` | nothing else crosses the boundary |
| `::test_a_device_name_cannot_smuggle_escape_sequences_into_the_terminal` | hardware-named devices cannot drive the terminal |
| `::test_a_recorder_note_cannot_smuggle_escape_sequences_either` | same, for the recorder's diagnostic note |
| `::test_a_failure_message_cannot_smuggle_escape_sequences_into_the_terminal` | same, for the `--list-devices` failure path |
| `::test_a_name_that_cannot_be_printed_verbatim_is_marked_as_unusable` | a mangled name is never presented as a copy-paste config value |
| `::test_authorized_with_no_model_is_not_ready` | the exit status never says ready when nothing can transcribe |
| `::test_an_unknown_exit_code_gets_a_generic_message_and_no_traceback` | the exit set stays closed; no traceback |

SECURITY: PASS. No Critical or High finding is open.

## Hardware evidence

| What | Evidence |
|---|---|
| Bundle builds and signs on the reference Mac | `codesign -dv` on the built bundle: `Identifier=cards.arda.vocalize.recorder`, `flags=0x10002(adhoc,runtime)`, `Signature=adhoc` |
| The check reaches the bundle's own TCC identity | direct exec `authorized` vs `open -W -n -a` `notDetermined`, same machine, same minute (above) |
| `--list-devices` enumerates real hardware | prints one real input device name, exit 0 |
| `listen --check` end to end | `Microphone: notDetermined - macOS has not asked yet...`, exit 5 |

**Not re-taken in this pass, and required before run 4:** T-30's acceptance criterion that a recorded file opens with stdlib `wave` at 16000 Hz / 1 ch / 16-bit. Recording needs the microphone, the grant on the reference Mac is `notDetermined`, and answering the prompt is owner-present work. The earlier evidence was taken under a different bundle identity, so it does not carry over. Run verification.md **Manual 1** with the owner present before run 4 starts, and check the WAV then.

## Deferred

- **`--list-devices` exits with the recorder's own code** rather than a mapped one. It touches no permission and has no contract exit set; left as is.
- **The `convert-<pid>.wav` temporary** is not removed by the signal handler. It lives inside the caller's 0700 temporary directory, which `dictate` removes in a `finally` (design.md, dictation flow), so it cannot outlive the session.

## Gate

```
bash docs/plans/2026-09-next-features/run-3-recorder/validate-exit.sh
Passed: 15 / 15
Failed: 0 / 15
ALL CHECKS PASSED
```

validate-exit: PASS
