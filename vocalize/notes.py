"""Transcription: turn audio or text sources into timestamped transcripts.

Runs per-source transcription only; no summary is generated.
The transcripts folder acts as the ledger: .md files in the folder are scanned
for (source_sha256, source) pairs to determine if a file is already processed.
"""

from __future__ import annotations

import datetime
import fcntl
import hashlib
import json
import math
import os
import re
import secrets
import select
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from collections.abc import Callable
from pathlib import Path

from . import __version__, config, dictate, vocabulary
from .exceptions import VocalizeError

ALLOWLISTED_SUFFIXES = {".m4a", ".mp3", ".wav", ".aif", ".aiff", ".caf", ".txt"}
_NOTES_WORKDIR_PREFIX = "vocalize-notes-"
_STALE_AGE = 86400.0  # 24 hours
_SEGMENT_TIMEOUT = 600.0  # Inactivity timeout for long transcriptions
_WORKER_DEADLINE = 24 * 3600.0  # A stuck worker cannot run indefinitely.
_MAX_WORKER_LINE_BYTES = 32 * 1024 * 1024
_MAX_WORKER_OUTPUT_BYTES = 64 * 1024 * 1024
_MAX_WORKER_SEGMENTS = 100_000
_MAX_WORKER_SECONDS = 7 * 24 * 3600.0
# Enough of the worker's stderr tail to recognise uv's offline refusal.
_MAX_WORKER_STDERR_TAIL = 16 * 1024
_WHISPER_INSTALL_HINT = "vocalize local install --stt --engine whisper"
# How much of a note `_done` reads looking for the end of its frontmatter.
_FRONTMATTER_MAX_BYTES = 64 * 1024
# The whole transcript of a recording with no sound: Whisper invents words
# ("Thank you.") for digital silence, so the worker is never run on one.
SILENT_TRANSCRIPT = "This recording is silent: it has no words."
_UV_MISSING = (
    "uv is not installed, and transcription runs under it. "
    "Install it from https://docs.astral.sh/uv/"
)


class NotesError(VocalizeError):
    """Raised on notes processing errors."""


def _slugify(text: str, fallback: str = "note") -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return slug or fallback


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def _is_under(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _sweep_stale_notes_dirs() -> None:
    """Sweep orphaned vocalize-notes-* temporary directories older than 24h."""
    cutoff = time.time() - _STALE_AGE
    try:
        candidates = list(Path(tempfile.gettempdir()).glob(_NOTES_WORKDIR_PREFIX + "*"))
    except OSError:
        return
    for path in candidates:
        try:
            if path.is_dir() and not path.is_symlink() and path.stat().st_mtime < cutoff:
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            continue


def _frontmatter_lines(path: Path) -> list[str]:
    """The lines of a note's frontmatter block, between its two `---` lines.

    Read line by line up to the closing line, never past
    `_FRONTMATTER_MAX_BYTES`: a long title (JSON escapes a non-ASCII
    letter as six characters) used to push `source:` out of a fixed-size
    head. A block that does not close within the bound, or a file that
    does not start with one, has no provenance.
    """
    lines: list[str] = []
    read = 0
    with open(path, "rb") as f:
        first = f.readline(_FRONTMATTER_MAX_BYTES)
        if first.strip() != b"---":
            return []
        read += len(first)
        while read < _FRONTMATTER_MAX_BYTES:
            raw = f.readline(_FRONTMATTER_MAX_BYTES - read)
            if not raw:
                return []
            read += len(raw)
            line = raw.decode("utf-8", errors="replace")
            if line.strip() == "---":
                return lines
            lines.append(line)
    return []


def _done(folder: Path) -> tuple[dict[tuple[str, str], Path], dict[str, Path]]:
    """Scan folder/*.md, read the frontmatter block of each.

    Returns (done_ledger, source_to_note):
    - done_ledger: {(source_sha256, source): path}
    - source_to_note: {source: path}
    """
    ledger: dict[tuple[str, str], Path] = {}
    source_to_note: dict[str, Path] = {}
    if not folder.is_dir():
        return ledger, source_to_note
    for path in sorted(folder.glob("*.md")):
        if path.name.startswith("."):
            continue
        try:
            head = _frontmatter_lines(path)
        except OSError:
            continue
        src_sha = None
        src_path = None
        for line in head:
            line_str = line.strip()
            if line_str.startswith("source:"):
                val = line_str.partition(":")[2].strip()
                try:
                    src_path = json.loads(val)
                except ValueError:
                    src_path = val.strip('"\'')
            elif line_str.startswith("source_sha256:"):
                val = line_str.partition(":")[2].strip()
                try:
                    src_sha = json.loads(val)
                except ValueError:
                    src_sha = val.strip('"\'')
        if src_path:
            source_to_note[src_path] = path
            if src_sha:
                ledger[(src_sha, src_path)] = path
    return ledger, source_to_note


def _format_timestamp(seconds: float) -> str:
    """Format seconds into [MM:SS] or [HH:MM:SS]."""
    total = int(max(0, seconds))
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    if h > 0:
        return f"[{h:02d}:{m:02d}:{s:02d}]"
    return f"[{m:02d}:{s:02d}]"


def _render_frontmatter(
    *,
    title: str,
    date_str: str,
    captured_str: str,
    source_path: str,
    source_sha256: str,
    duration_seconds: float | None,
    template_name: str,
    transcribed_by: str,
    summarized_by: str,
    left_machine: bool,
) -> str:
    dur_val = f"{round(duration_seconds)}" if duration_seconds is not None else "null"
    lines = [
        "---",
        f"title: {json.dumps(title)}",
        f"date: {json.dumps(date_str)}",
        'type: "recording"',
        f"captured: {json.dumps(captured_str)}",
        'license: "personal-use"',
        "topics: []",
        f"source: {json.dumps(source_path)}",
        f"source_sha256: {json.dumps(source_sha256)}",
        f"duration_seconds: {dur_val}",
        f"template: {json.dumps(template_name)}",
        f"transcribed_by: {json.dumps(transcribed_by)}",
        f"summarized_by: {json.dumps(summarized_by)}",
        f"left_machine: {'true' if left_machine else 'false'}",
        'trust: "untrusted-transcript"',
        f"vocalize: {json.dumps(__version__)}",
        "---",
    ]
    return "\n".join(lines) + "\n"


def _transcribe_long(
    cmd: list[str], duration: float | None, *, env: dict[str, str] | None = None,
    install_hint: str | None = None,
) -> tuple[str, list[dict]]:
    """Read bounded worker frames without blocking on partial lines or stderr.

    Only complete, advancing progress resets the inactivity timer. Raw worker
    diagnostics may contain transcript content, so only a bounded tail of
    stderr is held in memory, to recognise an offline start failure; it is
    never shown or stored.
    """
    from .local import runtime

    final_reply = None
    next_threshold_pct = 10
    last_progress = -1.0
    last_activity = time.monotonic()
    deadline = last_activity + _WORKER_DEADLINE
    pending = bytearray()
    output_bytes = 0
    max_seconds = _MAX_WORKER_SECONDS
    if isinstance(duration, (int, float)) and math.isfinite(duration) and duration > 0:
        max_seconds = min(max_seconds, duration + 30.0)

    def valid_seconds(value):
        return (type(value) in (int, float) and 0 <= value <= max_seconds
                and math.isfinite(value))

    def accept_frame(frame):
        nonlocal final_reply, next_threshold_pct, last_progress, last_activity
        if len(frame) > _MAX_WORKER_LINE_BYTES:
            raise NotesError("Transcription worker output exceeds limit")
        try:
            data = json.loads(frame)
        except (ValueError, UnicodeError, RecursionError):
            raise NotesError("Transcription worker returned invalid output") from None
        if not isinstance(data, dict) or final_reply is not None:
            raise NotesError("Transcription worker returned invalid output")
        if set(data) == {"progress"}:
            progress = data["progress"]
            if not valid_seconds(progress) or progress < last_progress:
                raise NotesError("Transcription worker returned invalid progress")
            if progress > last_progress:
                last_activity = time.monotonic()
                last_progress = progress
            if duration and duration > 0:
                pct = min(100, int(progress / duration * 100))
                if pct >= next_threshold_pct:
                    print(f"  transcribing... {pct}%", file=sys.stderr)
                    next_threshold_pct = (pct // 10 + 1) * 10
        elif type(data.get("ok")) is bool:
            if not data["ok"]:
                raise NotesError("Transcription worker failed")
            if (set(data) - {"ok", "text", "segments"}
                    or not ({"text", "segments"} & set(data))
                    or not isinstance(data.get("text", ""), str)):
                raise NotesError("Transcription worker returned invalid output")
            final_reply = data
        else:
            raise NotesError("Transcription worker returned invalid output")

    def remaining():
        now = time.monotonic()
        seconds = min(deadline - now, last_activity + _SEGMENT_TIMEOUT - now)
        if seconds <= 0:
            raise NotesError("transcription timed out")
        return seconds

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        cwd=tempfile.gettempdir(),
        env=env,
    )
    stderr_tail = bytearray()

    def drain_stderr() -> bool:
        """Keep only the newest bytes; False once stderr is closed."""
        while True:
            try:
                chunk = os.read(err_fileno, 65536)
            except BlockingIOError:
                return True
            if not chunk:
                return False
            stderr_tail.extend(chunk)
            del stderr_tail[:-_MAX_WORKER_STDERR_TAIL]

    try:
        assert proc.stdout is not None and proc.stderr is not None
        fileno = proc.stdout.fileno()
        err_fileno = proc.stderr.fileno()
        os.set_blocking(fileno, False)
        os.set_blocking(err_fileno, False)
        watched = [fileno, err_fileno]
        while True:
            readable, _, _ = select.select(watched, [], [], remaining())
            if not readable:
                raise NotesError("transcription timed out")
            if err_fileno in readable and not drain_stderr():
                watched.remove(err_fileno)
            if fileno not in readable:
                continue
            try:
                chunk = os.read(fileno, 65536)
            except BlockingIOError:
                continue
            if not chunk:
                if pending:
                    accept_frame(bytes(pending))
                break
            output_bytes += len(chunk)
            if output_bytes > _MAX_WORKER_OUTPUT_BYTES:
                raise NotesError("Transcription worker output exceeds limit")
            pending.extend(chunk)
            while (newline := pending.find(b"\n")) >= 0:
                frame = bytes(pending[:newline])
                del pending[:newline + 1]
                accept_frame(frame)
            if len(pending) > _MAX_WORKER_LINE_BYTES:
                raise NotesError("Transcription worker output exceeds limit")
        try:
            proc.wait(timeout=remaining())
        except subprocess.TimeoutExpired:
            raise NotesError("transcription timed out") from None
        if proc.returncode != 0:
            if err_fileno in watched:
                drain_stderr()
            offline = runtime.offline_failure(stderr_tail.decode("utf-8", "replace"))
            stderr_tail.clear()
            if offline:
                raise NotesError(
                    "Transcription worker could not start offline: "
                    f"{runtime.reinstall_hint(install_hint or _WHISPER_INSTALL_HINT)}"
                )
            raise NotesError("Transcription worker failed")
        if final_reply is None:
            raise NotesError("Transcription worker produced no output")
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        stderr_tail.clear()
        for stream in (proc.stdout, proc.stderr):
            if stream is not None:
                stream.close()

    raw_segments = final_reply.get("segments", [])
    if not isinstance(raw_segments, list) or len(raw_segments) > _MAX_WORKER_SEGMENTS:
        raise NotesError("Transcription worker returned invalid segments")
    clean_segments = []
    previous_start = 0.0
    for s in raw_segments:
        if (not isinstance(s, dict) or set(s) != {"start", "end", "text"}
                or not valid_seconds(s["start"]) or not valid_seconds(s["end"])
                or s["start"] < previous_start or s["end"] < s["start"]
                or not isinstance(s["text"], str)):
            raise NotesError("Transcription worker returned invalid segments")
        previous_start = s["start"]
        clean_text = dictate.sanitize(s["text"])
        clean_segments.append({
            "start": s["start"],
            "end": s["end"],
            "text": clean_text,
        })

    full_text = " ".join(s["text"] for s in clean_segments if s["text"])
    if not full_text:
        full_text = dictate.sanitize(final_reply.get("text", ""))

    return full_text, clean_segments


def _write_note_atomic(folder: Path, target_path: Path, content: str | bytes) -> None:
    """Write note file atomically using a .tmp file mode 0600 with O_NOFOLLOW."""
    token = secrets.token_hex(4)
    tmp_path = folder / f".{target_path.name}.{token}.tmp"
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(tmp_path, flags, 0o600)
    try:
        if isinstance(content, bytes):
            with open(fd, "wb") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
        else:
            with open(fd, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
        os.replace(tmp_path, target_path)
        directory_fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _whisper_worker(take_wav: Path, model_name: str, stt: dict) -> tuple[list[str], dict[str, str]]:
    """(command, environment) for Whisper's segment mode."""
    from .local import install, runtime, uv_path
    from .local import whisper_manifest as w_manifest

    ready, reason = install.installed(
        w_manifest,
        files=[w_manifest.file_for(model_name)],
        install_hint=_WHISPER_INSTALL_HINT,
    )
    if not ready:
        raise NotesError(f"Speech-to-text model {model_name}: {reason}")
    try:
        selected = runtime.selection(w_manifest.RUNTIME_PACKAGE)
    except runtime.RuntimeSelectionError as exc:
        raise NotesError(str(exc)) from exc
    uv = "" if selected else uv_path()
    if not selected and uv is None:
        raise NotesError(_UV_MISSING)
    worker_cmd = runtime.command(
        w_manifest, dictate.worker_argv(uv, take_wav, stt) + ["--segments"], selected,
    )
    return worker_cmd, runtime.environment(selected)


def _file_engine(stt: dict, model_name: str) -> str:
    """"whisper" or "parakeet" for file transcription.

    A chosen engine is returned as it is, so one that cannot run is its
    own error. "auto" keeps Whisper whenever its model is installed, else
    takes a Parakeet that is installed and can run here.
    """
    from .local import install, parakeet_manifest
    from .local import whisper_manifest as w_manifest

    engine = stt.get("engine", "auto")
    if engine != "auto":
        return engine
    if install.installed(w_manifest, files=[w_manifest.file_for(model_name)])[0]:
        return "whisper"
    if parakeet_manifest.supported() and install.installed(
        parakeet_manifest, install_hint=dictate.PARAKEET_INSTALL_HINT,
    )[0]:
        return "parakeet"
    raise NotesError(
        "No speech-to-text model is installed. Install one with: "
        f"{_WHISPER_INSTALL_HINT} (or, on an Apple Silicon Mac: {dictate.PARAKEET_INSTALL_HINT})"
    )


def _parakeet_worker(take_wav: Path) -> tuple[list[str], dict[str, str], str, str]:
    """(command, environment, transcribed_by, install hint) for Parakeet's
    chunked segment mode. Reached when `[stt] engine` is "parakeet", or
    "auto" with no Whisper model installed; a Parakeet that cannot run
    here is an error, never a silent Whisper."""
    from .local import install, runtime, uv_path
    from .local import parakeet_manifest as p_manifest

    if not p_manifest.supported():
        raise NotesError(
            'Parakeet needs an Apple Silicon Mac. Use [stt] engine = "whisper" to transcribe files here.'
        )
    ready, reason = install.installed(p_manifest, install_hint=dictate.PARAKEET_INSTALL_HINT)
    if not ready:
        raise NotesError(f"Speech-to-text model parakeet: {reason}")
    uv = uv_path()
    if uv is None:
        raise NotesError(_UV_MISSING)
    worker_cmd = [uv, *p_manifest.runtime_argv(), "--transcribe", str(take_wav), "--segments"]
    model = p_manifest.REPO.rsplit("/", 1)[-1]
    return worker_cmd, runtime.environment(), f"parakeet:{model}", dictate.PARAKEET_INSTALL_HINT


class RunLock:
    """Machine-local run lock on <folder>/.vocalize.lock."""

    def __init__(self, folder: Path):
        self.folder = folder
        self.lock_file = folder / ".vocalize.lock"
        self.fd: int | None = None

    def __enter__(self):
        self.fd = os.open(self.lock_file, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            os.close(self.fd)
            self.fd = None
            print("vocalize: another transcription run is in progress", file=sys.stderr)
            sys.exit(1)
        return self

    def __exit__(self, *exc):
        if self.fd is not None:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None


def process_sources(
    sources: list[str | Path],
    *,
    force: bool = False,
    keep_audio: bool | None = None,
    config_dict: dict | None = None,
    work_root: Path | None = None,
    transcript_checkpoint: Callable[[str], None] | None = None,
    offline: bool = False,
) -> list[Path]:
    """Transcribe files or directories into transcripts.

    Returns the list of created or updated transcript file paths.
    All processing is local; ``offline`` is retained for caller compatibility.
    """
    notes_cfg = config.resolve_transcribe(config_dict)
    folder_str = notes_cfg["folder"]
    folder = Path(folder_str).expanduser().resolve()

    # Permission check and creation
    try:
        if not folder.exists():
            folder.mkdir(parents=True, mode=0o700)
    except PermissionError as exc:
        raise NotesError(
            f"Permission denied accessing transcripts folder {folder}. "
            f"Check System Settings › Privacy & Security › Files and Folders."
        ) from exc

    keep_audio_flag = notes_cfg["keep_audio"] if keep_audio is None else keep_audio

    # Effective STT model
    stt_cfg = config.resolve_stt(config_dict)
    model_name = notes_cfg.get("model") or stt_cfg["model"]
    stt_for_worker = dict(stt_cfg)
    stt_for_worker["model"] = model_name

    created_notes: list[Path] = []

    with RunLock(folder):
        if work_root is None:
            _sweep_stale_notes_dirs()
        done_map, source_to_note = _done(folder)

        # Collect source files
        candidate_files: list[Path] = []
        for s in sources:
            p = Path(s)
            if not p.exists():
                print(f"vocalize: skipping non-existent source {p}", file=sys.stderr)
                continue
            if p.is_dir():
                for child in sorted(p.iterdir()):
                    if child.name.startswith("."):
                        continue
                    if child.is_file() and child.suffix.lower() in ALLOWLISTED_SUFFIXES:
                        candidate_files.append(child)
            elif p.is_file():
                if p.name.startswith("."):
                    continue
                if p.suffix.lower() in ALLOWLISTED_SUFFIXES:
                    candidate_files.append(p)
                else:
                    print(f"vocalize: skipping unsupported file type {p.name}", file=sys.stderr)

        for src in candidate_files:
            resolved_src = src.resolve()

            # Self-ingestion guard
            if resolved_src == folder or _is_under(resolved_src, folder):
                print(f"Skipping {src.name}: inside transcripts folder (self-ingestion guard)")
                continue

            src_sha = _sha256_file(resolved_src)
            resolved_src_str = str(resolved_src)

            # Done check on both hash and resolved path
            existing_note = done_map.get((src_sha, resolved_src_str))
            if existing_note and not force:
                print(f"Skipping {src.name}: already processed as {existing_note.name}")
                continue

            # Determine date and slug
            st = resolved_src.stat()
            btime = getattr(st, "st_birthtime", st.st_mtime)
            file_date_ts = min(btime, st.st_mtime)
            date_str = datetime.datetime.fromtimestamp(file_date_ts, tz=datetime.timezone.utc).date().isoformat()
            captured_str = datetime.datetime.now(tz=datetime.timezone.utc).date().isoformat()
            slug = _slugify(resolved_src.stem, fallback=src_sha[:8])

            # Determine target note path
            prior_note = existing_note or source_to_note.get(resolved_src_str)
            if force and prior_note:
                target_note = prior_note
            else:
                base_name = f"{date_str}-{slug}.md"
                if not (folder / base_name).exists():
                    target_note = folder / base_name
                else:
                    count = 2
                    while (folder / f"{date_str}-{slug}-{count}.md").exists():
                        count += 1
                    target_note = folder / f"{date_str}-{slug}-{count}.md"

            # Transcribe / read
            is_text = resolved_src.suffix.lower() == ".txt"
            segments: list[dict] = []
            take_wav: Path | None = None

            if is_text:
                try:
                    with open(resolved_src, "r", encoding="utf-8", errors="replace") as f:
                        raw_content = f.read()
                except OSError as exc:
                    print(f"vocalize: could not read {src}: {exc}", file=sys.stderr)
                    continue
                transcript = dictate.sanitize(raw_content)
                duration_seconds = None
                transcribed_by = "file"
            else:
                # Audio conversion
                tmp_dir = Path(tempfile.mkdtemp(prefix=_NOTES_WORKDIR_PREFIX, dir=work_root))
                os.chmod(tmp_dir, 0o700)
                try:
                    take_wav = tmp_dir / "take.wav"
                    afconvert_cmd = [
                        "afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1",
                        str(resolved_src), str(take_wav),
                    ]
                    conv_res = subprocess.run(
                        afconvert_cmd, capture_output=True, text=True, timeout=300, check=False
                    )
                    if conv_res.returncode != 0:
                        print("vocalize: audio conversion failed", file=sys.stderr)
                        continue

                    try:
                        with wave.open(str(take_wav), "rb") as w:
                            frames = w.getnframes()
                            rate = w.getframerate()
                            duration_seconds = frames / float(rate)
                    except (OSError, wave.Error):
                        duration_seconds = 0.0

                    if _file_engine(stt_cfg, model_name) == "parakeet":
                        worker_cmd, worker_env, transcribed_by, hint = _parakeet_worker(take_wav)
                        extra = {"install_hint": hint}
                    else:
                        worker_cmd, worker_env = _whisper_worker(take_wav, model_name, stt_for_worker)
                        transcribed_by = f"whisper:{model_name}"
                        extra = {}
                    if dictate._is_silent(take_wav):
                        # The same guard as dictation and `listen --wav`.
                        transcript = SILENT_TRANSCRIPT
                    else:
                        transcript, segments = _transcribe_long(
                            worker_cmd, duration_seconds, env=worker_env, **extra,
                        )
                        # After either engine: Parakeet never saw the vocabulary.
                        terms = stt_cfg.get("vocabulary") or []
                        transcript = vocabulary.correct(transcript, terms)
                        for seg in segments:
                            seg["text"] = vocabulary.correct(seg["text"], terms)

                    if keep_audio_flag and take_wav.is_file():
                        dest_wav = target_note.with_suffix(".wav")
                        _write_note_atomic(folder, dest_wav, take_wav.read_bytes())
                finally:
                    shutil.rmtree(tmp_dir, ignore_errors=True)

            if transcript_checkpoint is not None:
                transcript_checkpoint(transcript)

            # Format transcript section
            if segments:
                transcript_lines = []
                for seg in segments:
                    clean_seg = dictate.sanitize(seg.get("text", ""))
                    if clean_seg:
                        ts = _format_timestamp(seg["start"])
                        transcript_lines.append(f"{ts} {clean_seg}")
                formatted_transcript = "\n".join(transcript_lines)
            else:
                formatted_transcript = transcript

            frontmatter = _render_frontmatter(
                title=resolved_src.stem,
                date_str=date_str,
                captured_str=captured_str,
                source_path=resolved_src_str,
                source_sha256=src_sha,
                duration_seconds=duration_seconds,
                template_name="none",
                transcribed_by=transcribed_by,
                summarized_by="none",
                left_machine=False,
            )

            note_body = f"{frontmatter}\n## Transcript\n\n{formatted_transcript}\n"
            _write_note_atomic(folder, target_note, note_body)
            done_map[(src_sha, resolved_src_str)] = target_note
            created_notes.append(target_note)
            print(f"Wrote transcript: {target_note.name}")

    return created_notes
