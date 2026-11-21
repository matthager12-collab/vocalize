# Settings and choices with no control today

Read from the settings page and the config loader on `main` at `7ce02b1`
on 2026-11-21. Ordered by how often an owner is likely to change each one.
Task T-25 in [plan.md](./plan.md) closes this list: each item gets a
control or an action in the settings page, or a written exemption.

| # | Setting or choice | Today |
|---|---|---|
| 1 | Hotkey chords and dictate mode (toggle or hold) | the page says to edit `[app]` in the file |
| 2 | Paste after dictating (`stt.paste`) | file only |
| 3 | Input device | free text; the list comes from a terminal command |
| 4 | Dictation engine (Parakeet or Whisper) and their installs | command only |
| 5 | Voice per engine, with a preview for each engine | Kokoro and `say` only |
| 6 | Cues and sounds (`stt.cues`, `stt.sounds`) | file only |
| 7 | Verbatim (`stt.verbatim`) | file only |
| 8 | Recording limits (`stt.max_seconds`, `stt.max_take_seconds`) | file only |
| 9 | Long reads (`max_chars`, `overflow`) | file only |
| 10 | Reading rules (`[speech]`, thirteen keys) | file only, and a file with this table cannot be saved by the page |
| 11 | Beam size (`stt.beam_size`, Whisper only) | file only |
| 12 | What the stop hotkey does (`app.stop_hotkey`) | file only |
| 13 | Removing one model; removing Kokoro; installing the cleanup model | no per-model remove; no Kokoro remove command; the server route exists with no button |
| 14 | Audio cache size, and clearing it | none; the cache never shrinks |
| 15 | Quick Actions and the Claude Code hook: install and remove; the hook's character cap | install by command; no remove; the cap is a constant in a script |
| 16 | Menu-bar app restart and remove | install only |
| 17 | Transcription folder, keep-audio and model | file only |
| 18 | Machine profile | does not exist |

Exempt by nature, shown as guidance with a link to the right pane of System
Settings: the microphone grant and the Accessibility grant.
