// VocalizeApp — the menu-bar app: three global hotkeys, one status icon, and
// nothing else.
//
// It exists because a hotkey has to reach vocalize from wherever the user is
// typing, because a recording indicator has to be visible without a window, and
// because a synthetic Command-C or Command-V is granted to a *bundle* under
// Accessibility, not to a script. It holds no state of its own: every hotkey is
// one fresh `vocalize` process (DEC-028), so an upgrade lands on the next press
// and a crash in one press leaves nothing behind. The icon is read from
// `~/.cache/vocalize/dictate.session`, which `dictate` writes; the chords are
// read from `vocalize settings`, which the app re-runs when `~/.config/vocalize`
// changes.
//
// Contract (docs/plans/2026-09-app-roadmap/design.md, "App spawn contract and
// binary discovery", "Files under ~/.cache/vocalize"):
//
//   spawn:   fresh environment, cwd /tmp, stdin+stdout /dev/null, stderr to app.log
//   verbs:   dictate [--start|--stop], clip, stop, listen --cancel, settings, portal
//   binary:  UserDefaults VocalizeBinary, else ~/.local/bin, /opt/homebrew/bin,
//            /usr/local/bin — the override is stat-checked exactly like a candidate
//   writes:  ~/.cache/vocalize/app.status (0600, replaced atomically) on launch
//            and on every dispatch; children's stderr appended to app.log
//
// Three rules this file must never break:
//
//   1. No pasteboard or transcript text ever reaches a notification, the log, a
//      menu title or an argument. `notifier.show` takes a `Notice` case, not a
//      String, so there is nowhere for dynamic text to enter; the app never
//      reads the pasteboard's contents at all, only its `changeCount`.
//   2. Never execute a path it has not stat-checked (`checkedBinary`) — this
//      process holds the Accessibility grant.
//   3. Never paste on a marker it cannot tie to the dictation it watched
//      (`pasteIfNonceMatches`).
//
// This source ships complete in 0.13.0 and is frozen after it: the bundle is
// ad-hoc signed and the Accessibility grant is keyed to that signature, so
// every later edit costs each user a re-grant (DEC-032). Both hotkey backends,
// the hold dispatch and the paste watcher are therefore all here from the first
// release, even though only some of them are wired up in Python yet.

import AppKit
import ApplicationServices
import Carbon
import Foundation

// MARK: - Build-time choices

/// Which global-hotkey mechanism this build uses.
///
/// Carbon needs no permission and delivers key-up, which hold-to-talk needs;
/// the event monitor rides the Accessibility grant the app already takes for
/// speak-the-selection (DEC-021). The T-60 spike delivered a clean down and up
/// from Carbon with an Electron app, a self-drawn terminal and a full-screen
/// window in front, so Carbon it is (DEC-033). There is no runtime fallback:
/// a macOS regression of the chosen backend surfaces as `hotkeys: failed` in
/// `app.status` and a doctor row, and switching is a rebuild and a re-grant.
enum HotkeyBackend: String {
    case carbon
    case monitor
}

let hotkeyBackend: HotkeyBackend = .carbon

// MARK: - Paths
//
// Both directories are the CLI's, not the app's: `~/.cache/vocalize` is where
// dictate keeps its session, and `~/.config/vocalize` is where the config file
// lives. The app creates them if they are missing only so the vnode sources
// below have something to open.

let homeDirectory = NSHomeDirectory()
let cacheDirectory = homeDirectory + "/.cache/vocalize"
let configDirectory = homeDirectory + "/.config/vocalize"
let sessionPath = cacheDirectory + "/dictate.session"
let copiedMarkerPath = cacheDirectory + "/dictate.copied"
let statusPath = cacheDirectory + "/app.status"
let logPath = cacheDirectory + "/app.log"

/// One megabyte of children's stderr is already more than anyone reads. The cap
/// is checked on every dispatch, not only at launch, because the app is kept
/// alive by launchd and may not restart for weeks — and one wedged child
/// looping on stderr is exactly the case a launch-time check never sees.
let logSizeCap: off_t = 1_048_576

func ensureDirectory(_ path: String) {
    try? FileManager.default.createDirectory(
        atPath: path, withIntermediateDirectories: true,
        attributes: [.posixPermissions: 0o700])
}

// MARK: - Fixed user-visible strings
//
// Every notice the app can ever show. Nothing is interpolated: a transcript, a
// selection or a config value must never reach the menu bar, so `Notice` is an
// enum and `show` takes a case — there is no code path that can build one of
// these out of data.

enum Notice: String {
    case nothingSelected = "Nothing selected"
    case binaryMissing = "vocalize could not run — run: uv tool install --reinstall vocalize-cli"
    case overrideIgnored = "The VocalizeBinary override was ignored — it is not a checked executable."
    case clipCredential = "That looked like a credential, so it was not spoken."
    case clipFailed = "The selection could not be spoken."
    case notPasted = "Copied, not pasted (window changed)."
    case accessibilityNeeded = "Vocalize needs Accessibility to copy the selection."
}

/// A notice is a short flash of the status item's own title, not a system
/// notification.
///
/// `UNUserNotificationCenter.current()` raises an Objective-C exception —
/// uncatchable from Swift, so an abort — when LaunchServices holds no record of
/// the running bundle, and this bundle is launched by a LaunchAgent out of
/// `~/Library/Application Support/vocalize`, which is not a directory
/// LaunchServices scans. With `KeepAlive {SuccessfulExit: false}` that abort is
/// a crash loop at launch: no icon, no hotkeys, and this source is frozen after
/// 0.13.0, so no fix that does not cost every user a re-grant. The status item
/// is already on screen and already carries fixed strings, so the notice goes
/// there instead — no framework, no authorization, no prompt (review round 2).
/// Every body is one of the cases above; nothing is interpolated.
final class Notifier {
    /// Set once the status item exists. Before that — a failed override read
    /// during `applicationDidFinishLaunching` — there is nowhere to show a
    /// notice and the app stays quiet, which is what it did before too.
    weak var statusItem: StatusItemController?

    func show(_ notice: Notice) {
        DispatchQueue.main.async { [weak self] in self?.statusItem?.flash(notice) }
    }
}

let notifier = Notifier()

// MARK: - Binary discovery

let binaryCandidates = [
    homeDirectory + "/.local/bin/vocalize",
    "/opt/homebrew/bin/vocalize",
    "/usr/local/bin/vocalize",
]

/// A regular file, executable, owned by this user or root, and not writable by
/// everyone. The app holds the Accessibility grant and is kept alive by
/// launchd, so it never executes a path it has not checked.
func isCheckedExecutable(_ path: String) -> Bool {
    var info = stat()
    guard stat(path, &info) == 0 else { return false }
    guard (info.st_mode & S_IFMT) == S_IFREG else { return false }
    guard (info.st_mode & S_IXUSR) != 0 else { return false }
    guard info.st_uid == getuid() || info.st_uid == 0 else { return false }
    guard (info.st_mode & S_IWOTH) == 0 else { return false }
    return true
}

/// The `vocalize` to spawn, or nil.
///
/// The `VocalizeBinary` default is the override for an install the three fixed
/// paths cannot see (a uv tool directory somewhere else). It passes exactly the
/// checks a candidate passes; an override that fails them is ignored with a
/// fixed notice and discovery carries on down the list, so a stale or hostile
/// default can never point the app at something it would not have run anyway
/// (review R1). Nothing is baked at install time: the answer is recomputed
/// whenever a spawn fails, which is how an upgrade that moved the binary is
/// picked up without a rebuild.
func checkedBinary() -> String? {
    if let override = UserDefaults.standard.string(forKey: "VocalizeBinary"),
        !override.isEmpty {
        if isCheckedExecutable(override) { return override }
        notifier.show(.overrideIgnored)
    }
    for candidate in binaryCandidates where isCheckedExecutable(candidate) {
        return candidate
    }
    return nil
}

// MARK: - app.status

/// The four lines `app status`, readiness, doctor and the portal read. Written
/// on launch and on every dispatch, 0600, replaced atomically so a reader never
/// sees half a file.
/// Every field is behind one lock because the three dispatch queues write this
/// file, and one of them is concurrent: two `stop` presses land on two threads.
final class StatusWriter {
    /// Only the first failing chord is named: the contract's line is
    /// `hotkeys: failed:<name>[:taken]`, one name, and a user with two broken
    /// chords fixes them one at a time anyway.
    /// `settings` until the first registration completes: the launch write
    /// happens before `vocalize settings` has been read, and `hotkeys: ok`
    /// there would tell readiness and doctor that chords are live seconds
    /// before any of them is registered (review round 3).
    private var firstFailure: String? = "settings"
    private var binaryPath: String?
    /// Two concurrent writers must not pick the same temporary name, or one
    /// renames the file the other is still filling.
    private var sequence: UInt64 = 0
    private let lock = NSLock()

    func setFirstFailure(_ name: String?) {
        lock.lock()
        firstFailure = name
        lock.unlock()
    }

    func setBinaryPath(_ path: String?) {
        lock.lock()
        binaryPath = path
        lock.unlock()
    }

    func write() {
        // The lock is held across the rename, not only across the snapshot: two
        // writers that read different states and then race on `rename` can land
        // the older of the two last, and `app.status` would then report a
        // hotkey failure that has already been fixed. A few hundred bytes and
        // one rename is a short thing to hold a lock over (review round 3).
        lock.lock()
        defer { lock.unlock() }
        let binary = binaryPath
        let failure = firstFailure
        sequence &+= 1
        let ticket = sequence
        var text = "accessibility: \(AXIsProcessTrusted() ? "granted" : "not granted")\n"
        text += "vocalize: \(binary ?? "none")\n"
        text += "hotkeys: \(failure.map { "failed:\($0)" } ?? "ok")\n"
        text += "hotkey_backend: \(hotkeyBackend.rawValue)\n"
        ensureDirectory(cacheDirectory)
        let temporary = cacheDirectory + "/app.status.\(getpid()).\(ticket)"
        let manager = FileManager.default
        try? manager.removeItem(atPath: temporary)
        // Created at 0600 rather than written and chmodded: the file is never
        // readable by anyone else, not even for the width of one syscall.
        guard manager.createFile(
            atPath: temporary, contents: Data(text.utf8),
            attributes: [.posixPermissions: 0o600])
        else { return }
        if rename(temporary, statusPath) != 0 {
            try? manager.removeItem(atPath: temporary)
        }
    }
}

let statusWriter = StatusWriter()

// MARK: - Spawning

struct SpawnResult {
    let launched: Bool  // false means the binary was missing or would not exec
    let status: Int32
    let output: String  // only ever filled for `settings`, never shown to anyone
}

/// The three queues. `dictate` is serial so a second press cannot start a
/// second child; `speak` is serial for the same reason; `stop` is concurrent so
/// "make it stop" never waits behind a transcription that has minutes to run.
/// `settings` and `portal` ride the concurrent queue too: neither should queue
/// behind a `clip` that is halfway through reading a paragraph aloud.
let dictateQueue = DispatchQueue(label: "cards.arda.vocalize.app.dictate")
let speakQueue = DispatchQueue(label: "cards.arda.vocalize.app.speak")
let stopQueue = DispatchQueue(label: "cards.arda.vocalize.app.stop", attributes: .concurrent)

/// Children's stderr, appended, created 0600, truncated in place above the cap.
///
/// O_APPEND, not seek-to-end: the stop queue is concurrent, so two children can
/// hold this file at once and each write has to land at whatever the end is
/// *now*. Truncation is in place rather than unlink-and-recreate because a
/// `tail -f` the user is watching should survive it, and because the file's
/// mode is then never re-derived from the umask.
/// Returns nil when the log cannot be opened; the caller then sends stderr to
/// the null device rather than closing the shared one afterwards.
/// `O_NOFOLLOW` for the same reason the cache files are read that way: this is
/// a path in a directory any process running as this user can write, and
/// without it a symlink dropped in its place would aim every child's stderr at
/// the target and — once the target passed the cap — truncate it to nothing.
/// ELOOP then reads as "no log", which is what this function already promises.
func logHandle() -> FileHandle? {
    ensureDirectory(cacheDirectory)
    let descriptor = open(logPath, O_WRONLY | O_APPEND | O_CREAT | O_NOFOLLOW, 0o600)
    guard descriptor >= 0 else { return nil }
    var info = stat()
    if fstat(descriptor, &info) == 0, (info.st_mode & S_IFMT) == S_IFREG,
        info.st_size > logSizeCap {
        ftruncate(descriptor, 0)
    }
    return FileHandle(fileDescriptor: descriptor, closeOnDealloc: false)
}

/// The child's environment, built from nothing.
///
/// Not inherited: a launchd-provided environment carries whatever the user's
/// login session had, `DYLD_*` included, and this app runs with an Accessibility
/// grant. `TMPDIR` is the one variable passed through, because launchd's
/// per-user value is the only correct one and Python's tempfile wants it.
func childEnvironment() -> [String: String] {
    var environment = [
        "HOME": homeDirectory,
        "USER": NSUserName(),
        "LANG": "en_US.UTF-8",
        "PATH": "/opt/homebrew/bin:/usr/local/bin:\(homeDirectory)/.local/bin:/usr/bin:/bin",
    ]
    if let temporary = ProcessInfo.processInfo.environment["TMPDIR"], !temporary.isEmpty {
        environment["TMPDIR"] = temporary
    }
    return environment
}

/// The wall-clock cap on one child, by verb.
///
/// A dictation is minutes of speech and then a transcription; `clip` reads a
/// paragraph aloud and some paragraphs are long; everything else is a few
/// hundred milliseconds of Python. Past the cap the child is doing none of
/// those: it is wedged on a lock or on a device that never answered, and — on
/// the two serial queues — it holds every later press behind it for the life of
/// the app, which launchd keeps running for weeks (review round 3).
func timeoutSeconds(for arguments: [String]) -> Double {
    switch arguments.first {
    case "dictate": return 1200  // 20 minutes
    case "clip": return 1800  // 30 minutes
    // The portal is a server: it runs until Ctrl-C or fifteen idle minutes
    // (DEC-018), so the cap is a backstop, not a budget. Sixty seconds here
    // would kill the page a minute after the menu opened it.
    case "portal": return 4 * 3600
    default: return 60
    }
}

/// Run one `vocalize` verb.
///
/// The single door out of this app. Everything the design's spawn contract
/// names lives here: the fresh environment, the temporary working directory,
/// stdin and stdout on /dev/null, stderr on the capped log, `app.status`
/// rewritten on every dispatch, and the one re-resolve on ENOENT or 126/127
/// (an upgrade moved the binary out from under a path resolved minutes ago).
///
/// `completion` runs on `queue` and runs on *every* path, the failures
/// included: a caller that latches a flag until it hears back — `dictate` does
/// — must never be left holding it because the binary went missing for a few
/// seconds during an upgrade.
///
/// `captureStandardOutput` is used by exactly one caller, `vocalize settings`,
/// whose `key=value` lines the app has to read. Nothing that comes back that
/// way is ever shown to the user.
func run(
    _ arguments: [String],
    on queue: DispatchQueue,
    captureStandardOutput: Bool = false,
    completion: ((SpawnResult) -> Void)? = nil
) {
    queue.async {
        var resolved = checkedBinary()
        statusWriter.setBinaryPath(resolved)
        statusWriter.write()

        var attempt = 0
        while true {
            guard let binary = resolved else {
                notifier.show(.binaryMissing)
                completion?(SpawnResult(launched: false, status: -1, output: ""))
                return
            }
            let process = Process()
            process.executableURL = URL(fileURLWithPath: binary)
            process.arguments = arguments
            process.environment = childEnvironment()
            process.currentDirectoryURL = URL(fileURLWithPath: NSTemporaryDirectory())
            process.standardInput = FileHandle.nullDevice
            let pipe = captureStandardOutput ? Pipe() : nil
            process.standardOutput = pipe ?? FileHandle.nullDevice
            let errorHandle = logHandle()
            process.standardError = errorHandle ?? FileHandle.nullDevice

            do {
                try process.run()
            } catch {
                // ENOENT and friends: the path passed its stat check and then
                // stopped existing. Re-resolve once, then give up.
                try? errorHandle?.close()
                if attempt == 0 {
                    attempt += 1
                    resolved = checkedBinary()
                    statusWriter.setBinaryPath(resolved)
                    statusWriter.write()
                    continue
                }
                notifier.show(.binaryMissing)
                completion?(SpawnResult(launched: false, status: -1, output: ""))
                return
            }

            // SIGTERM first, not SIGKILL: `dictate` releases its session and
            // clears its workdir on a term, and a child killed outright leaves
            // the session file behind for the next press to trip over. Ten
            // seconds later SIGKILL, because a child blocked in the kernel or
            // ignoring the term would otherwise leave `waitUntilExit` — and the
            // serial queue behind it — stuck for the life of the app. The
            // completion below still runs on this path, so nothing latches.
            let capExpired = DispatchWorkItem {
                guard process.isRunning else { return }
                process.terminate()
                if let handle = logHandle() {
                    try? handle.write(
                        contentsOf: Data("vocalize-app: child timed out, terminated\n".utf8))
                    try? handle.close()
                }
                DispatchQueue.global().asyncAfter(deadline: .now() + 10) {
                    if process.isRunning { kill(process.processIdentifier, SIGKILL) }
                }
            }
            DispatchQueue.global().asyncAfter(
                deadline: .now() + timeoutSeconds(for: arguments), execute: capExpired)

            // Read before waiting: a child that fills the pipe buffer would
            // otherwise never exit. `settings` prints a few hundred bytes.
            var output = ""
            if let pipe {
                let data = pipe.fileHandleForReading.readDataToEndOfFile()
                output = String(data: data, encoding: .utf8) ?? ""
            }
            process.waitUntilExit()
            capExpired.cancel()
            let status = process.terminationStatus
            try? errorHandle?.close()

            // 126 (not executable) and 127 (not found) come from the exec side
            // of a shim, which is the same "it moved" story as ENOENT.
            if (status == 126 || status == 127) && attempt == 0 {
                attempt += 1
                resolved = checkedBinary()
                statusWriter.setBinaryPath(resolved)
                statusWriter.write()
                continue
            }
            if status == 126 || status == 127 {
                notifier.show(.binaryMissing)
            }
            completion?(SpawnResult(launched: true, status: status, output: output))
            return
        }
    }
}

// MARK: - The chord grammar
//
// Values arrive canonical from Python — lowercase, `ctrl alt cmd shift` order,
// aliases already resolved — but they arrive over a file the user edits, so the
// parser here assumes nothing. Anything it does not understand disables that
// one chord and is reported as `hotkeys: failed:<name>`; it never throws and
// never takes the other chords down with it.

/// The key names a chord may end in, and the virtual key code each one means.
///
/// Exactly the 48 names in `vocalize.config.CHORD_KEYS`; a Python test parses
/// this literal and asserts the two sets are equal, because a name Python
/// accepts and this table does not is a chord that silently never fires. Keep
/// it flat: the test reads from the first `[` to the first `]`.
let keyCodes: [String: UInt32] = [
    "a": UInt32(kVK_ANSI_A), "b": UInt32(kVK_ANSI_B), "c": UInt32(kVK_ANSI_C),
    "d": UInt32(kVK_ANSI_D), "e": UInt32(kVK_ANSI_E), "f": UInt32(kVK_ANSI_F),
    "g": UInt32(kVK_ANSI_G), "h": UInt32(kVK_ANSI_H), "i": UInt32(kVK_ANSI_I),
    "j": UInt32(kVK_ANSI_J), "k": UInt32(kVK_ANSI_K), "l": UInt32(kVK_ANSI_L),
    "m": UInt32(kVK_ANSI_M), "n": UInt32(kVK_ANSI_N), "o": UInt32(kVK_ANSI_O),
    "p": UInt32(kVK_ANSI_P), "q": UInt32(kVK_ANSI_Q), "r": UInt32(kVK_ANSI_R),
    "s": UInt32(kVK_ANSI_S), "t": UInt32(kVK_ANSI_T), "u": UInt32(kVK_ANSI_U),
    "v": UInt32(kVK_ANSI_V), "w": UInt32(kVK_ANSI_W), "x": UInt32(kVK_ANSI_X),
    "y": UInt32(kVK_ANSI_Y), "z": UInt32(kVK_ANSI_Z),
    "0": UInt32(kVK_ANSI_0), "1": UInt32(kVK_ANSI_1), "2": UInt32(kVK_ANSI_2),
    "3": UInt32(kVK_ANSI_3), "4": UInt32(kVK_ANSI_4), "5": UInt32(kVK_ANSI_5),
    "6": UInt32(kVK_ANSI_6), "7": UInt32(kVK_ANSI_7), "8": UInt32(kVK_ANSI_8),
    "9": UInt32(kVK_ANSI_9),
    "f1": UInt32(kVK_F1), "f2": UInt32(kVK_F2), "f3": UInt32(kVK_F3),
    "f4": UInt32(kVK_F4), "f5": UInt32(kVK_F5), "f6": UInt32(kVK_F6),
    "f7": UInt32(kVK_F7), "f8": UInt32(kVK_F8), "f9": UInt32(kVK_F9),
    "f10": UInt32(kVK_F10), "f11": UInt32(kVK_F11), "f12": UInt32(kVK_F12),
]

/// The four canonical modifier words, plus the three aliases Python accepts.
let modifierAliases: [String: String] = [
    "ctrl": "ctrl", "control": "ctrl",
    "alt": "alt", "option": "alt",
    "cmd": "cmd", "command": "cmd",
    "shift": "shift",
]

struct Chord {
    let modifiers: Set<String>
    let keyCode: UInt32

    /// Carbon's modifier mask.
    var carbonModifiers: UInt32 {
        var mask: UInt32 = 0
        if modifiers.contains("ctrl") { mask |= UInt32(controlKey) }
        if modifiers.contains("alt") { mask |= UInt32(optionKey) }
        if modifiers.contains("cmd") { mask |= UInt32(cmdKey) }
        if modifiers.contains("shift") { mask |= UInt32(shiftKey) }
        return mask
    }

    /// The same chord as `NSEvent` reports it.
    var eventModifiers: NSEvent.ModifierFlags {
        var flags: NSEvent.ModifierFlags = []
        if modifiers.contains("ctrl") { flags.insert(.control) }
        if modifiers.contains("alt") { flags.insert(.option) }
        if modifiers.contains("cmd") { flags.insert(.command) }
        if modifiers.contains("shift") { flags.insert(.shift) }
        return flags
    }
}

enum ChordParse {
    case disabled  // `""` — the documented way to turn one chord off
    case parsed(Chord)
    case invalid
}

/// `"ctrl+alt+cmd+d"` -> the chord. Tokens split on `+`; every token but the
/// last is a modifier; the last is a name from the key-code table above. A
/// chord must carry ctrl or cmd, because macOS refuses the rest with -9868 and
/// a chord that cannot register is worse than one that is honestly disabled.
func parseChord(_ text: String) -> ChordParse {
    let trimmed = text.trimmingCharacters(in: .whitespaces)
    if trimmed.isEmpty { return .disabled }
    let tokens = trimmed.lowercased().split(separator: "+", omittingEmptySubsequences: false)
        .map { String($0) }
    guard tokens.count >= 2 else { return .invalid }
    var modifiers = Set<String>()
    for token in tokens.dropLast() {
        guard let canonical = modifierAliases[token] else { return .invalid }
        guard !modifiers.contains(canonical) else { return .invalid }
        modifiers.insert(canonical)
    }
    guard let code = keyCodes[tokens[tokens.count - 1]] else { return .invalid }
    guard modifiers.contains("ctrl") || modifiers.contains("cmd") else { return .invalid }
    return .parsed(Chord(modifiers: modifiers, keyCode: code))
}

// MARK: - Settings

/// The three chords this build knows how to act on. The raw value is the Carbon
/// hot-key id and the key of `isDown`; `name` is what `app.status` reports. One
/// source of truth for all three, because a second table would be a second
/// thing to keep in step in a file nobody can edit later.
/// A chord name that is not one of these is not a chord at all — see
/// `SettingsReader.parse`.
enum Action: UInt32, CaseIterable {
    case dictate = 1
    case speak = 2
    case stop = 3

    var name: String {
        switch self {
        case .dictate: return "dictate"
        case .speak: return "speak"
        case .stop: return "stop"
        }
    }
}

struct Settings {
    var chords: [Action: Chord] = [:]
    var failed: [String] = []
    var holdToTalk = false
    var paste = false
}

/// Reads `vocalize settings` and keeps only the five lines this app acts on.
final class SettingsReader {
    /// Parse the `key=value` lines.
    ///
    /// Only `app.dictate`, `app.dictate_mode`, `app.speak`, `app.stop` and
    /// `stt.paste` are recognised. **Any other `app.*` key is ignored**, not
    /// treated as a chord: a later release adds `app.stop_hotkey` (0.13.1) or
    /// another key, and this app has to shrug at it, because noticing it would
    /// mean a new build and a new build means every user re-grants
    /// Accessibility. Same reason `stt.paste` being absent means false rather
    /// than an error — 0.13.0's CLI does not print it yet.
    func parse(_ text: String) -> Settings {
        var settings = Settings()
        for rawLine in text.split(separator: "\n", omittingEmptySubsequences: true) {
            let line = String(rawLine)
            guard let separator = line.firstIndex(of: "=") else { continue }
            let key = String(line[line.startIndex..<separator])
            let value = String(line[line.index(after: separator)...])
            let action: Action?
            switch key {
            case "app.dictate": action = .dictate
            case "app.speak": action = .speak
            case "app.stop": action = .stop
            case "app.dictate_mode":
                settings.holdToTalk = value == "hold"
                continue
            case "stt.paste":
                settings.paste = value == "true"
                continue
            default:
                continue  // every other key, `app.*` included, is not ours
            }
            guard let action else { continue }
            switch parseChord(value) {
            case .parsed(let chord): settings.chords[action] = chord
            case .disabled: break  // "" is off on purpose, not a failure
            case .invalid: settings.failed.append(action.name)
            }
        }
        return settings
    }

    /// Run the CLI and hand the parsed result back on the main thread. The flag
    /// is whether `vocalize settings` could be read at all: a settings read that
    /// failed registered no chords, and the caller has to say so rather than
    /// report `hotkeys: ok`.
    func reload(_ completion: @escaping (Settings, Bool) -> Void) {
        // The concurrent queue: re-reading settings spawns a Python process and
        // must not wait behind a `clip` that is still speaking.
        run(["settings"], on: stopQueue, captureStandardOutput: true) { result in
            let readable = result.launched && result.status == 0
            let settings = readable ? self.parse(result.output) : Settings()
            DispatchQueue.main.async { completion(settings, readable) }
        }
    }
}

// MARK: - Hotkeys

/// The registrar, with both backends compiled in.
///
/// `isDown` is what replaces the CLI's one-second debounce for presses that
/// come from this app: a key held down repeats, and a repeat is not a second
/// press. Key-up clears it — and a key-up whose key-down was never seen is
/// dropped, so a chord rebound mid-press cannot emit a `--stop` for a dictation
/// that never started.
final class Hotkeys {
    var onPress: ((Action) -> Void)?
    var onRelease: ((Action) -> Void)?

    private var carbonRefs: [EventHotKeyRef] = []
    private var carbonHandler: EventHandlerRef?
    private var monitor: Any?
    private var registered: [Action: Chord] = [:]
    private var isDown: [Action: Bool] = [:]

    /// Registers the given chords, replacing whatever was registered before.
    /// Returns the names that would not register, in the order they were tried,
    /// each with the `:taken` suffix when another app already owns the chord.
    /// Whether anything is registered. A settings read that failed must not be
    /// reported as `hotkeys: ok` if it left the app with no chords at all.
    var hasChords: Bool { !registered.isEmpty }

    func register(_ chords: [Action: Chord]) -> [String] {
        // A chord rebound while it was held would otherwise never deliver its
        // key-up, and in hold mode that key-up is what closes the microphone.
        // `dictate --stop` with no session exits 0, so a stop nobody needed
        // costs nothing; a stop that never arrives runs the recorder to
        // `max_seconds` with the microphone open (review round 2).
        let held = isDown.filter { $0.value }.map(\.key)
        unregisterAll()
        registered = chords
        isDown = [:]
        for action in held { onRelease?(action) }
        switch hotkeyBackend {
        case .carbon: return registerCarbon(chords)
        case .monitor: return registerMonitor(chords)
        }
    }

    func unregisterAll() {
        for reference in carbonRefs { UnregisterEventHotKey(reference) }
        carbonRefs = []
        if let monitor {
            NSEvent.removeMonitor(monitor)
            self.monitor = nil
        }
        registered = [:]
        isDown = [:]
    }

    // MARK: Carbon

    private func registerCarbon(_ chords: [Action: Chord]) -> [String] {
        let wanted = Action.allCases.filter { chords[$0] != nil }.map(\.name)
        // Registration succeeds whether or not a handler is listening, so a
        // failed handler would otherwise report `hotkeys: ok` for chords that
        // can never fire — the one diagnostic state worse than a plain failure.
        guard installCarbonHandler() == noErr else { return wanted }
        var failures: [String] = []
        for action in Action.allCases {
            guard let chord = chords[action] else { continue }
            var reference: EventHotKeyRef?
            let hotKeyID = EventHotKeyID(signature: OSType(0x7663_6C7A), id: action.rawValue)  // 'vclz'
            let status = RegisterEventHotKey(
                chord.keyCode, chord.carbonModifiers, hotKeyID,
                GetApplicationEventTarget(), 0, &reference)
            if status == noErr, let reference {
                carbonRefs.append(reference)
            } else if status == OSStatus(eventHotKeyExistsErr) {
                // Another app owns this chord — Hammerspoon, most likely, which
                // `app install` warns about. Named separately because it is the
                // one hotkey failure the user can actually fix.
                failures.append("\(action.name):taken")
            } else {
                failures.append(action.name)
            }
        }
        return failures
    }

    /// `noErr`, or the reason no key press will ever arrive. The handler is
    /// left nil on failure, so the next settings reload tries again.
    private func installCarbonHandler() -> OSStatus {
        guard carbonHandler == nil else { return noErr }
        var specs = [
            EventTypeSpec(
                eventClass: OSType(kEventClassKeyboard),
                eventKind: UInt32(kEventHotKeyPressed)),
            EventTypeSpec(
                eventClass: OSType(kEventClassKeyboard),
                eventKind: UInt32(kEventHotKeyReleased)),
        ]
        return InstallEventHandler(
            GetApplicationEventTarget(), carbonHotKeyCallback, specs.count, &specs, nil,
            &carbonHandler)
    }

    // MARK: Global event monitor

    private func registerMonitor(_ chords: [Action: Chord]) -> [String] {
        // The monitor sees nothing at all without the Accessibility grant, so
        // an ungranted app reports every chord as failed rather than pretending
        // to have registered them.
        let wanted = Action.allCases.filter { chords[$0] != nil }.map(\.name)
        guard AXIsProcessTrusted() else { return wanted }
        monitor = NSEvent.addGlobalMonitorForEvents(matching: [.keyDown, .keyUp]) {
            [weak self] event in
            self?.handleMonitorEvent(event)
        }
        return monitor == nil ? wanted : []
    }

    private func handleMonitorEvent(_ event: NSEvent) {
        // Only the four modifiers a chord can name: caps lock, fn and the
        // numeric-pad bit are not part of any chord and must not defeat a match.
        let interesting: NSEvent.ModifierFlags = [.command, .control, .option, .shift]
        let flags = event.modifierFlags.intersection(interesting)
        let up = event.type == .keyUp
        for (action, chord) in registered {
            guard UInt32(event.keyCode) == chord.keyCode else { continue }
            // A key-up is matched on the key alone. Nobody lifts the four keys
            // of a chord in the order they pressed them, and a release whose
            // modifiers went first is still this chord's release — in hold mode
            // it is the one that closes the microphone (review round 3). A
            // key-up for a key that was never down is dropped in `deliver`.
            guard up ? isDown[action] == true : flags == chord.eventModifiers else { continue }
            deliver(action, pressed: !up)
            return
        }
    }

    // MARK: Both

    /// Every hotkey edge, from either backend, on the main thread.
    func deliver(_ action: Action, pressed: Bool) {
        if pressed {
            // A held key repeats; only the first down is a press.
            if isDown[action] == true { return }
            isDown[action] = true
            onPress?(action)
        } else {
            // An unmatched key-up is not a release of anything.
            if isDown[action] != true { return }
            isDown[action] = false
            onRelease?(action)
        }
    }
}

/// The one live registrar, reachable from the Carbon callback below.
var hotkeys: Hotkeys?

/// Carbon's handler must be a plain C function: it is called on the main run
/// loop with no context of its own, so it captures nothing and reaches the
/// registrar through the global above rather than through `userData`.
func carbonHotKeyCallback(
    _ nextHandler: EventHandlerCallRef?,
    _ event: EventRef?,
    _ userData: UnsafeMutableRawPointer?
) -> OSStatus {
    guard let event else { return OSStatus(eventNotHandledErr) }
    var hotKeyID = EventHotKeyID()
    let status = GetEventParameter(
        event, EventParamName(kEventParamDirectObject), EventParamType(typeEventHotKeyID),
        nil, MemoryLayout<EventHotKeyID>.size, nil, &hotKeyID)
    guard status == noErr, let action = Action(rawValue: hotKeyID.id) else { return status }
    let pressed = GetEventKind(event) == UInt32(kEventHotKeyPressed)
    // Carbon already calls this on the main run loop; the hop makes that a
    // contract rather than a happy accident, because everything downstream
    // touches AppKit.
    DispatchQueue.main.async {
        hotkeys?.deliver(action, pressed: pressed)
    }
    return noErr
}

// MARK: - The status item

/// What the icon can say. Four states come from `dictate.session`; the fifth is
/// the honest "this file says something I was not built to understand".
enum IconState {
    case idle
    case starting
    case recording
    case transcribing
    case unrecognised
}

/// Read from an untrusted file, so every branch is explicit.
///
/// No session at all is idle. A session with no `state` key is a 0.12.0
/// dictation, which only ever wrote `{"dir", "started"}` — that is a live
/// recording, and showing it as anything else would be a lie during the one
/// release where both versions coexist. Anything else is `unrecognisedState`:
/// a word from a newer CLI, or a corrupt file, and it must never borrow the
/// recording icon, because "is my microphone open" is the one question this
/// icon exists to answer (DEC-036, owner question 3).
func iconState(forSessionState word: String?) -> IconState {
    guard let word else { return .recording }
    switch word {
    case "starting": return .starting
    case "recording": return .recording
    case "transcribing": return .transcribing
    default: return unrecognisedState(word)
    }
}

/// The named default branch: one place, one answer, no fallthrough to
/// `.recording`.
func unrecognisedState(_ word: String) -> IconState {
    _ = word  // deliberately unused: the word itself never reaches the user
    return .unrecognised
}

/// The menu-bar item. Created in `applicationDidFinishLaunching`, never before:
/// `NSStatusBar.system` is AppKit, and AppKit wants `NSApplication.shared` to
/// exist first — a status item built from a stored-property initialiser is
/// built during global initialisation, and the failure is a nil `button` and a
/// menu bar with nothing in it.
final class StatusItemController {
    private var item: NSStatusItem?
    /// What the icon is showing. The session is re-read on a timer as well as
    /// on a vnode event, so most reads say what the last one said; rebuilding
    /// the image for a state that has not moved is work nobody asked for.
    /// A notice flash restores this state when it ends.
    private var current: IconState = .idle
    private var flashWork: DispatchWorkItem?

    /// SF Symbols, one per state. The names are literals, never built from the
    /// session file.
    private func symbolName(_ state: IconState) -> String {
        switch state {
        case .idle: return "mic"
        case .starting: return "hourglass"
        case .recording: return "mic.fill"
        case .transcribing: return "waveform"
        case .unrecognised: return "exclamationmark.triangle"
        }
    }

    /// Fixed, and never the session's own words.
    private func label(_ state: IconState) -> String {
        switch state {
        case .idle: return "Vocalize"
        case .starting: return "Vocalize — starting"
        case .recording: return "Vocalize — recording"
        case .transcribing: return "Vocalize — transcribing"
        case .unrecognised: return "Vocalize — unknown state"
        }
    }

    func start(menu: NSMenu) {
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        item.menu = menu
        self.item = item
        render(.idle)
    }

    /// Main thread only: every caller is a watcher or the poll, and both hop.
    func show(_ state: IconState) {
        let moved = state != current
        current = state
        // A notice is on screen and owns the item until it ends; it draws
        // `current` itself when it does.
        guard moved, flashWork == nil else { return }
        render(state)
    }

    /// One notice, held for four seconds, in the item's own title — the whole
    /// of the app's user-visible vocabulary, see `Notifier`.
    func flash(_ notice: Notice) {
        guard let button = item?.button else { return }
        flashWork?.cancel()
        let image = NSImage(
            systemSymbolName: "exclamationmark.triangle", accessibilityDescription: notice.rawValue)
        image?.isTemplate = true
        button.image = image
        button.title = " " + notice.rawValue
        button.contentTintColor = nil
        button.toolTip = notice.rawValue
        let work = DispatchWorkItem { [weak self] in
            guard let self else { return }
            self.flashWork = nil
            self.render(self.current)
        }
        flashWork = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 4, execute: work)
    }

    private func render(_ state: IconState) {
        guard let button = item?.button else { return }
        let text = label(state)
        let image = NSImage(systemSymbolName: symbolName(state), accessibilityDescription: text)
        image?.isTemplate = true
        button.image = image
        // A symbol a future macOS renamed would otherwise leave an invisible
        // item in the menu bar; the label is one of the fixed strings above.
        button.title = image == nil ? text : ""
        // Red is the whole point of the recording state: it has to read as
        // "live" at a glance, in both menu-bar appearances.
        button.contentTintColor = state == .recording ? NSColor.systemRed : nil
        button.toolTip = text
    }
}

// MARK: - Watchers

/// A vnode source on a **directory**.
///
/// The directory, not the file, because both files this app watches are
/// replaced rather than rewritten: `config.toml` through `_write_config`'s
/// atomic replace, and `dictate.session` through a create-and-rename for every
/// state it passes through, then an unlink at the end. A source on the file
/// itself would go deaf the first time it changed.
///
/// What a directory source does *not* report is the reason the cache is polled
/// as well (see `AppDelegate.poll`): a write into a file that is already in the
/// directory is no directory event at all, so a session whose state is rewritten
/// in place — which is how the design's file table still describes it — would
/// never move the icon off whatever the first event showed. This watcher is the
/// fast path, not the only one.
final class DirectoryWatcher {
    private var source: DispatchSourceFileSystemObject?
    private let queue = DispatchQueue(label: "cards.arda.vocalize.app.watch")

    func start(path: String, onChange: @escaping () -> Void) {
        stop()
        ensureDirectory(path)
        let descriptor = open(path, O_EVTONLY)
        // A directory that cannot be opened or created — a home directory not
        // mounted yet at login, a parent someone chmodded — would otherwise
        // leave this watcher dead until the next login. Ask again every five
        // seconds instead; the poll covers the cache in the meantime and a
        // config edit made during the gap is picked up by the reload after it.
        guard descriptor >= 0 else {
            queue.asyncAfter(deadline: .now() + 5) { [weak self] in
                self?.start(path: path, onChange: onChange)
            }
            return
        }
        let source = DispatchSource.makeFileSystemObjectSource(
            fileDescriptor: descriptor,
            eventMask: [.write, .delete, .rename, .attrib, .extend],
            queue: queue)
        source.setEventHandler { [weak self] in
            // Read before the callback: `start` below replaces the source.
            let events = self?.source?.data ?? []
            onChange()
            // The directory itself went — `~/.cache/vocalize` is documented as
            // safe to delete, and a descriptor on an unlinked vnode never
            // speaks again. Re-open on the path rather than go deaf until the
            // next login.
            if events.contains(.delete) || events.contains(.rename) {
                self?.start(path: path, onChange: onChange)
            }
        }
        source.setCancelHandler { close(descriptor) }
        self.source = source
        source.resume()
    }

    func stop() {
        source?.cancel()
        source = nil
    }
}

/// What the app knows about the running dictation: the icon it should show, and
/// the nonce the paste marker has to match.
struct SessionReading {
    let state: IconState
    let nonce: String?
    /// The session's own `started` epoch, used to refuse a nonce from a session
    /// that predates the key press this app is holding a paste window open for.
    let started: Double?
}

/// Nobody's session or marker is anywhere near this big; a file that is, is not
/// one of ours.
let cacheReadCap = 65_536

/// Open one of the small cache files for reading, or nil.
///
/// Both files live in a directory any process running as this user can write,
/// and both are read on the main thread, so every way the path can be something
/// other than a small regular file is refused here rather than survived later:
/// `O_NOFOLLOW` refuses a symlink to somewhere else, `O_NONBLOCK` refuses to
/// block for ever on a FIFO planted in the file's place, the `S_IFREG` check
/// refuses a directory — whose read raises an Objective-C exception Swift
/// cannot catch, i.e. an abort — and the cap refuses a file planted to exhaust
/// memory. Callers read with `read(upToCount:)`, which throws rather than
/// raising, for the same reason.
func openCacheFile(_ path: String) -> FileHandle? {
    let descriptor = open(path, O_RDONLY | O_NOFOLLOW | O_NONBLOCK)
    guard descriptor >= 0 else { return nil }
    let handle = FileHandle(fileDescriptor: descriptor, closeOnDealloc: true)
    var info = stat()
    guard fstat(descriptor, &info) == 0,
        (info.st_mode & S_IFMT) == S_IFREG,
        info.st_size <= off_t(cacheReadCap)
    else { return nil }
    return handle
}

/// Reads `dictate.session`. Untrusted input: anything that is not the JSON
/// object this app expects — and anything that is not a live dictation — reads
/// as "no session".
/// Whether `path` could be one of `dictate`'s own workdirs: a `mkdtemp`
/// directory, named the way `dictate` names them, directly under a local
/// temporary root — the same rule `dictate._is_workdir` applies to the same
/// value. Checked before the file system is asked anything about it, because
/// `dir` comes out of a file any process running as this user can write and the
/// existence check below runs on the main thread: a path on an unresponsive
/// network mount would otherwise stat for ever and take every hotkey with it.
func isPlausibleWorkdir(_ path: String) -> Bool {
    let url = URL(fileURLWithPath: path)
    guard url.lastPathComponent.hasPrefix("vocalize-dictate-") else { return false }
    // `/tmp` for an ssh or launchd context, the per-user `/var/folders/…/T` for
    // everything else — `dictate._tmp_roots`, for the same reason it has them.
    let roots = ["/tmp", "/private/tmp", NSTemporaryDirectory()]
    let parent = (url.deletingLastPathComponent().path as NSString).standardizingPath
    return roots.contains { ($0 as NSString).standardizingPath == parent }
}

func readSession() -> SessionReading {
    let none = SessionReading(state: .idle, nonce: nil, started: nil)
    guard let handle = openCacheFile(sessionPath) else { return none }
    guard let data = (try? handle.read(upToCount: cacheReadCap)) ?? nil,
        let parsed = try? JSONSerialization.jsonObject(with: data),
        let object = parsed as? [String: Any]
    else {
        // `_claim_session` creates this file with `O_EXCL` and writes the JSON
        // a moment later, so a file that does not parse *yet* is a dictation
        // starting, not a corrupt one — and idle here would be a microphone
        // about to open that the icon says nothing about. Only for the first
        // second of the file's life, and only forwards: after that it is a
        // press that died before it wrote, which `dictate` clears and this app
        // shows as idle (DEC-011). The poll reads it again once the words are
        // there, which is when the nonce and the real state are adopted.
        var info = stat()
        guard fstat(handle.fileDescriptor, &info) == 0 else { return none }
        let age = Date().timeIntervalSince1970 - Double(info.st_mtimespec.tv_sec)
        guard age > -1.0, age < 1.0 else { return none }
        return SessionReading(state: .starting, nonce: nil, started: nil)
    }
    // A session whose workdir is gone is a dictation that died — killed, or
    // slept through. `dictate` reads its own file the same way (`_is_workdir`),
    // and without this check the icon would report an open microphone for ever:
    // no later vnode event corrects a stale file, because every read of it says
    // the same thing.
    guard let workdir = object["dir"] as? String,
        isPlausibleWorkdir(workdir),
        FileManager.default.fileExists(atPath: workdir)
    else {
        return none
    }
    let word = object["state"] as? String
    let nonce = object["nonce"] as? String
    let started = object["started"] as? Double
    return SessionReading(
        state: iconState(forSessionState: word), nonce: nonce, started: started)
}

// MARK: - Synthetic keystrokes
//
// The two the Accessibility grant is for. Both are chord presses with no text
// in them: the app types Command-C and Command-V and nothing else, ever.

/// Posted at the *session* tap, not the HID tap: an event injected at the HID
/// point is re-stamped with whatever the hardware modifiers are at that moment,
/// and the hotkey that asked for this is a chord the user is still holding —
/// `ctrl+alt+cmd+s` would arrive as ctrl-option-Command-C and copy nothing. At
/// the annotated session tap the event's own flags are the ones that count.
func postKeystroke(virtualKey: CGKeyCode) {
    let source = CGEventSource(stateID: .combinedSessionState)
    guard let down = CGEvent(keyboardEventSource: source, virtualKey: virtualKey, keyDown: true),
        let up = CGEvent(keyboardEventSource: source, virtualKey: virtualKey, keyDown: false)
    else { return }
    down.flags = .maskCommand
    up.flags = .maskCommand
    down.post(tap: .cgAnnotatedSessionEventTap)
    up.post(tap: .cgAnnotatedSessionEventTap)
}

/// Belt to the session tap's braces: wait for the user's own modifiers to lift
/// before synthesising a chord, because an application that reads the hardware
/// state itself still sees them held. Polled on the main queue every 20 ms, the
/// same hop `waitForCopy` uses, and never for longer than 300 ms — a user who
/// leans on the key gets the keystroke anyway rather than silence.
func whenModifiersClear(attempt: Int = 0, _ body: @escaping () -> Void) {
    let held: CGEventFlags = [.maskCommand, .maskControl, .maskAlternate, .maskShift]
    let flags = CGEventSource.flagsState(.combinedSessionState)
    guard !flags.intersection(held).isEmpty, attempt < 15 else {  // 15 × 20 ms
        body()
        return
    }
    DispatchQueue.main.asyncAfter(deadline: .now() + 0.02) {
        whenModifiersClear(attempt: attempt + 1, body)
    }
}

/// Post Command-V, and nothing else: the app never learns what it pasted.
func postPaste() {
    whenModifiersClear { postKeystroke(virtualKey: CGKeyCode(kVK_ANSI_V)) }
}

// MARK: - The app

final class AppDelegate: NSObject, NSApplicationDelegate {
    private let statusItem = StatusItemController()
    private let settingsReader = SettingsReader()
    private let configWatcher = DirectoryWatcher()
    private let cacheWatcher = DirectoryWatcher()
    private let registrar = Hotkeys()

    private var settings = Settings()
    /// The nonce of the dictation this app watched start. Kept after the
    /// session file goes away, because `dictate.copied` is written around the
    /// same moment the session is released and the two orders race; cleared the
    /// moment it buys a paste.
    private var watchedNonce: String?
    /// The application that was frontmost when the dictate key went down. The
    /// transcript is only pasted back into that same window.
    private var dictateFrontmostBundleID: String?
    /// A dictate child is running: a second key-down is dropped, not queued.
    private var dictateChildRunning = false
    /// Auto-paste is armed by a dictate press of this app's own and disarmed
    /// once the dictation is over. A nonce is adopted only inside the arming
    /// window, so a session file planted at any other moment teaches this app
    /// nothing; the window and the recorded frontmost app are both dropped a
    /// few seconds after the session goes away, so a marker planted later has
    /// nothing left to match (review R6).
    private var pasteArmedUntil: Date?
    /// When that window opened. A session that started before the press is not
    /// the press's session, whoever wrote it.
    private var pasteArmedAt: Date?
    private var disarmPaste: DispatchWorkItem?
    /// How many times a failed `vocalize settings` has been retried.
    private var settingsRetries = 0
    /// The session poll — see `DirectoryWatcher`: a directory source says
    /// nothing about a file rewritten in place, and the red recording icon is
    /// the one answer this app cannot afford to get wrong in a source that is
    /// frozen after 0.13.0 (review round 2). Half a second is one small read
    /// and, when the state has not moved, no drawing at all.
    private var poll: Timer?
    /// The settings reload a config change is waiting on. One atomic replace of
    /// `config.toml` is several directory events, and each one unanswered would
    /// be a Python interpreter.
    private var pendingReload: DispatchWorkItem?
    /// The same, for the cache directory this app writes into itself.
    private var pendingCacheRead: DispatchWorkItem?

    func applicationDidFinishLaunching(_ notification: Notification) {
        ensureDirectory(cacheDirectory)
        ensureDirectory(configDirectory)
        notifier.statusItem = statusItem
        hotkeys = registrar
        registrar.onPress = { [weak self] action in self?.press(action) }
        registrar.onRelease = { [weak self] action in self?.release(action) }
        statusItem.start(menu: buildMenu())
        statusWriter.setBinaryPath(checkedBinary())
        statusWriter.write()

        configWatcher.start(path: configDirectory) { [weak self] in
            DispatchQueue.main.async { self?.scheduleReload() }
        }
        cacheWatcher.start(path: cacheDirectory) { [weak self] in
            DispatchQueue.main.async { self?.scheduleCacheRead() }
        }
        // `.common`, so a poll is not held up for as long as the user leaves
        // the menu open.
        let timer = Timer.scheduledTimer(withTimeInterval: 0.5, repeats: true) { [weak self] _ in
            self?.cacheChanged()
        }
        RunLoop.main.add(timer, forMode: .common)
        poll = timer
        reloadSettings()
        cacheChanged()
    }

    func applicationWillTerminate(_ notification: Notification) {
        registrar.unregisterAll()
        configWatcher.stop()
        cacheWatcher.stop()
        poll?.invalidate()
        poll = nil
    }

    // MARK: Menu

    private func buildMenu() -> NSMenu {
        let menu = NSMenu()
        let items: [(String, Selector)] = [
            ("Dictate", #selector(menuDictate)),
            ("Speak selection", #selector(menuSpeak)),
            ("Stop", #selector(menuStop)),
            ("Cancel dictation", #selector(menuCancel)),
            ("Open portal", #selector(menuPortal)),
            ("Reload settings", #selector(menuReload)),
        ]
        for (title, action) in items {
            let item = NSMenuItem(title: title, action: action, keyEquivalent: "")
            item.target = self
            menu.addItem(item)
        }
        menu.addItem(NSMenuItem.separator())
        // Quit stays quit: the LaunchAgent's KeepAlive is
        // {SuccessfulExit: false}, so a clean exit is not restarted.
        let quit = NSMenuItem(title: "Quit", action: #selector(menuQuit), keyEquivalent: "q")
        quit.target = self
        menu.addItem(quit)
        return menu
    }

    /// A menu click is always the toggle press, even in hold mode — there is no
    /// way to hold a menu item down, and a `--start` with no possible `--stop`
    /// would leave the microphone open.
    @objc private func menuDictate() { startDictation() }
    @objc private func menuSpeak() { speakSelection() }
    @objc private func menuStop() { run(["stop"], on: stopQueue) }
    @objc private func menuCancel() { run(["listen", "--cancel"], on: stopQueue) }
    @objc private func menuPortal() { run(["portal"], on: stopQueue) }
    @objc private func menuReload() {
        settingsRetries = 0  // a person asking is a fresh start, not a retry
        reloadSettings()
    }
    @objc private func menuQuit() { NSApplication.shared.terminate(nil) }

    // MARK: Settings

    /// Coalesce the burst. A save of `config.toml` is a create, a rename and an
    /// attribute change, and every one of them would otherwise be its own
    /// `vocalize settings` — three interpreters for one edit, and as many as a
    /// process touching that directory in a loop cares to ask for. The last
    /// event in a quarter-second wins; the menu's Reload calls
    /// `reloadSettings` directly, because a person waiting on a click should
    /// not.
    private func scheduleReload() {
        settingsRetries = 0  // the config changed: worth the full ladder again
        pendingReload?.cancel()
        let work = DispatchWorkItem { [weak self] in self?.reloadSettings() }
        pendingReload = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.25, execute: work)
    }

    private func reloadSettings() {
        settingsReader.reload { [weak self] settings, readable in
            guard let self else { return }
            guard readable else {
                // A settings read fails for a few seconds at a time — launchd
                // starts this app before `~/.local/bin` is populated, or
                // `uv tool install --reinstall` replaces the binary mid-read.
                // Registering an empty set there would leave a menu-bar icon
                // no key press could revive, and the only way back would be a
                // menu click. Keep what is registered and ask again.
                // `settings`, not a chord name: no chord failed, the read did,
                // and naming one would send the user to fix a chord that is
                // spelled correctly.
                if !self.registrar.hasChords { statusWriter.setFirstFailure("settings") }
                statusWriter.setBinaryPath(checkedBinary())
                statusWriter.write()
                self.scheduleSettingsRetry()
                return
            }
            self.settingsRetries = 0
            // Unregister and re-register on every change: a chord that moved
            // has to stop firing at its old place in the same breath. Ordered
            // before `self.settings` is replaced, so a key still held is
            // released under the mode it was pressed in.
            var failures = self.registrar.register(settings.chords)
            self.settings = settings
            failures.append(contentsOf: settings.failed)
            statusWriter.setFirstFailure(failures.first)
            statusWriter.setBinaryPath(checkedBinary())
            statusWriter.write()
        }
    }

    /// Three tries, then stop: a binary that is still unreadable two minutes
    /// later is not coming back on its own, and a retry every few seconds for
    /// ever is a Python interpreter every few seconds for ever. A config edit
    /// or the menu's Reload starts the count again.
    private func scheduleSettingsRetry() {
        let delays: [Double] = [5, 30, 120]
        guard settingsRetries < delays.count else { return }
        let delay = delays[settingsRetries]
        settingsRetries += 1
        DispatchQueue.main.asyncAfter(deadline: .now() + delay) { [weak self] in
            self?.reloadSettings()
        }
    }

    // MARK: The dispatch table

    private func press(_ action: Action) {
        switch action {
        case .dictate:
            guard settings.holdToTalk else {
                startDictation()
                return
            }
            // Hold mode: the key-down opens the microphone and the key-up
            // closes it. Armed before the spawn, so the paste — if there is
            // one — goes back to the window the words were spoken into.
            armPaste()
            run(["dictate", "--start"], on: dictateQueue)
        case .speak:
            speakSelection()
        case .stop:
            run(["stop"], on: stopQueue)  // concurrent: never behind a transcription
        }
    }

    private func release(_ action: Action) {
        // Only dictate cares about key-up, and only in hold mode.
        guard action == .dictate, settings.holdToTalk else { return }
        run(["dictate", "--stop"], on: dictateQueue)
    }

    /// Toggle mode, and every menu click. A press while a child is still
    /// running is dropped, not queued: the second press of a toggle is the
    /// CLI's own business, and a queued third press would land after the
    /// transcription and start a recording nobody asked for.
    private func startDictation() {
        guard !dictateChildRunning else { return }
        dictateChildRunning = true
        armPaste()
        // The completion clears the flag on every path the child can exit by,
        // and `run`'s wall-clock cap — SIGTERM, then SIGKILL — guarantees the
        // child exits, so the flag cannot latch for the life of the app. No
        // separate watchdog here: one that fired before the cap would let
        // presses through while the serial queue was still blocked, and they
        // would all land at once when the child finally died.
        run(["dictate"], on: dictateQueue) { [weak self] _ in
            DispatchQueue.main.async { self?.dictateChildRunning = false }
        }
    }

    // MARK: Speak the selection

    /// Copy the selection with a synthetic Command-C, then hand the clipboard
    /// to `vocalize clip`.
    ///
    /// The app never reads the pasteboard. It watches `changeCount`, which is a
    /// number, and lets the CLI read the words — so no selected text can reach
    /// this process, its log, or a notification.
    private func speakSelection() {
        let options =
            [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
        guard AXIsProcessTrustedWithOptions(options) else {
            // The call above put the one system prompt on screen; the notice
            // says why it appeared.
            notifier.show(.accessibilityNeeded)
            return
        }
        // The chord that got here is still held, and the count has to be taken
        // immediately before the copy, not before the wait.
        whenModifiersClear { [weak self] in
            let before = NSPasteboard.general.changeCount
            postKeystroke(virtualKey: CGKeyCode(kVK_ANSI_C))
            self?.waitForCopy(before: before, attempt: 0)
        }
    }

    /// Poll `changeCount` every 20 ms for up to 500 ms. On the main queue, but
    /// never blocking it: each poll is its own delayed hop.
    private func waitForCopy(before: Int, attempt: Int) {
        if NSPasteboard.general.changeCount != before {
            run(["clip"], on: speakQueue) { result in
                guard result.launched, result.status != 0 else { return }
                // Fixed strings only. `clip` exits 3 when the clipboard looks
                // like a credential and it refused to speak it.
                notifier.show(result.status == 3 ? .clipCredential : .clipFailed)
            }
            return
        }
        guard attempt < 25 else {  // 25 × 20 ms = 500 ms
            // Nothing copied means nothing was selected. Command-C reaching a
            // window that ignores it looks exactly the same, and says the same
            // thing to the user.
            notifier.show(.nothingSelected)
            return
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.02) { [weak self] in
            self?.waitForCopy(before: before, attempt: attempt + 1)
        }
    }

    // MARK: The cache watcher

    /// Coalesce the burst, the way `scheduleReload` does for the config. This
    /// app writes `app.status` into the directory it is watching on every
    /// dispatch, and a temporary-file-and-rename is several events; the poll
    /// answers the same question twice a second anyway, so fifty milliseconds
    /// of waiting costs nothing anyone can see (review round 3).
    private func scheduleCacheRead() {
        pendingCacheRead?.cancel()
        let work = DispatchWorkItem { [weak self] in self?.cacheChanged() }
        pendingCacheRead = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.05, execute: work)
    }

    private func cacheChanged() {
        let reading = readSession()
        statusItem.show(reading.state)
        // The nonce is adopted only inside the window a dictate press of this
        // app's own opened, and only from a session that started after that
        // press. Both files sit in a directory any process running as this user
        // can write, so this gate bounds a planted paste to a window a real key
        // press opened — with the frontmost-application and freshness checks in
        // `pasteIfNonceMatches` — rather than eliminating one.
        if let nonce = reading.nonce, let until = pasteArmedUntil, Date() < until,
            let started = reading.started, let armed = pasteArmedAt,
            started >= armed.timeIntervalSince1970 - 1.0 {  // a second of clock slack
            watchedNonce = nonce
            pasteArmedUntil = nil  // one press, one nonce
        }
        if reading.state == .idle {
            scheduleDisarm()
        } else {
            disarmPaste?.cancel()
            disarmPaste = nil
        }
        if FileManager.default.fileExists(atPath: copiedMarkerPath) {
            pasteIfNonceMatches()
        }
    }

    /// The dictate key-down: the window in which a session file may claim to be
    /// this app's dictation, and the window the transcript may be pasted back
    /// into. The session appears within a second of the press; ten is slack for
    /// a cold start, not a promise.
    private func armPaste() {
        dictateFrontmostBundleID = NSWorkspace.shared.frontmostApplication?.bundleIdentifier
        pasteArmedAt = Date()
        pasteArmedUntil = Date().addingTimeInterval(10)
        disarmPaste?.cancel()
        disarmPaste = nil
    }

    /// The session is gone, so the dictation is over. The marker is written
    /// around the same moment and the two orders race, so the nonce and the
    /// frontmost application are held for a few seconds more and then dropped —
    /// a gate left armed is a paste some later marker can still buy.
    private func scheduleDisarm() {
        guard disarmPaste == nil else { return }
        // The session file is not there yet: an idle reading a moment after the
        // press is the dictation not having started, not it having ended, and
        // disarming on it would spend the arm window before the nonce exists.
        // The half-second poll comes back here once the window has really run
        // out, so nothing is left armed.
        if let until = pasteArmedUntil, Date() < until { return }
        let work = DispatchWorkItem { [weak self] in
            guard let self else { return }
            self.watchedNonce = nil
            self.dictateFrontmostBundleID = nil
            self.pasteArmedUntil = nil
            self.pasteArmedAt = nil
            self.disarmPaste = nil
        }
        disarmPaste = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 5, execute: work)
    }

    /// The auto-paste gate.
    ///
    /// The marker is unlinked first, through a descriptor opened before the
    /// unlink, so it is gone before anything is decided and cannot be swapped
    /// underneath the checks. Then four things must all hold: paste is on, the
    /// marker carries the nonce of the dictation *this* app watched start, the
    /// same application is still frontmost, and the marker is fresh. The nonce
    /// is not a secret this app holds alone — it is read from a session file in
    /// the same this-user-writable directory as the marker — so the honest
    /// claim is narrower than "a planted marker pastes nothing": a planted
    /// marker can only paste inside a window a real dictate press of this app's
    /// own opened, into the application that press was made in, within two
    /// seconds of the transcript landing. `[stt] paste` is off by default for
    /// the residue (review R6, round 2).
    private func pasteIfNonceMatches() {
        // Checked and opened before the unlink, so what is read is what was
        // removed and nothing can be swapped in underneath the checks.
        guard let handle = openCacheFile(copiedMarkerPath) else {
            unlink(copiedMarkerPath)  // not a marker of ours; do not read it again
            return
        }
        unlink(copiedMarkerPath)
        let data = (try? handle.read(upToCount: cacheReadCap)) ?? nil

        // Paste off is the default and says nothing: the transcript is on the
        // clipboard, which is exactly what the user asked for.
        guard settings.paste else { return }

        guard let data,
            let parsed = try? JSONSerialization.jsonObject(with: data),
            let object = parsed as? [String: Any],
            let nonce = object["nonce"] as? String,
            let epoch = object["epoch"] as? Double
        else {
            notifier.show(.notPasted)
            return
        }
        guard let expected = watchedNonce, nonce == expected else {
            notifier.show(.notPasted)
            return
        }
        // Under two seconds old, and not dated in the future: a marker with a
        // forward clock must not buy itself an open-ended paste window.
        let age = Date().timeIntervalSince1970 - epoch
        guard age >= -1.0, age < 2.0 else {
            notifier.show(.notPasted)
            return
        }
        let frontmost = NSWorkspace.shared.frontmostApplication?.bundleIdentifier
        guard let started = dictateFrontmostBundleID, frontmost == started else {
            notifier.show(.notPasted)
            return
        }
        // One marker, one paste: the gate closes before the keystroke, not
        // after it.
        watchedNonce = nil
        dictateFrontmostBundleID = nil
        postPaste()
    }
}

// MARK: - main

let delegate = AppDelegate()
let application = NSApplication.shared
// Accessory, not regular: no Dock tile, no menu bar of its own, never steals
// focus from the window the user is typing in. `LSUIElement` in Info.plist says
// the same thing to LaunchServices; this says it to the running process.
application.setActivationPolicy(.accessory)
application.delegate = delegate
application.run()
