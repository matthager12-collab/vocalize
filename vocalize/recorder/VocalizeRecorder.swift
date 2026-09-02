// VocalizeRecorder — the only part of vocalize that touches a microphone.
//
// It exists because macOS grants the microphone to a *bundle* with a usage
// string, not to a script: a Quick Action runs under WorkflowServiceRunner,
// which has no usage string and can never be granted. `local install --stt`
// compiles this file into "Vocalize Recorder.app" and ad-hoc signs it, so the
// grant belongs to a stable, named identity the user can see in
// System Settings > Privacy & Security > Microphone.
//
// Contract (docs/plans/2026-09-next-features/design.md, "Recorder contract"):
//
//   recorder --out PATH --stop PATH --max SECONDS [--device NAME]
//   recorder --check [--device NAME] [--status-file PATH]
//   recorder --list-devices
//
// Output is always 16 kHz mono 16-bit LPCM WAV — whisper.cpp's native input,
// and a format Python's stdlib `wave` can open.
//
// Exit codes, identical in both modes:
//   0  recorded, or authorized
//   1  an internal failure (bad arguments, I/O, the audio engine)
//   2  authorization denied or restricted
//   3  no input device, or --device named one that does not exist
//   4  --max seconds reached (the WAV is still valid)
//   5  authorization not yet determined (--check only)
//
// Two things this file must never do: print or log any audio (only device
// names and status words ever reach stdout), and request permission in
// --check mode (--check reports, it does not prompt).

import AVFoundation
import CoreAudio
import Foundation

let exitOK: Int32 = 0
let exitFailure: Int32 = 1
let exitDenied: Int32 = 2
let exitNoDevice: Int32 = 3
let exitMaxSeconds: Int32 = 4
let exitNotDetermined: Int32 = 5

// The recorder's own PID file, removed on every exit path below — including a
// signal, which is how `dictate` enforces --max as a backstop. `dictate` reads
// the file to tell "still recording" from "died before it started", and never
// signals the PID without first checking the process name.
//
// Held as a C string because the signal handler may only call things that are
// async-signal-safe: `unlink` and `_exit` are, anything touching a Swift String
// is not.
var pidFileCPath: UnsafeMutablePointer<CChar>?

func removePidFile() {
    if let path = pidFileCPath {
        pidFileCPath = nil  // cleared first: the handler must never see a stale pointer
        unlink(path)
    }
}

/// SIGTERM/SIGINT/SIGHUP. Without this the contract's "removes it on exit"
/// would be false exactly when it matters — a recorder killed for overrunning
/// --max would leave rec.pid behind for the next dictation to misread.
func onSignal(_ signalNumber: Int32) {
    if let path = pidFileCPath { unlink(path) }
    _exit(128 + signalNumber)
}

func installSignalHandlers() {
    signal(SIGTERM, onSignal)
    signal(SIGINT, onSignal)
    signal(SIGHUP, onSignal)
}

func note(_ message: String) {
    FileHandle.standardError.write(Data((message + "\n").utf8))
}

func finish(_ code: Int32) -> Never {
    removePidFile()
    exit(code)
}

func fail(_ message: String, _ code: Int32) -> Never {
    note(message)
    finish(code)
}

// MARK: - Devices
//
// AVAudioRecorder cannot be pointed at an input device, so a named device is
// recorded through AVAudioEngine instead. Both paths need CoreAudio to answer
// "which devices can record, and what are they called" — and those names are
// exactly what --list-devices prints, so --device only ever takes a string the
// user has already seen from this same enumeration.

struct InputDevice {
    let id: AudioDeviceID
    let name: String
}

func deviceName(_ id: AudioDeviceID) -> String? {
    var address = AudioObjectPropertyAddress(
        mSelector: kAudioObjectPropertyName,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain)
    var name: CFString = "" as CFString
    var size = UInt32(MemoryLayout<CFString>.size)
    let status = withUnsafeMutablePointer(to: &name) {
        AudioObjectGetPropertyData(id, &address, 0, nil, &size, $0)
    }
    guard status == noErr else { return nil }
    return name as String
}

func hasInputStream(_ id: AudioDeviceID) -> Bool {
    var address = AudioObjectPropertyAddress(
        mSelector: kAudioDevicePropertyStreamConfiguration,
        mScope: kAudioObjectPropertyScopeInput,
        mElement: kAudioObjectPropertyElementMain)
    var size: UInt32 = 0
    guard AudioObjectGetPropertyDataSize(id, &address, 0, nil, &size) == noErr, size > 0 else {
        return false
    }
    let raw = UnsafeMutableRawPointer.allocate(
        byteCount: Int(size), alignment: MemoryLayout<AudioBufferList>.alignment)
    defer { raw.deallocate() }
    guard AudioObjectGetPropertyData(id, &address, 0, nil, &size, raw) == noErr else { return false }
    let buffers = UnsafeMutableAudioBufferListPointer(
        raw.assumingMemoryBound(to: AudioBufferList.self))
    for buffer in buffers where buffer.mNumberChannels > 0 { return true }
    return false
}

func inputDevices() -> [InputDevice] {
    var address = AudioObjectPropertyAddress(
        mSelector: kAudioHardwarePropertyDevices,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain)
    var size: UInt32 = 0
    let system = AudioObjectID(kAudioObjectSystemObject)
    guard AudioObjectGetPropertyDataSize(system, &address, 0, nil, &size) == noErr else { return [] }
    let count = Int(size) / MemoryLayout<AudioDeviceID>.size
    guard count > 0 else { return [] }
    var ids = [AudioDeviceID](repeating: 0, count: count)
    guard AudioObjectGetPropertyData(system, &address, 0, nil, &size, &ids) == noErr else {
        return []
    }
    return ids.compactMap { id in
        guard hasInputStream(id), let name = deviceName(id) else { return nil }
        return InputDevice(id: id, name: name)
    }
}

func defaultInputDevice() -> InputDevice? {
    var address = AudioObjectPropertyAddress(
        mSelector: kAudioHardwarePropertyDefaultInputDevice,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain)
    var id = AudioDeviceID(0)
    var size = UInt32(MemoryLayout<AudioDeviceID>.size)
    let status = AudioObjectGetPropertyData(
        AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size, &id)
    guard status == noErr, id != AudioDeviceID(0), hasInputStream(id) else { return nil }
    guard let name = deviceName(id) else { return nil }
    return InputDevice(id: id, name: name)
}

/// The device a run would use: the exact `--device` name, or the system default.
///
/// An empty name means the system default — `[stt] input_device = ""` is the
/// documented way to say "whatever macOS is using", and it reaches --device as
/// an empty string rather than as an absent flag.
func resolveDevice(_ wanted: String?) -> InputDevice? {
    guard let wanted, !wanted.isEmpty else { return defaultInputDevice() }
    return inputDevices().first { $0.name == wanted }
}

// MARK: - Written-format assertion
//
// The spike caught AVCaptureAudioFileOutput writing 48 kHz float WAVE-extensible
// while claiming to honour 16 kHz Int16 settings, which Python's `wave` refuses
// to open. So the file is re-opened and checked every time, whichever path
// wrote it, and anything else goes through afconvert before the recorder exits.

func writtenFormatIsCorrect(_ url: URL) -> Bool {
    guard let file = try? AVAudioFile(forReading: url) else { return false }
    let format = file.fileFormat.streamDescription.pointee
    return format.mFormatID == kAudioFormatLinearPCM
        && Int(format.mSampleRate.rounded()) == 16000
        && format.mChannelsPerFrame == 1
        && format.mBitsPerChannel == 16
        && (format.mFormatFlags & kAudioFormatFlagIsFloat) == 0
}

func convertToContract(_ url: URL) -> Bool {
    // Beside the output, which is inside the caller's own 0700 directory —
    // never a shared /tmp path another user could pre-create.
    let temporary = url.deletingLastPathComponent()
        .appendingPathComponent("convert-\(getpid()).wav")
    let process = Process()
    process.executableURL = URL(fileURLWithPath: "/usr/bin/afconvert")
    process.arguments = [
        "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", url.path, temporary.path,
    ]
    process.standardOutput = FileHandle.nullDevice
    process.standardError = FileHandle.nullDevice
    do {
        try process.run()
    } catch {
        return false
    }
    process.waitUntilExit()
    guard process.terminationStatus == 0 else {
        try? FileManager.default.removeItem(at: temporary)
        return false
    }
    do {
        _ = try FileManager.default.replaceItemAt(url, withItemAt: temporary)
    } catch {
        try? FileManager.default.removeItem(at: temporary)
        return false
    }
    return writtenFormatIsCorrect(url)
}

// MARK: - Recording

/// The 16 kHz mono 16-bit LPCM settings, in one place for both paths.
let contractSettings: [String: Any] = [
    AVFormatIDKey: Int(kAudioFormatLinearPCM),
    AVSampleRateKey: 16000.0,
    AVNumberOfChannelsKey: 1,
    AVLinearPCMBitDepthKey: 16,
    AVLinearPCMIsFloatKey: false,
    AVLinearPCMIsBigEndianKey: false,
]

/// Poll for the stop file. Returns true when `--max` ran out first.
func waitForStop(stopPath: String, maxSeconds: Double) -> Bool {
    let deadline = Date().addingTimeInterval(maxSeconds)
    let manager = FileManager.default
    while true {
        if manager.fileExists(atPath: stopPath) { return false }
        if Date() >= deadline { return true }
        Thread.sleep(forTimeInterval: 0.1)
    }
}

/// Records the system default input. AVAudioRecorder honours the settings
/// above (the spike checked), and finalises the WAV header on stop().
func recordWithAudioRecorder(to url: URL, stopPath: String, maxSeconds: Double) -> Bool {
    let recorder: AVAudioRecorder
    do {
        recorder = try AVAudioRecorder(url: url, settings: contractSettings)
    } catch {
        fail("could not start recording: \(error.localizedDescription)", exitFailure)
    }
    guard recorder.record() else { fail("the microphone would not start", exitFailure) }
    let hitMax = waitForStop(stopPath: stopPath, maxSeconds: maxSeconds)
    recorder.stop()
    return hitMax
}

/// Records one named device. AVAudioRecorder cannot choose an input, so the
/// device is set on the input node's audio unit and the tap's buffers are
/// converted into the same 16 kHz mono Int16 file.
final class DeviceRecorder {
    private let engine = AVAudioEngine()
    private var file: AVAudioFile?
    // `removeTap` does not promise an in-flight tap callback has returned, so
    // the audio thread's read of `file` and this thread's store of nil are two
    // threads touching one strong reference. Serialize them.
    private let lock = NSLock()

    func record(device: InputDevice, to url: URL, stopPath: String, maxSeconds: Double) -> Bool {
        let input = engine.inputNode
        guard let unit = input.audioUnit else {
            fail("the audio engine has no input unit", exitFailure)
        }
        var deviceID = device.id
        let status = AudioUnitSetProperty(
            unit, kAudioOutputUnitProperty_CurrentDevice, kAudioUnitScope_Global, 0,
            &deviceID, UInt32(MemoryLayout<AudioDeviceID>.size))
        guard status == noErr else {
            fail("could not select the input device \(device.name)", exitNoDevice)
        }

        let inputFormat = input.inputFormat(forBus: 0)
        guard inputFormat.sampleRate > 0, inputFormat.channelCount > 0 else {
            fail("the input device \(device.name) offers no usable format", exitNoDevice)
        }

        let written: AVAudioFile
        do {
            written = try AVAudioFile(forWriting: url, settings: contractSettings)
        } catch {
            fail("could not open the output file: \(error.localizedDescription)", exitFailure)
        }
        file = written
        // AVAudioFile.write(from:) takes buffers in the file's *processing*
        // format (deinterleaved float at the file's rate), and converts them to
        // the Int16 settings above on the way to disk — so that, not the raw
        // Int16 layout, is what the converter has to produce.
        guard let converter = AVAudioConverter(from: inputFormat, to: written.processingFormat) else {
            fail("cannot convert \(Int(inputFormat.sampleRate)) Hz input to 16 kHz mono", exitFailure)
        }

        // Captured strongly on purpose: the tap outlives this call, and a
        // weak capture would leave every buffer dropped on the floor. The
        // cycle it makes dies with the process.
        input.installTap(onBus: 0, bufferSize: 4096, format: inputFormat) { buffer, _ in
            self.append(buffer, using: converter)
        }
        do {
            try engine.start()
        } catch {
            input.removeTap(onBus: 0)
            fail("the microphone would not start: \(error.localizedDescription)", exitFailure)
        }

        let hitMax = waitForStop(stopPath: stopPath, maxSeconds: maxSeconds)

        engine.stop()  // stop feeding the tap before taking it away
        input.removeTap(onBus: 0)
        lock.lock()
        file = nil  // closes the file, writing the WAV header
        lock.unlock()
        return hitMax
    }

    private func append(_ buffer: AVAudioPCMBuffer, using converter: AVAudioConverter) {
        lock.lock()
        defer { lock.unlock() }
        guard let file else { return }
        let ratio = converter.outputFormat.sampleRate / converter.inputFormat.sampleRate
        let capacity = AVAudioFrameCount(Double(buffer.frameLength) * ratio) + 1024
        guard let converted = AVAudioPCMBuffer(
            pcmFormat: converter.outputFormat, frameCapacity: capacity) else { return }

        var consumed = false
        var conversionError: NSError?
        let status = converter.convert(to: converted, error: &conversionError) { _, outStatus in
            if consumed {
                outStatus.pointee = .noDataNow
                return nil
            }
            consumed = true
            outStatus.pointee = .haveData
            return buffer
        }
        guard status != .error, converted.frameLength > 0 else { return }
        try? file.write(from: converted)
    }
}

// MARK: - Arguments

struct Options {
    var out: String?
    var stop: String?
    var device: String?
    var statusFile: String?
    var maxSeconds: Double = 120
    var check = false
    var listDevices = false
}

/// Report the --check result to a file as well as to stdout.
///
/// `vocalize listen --check` has to launch this bundle through LaunchServices,
/// because TCC answers for the *responsible* process and a binary exec'd by a
/// shell is answered for by that shell. `open -W` relays neither stdout nor the
/// app's exit status, so this file is the only channel back.
func writeStatusFile(_ path: String, word: String, device: String, code: Int32, note: String?) {
    var text = "status: \(word)\ndevice: \(device)\nexit: \(code)\n"
    if let note { text += "note: \(note)\n" }
    try? text.write(toFile: path, atomically: true, encoding: .utf8)
}

func parseArguments() -> Options {
    var options = Options()
    var arguments = Array(CommandLine.arguments.dropFirst())
    while !arguments.isEmpty {
        let flag = arguments.removeFirst()
        func value(_ name: String) -> String {
            guard !arguments.isEmpty else { fail("\(name) needs a value", exitFailure) }
            return arguments.removeFirst()
        }
        switch flag {
        case "--out": options.out = value("--out")
        case "--stop": options.stop = value("--stop")
        case "--device": options.device = value("--device")
        case "--status-file": options.statusFile = value("--status-file")
        case "--max":
            let raw = value("--max")
            guard let seconds = Double(raw), seconds >= 1, seconds <= 600 else {
                fail("--max must be a number of seconds between 1 and 600", exitFailure)
            }
            options.maxSeconds = seconds
        case "--check": options.check = true
        case "--list-devices": options.listDevices = true
        default:
            fail("unknown option \(flag)", exitFailure)
        }
    }
    return options
}

func authorizationWord(_ status: AVAuthorizationStatus) -> String {
    switch status {
    case .authorized: return "authorized"
    case .denied: return "denied"
    case .restricted: return "denied"
    case .notDetermined: return "notDetermined"
    @unknown default: return "unknown"
    }
}

/// Blocks until the user answers the system prompt. Recording only.
func requestMicrophoneAccess() -> Bool {
    let waiter = DispatchSemaphore(value: 0)
    var granted = false
    AVCaptureDevice.requestAccess(for: .audio) { allowed in
        granted = allowed
        waiter.signal()
    }
    waiter.wait()
    return granted
}

// MARK: - main

let options = parseArguments()

if options.listDevices {
    for device in inputDevices() { print(device.name) }
    finish(exitOK)
}

if options.check {
    // Reports only: `authorizationStatus` never prompts, and nothing here
    // opens the microphone.
    let status = AVCaptureDevice.authorizationStatus(for: .audio)
    let word = authorizationWord(status)
    print(word)
    let device = resolveDevice(options.device)
    print("device: \(device?.name ?? "none")")
    // Restricted is reported as "denied" to keep the vocabulary to three
    // words, so the reason travels separately — a greyed-out System Settings
    // pane is not something the user can act on.
    let restriction = status == .restricted
        ? "microphone access is restricted by policy" : nil
    if let restriction { note(restriction) }
    let code: Int32
    switch status {
    case .notDetermined: code = exitNotDetermined
    case .denied, .restricted: code = exitDenied
    case .authorized: code = device == nil ? exitNoDevice : exitOK
    @unknown default: code = exitFailure
    }
    if let path = options.statusFile {
        writeStatusFile(
            path, word: word, device: device?.name ?? "none", code: code, note: restriction)
    }
    finish(code)
}

guard let outPath = options.out, let stopPath = options.stop else {
    fail("usage: recorder --out PATH --stop PATH --max SECONDS [--device NAME]", exitFailure)
}

let outURL = URL(fileURLWithPath: outPath)

// macOS asks for the microphone the first time this bundle records, and
// the request below blocks until the user answers — ~150 s in the
// spike. `rec.pid` cannot be written until after that, so `dictate` had no
// way to tell "the dialog is on screen" from "the recorder died", gave up
// after its 5 s grace, and deleted the directory this recorder was about to
// write into. This marker is that signal: it exists only while the dialog
// does (DEC-014).
let promptURL = outURL.deletingLastPathComponent().appendingPathComponent("rec.prompt")
let asking = AVCaptureDevice.authorizationStatus(for: .audio) == .notDetermined
if asking {
    try? "1\n".write(to: promptURL, atomically: true, encoding: .utf8)
}
let granted = requestMicrophoneAccess()
if asking {
    try? FileManager.default.removeItem(at: promptURL)
}
guard granted else {
    fail("microphone access was not granted to Vocalize Recorder", exitDenied)
}

// An empty --device is the config's "system default", not a device to look up.
let namedDevice = options.device?.isEmpty == false

guard let device = resolveDevice(options.device) else {
    if namedDevice, let wanted = options.device {
        fail("no input device named \(wanted) — run: vocalize listen --list-devices", exitNoDevice)
    }
    fail("no input device is available", exitNoDevice)
}

// Written only once recording is actually about to start: `dictate` treats a
// session with no rec.pid as a recorder that died, never as one to wait for.
let pidURL = outURL.deletingLastPathComponent().appendingPathComponent("rec.pid")
installSignalHandlers()
do {
    try "\(getpid())\n".write(to: pidURL, atomically: true, encoding: .utf8)
    pidFileCPath = strdup(pidURL.path)
} catch {
    fail("could not write \(pidURL.path): \(error.localizedDescription)", exitFailure)
}

let hitMax: Bool
if !namedDevice {
    hitMax = recordWithAudioRecorder(to: outURL, stopPath: stopPath, maxSeconds: options.maxSeconds)
} else {
    let deviceRecorder = DeviceRecorder()
    hitMax = deviceRecorder.record(
        device: device, to: outURL, stopPath: stopPath, maxSeconds: options.maxSeconds)
}

if !writtenFormatIsCorrect(outURL), !convertToContract(outURL) {
    fail("the recording is not 16 kHz mono 16-bit and afconvert could not fix it", exitFailure)
}

finish(hitMax ? exitMaxSeconds : exitOK)
