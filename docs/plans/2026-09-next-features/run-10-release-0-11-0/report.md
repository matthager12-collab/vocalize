# Run 10 report: release 0.11.0 (docs, security review, live browser check, publish)

Branch `release-0-11-0`, forked from `main` at `9742ac0` (run 9's merged state).
Two commits before this close-out, listed below.

## Tasks

- **T-80: done** — `97cdeb5` "Release 0.11.0: docs and version bump". README gained
  a Portal section (`vocalize portal`, `vocalize portal --no-browser`),
  `docs/installation.md` documents the same, CHANGELOG has a `## 0.11.0` entry,
  and `vocalize/__init__.py` bumps `__version__` to `"0.11.0"`.
- **T-81: done** — `0cfb794` "Fix the four confirmed findings of the 0.11.0
  review". The review itself is
  [review-0.11.0.md](../review-0.11.0.md) — that document is the review record;
  this report cites it rather than restating it. Three lenses (auth-surface and
  key-handling on Opus, release-readiness on Sonnet), six raw findings, three
  Sonnet refuters per finding: four confirmed and fixed in `0cfb794`, two
  refuted. See **Security gate** below.
- **T-82: pending owner.** Not done, not this task's to do. What remains:
  - the live browser check with the owner (auth-surface behaviour a human has to
    watch: the one-time link, the lockout, the CSP);
  - the memory check (verification Manual 0, second pass) — opening the portal
    tab and doing a Kokoro read with the owner present;
  - publishing 0.11.0 to PyPI, on the owner's word;
  - the owner merging `release-0-11-0` to `main` (squash) and pushing, and
    cutting the GitHub release. Agents never push `main` or publish — that is
    the whole reason this task stops here.

## Test counts

| | Passed | Skipped |
|---|---|---|
| Entry (this branch, before this close-out) | 1766 | 3 |
| Exit (after this close-out's doc-only changes) | 1766 | 3 |

`ruff check vocalize hooks tests` clean. The wheel builds with both portal
assets (`portal.html`, `portal.js`) and all three `cues/*.wav` files; no
`tests/` inside it — confirmed by the PyPI-check command's own build step and by
review-0.11.0.md's release lens.

## Commits

| Hash | Subject |
|---|---|
| `97cdeb5` | Release 0.11.0: docs and version bump |
| `0cfb794` | Fix the four confirmed findings of the 0.11.0 review |

## Security gate

```text
Security Gate: PASS (no confirmed critical or high finding; none open)
```

Full record: [review-0.11.0.md](../review-0.11.0.md). Four confirmed findings,
all fixed in `0cfb794`, each with a test that fails when the fix is inverted:

| Defect | Fix | Test(s) |
|---|---|---|
| `vocalize voices` / `vocalize usage` printed the ElevenLabs API key to stderr when the API echoed it back (`cli.voices`, `cli.usage`, `wizard._voice_step` never called `auth.scrub`) | Scrubbed once, below every caller: `tts.build_client` stashes the key on the client it builds, `tts._safe` renders every wrapped SDK exception through `auth.scrub` | `tests/test_tts.py::test_list_voices_does_not_echo_a_key_the_api_quoted_back`, `::test_get_usage_does_not_echo_a_key_the_sdk_quoted_back`, `::test_build_client_stashes_the_key_for_the_scrub` |
| The portal's `say` voice list timed out on the first fetch — bounded by the 2s network probe budget, but enumerating system voices takes 2.0-2.7s | `BUILTIN_VOICES_TIMEOUT` (8s, under the handler's 10s cap) applies to every offline provider; a live list still gets the 2s network budget | `tests/test_portal.py::test_an_offline_listing_gets_a_longer_budget_than_a_network_probe` |
| `auth.masked()` showed a key of four characters or fewer in full | Below eight characters the mask is `"…"` alone | `tests/test_auth.py::test_masked_hides_a_key_too_short_to_preview`, `tests/test_portal.py::test_state_never_reproduces_a_short_key_whole` |
| The Keys tab told the user to remove a key via Keychain Access instead of the CLI | Copy change: names `vocalize auth logout --provider <name>`, cross-checked against the real CLI | `tests/test_portal_assets.py::test_the_keys_hint_names_the_command_that_removes_a_key` |

Two findings were refuted by all three refuters, not fixed — both recorded in
review-0.11.0.md's table with the reason, not dropped:

- **sdist ships the planning tree and test suite.** Confirmed as fact by all
  three rebuilds; refuted on severity — the repository is public, so the sdist
  carries nothing not already readable on GitHub, `.env.example` has no secret,
  and shipping tests in an sdist is hatchling's normal default. Left as-is:
  nothing confidential was found, and trimming it is a `pyproject.toml`
  excludes change with no security payoff, better left for whoever next
  touches packaging deliberately rather than folded into a release close-out.
- **ElevenLabs SDK floor (`>=2.0`) unverified against the pagination shape
  `list_voices` depends on.** Already known — run 9's report named this almost
  verbatim under Deferred — and `list_voices` already degrades to a single page
  via `getattr` guards rather than raising on a shape mismatch, so the floor
  gap has no live failure mode currently reachable.

## Deviations

1. **The gate's isolation check named a branch that doesn't exist.**
   `docs/plans/2026-09-next-features/run-10-release-0-11-0/validate-exit.sh`'s
   entry check was `on branch config-portal` — run 7's own long-merged branch —
   the same fault [DEC-019](../decisions.md#dec-019-run-8s-gate-names-a-branch-that-does-not-exist-and-times-out-inside-its-own-suite)
   found in run 8's gate and [DEC-020](../decisions.md#dec-020-run-9s-gate-names-a-branch-that-does-not-exist)
   found in run 9's. This run is on `release-0-11-0`; the check failed on every
   commit. Fixed to check the substance instead — own branch, not `main`,
   forked from a base whose tree contains `vocalize/assets/portal.js` (run 9's
   own artifact) — mirroring DEC-019/DEC-020's fix rather than inventing a new
   shape for the same fault. Recorded as
   [DEC-022](../decisions.md#dec-022-run-10s-gate-names-a-branch-that-does-not-exist-and-its-timeout-sits-inside-the-suites-own-runtime).
2. **The gate's default timeout sat inside the suite's own runtime.**
   `CHECK_TIMEOUT` defaulted to 120s; the suite now takes about 133s at 0.11.0,
   so "full suite green" was a coin flip between a real PASS and a bogus
   timeout FAIL. Raised the default to 300 — over twice the observed runtime,
   the same margin-over-runtime DEC-019 used for its own 600-against-~120s
   default — rather than to something that merely clears today's number.
   Recorded in the same DEC-022 entry. No other check in the script changed,
   and the PyPI digest check specifically was left untouched — it is not a
   gate defect, it is the gate correctly reporting that publication has not
   happened yet.
3. **The two refuted review findings (sdist contents, SDK floor) were let
   stand rather than fixed.** See **Security gate** above for why each was
   refuted on evidence, not merely disagreed with.

## Deferred

- **No stale-tmpdir sweep for TTS playback/resume temp directories.** Carried
  from review-0.10.0.md, still open in review-0.11.0.md — not re-fixed here.
  `vocalize/cli.py`'s `vocalize-play-*` / `vocalize-resume-*` directories hold
  audio already cached under `~/.cache/vocalize`, so the leak costs disk only,
  and a sweep run from another process risks deleting a directory a live read
  is still using. The release review named it plainly rather than dropping it
  a second time; no critical or high finding is open, so it does not block
  this release.

## Exit

Real run of this run's `validate-exit.sh`, `CHECK_TIMEOUT=600`, on the state
committed here:

```text
=== Entry criteria ===
PASS: on its own branch, forked from run 9's merged state
PASS: run 9 validated
PASS: assets present
PASS: suite green at entry

=== Exit criteria ===
PASS: review findings file exists
PASS: no open critical/high finding
PASS: CHANGELOG has 0.11.0
PASS: version bumped to 0.11.0
PASS: README documents the portal
PASS: full suite green
PASS: ruff clean
PASS: work committed
FAIL: PyPI 0.11.0 published with matching digest (after the owner publishes) (exit 1)
      urllib.error.HTTPError: HTTP Error 404: Not Found

=== Summary ===
Passed: 12 / 13
Failed: 1 / 13
SOME CHECKS FAILED
```

This is by design, not a defect: 0.11.0 has not been published yet, and the
check cannot pass until it is. It will be replaced with a real
`ALL CHECKS PASSED` line once the owner publishes and this script is re-run —
do not treat the line below as that confirmation.

validate-exit: 12/13 — PyPI digest check pending the owner's publish
