# Task report: run 1, local-first defaults

Date 2026-09-07. Branch `local-first`, worktree `.claude/worktrees/local-first`. Executed with implement-spec against [project-plan.md](./project-plan.md); exit gate [validate-exit.sh](./validate-exit.sh); result in [report.md](./report.md).

## Changelog

- `config.DEFAULT_CHAIN` is `("kokoro", "say")`; `auth.PROVIDER_NAMES` starts `kokoro, say`, so `usage`, `status` and the portal list the local providers first.
- `chain.run` prints `Spoke via say (fallback) — Kokoro is not installed; run: vocalize local install` when the primary was Kokoro and it was unavailable (model missing or `uv` missing). Any other fallback keeps the old line. One new private helper, `_fallback_message`.
- Tests: the four pinned literals updated; three fallback-note tests added in `test_chain.py`; four tests that assumed the default chain was all-ok with a cloud key now name their chain (`VOCALIZE_CHAIN=elevenlabs,say`) or their provider (`--provider elevenlabs` for the `--api-key` speed test).
- README leads with `vocalize local install`; the cloud-key instructions moved under "Providers and fallback" as "Storing a cloud API key"; the config-table comment no longer names the brand; `pyproject.toml` description and keywords put local first; CHANGELOG has the Unreleased entry with the upgrade note.

## Skipped

Nothing skipped. All four tasks delivered; `docs/installation.md` untouched as specified.

## Learnings

- **Tests that expect an all-ok `vocalize status` must name their chain.** With Kokoro first by default, any test that leaves the chain to the default and only sets a cloud key gets a `warn` row and exit 1 on a machine without the model. Three readiness tests and one CLI test carried that assumption; none were on the plan's list of four pinned literals. Grep for `ELEVENLABS_API_KEY` in tests when the default chain changes again.
- **`--api-key` refuses before `--speed` is looked at** when the chain does not start with ElevenLabs, so a test of speed validation via `--api-key` must name the provider.
- **Piping pytest into `tail` hides its exit code.** One checkpoint was committed with a red suite because of it; gate on `$?` of pytest itself, as verification.md says.

## Alternatives

- **Detect "not installed" by matching the reason string** instead of the exception class. Rejected: the class (`ProviderUnavailableError` from Kokoro as primary) is stable and covers `uv` missing too, which the same install command explains.
- **Print the note on the skip line** (where the raw reason already appears) instead of the fallback line. Rejected: the spec and DEC-022 put it on the line that says who spoke, and the skip line's wording belongs to every provider.
- **Delete the cloud-key instructions from the README** rather than moving them. Rejected: they are still the only place that explains the keychain flow; they moved under Providers.

## Specification, as built

Identical to [project-plan.md](./project-plan.md) with one addition to T-02: four more tests beyond the listed four needed the explicit chain or provider (see Learnings). No contract in design.md changed.
