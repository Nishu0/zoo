// zoovm: runs macOS microVMs on Apple Silicon with Virtualization.framework.
//
//   zoovm install <name> [--ipsw <path>] [--cpu N] [--memory MB] [--disk GB]
//   zoovm clone <source> <name>
//   zoovm set <name> [--cpu N] [--memory MB]
//   zoovm run <name>          boots headless, serves VNC on 127.0.0.1, runs until the guest stops
//   zoovm stop <name> [--timeout S]
//   zoovm ip <name>
//   zoovm vnc <name>
//   zoovm version <name>      the macOS version and build it was installed with, as JSON
//   zoovm list
//   zoovm delete <name>

import Foundation
import Virtualization
import CryptoKit
import Darwin

let root = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".zoovm/vms")

struct Config: Codable {
    var cpus: Int
    var memoryMB: UInt64
    var mac: String
    var width: Int
    var height: Int
}

let savedTopology = "mac-v1:virtio-block-uncached-full:nat:usb-keyboard:usb-pointer:entropy:balloon:mac-display"
let savedParts = ["config.json", "hardware.bin", "machine.bin", "aux.img", "disk.img", "installed", "version.json"]

struct SavedState: Codable {
    var format: Int
    var generation: String
    var source: String
    var host: String
    var uid: UInt32
    var hostVersion: String
    var topology: String
    var config: String
    var machine: String
    var hardware: String
}

struct SlotLease: Codable, Equatable {
    var source: String
    var owner: String
    var generation: String
}

enum StartMode {
    case cold      // fresh VZ boot of the bundle's disk
    case restore   // cross-slot: restore a cloned saved state (lease-checked, network-isolated until network-on)
    case resume    // same-name: consume this bundle's own saved state (stop/start resume)
}

final class BundleLock {
    var descriptor: Int32 = -1

    init(_ key: String, wait: Bool = false) throws {
        let locks = root.appendingPathComponent(".locks")
        try FileManager.default.createDirectory(at: locks, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        let path = locks.appendingPathComponent(digest(Data(key.utf8))).path
        let opened = open(path, O_CREAT | O_RDWR | O_CLOEXEC, 0o600)
        guard opened >= 0 else { throw Failure("cannot open VM lock: \(String(cString: strerror(errno)))") }
        guard flock(opened, LOCK_EX | (wait ? 0 : LOCK_NB)) == 0 else {
            close(opened)
            throw Failure("VM identity or slot is already in use")
        }
        descriptor = opened
    }

    deinit {
        if descriptor >= 0 {
            flock(descriptor, LOCK_UN)
            close(descriptor)
        }
    }
}

func digest(_ data: Data) -> String {
    SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
}

func originatingHost() throws -> String {
    var identifier = UUID().uuid
    var timeout = timespec(tv_sec: 5, tv_nsec: 0)
    guard gethostuuid(&identifier, &timeout) == 0 else { throw Failure("cannot identify the originating Mac") }
    return UUID(uuid: identifier).uuidString
}

func privateClone(_ source: URL, _ destination: URL) throws {
    guard clonefile(source.path, destination.path, 0) == 0 else {
        throw Failure("APFS private clone failed: \(String(cString: strerror(errno)))")
    }
}

func savedState(_ name: String, directory: URL) throws -> SavedState {
    try JSONDecoder().decode(SavedState.self, from: Data(contentsOf: directory.appendingPathComponent("saved.json")))
}

func validateSavedState(_ name: String, directory: URL) throws -> SavedState {
    let state = try savedState(name, directory: directory)
    guard state.format == 1, state.host == (try originatingHost()), state.uid == getuid(),
          state.hostVersion == ProcessInfo.processInfo.operatingSystemVersionString,
          state.topology == savedTopology else {
        throw Failure("saved state requires its originating Mac/account and unchanged host version/topology; re-prepare the slot")
    }
    for (part, expected) in [("config.json", state.config), ("machine.bin", state.machine), ("hardware.bin", state.hardware)] {
        guard digest(try Data(contentsOf: directory.appendingPathComponent(part))) == expected else {
            throw Failure("saved state has incompatible \(part)")
        }
    }
    return state
}

func lease(_ name: String) throws -> SlotLease {
    let claim = try JSONDecoder().decode(SlotLease.self, from: Data(contentsOf: file(name, "slot-lease.json")))
    let owner = try JSONDecoder().decode(SlotLease.self, from: Data(contentsOf: file(claim.source, ".lease/owner.json")))
    guard claim == owner, claim.owner == name else { throw Failure("saved slot lease does not belong to \(name)") }
    return claim
}

func requireUnleased(_ name: String) throws {
    guard !FileManager.default.fileExists(atPath: file(name, ".lease").path) else {
        throw Failure("\(name) is leased; delete its restored VM before reusing or updating the slot")
    }
}

func cloneState(_ source: String, _ name: String) throws {
    guard source != name else { throw Failure("the restored VM must have its own bundle name") }
    let sourceLock = try BundleLock("bundle:\(source)")
    let targetLock = try BundleLock("bundle:\(name)")
    defer { withExtendedLifetime((sourceLock, targetLock)) {} }
    try requireInstalled(source)
    let snapshot = file(source, "saved")
    let state = try validateSavedState(source, directory: snapshot)
    guard state.source == source else { throw Failure("saved slot source does not match its bundle") }
    guard !FileManager.default.fileExists(atPath: bundle(name).path) else { throw Failure("\(name) already exists") }
    try requireUnleased(source)
    guard mkdir(file(source, ".lease").path, 0o700) == 0 else { throw Failure("saved slot is already leased") }
    let claim = SlotLease(source: source, owner: name, generation: state.generation)
    var created = false
    do {
        try JSONEncoder().encode(claim).write(to: file(source, ".lease/owner.json"), options: .atomic)
        guard mkdir(bundle(name).path, 0o700) == 0 else { throw Failure("cannot create restored VM bundle") }
        created = true
        for part in savedParts + ["state.vzvmsave", "saved.json"] {
            try privateClone(snapshot.appendingPathComponent(part), file(name, part))
        }
        try JSONEncoder().encode(claim).write(to: file(name, "slot-lease.json"), options: .atomic)
    } catch {
        if created { try? FileManager.default.removeItem(at: bundle(name)) }
        try? FileManager.default.removeItem(at: file(source, ".lease"))
        throw error
    }
    print(String(decoding: try JSONEncoder().encode(claim), as: UTF8.self))
}

func controlSocket(_ name: String) throws -> Int32 {
    let descriptor = socket(AF_UNIX, SOCK_STREAM, 0)
    guard descriptor >= 0 else { throw Failure("cannot open runner control socket") }
    var noSignal: Int32 = 1
    setsockopt(descriptor, SOL_SOCKET, SO_NOSIGPIPE, &noSignal, socklen_t(MemoryLayout<Int32>.size))
    return descriptor
}

func socketAddress(_ name: String) throws -> sockaddr_un {
    var address = sockaddr_un()
    address.sun_family = sa_family_t(AF_UNIX)
    address.sun_len = UInt8(MemoryLayout<sockaddr_un>.size)
    let path = Array(file(name, "control.sock").path.utf8) + [0]
    guard path.count <= MemoryLayout.size(ofValue: address.sun_path) else { throw Failure("VM control socket path is too long") }
    withUnsafeMutableBytes(of: &address.sun_path) { buffer in
        buffer.copyBytes(from: path)
    }
    return address
}

func socketLine(_ descriptor: Int32) throws -> Data {
    var data = Data()
    var byte: UInt8 = 0
    while data.count < 4096 {
        guard read(descriptor, &byte, 1) == 1 else { throw Failure("runner control connection closed or timed out") }
        if byte == 10 { return data }
        data.append(byte)
    }
    throw Failure("runner control message is too large")
}

func socketSend(_ descriptor: Int32, _ object: [String: Any]) throws {
    var data = try JSONSerialization.data(withJSONObject: object)
    data.append(10)
    try data.withUnsafeBytes { buffer in
        var offset = 0
        while offset < buffer.count {
            let count = write(descriptor, buffer.baseAddress!.advanced(by: offset), buffer.count - offset)
            guard count > 0 else { throw Failure("cannot write runner control response") }
            offset += count
        }
    }
}

func runnerControl(_ name: String, op: String, sandboxID: String? = nil) throws {
    guard pid(name) != nil else { throw Failure("\(name) has no running VM controller") }
    let descriptor = try controlSocket(name)
    defer { close(descriptor) }
    var address = try socketAddress(name)
    let result = withUnsafePointer(to: &address) { pointer in
        pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) {
            Darwin.connect(descriptor, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
        }
    }
    guard result == 0 else { throw Failure("cannot connect to \(name)'s VM controller") }
    var timeout = timeval(tv_sec: 300, tv_usec: 0)
    setsockopt(descriptor, SOL_SOCKET, SO_RCVTIMEO, &timeout, socklen_t(MemoryLayout<timeval>.size))
    var request: [String: Any] = ["op": op]
    if let sandboxID { request["sandbox_id"] = sandboxID }
    try socketSend(descriptor, request)
    let data = try socketLine(descriptor)
    guard let reply = try JSONSerialization.jsonObject(with: data) as? [String: Any], reply["ok"] as? Bool == true else {
        throw Failure(String(decoding: data, as: UTF8.self))
    }
    print(String(decoding: data, as: UTF8.self))
}

struct Failure: Error, CustomStringConvertible {
    let description: String
    init(_ description: String) { self.description = description }
}

func fail(_ message: String) -> Never {
    FileHandle.standardError.write("zoovm: \(message)\n".data(using: .utf8)!)
    exit(1)
}

func validName(_ name: String) -> Bool {
    name.range(of: "^[A-Za-z0-9][A-Za-z0-9._-]*$", options: .regularExpression) != nil
}

func bundle(_ name: String) -> URL {
    guard validName(name) else { fail("invalid name \(name)") }
    return root.appendingPathComponent(name)
}

func file(_ name: String, _ part: String) -> URL { bundle(name).appendingPathComponent(part) }

func exists(_ name: String) -> Bool { FileManager.default.fileExists(atPath: file(name, "config.json").path) }

func requireInstalled(_ name: String) throws {
    guard FileManager.default.fileExists(atPath: file(name, "installed").path) else {
        throw Failure("\(name) is not installed; run `zoovm install \(name)`")
    }
}

func loadConfig(_ name: String) throws -> Config {
    guard exists(name) else { throw Failure("no VM named \(name)") }
    return try JSONDecoder().decode(Config.self, from: Data(contentsOf: file(name, "config.json")))
}

func saveConfig(_ name: String, _ config: Config) throws {
    try JSONEncoder().encode(config).write(to: file(name, "config.json"))
}

func option(_ args: [String], _ flag: String) -> String? {
    guard let i = args.firstIndex(of: flag), i + 1 < args.count else { return nil }
    return args[i + 1]
}

func pid(_ name: String) -> pid_t? {
    guard let text = try? String(contentsOf: file(name, "pid"), encoding: .utf8),
          let value = pid_t(text.trimmingCharacters(in: .whitespacesAndNewlines)),
          kill(value, 0) == 0 else { return nil }
    return value
}

func configuration(_ name: String) throws -> VZVirtualMachineConfiguration {
    let config = try loadConfig(name)
    guard let model = VZMacHardwareModel(dataRepresentation: try Data(contentsOf: file(name, "hardware.bin"))),
          let machine = VZMacMachineIdentifier(dataRepresentation: try Data(contentsOf: file(name, "machine.bin"))) else {
        throw Failure("corrupt VM bundle \(name)")
    }
    let platform = VZMacPlatformConfiguration()
    platform.hardwareModel = model
    platform.machineIdentifier = machine
    platform.auxiliaryStorage = VZMacAuxiliaryStorage(url: file(name, "aux.img"))

    let graphics = VZMacGraphicsDeviceConfiguration()
    graphics.displays = [VZMacGraphicsDisplayConfiguration(widthInPixels: config.width, heightInPixels: config.height, pixelsPerInch: 80)]

    let network = VZVirtioNetworkDeviceConfiguration()
    network.attachment = VZNATNetworkDeviceAttachment()
    guard let mac = VZMACAddress(string: config.mac) else { throw Failure("invalid MAC address \(config.mac)") }
    network.macAddress = mac

    let vm = VZVirtualMachineConfiguration()
    vm.platform = platform
    vm.bootLoader = VZMacOSBootLoader()
    vm.cpuCount = config.cpus
    vm.memorySize = config.memoryMB * 1024 * 1024
    vm.graphicsDevices = [graphics]
    vm.storageDevices = [VZVirtioBlockDeviceConfiguration(attachment: try VZDiskImageStorageDeviceAttachment(
        url: file(name, "disk.img"), readOnly: false, cachingMode: .uncached, synchronizationMode: .full
    ))]
    vm.networkDevices = [network]
    vm.keyboards = [VZUSBKeyboardConfiguration()]
    vm.pointingDevices = [VZUSBScreenCoordinatePointingDeviceConfiguration()]
    vm.entropyDevices = [VZVirtioEntropyDeviceConfiguration()]
    vm.memoryBalloonDevices = [VZVirtioTraditionalMemoryBalloonDeviceConfiguration()]
    try vm.validate()
    return vm
}

func download(_ url: URL, to destination: URL) throws {
    // curl shows progress and resumes a partial download if install is interrupted and run again.
    print("downloading \(url.absoluteString)")
    fflush(stdout)
    let partial = destination.appendingPathExtension("part")
    let curl = Process()
    curl.executableURL = URL(fileURLWithPath: "/usr/bin/curl")
    curl.arguments = ["-fL", "--retry", "5", "-C", "-", "--progress-bar", "-o", partial.path, url.absoluteString]
    try curl.run()
    curl.waitUntilExit()
    guard curl.terminationStatus == 0 else { throw Failure("download failed (curl exit \(curl.terminationStatus)); run install again to resume") }
    try? FileManager.default.removeItem(at: destination)
    try FileManager.default.moveItem(at: partial, to: destination)
}

// Restores share an immutable, build-specific IPSW outside VM bundles. Deleting
// a base must not delete its expensive download, and only one writer resumes it.
@MainActor
func cachedRestoreImage(_ latest: VZMacOSRestoreImage, legacy: URL) async throws -> URL {
    let cache = root.deletingLastPathComponent().appendingPathComponent("cache/restore")
    try FileManager.default.createDirectory(at: cache, withIntermediateDirectories: true)
    let key = digest(Data(latest.buildVersion.utf8))
    let destination = cache.appendingPathComponent("\(key).ipsw")
    let lock = try BundleLock("restore-cache:\(key)", wait: true)
    defer { withExtendedLifetime(lock) {} }
    if FileManager.default.fileExists(atPath: destination.path) {
        if let cached = try? await VZMacOSRestoreImage.image(from: destination), cached.buildVersion == latest.buildVersion {
            print("using cached macOS restore image (\(latest.buildVersion))")
            fflush(stdout)
            return destination
        }
        // Keep a bad or mismatched artifact for diagnosis; never truncate an
        // existing valid image in order to write a replacement download.
        let quarantine = cache.appendingPathComponent("\(key).invalid-\(UUID().uuidString).ipsw")
        try FileManager.default.moveItem(at: destination, to: quarantine)
    }
    if FileManager.default.fileExists(atPath: legacy.path),
       let existing = try? await VZMacOSRestoreImage.image(from: legacy), existing.buildVersion == latest.buildVersion {
        try FileManager.default.moveItem(at: legacy, to: destination)
        print("cached existing macOS restore image (\(latest.buildVersion))")
        fflush(stdout)
        return destination
    }
    try download(latest.url, to: destination)
    let downloaded = try await VZMacOSRestoreImage.image(from: destination)
    guard downloaded.buildVersion == latest.buildVersion else {
        throw Failure("downloaded macOS restore image has an unexpected build; cached image was not used")
    }
    return destination
}

@MainActor
func install(_ name: String, _ args: [String]) async throws {
    let lock = try BundleLock("bundle:\(name)")
    defer { withExtendedLifetime(lock) {} }
    try requireUnleased(name)
    guard !FileManager.default.fileExists(atPath: file(name, "slot-lease.json").path) else {
        throw Failure("a saved-state instance cannot be reinstalled")
    }
    if exists(name) {
        guard !FileManager.default.fileExists(atPath: file(name, "installed").path) else { throw Failure("\(name) already exists") }
        // An earlier install failed: start over, but keep the downloaded restore image.
        for part in ["config.json", "hardware.bin", "machine.bin", "aux.img", "disk.img"] {
            try? FileManager.default.removeItem(at: file(name, part))
        }
    }
    let cpus = Int(option(args, "--cpu") ?? "4") ?? 4
    let memory = UInt64(option(args, "--memory") ?? "8192") ?? 8192
    let disk = UInt64(option(args, "--disk") ?? "80") ?? 80
    try FileManager.default.createDirectory(at: bundle(name), withIntermediateDirectories: true)

    var ipsw = option(args, "--ipsw").map { URL(fileURLWithPath: $0) }
    if ipsw == nil {
        let latest = try await VZMacOSRestoreImage.latestSupported
        ipsw = try await cachedRestoreImage(latest, legacy: file(name, "restore.ipsw"))
    }
    let image = try await VZMacOSRestoreImage.image(from: ipsw!)
    // the base's version (with when it was prepared) is what sandboxes record as the base they were cloned from
    let os = image.operatingSystemVersion
    try JSONSerialization.data(withJSONObject: [
        "os": "\(os.majorVersion).\(os.minorVersion).\(os.patchVersion)", "build": image.buildVersion,
    ]).write(to: file(name, "version.json"))
    guard let requirements = image.mostFeaturefulSupportedConfiguration, requirements.hardwareModel.isSupported else {
        throw Failure("this Mac cannot run the macOS version in \(ipsw!.path)")
    }
    try requirements.hardwareModel.dataRepresentation.write(to: file(name, "hardware.bin"))
    try VZMacMachineIdentifier().dataRepresentation.write(to: file(name, "machine.bin"))
    _ = try VZMacAuxiliaryStorage(creatingStorageAt: file(name, "aux.img"), hardwareModel: requirements.hardwareModel, options: [])
    FileManager.default.createFile(atPath: file(name, "disk.img").path, contents: nil)
    let handle = try FileHandle(forWritingTo: file(name, "disk.img"))
    try handle.truncate(atOffset: disk * 1024 * 1024 * 1024)
    try handle.close()
    try saveConfig(name, Config(
        cpus: max(cpus, requirements.minimumSupportedCPUCount),
        memoryMB: max(memory, requirements.minimumSupportedMemorySize / 1024 / 1024),
        mac: VZMACAddress.randomLocallyAdministered().string,
        width: 1280,
        height: 800
    ))

    let vm = VZVirtualMachine(configuration: try configuration(name))
    let installer = VZMacOSInstaller(virtualMachine: vm, restoringFromImageAt: ipsw!)
    let observer = installer.progress.observe(\.fractionCompleted, options: [.initial, .new]) { progress, _ in
        print(String(format: "installing %.0f%%", progress.fractionCompleted * 100))
        fflush(stdout)
    }
    try await installer.install()
    observer.invalidate()
    FileManager.default.createFile(atPath: file(name, "installed").path, contents: nil)
    print("installed \(name); Zoo can now prepare its guest account automatically")
    fflush(stdout)
}

func clone(_ source: String, _ name: String) throws {
    let sourceLock = try BundleLock("bundle:\(source)")
    let targetLock = try BundleLock("bundle:\(name)")
    defer { withExtendedLifetime((sourceLock, targetLock)) {} }
    try requireInstalled(source)
    try requireUnleased(source)
    if pid(source) != nil { throw Failure("stop \(source) before cloning it") }
    if FileManager.default.fileExists(atPath: bundle(name).path) { throw Failure("\(name) already exists") }
    var config = try loadConfig(source)
    try FileManager.default.createDirectory(at: bundle(name), withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
    do {
        for part in ["hardware.bin", "aux.img", "disk.img", "version.json", "installed"] {
            try privateClone(file(source, part), file(name, part))
        }
        try VZMacMachineIdentifier().dataRepresentation.write(to: file(name, "machine.bin"))
        config.mac = VZMACAddress.randomLocallyAdministered().string
        try saveConfig(name, config)
    } catch {
        try? FileManager.default.removeItem(at: bundle(name))
        throw error
    }
}

func set(_ name: String, _ args: [String]) throws {
    let lock = try BundleLock("bundle:\(name)")
    defer { withExtendedLifetime(lock) {} }
    guard !FileManager.default.fileExists(atPath: file(name, "saved").path),
          !FileManager.default.fileExists(atPath: file(name, "slot-lease.json").path) else {
        throw Failure("saved slot topology is immutable; prepare another unique slot with the required size")
    }
    var config = try loadConfig(name)
    if let cpus = option(args, "--cpu").flatMap(Int.init) { config.cpus = cpus }
    if let memory = option(args, "--memory").flatMap(UInt64.init) { config.memoryMB = memory }
    try saveConfig(name, config)
}

@MainActor
final class Runner: NSObject, VZVirtualMachineDelegate {
    let name: String
    let vm: VZVirtualMachine
    let vmConfig: VZVirtualMachineConfiguration
    let bundleLock: BundleLock
    let identityLock: BundleLock
    let macLock: BundleLock
    var vnc: Any?
    var signals: [DispatchSourceSignal] = []
    var controller: DispatchSourceRead?
    var busy = false
    var isolated = false

    init(name: String) throws {
        self.name = name
        bundleLock = try BundleLock("bundle:\(name)")
        try requireUnleased(name)
        identityLock = try BundleLock("machine:\(digest(try Data(contentsOf: file(name, "machine.bin"))))")
        macLock = try BundleLock("mac:\(try loadConfig(name).mac.lowercased())")
        vmConfig = try configuration(name)
        self.vm = VZVirtualMachine(configuration: vmConfig)
        super.init()
        vm.delegate = self
    }

    func start(mode: StartMode) async throws {
        if pid(name) != nil { throw Failure("\(name) is already running") }
        try requireInstalled(name)
        switch mode {
        case .restore:
            guard #available(macOS 14.0, *) else { throw Failure("saved-state restore requires macOS 14 or newer") }
            let state = try validateSavedState(name, directory: bundle(name))
            let claim = try lease(name)
            guard state.source == claim.source, state.generation == claim.generation else { throw Failure("saved slot generation does not match its lease") }
            try vmConfig.validateSaveRestoreSupport()
            do {
                try await vm.restoreMachineStateFrom(url: file(name, "state.vzvmsave"))
            } catch {
                try? FileManager.default.removeItem(at: file(name, "state.vzvmsave"))
                throw error
            }
            try FileManager.default.removeItem(at: file(name, "state.vzvmsave"))
            guard vm.state == .paused else { throw Failure("restore did not leave the VM paused") }
            for device in vm.networkDevices { device.attachment = nil }
            isolated = true
            try await vm.resume()
        case .resume:
            // same-name stop/start: consume this slot's own saved state; identity, lease-free
            guard #available(macOS 14.0, *) else { throw Failure("saved-state resume requires macOS 14 or newer") }
            let state = try validateSavedState(name, directory: file(name, "saved"))
            try requireUnleased(name)
            guard state.source == name else { throw Failure("saved state does not belong to \(name)") }
            try vmConfig.validateSaveRestoreSupport()
            try await vm.restoreMachineStateFrom(url: file(name, "saved/state.vzvmsave"))
            guard vm.state == .paused else { throw Failure("resume did not leave the VM paused") }
            // the bundle keeps its own identity: live NAT like a cold boot, no isolation wait
            try await vm.resume()
        case .cold:
            guard !FileManager.default.fileExists(atPath: file(name, "slot-lease.json").path) else {
                throw Failure("saved-state instances must use restore, not a cold boot")
            }
            try await vm.start()
        }
        try "\(getpid())".write(to: file(name, "pid"), atomically: true, encoding: .utf8)
        try serveControl()
        for sig in [SIGTERM, SIGINT] {
            signal(sig, SIG_IGN)
            let source = DispatchSource.makeSignalSource(signal: sig, queue: .main)
            source.setEventHandler { [weak self] in self?.shutdown() }
            source.resume()
            signals.append(source)
        }
        serveVNC()
    }

    func serveControl() throws {
        let descriptor = try controlSocket(name)
        var address = try socketAddress(name)
        try? FileManager.default.removeItem(at: file(name, "control.sock"))
        let result = withUnsafePointer(to: &address) { pointer in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.bind(descriptor, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        guard result == 0, chmod(file(name, "control.sock").path, 0o600) == 0, listen(descriptor, 8) == 0 else {
            close(descriptor)
            throw Failure("cannot publish runner control socket")
        }
        guard fcntl(descriptor, F_SETFL, O_NONBLOCK) == 0 else {
            close(descriptor)
            throw Failure("cannot configure runner control socket")
        }
        let source = DispatchSource.makeReadSource(fileDescriptor: descriptor, queue: .main)
        source.setCancelHandler { close(descriptor) }
        source.setEventHandler { [self] in
            let client = accept(descriptor, nil, nil)
            guard client >= 0 else { return }
            guard fcntl(client, F_SETFL, 0) == 0 else {
                close(client)
                return
            }
            var timeout = timeval(tv_sec: 5, tv_usec: 0)
            var noSignal: Int32 = 1
            setsockopt(client, SOL_SOCKET, SO_RCVTIMEO, &timeout, socklen_t(MemoryLayout<timeval>.size))
            setsockopt(client, SOL_SOCKET, SO_NOSIGPIPE, &noSignal, socklen_t(MemoryLayout<Int32>.size))
            DispatchQueue.global().async {
                do {
                    guard let request = try JSONSerialization.jsonObject(with: socketLine(client)) as? [String: Any] else {
                        throw Failure("invalid runner control request")
                    }
                    Task { @MainActor in
                        let (response, saved) = await self.handleControl(request)
                        try? socketSend(client, response)
                        close(client)
                        if saved { self.finish(0, nil) }
                    }
                } catch {
                    try? socketSend(client, ["ok": false, "error": String(describing: error)])
                    close(client)
                }
            }
        }
        source.resume()
        controller = source
    }

    func handleControl(_ request: [String: Any]) async -> ([String: Any], Bool) {
        guard !busy else { return (["ok": false, "error": "a VM transition is in progress"], false) }
        busy = true
        defer { busy = false }
        do {
            switch request["op"] as? String {
            case "save-state":
                let state = try await saveState()
                return (["ok": true, "slot": name, "generation": state.generation, "state": "saved"], true)
            case "network-on":
                guard isolated, vm.state == .running,
                      let sandboxID = request["sandbox_id"] as? String,
                      sandboxID.range(of: "^[A-Za-z0-9][A-Za-z0-9._-]*$", options: .regularExpression) != nil else {
                    throw Failure("network activation requires an isolated restored VM and its freshly installed sandbox identity")
                }
                _ = try lease(name)
                guard vm.networkDevices.count == 1 else { throw Failure("saved network topology is incompatible") }
                vm.networkDevices[0].attachment = VZNATNetworkDeviceAttachment()
                isolated = false
                return (["ok": true, "sandbox_id": sandboxID, "network": "nat"], false)
            case "resume":
                guard vm.state == .paused else { throw Failure("VM is not paused") }
                try await vm.resume()
                return (["ok": true, "state": "running"], false)
            case "status":
                return (["ok": true, "state": String(describing: vm.state), "network": isolated ? "isolated" : "nat"], false)
            default:
                throw Failure("unknown runner control operation")
            }
        } catch {
            return (["ok": false, "error": String(describing: error)], false)
        }
    }

    func saveState() async throws -> SavedState {
        guard #available(macOS 14.0, *) else { throw Failure("saving VM state requires macOS 14 or newer") }
        guard !FileManager.default.fileExists(atPath: file(name, "slot-lease.json").path) else {
            throw Failure("a restored consumer cannot become another slot with the same machine identity")
        }
        try requireUnleased(name)
        try vmConfig.validateSaveRestoreSupport()
        guard vm.state == .running, vnc != nil else { throw Failure("save-state requires a running, prepared desktop slot") }
        let state = SavedState(
            format: 1, generation: UUID().uuidString, source: name, host: try originatingHost(), uid: getuid(),
            hostVersion: ProcessInfo.processInfo.operatingSystemVersionString, topology: savedTopology,
            config: digest(try Data(contentsOf: file(name, "config.json"))),
            machine: digest(try Data(contentsOf: file(name, "machine.bin"))),
            hardware: digest(try Data(contentsOf: file(name, "hardware.bin")))
        )
        let staging = file(name, ".saved-\(state.generation)")
        try FileManager.default.createDirectory(at: staging, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
        defer { try? FileManager.default.removeItem(at: staging) }
        try await vm.pause()
        guard vm.state == .paused else { throw Failure("VM did not pause for saving") }
        try await vm.saveMachineStateTo(url: staging.appendingPathComponent("state.vzvmsave"))
        for part in ["disk.img", "aux.img"] {
            let handle = try FileHandle(forUpdating: file(name, part))
            defer { try? handle.close() }
            try handle.synchronize()
        }
        for part in savedParts { try privateClone(file(name, part), staging.appendingPathComponent(part)) }
        try JSONEncoder().encode(state).write(to: staging.appendingPathComponent("saved.json"), options: .atomic)
        let destination = file(name, "saved")
        if FileManager.default.fileExists(atPath: destination.path) {
            guard renamex_np(staging.path, destination.path, UInt32(RENAME_SWAP)) == 0 else { throw Failure("cannot atomically replace saved slot") }
        } else {
            try FileManager.default.moveItem(at: staging, to: destination)
        }
        try await vm.stop()
        return state
    }

    func serveVNC() {
        let password = (0..<4).map { _ in String(format: "%04x", UInt16.random(in: 0...UInt16.max)) }.joined(separator: "-")
        guard let server = ZooVNCStart(vm, password) else { finish(1, "this macOS has no Virtualization VNC server") }
        vnc = server
        func publish(_ attempt: Int) {
            let port = ZooVNCPort(server)
            if port == 0 {
                if attempt > 100 { finish(1, "VNC server did not start") }
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.1) { publish(attempt + 1) }
                return
            }
            let url = "vnc://:\(password)@127.0.0.1:\(port)"
            FileManager.default.createFile(atPath: file(name, "vnc.url").path, contents: url.data(using: .utf8), attributes: [.posixPermissions: 0o600])
            print("VNC server is running at \(url)")
            fflush(stdout)
        }
        publish(0)
    }

    func shutdown() {
        if busy { return }
        if vm.canRequestStop { try? vm.requestStop() }
        DispatchQueue.main.asyncAfter(deadline: .now() + 20) { [self] in
            Task { @MainActor in
                guard !self.busy else { return }
                do {
                    try await self.vm.stop()
                    self.finish(0, nil)
                } catch { self.finish(1, "stop failed: \(error)") }
            }
        }
    }

    func finish(_ code: Int32, _ message: String?) -> Never {
        controller?.cancel()
        try? FileManager.default.removeItem(at: file(name, "control.sock"))
        try? FileManager.default.removeItem(at: file(name, "pid"))
        try? FileManager.default.removeItem(at: file(name, "vnc.url"))
        if let message { FileHandle.standardError.write("zoovm: \(message)\n".data(using: .utf8)!) }
        exit(code)
    }

    nonisolated func guestDidStop(_ virtualMachine: VZVirtualMachine) {
        Task { @MainActor in
            if !self.busy { self.finish(0, nil) }
        }
    }

    nonisolated func virtualMachine(_ virtualMachine: VZVirtualMachine, didStopWithError error: Error) {
        let message = error.localizedDescription
        Task { @MainActor in self.finish(1, "VM stopped: \(message)") }
    }

    nonisolated func virtualMachine(_ virtualMachine: VZVirtualMachine, networkDevice: VZNetworkDevice, attachmentWasDisconnectedWithError error: Error) {
        let message = error.localizedDescription
        Task { @MainActor in
            self.isolated = true
            FileHandle.standardError.write("zoovm: VM network disconnected: \(message)\n".data(using: .utf8)!)
        }
    }
}

func stop(_ name: String, _ args: [String]) {
    guard let process = pid(name) else { return }
    let timeout = Double(option(args, "--timeout") ?? "30") ?? 30
    kill(process, SIGTERM)
    let deadline = Date().addingTimeInterval(timeout)
    while Date() < deadline {
        if kill(process, 0) != 0 { return }
        usleep(200_000)
    }
    kill(process, SIGKILL)
    try? FileManager.default.removeItem(at: file(name, "pid"))
}

func octets(_ mac: String) -> [Int] {
    mac.split(separator: ":").compactMap { Int($0, radix: 16) }
}

func ip(_ name: String) throws -> String {
    let wanted = octets(try loadConfig(name).mac)
    guard let leases = try? String(contentsOfFile: "/var/db/dhcpd_leases", encoding: .utf8) else {
        throw Failure("no DHCP leases yet")
    }
    var found: (ip: String, lease: Int)?
    for entry in leases.components(separatedBy: "}") {
        var fields: [String: String] = [:]
        for line in entry.split(separator: "\n") {
            let parts = line.trimmingCharacters(in: .whitespaces).split(separator: "=", maxSplits: 1)
            if parts.count == 2 { fields[String(parts[0])] = String(parts[1]) }
        }
        guard let address = fields["ip_address"], let hw = fields["hw_address"] else { continue }
        let lease = Int(fields["lease"]?.replacingOccurrences(of: "0x", with: "") ?? "0", radix: 16) ?? 0
        if octets(String(hw.split(separator: ",").last ?? "")) == wanted, lease >= (found?.lease ?? -1) {
            found = (address, lease)
        }
    }
    guard let found else { throw Failure("\(name) has no IP address yet") }
    return found.ip
}

func list() throws {
    let names = (try? FileManager.default.contentsOfDirectory(atPath: root.path)) ?? []
    let rows = names.sorted().filter(validName).filter(exists).map { ["name": $0, "state": pid($0) == nil ? "stopped" : "running"] }
    print(String(data: try JSONSerialization.data(withJSONObject: rows), encoding: .utf8)!)
}

func delete(_ name: String) throws {
    let lock = try BundleLock("bundle:\(name)")
    defer { withExtendedLifetime(lock) {} }
    try requireUnleased(name)
    if pid(name) != nil { throw Failure("stop \(name) before deleting it") }
    guard FileManager.default.fileExists(atPath: bundle(name).path) else { return }
    if FileManager.default.fileExists(atPath: file(name, "slot-lease.json").path) {
        let claim = try lease(name)
        let sourceLock = try BundleLock("bundle:\(claim.source)")
        defer { withExtendedLifetime(sourceLock) {} }
        try FileManager.default.removeItem(at: bundle(name))
        try FileManager.default.removeItem(at: file(claim.source, ".lease"))
        return
    }
    try FileManager.default.removeItem(at: bundle(name))
}

func releaseSlot(_ source: String) throws {
    let lock = try BundleLock("bundle:\(source)")
    defer { withExtendedLifetime(lock) {} }
    let claim = try JSONDecoder().decode(SlotLease.self, from: Data(contentsOf: file(source, ".lease/owner.json")))
    let ownerLock = try BundleLock("bundle:\(claim.owner)")
    defer { withExtendedLifetime(ownerLock) {} }
    guard claim.source == source, !FileManager.default.fileExists(atPath: bundle(claim.owner).path) else {
        throw Failure("delete the restored VM before releasing its slot")
    }
    try FileManager.default.removeItem(at: file(source, ".lease"))
}

let args = Array(CommandLine.arguments.dropFirst())
let command = args.first ?? "help"
let name = args.count > 1 ? args[1] : ""
let rest = Array(args.dropFirst(2))
var activeRunner: Runner?
umask(0o077)

func needName() { if name.isEmpty { fail("usage: zoovm \(command) <name>") } }

do {
    switch command {
    case "install":
        needName()
        Task { @MainActor in
            do { try await install(name, rest); exit(0) } catch { fail("\(error)") }
        }
        dispatchMain()
    case "clone":
        guard args.count >= 3 else { fail("usage: zoovm clone <source> <name>") }
        try clone(args[1], args[2])
    case "clone-state":
        guard args.count == 3 else { fail("usage: zoovm clone-state <slot> <name>") }
        try cloneState(args[1], args[2])
    case "set":
        needName()
        try set(name, rest)
    case "run", "restore", "resume-state":
        needName()
        Task { @MainActor in
            do {
                let runner = try Runner(name: name)
                activeRunner = runner
                let mode: StartMode = command == "run" ? .cold : command == "restore" ? .restore : .resume
                do { try await runner.start(mode: mode) }
                catch { runner.finish(1, "\(error)") }
            } catch { fail("\(error)") }
        }
        dispatchMain()
    case "save-state", "resume", "status":
        needName()
        try runnerControl(name, op: command)
    case "network-on":
        needName()
        guard let sandboxID = option(rest, "--sandbox-id") else { throw Failure("network-on requires --sandbox-id after live guest reidentification") }
        try runnerControl(name, op: command, sandboxID: sandboxID)
    case "saved-state":
        needName()
        print(String(decoding: try JSONEncoder().encode(validateSavedState(name, directory: file(name, "saved"))), as: UTF8.self))
    case "release-slot":
        needName()
        try releaseSlot(name)
    case "stop":
        needName()
        stop(name, rest)
    case "ip":
        needName()
        print(try ip(name))
    case "vnc":
        needName()
        guard pid(name) != nil, let url = try? String(contentsOf: file(name, "vnc.url"), encoding: .utf8) else {
            throw Failure("\(name) is not running")
        }
        print(url)
    case "get":
        needName()
        try requireInstalled(name)
    case "version":
        needName()
        guard let data = try? Data(contentsOf: file(name, "version.json")) else { throw Failure("\(name) has no recorded version") }
        print(String(decoding: data, as: UTF8.self))
    case "list":
        try list()
    case "delete":
        needName()
        try delete(name)
    default:
        print("usage: zoovm install|clone|clone-state|set|run|restore|resume-state|save-state|saved-state|resume|status|network-on|release-slot|stop|ip|vnc|get|version|list|delete <name>")
        exit(command == "help" ? 0 : 1)
    }
} catch {
    fail("\(error)")
}
