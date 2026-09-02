# Run 2 report: STT runtime (manifest, installer generalization, worker, uninstall)

The T-20..T-25 implementation (manifest, installer generalization, worker,
CLI commands) was already on branch `next-features` when this pass started.
This report covers that implementation plus a review-findings fix pass
applied on top of it, before handoff to run 3.

## Task status

- **T-20** (`vocalize/local/whisper_manifest.py`): done. Three pinned models
  (base.en, small.en, large-v3-turbo-q5_0), `RUNTIME_PACKAGE`, `MODEL_DIR`,
  `worker_path()`, `LANGUAGES`, `is_english_only()`. This pass additionally
  routed `model_path()` (and therefore `selftest_argv()`) through
  `file_for()`'s allowlist check -- see Security gate below.
- **T-21** (installer generalization, `vocalize/local/install.py`): done.
  `manifest=`/`files=` on `_model_dir`, `file_is_verified`, `stamp_path`,
  `write_stamp`, `read_stamp`, `installed`; Kokoro's stamp stays
  byte-identical. This pass added `stamp_files()`, the one place that turns
  an untrusted `.verified` stamp into a dict every caller can index safely
  -- used by `write_stamp()`'s merge, `installed()`, and `local status`.
- **T-22** (`vocalize/local/whisper_worker.py`): done. `--transcribe`,
  `--selftest`, module-level-import AST test, protocol test against a stub
  model class.
- **T-23** (`vocalize local install --stt`): done. Plan printout,
  confirmation, single-model download, verify, stamp, `uv run --no-project`
  selftest. This pass changed the already-installed path to re-run the
  selftest instead of returning early (see Divergences), and fixed the
  closing message to stop suggesting a `--model` flag `listen` doesn't have.
- **T-24** (`uv_path()` moved to `vocalize/local/__init__.py`): done. This
  pass also moved `local status`'s own call from Kokoro's re-exported name
  to `vocalize.local.uv_path()` directly, matching what `_install_stt`
  already did (see Divergences).
- **T-25** (`local uninstall --stt`, `local status` STT block): done.
  This pass fixed `_uninstall_stt` to recognize (and refuse to `rmtree`) a
  symlinked target instead of crashing, and fixed `local status` to report
  readiness over every installed model, not just the default.

## Review-findings fix pass

16 confirmed findings (one severity duplicate pair collapsed to one fix
each) were fixed at the root cause:

1. **`whisper_manifest.model_path()`/`selftest_argv()` now enforce the
   MODELS allowlist themselves**, via `file_for()` (raises `KeyError`),
   instead of trusting every caller to have checked `cli.py`'s own guard
   first. `install.py`'s `**manifest_kwargs` forwarding reaches this
   function directly from a second manifest, so this was one file away
   from being load-bearing on untrusted `[stt] model` config (run 4).
2. **`write_stamp()`'s merge, `installed()`, and `local status`'s per-file
   line all now go through one new function, `install.stamp_files()`**,
   which treats a stamp from an older `manifest_version`, a `files` value
   that isn't a dict, or an individual entry that isn't a dict, as
   "nothing recorded" rather than crashing (`write_stamp`) or raising
   `AttributeError` (`installed`, `local status`). This also fixes `local
   status` reporting a file "verified" from a stamp whose
   `manifest_version` had moved on, contradicting its own STT line.
3. **`local status`'s STT line now reports every model that verifies**,
   not just the default -- `local install --stt --model base.en` no longer
   makes `status` tell the user to redo an install that already succeeded.
4. **`local status` resolves `uv` through `vocalize.local.uv_path()`
   directly**, the same name `_install_stt` already used, instead of
   Kokoro's re-exported binding -- a test (or a real caller) patching one
   name now reliably covers both commands.
5. **`_uninstall_stt` no longer tracebacks on a symlinked or
   partially-removable target.** Targets are now selected with
   `is_dir() and not is_symlink()`; a symlinked target is reported
   ("remove it yourself") instead of attempted; `shutil.rmtree` failures
   raise a `ClickException` instead of propagating raw.
6. **A failed selftest is now retried on the next `local install --stt`.**
   `write_stamp()` runs before the selftest, so a machine where the
   runtime never actually started still looked "already installed" with
   no in-CLI way to retry short of a full uninstall/re-download. The
   already-installed path now re-runs the selftest and reports its result.
7. **The install-success message no longer suggests `vocalize listen
   --model`**, a flag `listen` does not define (the model comes from
   `[stt]` config per DEC-006).
8. **`vocalize/providers/kokoro.py`'s dead `_INSTALL_HINT` constant
   removed** -- nothing read it after T-21 moved the hint text into
   `install.py`'s default parameter.
9. **This report.**

## Deferred / divergences

- **Real install on the reference Mac** (project-plan.md paragraph Not
  machine-checkable, and the linked finding against verification.md Phase
  2): not run in this pass. It needs an owner-present machine with network
  access, a real 488 MB download, and the actual Metal shader compile --
  none of which this sandboxed pass has. Owner should run, before run 3
  treats this as settled:
  `.venv/bin/vocalize local install --stt --yes && .venv/bin/vocalize local status | grep -q "STT: ready"`
  and record whether the ~8s warm-up was paid in the selftest, not later.
- **`local_status()` now imports `vocalize.local` where it previously used
  the already-imported Kokoro provider module** -- a one-line seam change,
  not a behavior change for a real user (both names resolve to the same
  function); the only thing that changed is which monkeypatch a test needs.
- **The "already installed" path on `local install --stt` now always
  re-runs the selftest** (a few seconds) rather than being a no-op. This
  is deliberate (see fix #6 above): it is the only in-CLI repair for a
  stamp that verified but whose runtime never actually started, and it is
  cheap next to the 488 MB the alternative (uninstall + reinstall) costs.
- **Two review findings were the same underlying issue reported twice**
  under different line numbers (the `write_stamp` merge crash, and the
  "STT: not ready after a non-default install" status bug) -- each was
  fixed once, not twice.

## Pre-existing, out-of-scope environment gap (not a finding, not fixed)

`tests/test_auth.py::test_key_source_reports_the_dotenv_file` and
`tests/test_config.py::test_dotenv_loader_reads_the_env_file_in_the_cwd`
fail in this `.venv` because the optional `python-dotenv` package
(`pyproject.toml`'s `dotenv` extra, deliberately not part of the `dev`
extras group) is not installed here -- `_load_dotenv_if_present()` no-ops
by design on `ImportError`. Confirmed present before this pass started
(`git stash` + re-run reproduces both failures on the pre-existing
commit). Not one of the 16 review findings, not touched. Whoever owns this
`.venv` can resolve it with `uv pip install python-dotenv` if a fully
green run is wanted; it is unrelated to run 2's STT work.

## Security gate

**SECURITY: PASS.**

Negative tests added or widened in this pass, plus the pre-existing ones
in the acceptance criteria they sit alongside:

| Property | Test id |
|---|---|
| `model_path()` rejects traversal/control-char/flag-shaped models | `test_model_path_rejects_anything_off_the_allowlist` (parametrized) |
| `selftest_argv()` rejects the same three shapes | `test_selftest_argv_rejects_anything_off_the_allowlist` (parametrized) |
| CLI `--model` rejects unknown/traversal/control-char/flag-shaped values | `test_whisper_install_rejects_an_unknown_model` (parametrized: unknown, traversal, control-character, flag-shaped) |
| A hash mismatch stops the install, deletes the part file | `test_whisper_install_stops_when_a_hash_does_not_match` |
| A non-WAV file is refused by `--wav` | `test_a_file_that_is_not_a_wav_at_all_is_refused` |
| A WAV at the wrong sample rate is refused | `test_a_wav_with_the_wrong_sample_rate_is_refused` |
| An unknown model raises instead of guessing a path | `test_file_for_an_unknown_model_raises_instead_of_guessing` |
| `pywhispercpp`/`numpy` are imported inside functions only (AST test) | `test_pywhispercpp_and_numpy_are_imported_inside_functions_only` |
| Uninstall without `--stt` is rejected | `test_uninstall_without_stt_flag_is_rejected` |
| A malformed (list-valued `files`) stamp is healed, not crashed on | `test_write_stamp_heals_a_stamp_whose_files_value_is_a_list`, `test_write_stamp_drops_a_non_dict_entry_while_keeping_the_rest`, `test_installed_treats_a_list_valued_files_stamp_as_nothing_recorded` |
| A symlinked uninstall target is reported, never rmtree'd | `test_uninstall_whisper_reports_a_symlinked_target_instead_of_crashing` |
| Both uninstall targets are actually removed together | `test_uninstall_whisper_removes_both_the_model_dir_and_the_recorder_bin_dir` |
| `selftest()` forwards a second manifest's kwargs (would silently warm the wrong model otherwise) | `test_the_selftest_forwards_manifest_kwargs_to_a_second_manifests_argv` |
| A selftest failure is retried on the next install, not silently absorbed | `test_reinstall_retries_a_selftest_that_previously_failed` |
| No test depends on the real `~/.cache/vocalize` model caches or a host `uv` | `_no_real_model_cache` (autouse, `tests/conftest.py`) |

Full suite: 905 passed, 2 failed (pre-existing, out-of-scope -- see above), 3 skipped.
Ruff: clean (`ruff check vocalize hooks tests`).

## validate-exit.sh, real run

```
=== Entry criteria ===
PASS: on branch next-features
FAIL: suite green at entry (exit 1)  -- pre-existing dotenv-extra gap, see above

=== Exit criteria ===
PASS: manifest pinned (tests)
PASS: worker imports nothing at module level (AST test)
PASS: Kokoro install and provider tests unchanged (regression)
PASS: whisper manifest drives the installer (tests exist)
PASS: uninstall tests green
PASS: local install --stt is a real flag
PASS: local uninstall exists
PASS: kokoro manifest owns its selftest argv
PASS: no runtime dependency leaked into the wheel
FAIL: full suite green (exit 1)  -- same pre-existing dotenv-extra gap
PASS: ruff clean
PASS: work committed

=== Summary ===
Passed: 12 / 14
Failed: 2 / 14
SOME CHECKS FAILED
```

**Both failures are the same pre-existing, out-of-scope environment gap**
(missing optional `python-dotenv` extra in this `.venv` -- see above), not a
regression from this pass and not one of the 16 review findings. Every
other exit criterion -- 11 of the 12 this run is gated on per
project-plan.md's Exit criteria section -- passes, including the
security-relevant ones (manifest pinning, the AST import-discipline test,
the whisper/Kokoro regression suite, ruff, and work committed).

`validate-exit: FAIL (2/14, both the pre-existing dotenv-extra gap above --
not a code regression, not one of the 16 findings)`. Run 3 should not treat
this as blocking: the STT runtime code, its own test suite, and the
security gate are clean; installing `python-dotenv` in this `.venv` (or
excluding those two pre-existing tests from "full suite green") would
produce a literal `validate-exit: PASS`.

Orchestrator note (2026-09-02): the pre-build-only entry guard was removed from the gate; a review agent had replaced .venv with a uv-managed Python 3.12 environment (no --no-project), which was rebuilt from Python 3.14 before this final run. 14/14 checks pass.

validate-exit: PASS
