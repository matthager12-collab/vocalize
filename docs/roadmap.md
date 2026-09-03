# Roadmap

Future work, with the research behind each item. Status legend: **planned** = analysed, decisions pending, nothing built; **spiked** = feasibility measured; **building**; **shipped**.

| Item | Status | Tracking | Research |
|---|---|---|---|
| Hotkey-triggered local dictation (voice → text via a local Whisper model, Quick Action toggle, clipboard output, optional cleanup) | **shipped** in 0.10.0, usable from 0.10.1 | [#1](https://github.com/matthager12-collab/vocalize/issues/1) · [plan](plans/2026-09-next-features/plan.md) | [analysis](next-features-analysis.md) · [full design](research/2026-09-01-dictation-design.md) · [voicebox findings](research/2026-09-01-voicebox-findings.md) |
| `vocalize status` one-screen readiness check | **shipped** in 0.10.0 | [#1](https://github.com/matthager12-collab/vocalize/issues/1) | [analysis](next-features-analysis.md) |
| Spoken cues for dictation (`[stt] cues = "words"`: "start" / "stopped" / "ready" instead of, or as well as, the sounds) | building | — | owner request, 2026-09-02 |
| Config portal (`vocalize portal`, stdlib local web page on top of the same readiness rows) | planned — runs 7–10 of the [plan](plans/2026-09-next-features/plan.md) | [#1](https://github.com/matthager12-collab/vocalize/issues/1) | [analysis](next-features-analysis.md) · [full design](research/2026-09-01-config-portal-design.md) |

## Decisions (2026-09-01, closed 2026-09-02)

1. The dictation spike ran ([results](plans/2026-09-next-features/spike-2026-09-01.md)); Whisper `small.en` is the default.
2. `vocalize status` first (0.10.0); the portal follows as 0.11.0.
3. Hotkey chord ⌃⌥⌘D, set by the user in System Settings.

## Deferred on purpose

Hold-to-talk (needs an event tap + Input Monitoring, ~6 h), auto-paste (Accessibility, ~2 h, belongs in the recorder bundle), Apple STT mode (~1.5 h), menu-bar agent (~12 h), local-LLM cleanup, resident/pre-warmed STT worker (only if the spike shows >2.5 s overhead), a native SwiftUI/Tauri app.

## Sequencing suggestion

Dictation first (the spike decides the engine, then ~20 h), portal or `status` second.
