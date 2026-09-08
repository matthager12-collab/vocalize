# Task report: run 5 (keys tab and the Anthropic slot)

## Changelog

- `vocalize/auth.py`: `CREDENTIAL_CHOICES`; `KEY_MAX_CHARS = 512` enforced in `_check_shape`.
- `vocalize/cli.py`: `--provider` on `auth login/status/logout` takes `CREDENTIAL_CHOICES`; `auth status` shows the slot; `usage` prints the anthropic row.
- `vocalize/portal.py`: `_PARAMETERIZED` and `ROUTES` rows for the two routes; `_slot_or_404`, `_login_target_or_404`; `_key_states` probes every slot; `_key_row` carries the stamp in `Row.action` and reports a locked keychain as `error`; `_key_public` keeps the provider entries' `key` shape; `"keys"` in the state; `_remove` and `_test_key`.
- `vocalize/assets/portal.js`: `STT_CLEANUP`, `keySlots`, the Keys tab from `data.keys`, the card's test and remove buttons, stamp, clipboard hint, `new-password`; the cleanup select on the Local tab.
- Tests: test_portal (+16), test_cli (+2, usage row), test_auth (+3), test_portal_assets (cleanup list, `new-password` pin, docstring), conftest (`deny_read`), harness `keyslots` scenario.
- Docs: provider-credentials § Anthropic, README Keys bullet, CHANGELOG Added; the gate's DEC-035 window.

## Skipped

Nothing skipped. Manual check 3 is deferred to run 6 by the plan, not by this run.

## Learnings

- `stored_key` and `key_source` flatten "could not look" into "not found" on purpose (resolution must not blow up); anything that *reports* has to use `probe_keychain` instead. The CLI already did; the portal now does in `_remove` and `_key_row`.
- A write route that takes no body still has to parse one: the `_WRITE_ROUTES` negatives post garbage to every write and expect a 400 before anything moves.
- The harness `Node` stub keeps property assignments (`box.autocomplete`) as plain properties, so a scenario can assert them directly; `attrs` holds only `setAttribute` values.
- A missing token is a 403 on this portal, not a 401.

## Alternatives

- Keys tab kept listing every provider (say, kokoro, polly with "No key needed") — dropped: with `keys` the tab lists what stores a key; polly's AWS note stays on the Providers tab.
- A browser `confirm()` before remove — dropped for the two-click arm: the harness has no dialogs and the page keeps no modal state.
- Clearing the field after a successful test — dropped: the user's next click is "Store this key".

## Specification (post)

As `project-plan.md`, with one refinement: the test route answers 502 (not `valid: false`) when the provider could not be reached, and the remove route answers 400 (not "nothing stored") when the keychain cannot be read.
