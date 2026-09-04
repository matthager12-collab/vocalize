# Run 8 report: portal server — writes, preview, install thread, `vocalize portal`

Branch `portal-writes`, forked from `main` at `7f40c98` (run 7's merged state),
executed 2026-09-03. Seven commits, listed below — six of them run 8's, plus
`ba7c52b`, which is not.

The branch name is not the one the plan wrote. See **Deviations 1** and
[DEC-019](../decisions.md#dec-019-run-8s-gate-names-a-branch-that-does-not-exist-and-times-out-inside-its-own-suite).

## This run is a port, not a merge — read this first

An earlier, unhardened implementation of run 8 exists and is still on the remote as
`origin/config-portal`. **It was not merged.** This branch rebuilt run 8 from the
plan text on top of `main`'s *hardened* run 7 — the run-7 code as it stands after
that run's adversarial review and fix rounds, not the run-7 code the reference
implementation was written against.

That distinction is the whole reason for the rebuild. The reference was built on a
`portal.py` whose `Host` pin, idle clock, `send_error` path, lock discipline and
`compare_digest` behaviour had not yet been corrected, and it inherited those
assumptions. **The builders recorded about thirty defects avoided** by rebuilding
against the corrected module rather than merging code written against the earlier
one. That count is theirs, recorded here as reported; this document does not
re-derive it, and a reader wanting the detail should go to the branch commits rather
than treat the number as verified here.

Practical consequence for run 9 and anyone after it: **`origin/config-portal` is
superseded, not a second opinion.** Do not diff against it looking for things this
branch "missed" — where the two differ, this branch is deliberately different.

## Tasks

- **T-62: done** — writes for chain, provider table, `[stt]` table and key login, all
  through `config._validate_*` and a new `wizard.write_config_if_unchanged`.
  Compare-and-swap on `{mtime_ns, sha256}` of the file read at page load, the
  sentinel `"absent"` for a file that is not there, and an `O_EXCL` first write.
  A mismatch raises the new `exceptions.ConfigChangedError` (a `ConfigError`
  subclass, so every existing handler and `cli.run()`'s error funnel keep working).
  `GET /api/state` gained the `fingerprint` key run 7 deferred, taken **before** the
  values it accompanies — the ordering is the contract, and `design.md` now carries
  it.
- **T-63: done** — preview through `chain.run(text, chain=[name], file_config=…,
  forced=True)` under one module lock, bytes returned for `fetch → Blob` with
  `Accept-Ranges: none`, never played. Kokoro/STT install on a daemon worker thread
  with the progress endpoint the page polls, inside run 7's `suspend_idle()`.
- **T-64: done** — `vocalize portal`, with `--no-browser` beyond the plan text (see
  Deviations 3). Prints the one-time URL once, prints DEC-018's single-user note,
  opens `webbrowser.open`, and blocks on `serve_until_stopped()`.

## Test counts

| | Passed | Skipped |
|---|---|---|
| Entry (`main` at `7f40c98`) | see [run 7's report](../run-7-portal-read/report.md) | 3 |
| Exit | 1658 | 3 |

`ruff check vocalize hooks tests` clean. Additions are concentrated in
`tests/test_portal.py` (+1811/-9) and `tests/test_wizard.py` (+375/-1), with the
`vocalize portal` command's own tests in `tests/test_cli.py`.

## Commits

| Hash | Subject |
|---|---|
| `ba7c52b` | Freeze the clock the dictation start grace is measured against |
| `1a20374` | Add the portal's write routes and the config compare-and-swap |
| `1446197` | Add the portal's voice preview and local install thread |
| `7b59e85` | Add the vocalize portal command and the suite's browser guard |
| `c9aaf79` | Serialise the config write and give each writer its own temp file |
| `db8c2fe` | Stop a test undoing the fixtures that keep it off the real cache |
| *(close-out)* | Own up to an install the portal cut short, and close run 8 out |

`ba7c52b` is **not run 8's work**: it is a one-line test fix for issue #6, with a
seven-line comment explaining it, that landed
on this branch because this branch was the working tree at the time. Flagged rather
than tidied away — it will squash into run 8's merge and a reader of `main`'s history
would otherwise find it attributed here.

Files changed outside `vocalize/` and `tests/`: `README.md` (a `vocalize portal`
section), `docs/plans/2026-09-next-features/design.md` (the `fingerprint` row and its
ordering contract), `CHANGELOG.md` (close-out), this run's `validate-exit.sh`
(Deviations 1), and `decisions.md` (DEC-019).

## Security gate

```text
Security Gate: PASS WITH NOTES
```

**Attack surface.** Run 7's loopback socket becomes a *mutating* surface: five write
routes reach `config.toml` and the system keychain, one reaches a paid provider's
API, one starts a several-hundred-megabyte download, and one deletes a file. Classes
touched: APP-AUTHZ, APP-INPUT, APP-SECRETS, APP-PATH, APP-CMD, APP-LOG,
APP-LIFECYCLE. Untrusted inputs are the request body, the `fingerprint` object, the
provider and model route parameters, and the submitted API key. Sensitive sinks are
the config file, the keychain, a subprocess argv, a filesystem unlink, the ledger,
and stderr.

**Negative tests named in the acceptance criteria**, by test id (all in
`tests/test_portal.py` unless marked):

| Criterion | Test |
|---|---|
| a file changed on disk between read and write is refused | `test_writing_with_a_stale_fingerprint_is_409_and_changes_nothing` |
| a file created underneath an `"absent"` fingerprint is refused | `test_writing_under_an_absent_fingerprint_over_a_file_that_appeared_is_409` |
| an `"absent"` fingerprint over a genuinely missing file creates it | `test_writing_with_an_absent_fingerprint_creates_the_file` |
| a fingerprint of the wrong shape, or none at all, is 400 | `test_a_write_with_a_fingerprint_of_the_wrong_shape_is_400`, `test_a_write_with_no_fingerprint_at_all_is_400` |
| validators' errors surface as 400 in the CLI's own wording | `test_writing_an_unknown_provider_in_the_chain_uses_the_cli_wording`, `test_writing_a_provider_speed_out_of_range_uses_the_cli_wording`, `test_writing_a_bad_monthly_chars_uses_the_cli_wording`, `test_writing_a_bad_stt_model_uses_the_cli_wording` |
| a refused value is never echoed back | `test_writing_an_unusable_provider_value_is_400_without_echoing_it`, `test_a_login_for_an_unknown_provider_is_404_without_reflecting_it`, `test_a_refused_model_name_is_never_echoed_back` |
| the login response body never contains the submitted key | `test_the_login_response_never_contains_the_key` |
| …and never logs it, and writes no config file | `test_the_login_route_never_logs_the_key`, `test_the_login_route_writes_no_config_file` |
| a budget-capped provider returns the CLI's refusal | `test_a_budget_capped_preview_is_refused_in_the_chains_own_words` |
| a repeat preview is a cache hit, on the test cache | `test_a_preview_spends_the_ledger_and_a_repeat_is_a_cache_hit`, `test_a_preview_uses_the_test_audio_cache_and_not_the_real_one` |
| two concurrent previews run one at a time | `test_two_previews_run_one_at_a_time`, `test_a_queued_preview_gives_up_rather_than_piling_up` |
| a preview never carries a stored key anywhere | `test_a_preview_never_carries_a_stored_key_anywhere` |
| a preview never plays audio | `test_a_preview_never_goes_through_the_playback_path`, `test_a_preview_never_speaks_the_pages_words` |
| install progress advances under a fake opener | `test_an_install_reports_progress_while_it_runs`, `test_an_install_downloads_verifies_stamps_and_warms_the_runtime` |
| the model reaching the runtime argv is the allowlisted one (APP-CMD) | `test_the_model_that_reaches_the_runtime_argv_is_the_allowlisted_one` |
| an unknown model is 400 and downloads nothing | `test_an_install_of_an_unknown_model_is_400_and_downloads_nothing` |
| a failed write or install never touches the lockout counter | `test_a_failed_write_never_touches_the_lockout_counter`, `test_an_install_never_touches_the_lockout_counter` |
| the CLI command with a fake browser opener (T-64) | `tests/test_cli.py::test_portal_opens_the_browser_at_the_one_time_url`, `…::test_portal_no_browser_prints_the_url_and_opens_nothing`, `…::test_portal_says_so_when_no_browser_would_open` |

**Close-out additions** (the abandoned-download fix, this document's own commit):

| Property | Test |
|---|---|
| a portal closed mid-download deletes the part file and names it | `test_closing_the_portal_mid_download_takes_the_part_file_back` |
| a finished install leaves nothing to delete (APP-LIFECYCLE) | `test_a_finished_install_leaves_nothing_to_discard` |
| nothing from a request reaches the path that gets unlinked (APP-PATH) | `test_a_refused_model_never_reaches_the_path_the_portal_deletes` (5 traversal shapes) |
| the lockout message makes no claim about the user's own settings | `test_the_lockout_message_makes_no_claim_about_the_users_own_settings` |
| the command reports a cut-short install, and stays quiet otherwise | `tests/test_cli.py::test_portal_owns_up_to_an_install_it_cut_short`, `…::test_portal_stays_quiet_when_no_install_was_running` |

**Security evidence — RED then GREEN, by mutation**, each one broken in place, the
named test re-run, then reverted:

| Control broken | Test | Result |
|---|---|---|
| `_install_model`'s manifest allowlist disabled | `test_a_refused_model_never_reaches_the_path_the_portal_deletes` | RED (5/5 traversal shapes) |
| the worker's `_downloading = None` cleanup removed | `test_a_finished_install_leaves_nothing_to_discard` | RED |
| the part-file `unlink` removed | `test_closing_the_portal_mid_download_takes_the_part_file_back` | RED |
| the part path never recorded by `_install_stt` | `test_closing_the_portal_mid_download_takes_the_part_file_back` | RED |
| `LOCKOUT_MESSAGE` restored to "Nothing was changed." | `test_the_lockout_message_makes_no_claim_about_the_users_own_settings` | RED |
| the command's cut-short line removed | `tests/test_cli.py::test_portal_owns_up_to_an_install_it_cut_short` | RED |
| the temp file chmod'd `0o644` | `tests/test_wizard.py::test_an_ordinary_rewrite_does_not_widen_the_file_mode` | RED |

APP-AUTHZ needed no new evidence: this run added no route-table entries of its own
beyond the ones run 7 already declared with `auth == "token"`, so run 7's
`ROUTES`-parameterized `Host`, token and token-in-query negatives already cover every
write, preview and install route without anyone remembering to extend them.
`discard_partial_download` is reachable only from the CLI process that owns the
Portal — it is not in `ROUTES`, and dispatch is by explicit path, never by attribute.

## Deviations from the written design

1. **`validate-exit.sh`'s entry check amended, and its timeout raised.** The check
   was `on branch config-portal`; this run is on `portal-writes`, so it failed on
   every commit and the gate had never passed as written. It now checks the
   criterion's substance — on a branch that is not `main`, forked from a base whose
   tree carries `vocalize/portal.py` — proved four ways in a scratch repository
   before use (green on the real shape; red on `main`, red on a base without
   `portal.py`, red on a detached HEAD). `CHECK_TIMEOUT`'s default went from 120s to
   600s because two checks run the whole suite, which takes 115-121s: the cap sat
   inside the spread and made those two checks a coin flip. Both recorded as
   [DEC-019](../decisions.md#dec-019-run-8s-gate-names-a-branch-that-does-not-exist-and-times-out-inside-its-own-suite),
   on the narrow ground DEC-017 opened and no wider.
2. **`_render_config_text` had to learn `[stt]` before any of this could work.** The
   serialiser rendered flat keys and `[providers.*]` and nothing else, so a `dict`
   under `stt` reached the scalar renderer and raised. That made the whole run
   impossible — the portal cannot write a config it cannot render — but it is not a
   portal bug: it has shipped since 0.10.0 and it broke `vocalize wizard` and
   `vocalize chain` for **every dictation user**, both refusing with "edit that file
   by hand". Verified against the shipped `v0.10.2` tree before it was written up.
   Fixed here and given a CHANGELOG entry, because a fix to shipped behaviour is not
   a portal detail. One cosmetic consequence: a rewritten file puts `[stt]` ahead of
   `[providers.*]` whatever order it was in.
3. **`vocalize portal --no-browser` is beyond the plan text**, which asked only for
   `webbrowser.open` plus a printed URL. A flag that prints instead of opening is
   what makes the command usable over SSH and testable without a browser guard doing
   all the work; the guard exists too.
4. **`local/install.py` gained `REGRANT_WARNING`** — a constant lifted out of the
   CLI so the portal's progress dict can carry the same words. A recorder rebuild
   that goes unannounced silently breaks the user's dictation, and there are now two
   callers that must announce it.
5. **`GET /api/state`'s `fingerprint` is `null` on a path that cannot be read at
   all**, a third shape beyond DEC-005's two. No write can be made under it; the page
   must poll again. Recorded in `design.md`'s payload table rather than only here,
   because run 9 renders it.

## The adversarial review

Five lenses, three independent refuters per finding. **Twenty findings raised, three
confirmed, three fixed.** The other seventeen are below under *Not looked at*.

Confirmed and fixed:

1. **HIGH — lost update.** `write_config_if_unchanged` compared the fingerprint and
   then renamed, with nothing holding the two together, so two writers in one process
   could both see an unchanged file, both rename, and both answer `{"ok": true}`
   while only the second survived. Now under a module-level `threading.Lock` across
   compare and commit. Measured with the two writers aligned on a barrier:
   301 lost updates in 400 trials before, 0 in 400 after. An earlier harness that
   did not align the threads saw only 8 in 400 — the defect's real rate depends
   entirely on how hard the harness tries to hit the window, so treat 301/400 as
   the reproduction and 8/400 as a lower bound on a lazy harness.
2. **MEDIUM — shared temp path.** Every writer rendered through a fixed
   `config.toml.tmp`. Now `tempfile.mkstemp` in the same directory, so the rename
   stays atomic on one filesystem and `mkstemp`'s own `0600` replaces the explicit
   chmod. Measured over 400 trials: ~25% of writers raised `ConfigError`, and in ~47%
   of trials a writer reported success for bytes that were not in the file; both zero
   after. The reviewer could **not** reproduce the *unparseable file* symptom the
   finding described — CPython's buffered writer flushes a whole render in one
   `write()` on APFS — and said so rather than claiming the stronger result.
3. **LOW — an abandoned download.** A lockout or Ctrl-C kills an in-flight install:
   nothing joins the daemon worker, so `download_file`'s own part-file cleanup never
   runs, a part file the size of the model stays in the cache, and the terminal said
   "Nothing was changed", which was false. Fixed in the close-out commit: the lockout
   message no longer makes that claim (it was false a second way too — a legitimate
   page may have saved settings before the junk requests arrived), and
   `Portal.discard_partial_download()` deletes the part file and returns the line the
   command prints. **The thread is deliberately not joined**: a 488 MB download will
   not finish inside any wait worth making, and the part file is not resumable
   (`download_file` opens it `"wb"` every time), so there is nothing to preserve.

## Residual risk — known-broken, and left that way on purpose

Every item here was looked at and a decision was made not to fix it.

- **[MEDIUM] A Save deletes every comment in `config.toml`, and rewrites TOML dates
  as strings.** The writer parses the file with `tomllib` into a dict and renders the
  dict back; comments never survive that round trip, and a `date` comes back as
  `"2026-09-01"` — a string, which is a *type* change, not only a formatting one.
  Verified directly against this branch. Nobody's config has a date in it today and
  no vocalize key takes one, so the type change is currently latent. The comments are
  not latent: anyone who has annotated their config loses the annotations the first
  time they touch the portal, the wizard or `vocalize chain`. Not fixed, because
  preserving them means a round-tripping TOML writer — a dependency, and a large one
  — for a cosmetic loss on a file most people never hand-edit. **If it is ever fixed,
  the fix is `tomlkit`, and it should be decided rather than slipped in.**
- **[MEDIUM] A symlinked `config.toml` is replaced by a regular file.** `os.replace`
  renames onto the *link*, not through it. Verified: a `config.toml` symlinked into a
  dotfiles repo becomes a plain file after one Save, and the dotfiles copy is orphaned
  holding stale content — silently, with the write reporting success. This is a real
  pattern for exactly the kind of person who runs this tool. Not fixed because the fix
  (`path.resolve()` before the rename) changes where writes land for everyone and
  interacts with the compare-and-swap fingerprint, which is taken on the unresolved
  path. **This one is the strongest candidate for the next run to pick up**, and it is
  a data-loss shape, not a cosmetic one.
- **[LOW] The compare-and-swap is single-process.** DEC-005 weighed cross-process
  locking as option B and turned it down. Two `vocalize` *processes* saving in the
  same instant can still interleave; the fingerprint narrows the window to
  microseconds but does not close it. Stated in the `_WRITE_LOCK` comment and the
  `write_config_if_unchanged` docstring rather than implied away. Closing it means a
  lock file, which would be reopening an adopted decision.
- **[LOW] A `SIGKILL` between the render and the rename leaves a stale
  `config.toml.<random>.tmp`.** The old fixed name left at most one such file ever;
  unique names mean one per kill. `0600`, harmless, and swept by nothing. Marked with
  a `ponytail:` comment naming the ceiling rather than given a sweeper for a case
  nobody has hit.
- **[LOW] A window between claiming the install slot and opening the part file.** If
  the portal closes in that gap, `discard_partial_download` reports the install was
  cut short but has no path to delete, so a part file opened a moment later would
  survive. Microseconds wide, and the next install truncates that file anyway.
- **[LOW] DEC-018 still stands.** Any local process can close the portal with five
  `Origin`-less POSTs. Availability only; unchanged by this run, and now with one more
  consequence — closing the portal that way is what abandons an install.

## Not looked at — the honest gap

The review capped its verification effort. **Six low-severity findings were raised
and left unverified over that cap, and eleven were raised and refuted.** Neither set
was written into this repository: there is no `run-8-portal-write/review-findings.md`
the way run 7 has one, and the close-out could not reconstruct seventeen findings it
never saw.

So, plainly, for whoever reads this next:

- The three confirmed findings above are fixed and proved.
- The six residuals above were each looked at and consciously left.
- **Seventeen more findings existed and their text is lost.** Eleven of them were
  judged wrong by three refuters each, which is decent evidence they were wrong. Six
  were never adjudicated at all. Nothing here should be read as "run 8 was reviewed
  clean" — it should be read as "run 8 was reviewed, three defects were fixed, and six
  low-severity questions were left open with their text gone."
- Run 7's review, by contrast, *is* in the repo at
  [run-7-portal-read/review-findings.md](../run-7-portal-read/review-findings.md).
  **A future run doing an adversarial review should write the findings file into the
  run's own directory before fixing anything**, which is the process gap this
  paragraph exists to record.

## Deferred

- **No portal page.** `GET /` and `/portal.js` still serve run 7's built-in
  placeholder; `vocalize/assets/portal.html` and `portal.js` are run 9's (T-65), and
  their absence stays run 9's entry guard.
- **No `0.11.0` release.** That is run 10. The CHANGELOG entries this run added sit
  under **Unreleased** with no version and no date; the portal itself gets no
  CHANGELOG entry at all yet, because it has never shipped and "Fixed" entries for an
  unreleased feature are noise.
- **The developer's real model cache was poisoned by an earlier fixture bug** and is
  not this run's to clean. `~/.cache/vocalize/models/whisper/.verified` carries a
  `ggml-fake.bin` entry left by a test whose `monkeypatch.undo()` dropped the
  isolation fixtures (fixed in `db8c2fe`), and a stale `ggml-small.en.bin.part` sits
  beside the intact model. Removing them is the owner's call:

  ```bash
  rm ~/.cache/vocalize/models/whisper/ggml-small.en.bin.part
  vocalize local install --stt   # re-verifies, rewriting .verified without the fixture entry
  ```

## Note for the choreography's pre-build table

The script still has **13 checks**, which is the number
[choreography.md](../choreography.md) § Pre-build validation records for this run, so
that table needs no edit. Deviations 1 changed what one entry check *asks*, not how
many there are.

## Exit

Real output of `docs/plans/2026-09-next-features/run-8-portal-write/validate-exit.sh`,
exit status `0`:

```text
=== Entry criteria ===
PASS: on its own branch, forked from run 7's merged state
PASS: run 7 validated
PASS: portal module present
PASS: suite green at entry

=== Exit criteria ===
PASS: compare-and-swap helper exists
PASS: writes are safe (cas, absent sentinel, login never echoes key)
PASS: preview respects budgets, cache and the lock
PASS: install thread progress
PASS: vocalize portal command exists
PASS: chain setter and wizard still green with the helper
PASS: full suite green
PASS: ruff clean
PASS: work committed

=== Summary ===
Passed: 13 / 13
Failed: 0 / 13
ALL CHECKS PASSED
```

validate-exit: PASS
