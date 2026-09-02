# Roadmap

Future work, with the research behind each item. Status legend: **planned** = analysed, decisions pending, nothing built; **spiked** = feasibility measured; **building**; **shipped**.

| Item | Status | Tracking | Research |
|---|---|---|---|
| Hotkey-triggered local dictation (voice → text via a local Whisper model, Quick Action toggle, clipboard output, optional cleanup) | planned | [#1](https://github.com/matthager12-collab/vocalize/issues/1) | [analysis](next-features-analysis.md) · [full design](research/2026-09-01-dictation-design.md) · [voicebox findings](research/2026-09-01-voicebox-findings.md) |
| Config portal (`vocalize portal`, stdlib local web page with a readiness checklist) — or the one-day `vocalize status` screen first | planned | [#1](https://github.com/matthager12-collab/vocalize/issues/1) | [analysis](next-features-analysis.md) · [full design](research/2026-09-01-config-portal-design.md) |

## Open decisions (2026-09-01)

1. Run the 3-hour dictation spike? It measures Whisper (`base.en` / `small.en` / `large-v3-turbo-q5_0`) against Apple's on-device recognizer on the owner's own voice with developer jargon, plus latency and RAM on the 8 GB M3. If Apple's engine wins, the Whisper branch is deleted from the plan.
2. Portal (~50 h) or `vocalize status` (~1 day) first?
3. Hotkey chord — proposed ⌃⌥⌘D.

## Deferred on purpose

Hold-to-talk (needs an event tap + Input Monitoring, ~6 h), auto-paste (Accessibility, ~2 h, belongs in the recorder bundle), Apple STT mode (~1.5 h), menu-bar agent (~12 h), local-LLM cleanup, resident/pre-warmed STT worker (only if the spike shows >2.5 s overhead), a native SwiftUI/Tauri app.

## Sequencing suggestion

Dictation first (the spike decides the engine, then ~20 h), portal or `status` second.
