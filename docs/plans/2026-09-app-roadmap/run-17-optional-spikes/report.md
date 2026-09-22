# Report: run 17, optional spikes

Date 2026-09-22. Branch `spikes-run-17`. Source plan in [project-plan.md](./project-plan.md); numbers and verdicts in [../spike-notes.md](../spike-notes.md) § Foundation Models, § SpeechAnalyzer, and § Voice Memos.

- T-160: done — Foundation Models Swift probe against `FoundationModels.framework`; `SystemLanguageModel.default.availability` returns `unavailable(appleIntelligenceNotEnabled)` because Apple Intelligence is disabled in macOS settings; verdict: drop / defer, retaining Qwen3.5-4B-4bit (mlx-lm).
- T-161: done — SpeechAnalyzer Swift probe using macOS 26 `Speech.SpeechAnalyzer` and `SpeechTranscriber` on 20.45 s jargon audio; 0.39 s real execution, 18.7 MB peak RSS, but 7/10 accuracy on technical terms (missed pyproject, uv no-project, sha256); verdict: no-go for developer dictation compared to whisper turbo's 12/12.
- T-162: done — Voice Memos probe; `~/Library/Group Containers/group.com.apple.VoiceMemos.shared` is blocked by macOS TCC sandbox (`Operation not permitted`); on-device transcripts are embedded in exported `.m4a` files via custom `tsrp` JSON leaf atom (`moov.udta.tsrp`); verdict: defer repository integration to future out-of-band task, leaving `vocalize/` untouched.

Security gate: No tracked application or library code touched in spike-only run. Scratch binaries and probes kept isolated outside repository code.

Suite: 2,231 passed, 3 skipped. Ruff clean.

validate-exit: PASS
