# Report: run 13, spikes

Date 2026-09-21. Branch `notes`. Source plan in [project-plan.md](./project-plan.md); numbers in [../spike-notes.md](../spike-notes.md) § LLM and § Parakeet; decisions in [../decisions.md](../decisions.md) DEC-034.

- T-120: done — Parakeet spike with `sherpa-onnx==1.13.7` int8 model against Whisper `small.en` and `large-v3-turbo-q5_0` on 20 s jargon clip and 5-minute take; DEC-034 decided B (no-go) due to RSS exceeding 1.5 GB limit (1.73–3.03 GB) and zero-miss Whisper turbo outperforming Parakeet on dev jargon.
- T-121: done — mlx-lm spike with Qwen3.5-4B 4-bit; all 7 items confirmed offline and recorded under `spike-notes.md` § LLM (signatures, offline flags, model_type, think-off template, single safetensors shard, cold median 4.19 s noting resident session in T-133, warm median 1.86 s, peak RSS 1,123.3 MB).
- T-122: skipped — DEC-034 is no-go; whisper remains the sole STT engine for 0.14.0, no Parakeet manifest, worker, or engine dispatch needed.

Security gate: No tracked application or library code touched in spike-only run. Offline verification confirmed for MLX and Sherpa-ONNX runtimes.

Suite: 2,231 passed, 3 skipped. Ruff clean.

validate-exit: PASS
