import SwiftUI
import AppKit
import Vision
import Darwin

struct ProxyProfile: Identifiable, Decodable {
    let id: String
    let name: String
    let `protocol`: String
    let server: String
    let port: Int
    let network: String
    let security: String
    let source: String
}

struct PingResult: Decodable {
    let profile: String
    let ok: Bool
    let ms: Int
    let output: String
}

@MainActor
final class ProxyModel: ObservableObject {
    @Published var profiles: [ProxyProfile] = []
    @Published var selectedID: String?
    @Published var statusText = "Ready."
    @Published var logText = ""
    @Published var busy = false
    @Published var pings: [String: String] = [:]
    @Published var activeTab = "proxies"
    @Published var outboundMode = "全局连接"
    @Published var showSpeed = true
    @Published var proxyEnabled = false
    @Published var alertMessage: String?
    @Published var speedText = "0KB/s"

    private var managerPath: String {
        let fm = FileManager.default
        let executable = URL(fileURLWithPath: CommandLine.arguments[0]).standardizedFileURL
        let fromExecutable = executable
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("Resources/proxy_manager.py")
            .path
        if fm.fileExists(atPath: fromExecutable) {
            return fromExecutable
        }
        if let resource = Bundle.main.resourcePath {
            let bundled = "\(resource)/proxy_manager.py"
            if fm.fileExists(atPath: bundled) {
                return bundled
            }
        }
        let outputs = Bundle.main.bundleURL.deletingLastPathComponent().path
        return "\(outputs)/proxy_manager.py"
    }

    init() {
        Task { await refresh() }
    }

    func run(_ args: [String]) async -> (Int32, String) {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        process.arguments = [managerPath] + args
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = pipe
        do {
            try process.run()
            process.waitUntilExit()
            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            let output = String(data: data, encoding: .utf8) ?? ""
            return (process.terminationStatus, output)
        } catch {
            return (127, "Error: \(error.localizedDescription)")
        }
    }

    func appendLog(_ text: String) {
        let stamp = DateFormatter.localizedString(from: Date(), dateStyle: .none, timeStyle: .medium)
        logText += "\n[\(stamp)] \(text)"
    }

    func refresh() async {
        let (_, out) = await run(["list-profiles"])
        if let data = out.data(using: .utf8),
           let decoded = try? JSONDecoder().decode([ProxyProfile].self, from: data) {
            profiles = decoded
            if selectedID == nil {
                selectedID = decoded.first?.id
            }
        } else {
            appendLog("Failed to load profiles: \(out)")
        }
        let (_, status) = await run(["status"])
        statusText = status.trimmingCharacters(in: .whitespacesAndNewlines)
        proxyEnabled = statusText.contains("Xray: running") && statusText.contains("Port 56542: listening")
    }

    func perform(_ title: String, _ args: [String], refreshAfter: Bool = true) async {
        busy = true
        appendLog("$ proxy_manager.py \(args.joined(separator: " "))")
        let (code, out) = await run(args)
        appendLog(out.trimmingCharacters(in: .whitespacesAndNewlines))
        if code != 0 {
            appendLog("[exit \(code)]")
            alertMessage = out.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? "Command failed with exit \(code)." : out
        }
        if refreshAfter { await refresh() }
        busy = false
    }

    func startSelected(systemProxy: Bool) async {
        guard let id = selectedID else { return }
        var args = ["start", "--profile", id]
        if !systemProxy { args.append("--no-system-proxy") }
        await perform("start", args)
    }

    func toggleProxy() async {
        if proxyEnabled {
            await perform("stop", ["stop", "--only-own-system-proxy"])
        } else {
            await startSelected(systemProxy: true)
        }
    }

    func importClipboard() async {
        guard let text = NSPasteboard.general.string(forType: .string), !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            appendLog("Clipboard is empty.")
            return
        }
        await perform("import clipboard", ["import-text", text, "--source", "clipboard"])
    }

    func importSubscriptionURL() async {
        let alert = NSAlert()
        alert.messageText = "导入 3x-ui 订阅"
        alert.informativeText = "粘贴订阅 URL，支持 base64 或明文 vmess/vless 列表。"
        alert.addButton(withTitle: "导入")
        alert.addButton(withTitle: "取消")
        let input = NSTextField(frame: NSRect(x: 0, y: 0, width: 460, height: 24))
        input.placeholderString = "https://..."
        alert.accessoryView = input
        if alert.runModal() == .alertFirstButtonReturn {
            let url = input.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
            if !url.isEmpty {
                await perform("import url", ["import-url", url])
            }
        }
    }

    func importQRImage() async {
        let panel = NSOpenPanel()
        panel.title = "选择二维码图片"
        panel.allowedContentTypes = [.png, .jpeg, .tiff, .gif, .bmp]
        panel.allowsMultipleSelection = false
        if panel.runModal() != .OK { return }
        guard let url = panel.url, let image = CIImage(contentsOf: url) else {
            appendLog("Could not read QR image.")
            return
        }
        let request = VNDetectBarcodesRequest()
        request.symbologies = [.qr]
        let handler = VNImageRequestHandler(ciImage: image, options: [:])
        do {
            try handler.perform([request])
            let payloads = (request.results ?? []).compactMap { $0.payloadStringValue }
            if let first = payloads.first {
                await perform("import qr", ["import-text", first, "--source", "qr"])
            } else {
                appendLog("No QR code found.")
            }
        } catch {
            appendLog("QR decode failed: \(error.localizedDescription)")
        }
    }

    func ping(_ profile: ProxyProfile) async {
        pings[profile.id] = "..."
        let (code, out) = await run(["ping-profile", profile.id])
        if let data = out.data(using: .utf8),
           let decoded = try? JSONDecoder().decode(PingResult.self, from: data),
           decoded.ok {
            pings[profile.id] = "\(decoded.ms)ms"
        } else {
            pings[profile.id] = "timeout"
            if code != 0 { appendLog("Ping \(profile.name): \(out.trimmingCharacters(in: .whitespacesAndNewlines))") }
        }
    }

    func pingAll(onProgress: (() -> Void)? = nil) async {
        busy = true
        for profile in profiles {
            await ping(profile)
            onProgress?()
        }
        busy = false
    }
}

struct ContentView: View {
    @ObservedObject var model: ProxyModel
    @Environment(\.colorScheme) private var colorScheme

    var appBackground: Color {
        colorScheme == .dark ? Color(red: 0.06, green: 0.08, blue: 0.12) : Color(red: 0.93, green: 0.95, blue: 0.98)
    }

    var sidebarBackground: Color {
        colorScheme == .dark ? Color(red: 0.08, green: 0.11, blue: 0.16) : Color(red: 0.98, green: 0.99, blue: 1.0)
    }

    var panelBackground: Color {
        colorScheme == .dark ? Color(red: 0.10, green: 0.13, blue: 0.19) : Color.white
    }

    var cardBackground: Color {
        colorScheme == .dark ? Color(red: 0.13, green: 0.17, blue: 0.24) : Color(red: 0.985, green: 0.99, blue: 1.0)
    }

    var headerBackground: Color {
        colorScheme == .dark ? Color(red: 0.08, green: 0.11, blue: 0.16) : Color(red: 0.965, green: 0.98, blue: 1.0)
    }

    var strokeColor: Color {
        colorScheme == .dark ? Color.white.opacity(0.10) : Color.black.opacity(0.08)
    }

    var body: some View {
        HStack(spacing: 0) {
            sidebar
            Divider()
            mainPanel
        }
        .frame(minWidth: 980, minHeight: 640)
        .background(appBackground)
        .alert("操作失败", isPresented: Binding(
            get: { model.alertMessage != nil },
            set: { if !$0 { model.alertMessage = nil } }
        )) {
            Button("好", role: .cancel) { model.alertMessage = nil }
        } message: {
            Text(model.alertMessage ?? "")
        }
    }

    var sidebar: some View {
        VStack(spacing: 18) {
            VStack(spacing: 8) {
                Image(systemName: "bolt.horizontal.circle.fill")
                    .font(.system(size: 46))
                    .foregroundStyle(.blue)
                Text("Proxy")
                    .font(.headline)
                    .foregroundStyle(.blue)
            }
            .padding(.top, 28)

            navButton("代理", icon: "rectangle.connected.to.line.below", tab: "proxies")
            navButton("导入", icon: "plus.square", tab: "import")
            navButton("日志", icon: "doc.text", tab: "logs")
            navButton("设置", icon: "gearshape", tab: "settings")
            Spacer()
            Text("Xray / 3x-ui")
                .font(.caption)
                .foregroundStyle(.secondary)
                .padding(.bottom, 20)
        }
        .frame(width: 168)
        .background(sidebarBackground)
    }

    func navButton(_ title: String, icon: String, tab: String) -> some View {
        Button {
            model.activeTab = tab
        } label: {
            HStack {
                Image(systemName: icon)
                Text(title)
                Spacer()
            }
            .padding(.horizontal, 18)
            .padding(.vertical, 12)
            .foregroundStyle(model.activeTab == tab ? .white : Color(nsColor: .secondaryLabelColor))
            .background(model.activeTab == tab ? Color.blue : Color.clear)
            .clipShape(RoundedRectangle(cornerRadius: 10))
        }
        .buttonStyle(.plain)
        .padding(.horizontal, 16)
    }

    var mainPanel: some View {
        VStack(spacing: 0) {
            header
            Divider()
            if model.activeTab == "proxies" { proxiesView }
            if model.activeTab == "import" { importView }
            if model.activeTab == "logs" { logsView }
            if model.activeTab == "settings" { settingsView }
        }
    }

    var header: some View {
        HStack {
            VStack(alignment: .leading, spacing: 4) {
                Text("策略组")
                    .font(.system(size: 28, weight: .bold))
                    .foregroundStyle(.blue)
                Text("HTTP 56542 / SOCKS 56543")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            Button("测速全部") { Task { await model.pingAll() } }
                .disabled(model.busy)
            Toggle(isOn: Binding(
                get: { model.proxyEnabled },
                set: { _ in Task { await model.toggleProxy() } }
            )) {
                Text("启动代理")
                    .font(.headline)
            }
            .toggleStyle(.switch)
            .disabled(model.selectedID == nil || model.busy)
        }
        .padding(24)
        .background(headerBackground)
    }

    var proxiesView: some View {
        ScrollView {
            VStack(spacing: 12) {
                ForEach(model.profiles) { profile in
                    profileRow(profile)
                }
            }
            .padding(24)
        }
        .background(appBackground)
    }

    func profileRow(_ profile: ProxyProfile) -> some View {
        let selected = model.selectedID == profile.id
        return HStack(spacing: 14) {
            VStack {
                Text(profile.protocol.uppercased())
                    .font(.caption.bold())
                    .rotationEffect(.degrees(-90))
                    .frame(width: 54, height: 54)
                    .foregroundStyle(selected ? .white : .blue)
            }
            .background(selected ? Color.blue : Color.blue.opacity(0.10))
            .clipShape(RoundedRectangle(cornerRadius: 8))

            VStack(alignment: .leading, spacing: 7) {
                HStack {
                    Text(profile.name).font(.headline)
                    sourceBadge(profile.source)
                }
                HStack(spacing: 8) {
                    tag(profile.server)
                    tag(":\(profile.port)")
                    tag(profile.network)
                    tag(profile.security)
                }
            }
            Spacer()
            Text(model.pings[profile.id] ?? "-")
                .font(.system(.body, design: .monospaced))
                .foregroundStyle((model.pings[profile.id] ?? "").contains("timeout") ? .red : .green)
                .frame(width: 86)
            Button {
                Task { await model.ping(profile) }
            } label: {
                Image(systemName: "speedometer")
            }
            Button {
                model.selectedID = profile.id
            } label: {
                Image(systemName: selected ? "checkmark.circle.fill" : "circle")
            }
        }
        .padding(14)
        .background(selected ? Color.accentColor.opacity(colorScheme == .dark ? 0.26 : 0.14) : cardBackground)
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(selected ? Color.blue : strokeColor, lineWidth: 1))
        .clipShape(RoundedRectangle(cornerRadius: 10))
        .shadow(color: Color.black.opacity(colorScheme == .dark ? 0.18 : 0.04), radius: 10, x: 0, y: 5)
    }

    func tag(_ text: String) -> some View {
        Text(text.isEmpty ? "none" : text)
            .font(.caption)
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(Color.accentColor.opacity(0.14))
            .foregroundStyle(.blue)
            .clipShape(Capsule())
    }

    func sourceBadge(_ text: String) -> some View {
        Text(text)
            .font(.caption2.bold())
            .padding(.horizontal, 7)
            .padding(.vertical, 3)
            .background(Color.green.opacity(0.16))
            .foregroundStyle(.green)
            .clipShape(Capsule())
    }

    var importView: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("导入配置").font(.title2.bold())
            HStack(spacing: 14) {
                importCard("剪贴板", icon: "doc.on.clipboard", text: "读取剪贴板中的 vmess/vless 或订阅链接") {
                    Task { await model.importClipboard() }
                }
                importCard("订阅 URL", icon: "link", text: "导入 3x-ui 订阅，支持 base64 列表") {
                    Task { await model.importSubscriptionURL() }
                }
                importCard("扫码/图片", icon: "qrcode.viewfinder", text: "选择二维码图片并识别节点链接") {
                    Task { await model.importQRImage() }
                }
            }
            Spacer()
        }
        .padding(24)
        .background(appBackground)
    }

    func importCard(_ title: String, icon: String, text: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 12) {
                Image(systemName: icon).font(.system(size: 32)).foregroundStyle(.blue)
                Text(title).font(.headline)
                Text(text).font(.caption).foregroundStyle(.secondary).multilineTextAlignment(.leading)
                Spacer()
            }
            .frame(maxWidth: .infinity, minHeight: 150, alignment: .leading)
            .padding(18)
            .background(cardBackground)
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(strokeColor))
            .clipShape(RoundedRectangle(cornerRadius: 12))
        }
        .buttonStyle(.plain)
    }

    var logsView: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("日志").font(.title2.bold())
            ScrollView {
                Text(model.logText.isEmpty ? "暂无日志。" : model.logText)
                    .font(.system(.body, design: .monospaced))
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding()
            }
            .background(Color(nsColor: .textBackgroundColor))
            .foregroundStyle(Color(nsColor: .labelColor))
            .clipShape(RoundedRectangle(cornerRadius: 10))
        }
        .padding(24)
        .background(appBackground)
    }

    var settingsView: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("设置").font(.title2.bold())
            HStack(spacing: 12) {
                Button("刷新状态") {
                    Task { await model.refresh(); model.appendLog(model.statusText) }
                }
                Button("测试出口") {
                    Task { await model.perform("test", ["test", "https://api.ipify.org"], refreshAfter: false) }
                }
                Button("关闭系统代理") {
                    Task { await model.perform("proxy off", ["proxy-off"]) }
                }
                Button("切回 Clash") {
                    Task { await model.perform("clash", ["proxy-clash"]) }
                }
            }
            Text(model.statusText)
                .font(.system(.body, design: .monospaced))
                .padding()
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(cardBackground)
                .clipShape(RoundedRectangle(cornerRadius: 10))
            Spacer()
        }
        .padding(24)
        .background(appBackground)
    }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    let model = ProxyModel()
    private var statusItem: NSStatusItem?
    private var window: NSWindow?
    private var speedTimer: Timer?
    private var lastTraffic: (rx: UInt64, tx: UInt64, time: Date)?

    private var managerPath: String {
        let executable = URL(fileURLWithPath: CommandLine.arguments[0]).standardizedFileURL
        let bundled = executable
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("Resources/proxy_manager.py")
            .path
        if FileManager.default.fileExists(atPath: bundled) { return bundled }
        return Bundle.main.bundleURL.deletingLastPathComponent().appendingPathComponent("proxy_manager.py").path
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        setupStatusItem()
        rebuildMenu()
        startSpeedTimer()
        Task { await model.refresh(); rebuildMenu() }
    }

    func applicationWillTerminate(_ notification: Notification) {
        _ = runManagerSync(["stop", "--only-own-system-proxy"])
    }

    private func setupStatusItem() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let button = statusItem?.button {
            button.image = NSImage(systemSymbolName: "bolt.horizontal.circle.fill", accessibilityDescription: "Proxy")
            button.image?.isTemplate = true
            button.title = model.showSpeed ? " \(model.speedText)" : ""
        }
    }

    private func startSpeedTimer() {
        speedTimer?.invalidate()
        speedTimer = Timer.scheduledTimer(withTimeInterval: 2.0, repeats: true) { [weak self] _ in
            Task { @MainActor in
                guard let self else { return }
                self.updateSpeedText()
            }
        }
        updateSpeedText()
    }

    private func updateSpeedText() {
        let sample = trafficSample()
        defer {
            lastTraffic = (sample.rx, sample.tx, Date())
        }
        guard let previous = lastTraffic else {
            statusItem?.button?.title = model.showSpeed ? " \(model.speedText)" : ""
            return
        }
        let now = Date()
        let dt = max(0.5, now.timeIntervalSince(previous.time))
        let down = Double(sample.rx >= previous.rx ? sample.rx - previous.rx : 0) / dt
        let up = Double(sample.tx >= previous.tx ? sample.tx - previous.tx : 0) / dt
        model.speedText = "↓\(formatBytes(down))/s ↑\(formatBytes(up))/s"
        statusItem?.button?.title = model.showSpeed ? " \(model.speedText)" : ""
    }

    private func trafficSample() -> (rx: UInt64, tx: UInt64) {
        var ptr: UnsafeMutablePointer<ifaddrs>?
        var rx: UInt64 = 0
        var tx: UInt64 = 0
        guard getifaddrs(&ptr) == 0, let first = ptr else { return (0, 0) }
        defer { freeifaddrs(ptr) }
        var cursor: UnsafeMutablePointer<ifaddrs>? = first
        while let item = cursor {
            let flags = Int32(item.pointee.ifa_flags)
            let isUp = (flags & IFF_UP) != 0
            let isLoopback = (flags & IFF_LOOPBACK) != 0
            if isUp && !isLoopback,
               item.pointee.ifa_addr.pointee.sa_family == UInt8(AF_LINK),
               let data = item.pointee.ifa_data {
                let networkData = data.assumingMemoryBound(to: if_data.self).pointee
                rx += UInt64(networkData.ifi_ibytes)
                tx += UInt64(networkData.ifi_obytes)
            }
            cursor = item.pointee.ifa_next
        }
        return (rx, tx)
    }

    private func formatBytes(_ bytes: Double) -> String {
        if bytes >= 1024 * 1024 {
            return String(format: "%.1fMB", bytes / 1024 / 1024)
        }
        if bytes >= 1024 {
            return String(format: "%.0fKB", bytes / 1024)
        }
        return String(format: "%.0fB", bytes)
    }

    private func rebuildMenu() {
        let menu = NSMenu()

        let header = NSMenuItem(title: "出站模式（\(model.outboundMode)）", action: nil, keyEquivalent: "")
        header.isEnabled = false
        menu.addItem(header)

        let modeMenu = NSMenu()
        addModeItem("全局连接", to: modeMenu)
        addModeItem("规则判断", to: modeMenu)
        addModeItem("直接连接", to: modeMenu)
        let modeItem = NSMenuItem(title: "模式切换", action: nil, keyEquivalent: "")
        modeItem.submenu = modeMenu
        menu.addItem(modeItem)

        menu.addItem(.separator())

        let nodesMenu = NSMenu()
        for profile in model.profiles {
            let ping = model.pings[profile.id] ?? "-"
            let item = NSMenuItem(title: "\(profile.name)    \(ping)", action: #selector(selectProfile(_:)), keyEquivalent: "")
            item.target = self
            item.representedObject = profile.id
            item.state = model.selectedID == profile.id ? .on : .off
            nodesMenu.addItem(item)
        }
        if model.profiles.isEmpty {
            let empty = NSMenuItem(title: "没有节点", action: nil, keyEquivalent: "")
            empty.isEnabled = false
            nodesMenu.addItem(empty)
        }
        let nodesItem = NSMenuItem(title: "选择节点", action: nil, keyEquivalent: "")
        nodesItem.submenu = nodesMenu
        menu.addItem(nodesItem)

        menu.addItem(.separator())

        let toggleProxy = menuItem("启动代理", action: #selector(toggleProxy), key: "s")
        toggleProxy.state = model.proxyEnabled ? .on : .off
        menu.addItem(toggleProxy)
        menu.addItem(menuItem("测速全部", action: #selector(pingAll), key: "t"))

        let speed = menuItem("显示实时速度", action: #selector(toggleSpeed), key: "")
        speed.state = model.showSpeed ? .on : .off
        menu.addItem(speed)

        menu.addItem(.separator())
        menu.addItem(menuItem("显示面板", action: #selector(showPanel), key: "d"))
        menu.addItem(menuItem("导入配置", action: #selector(showImport), key: "i"))
        menu.addItem(menuItem("切回 Clash", action: #selector(backToClash), key: "c"))
        menu.addItem(.separator())
        menu.addItem(menuItem("退出", action: #selector(quitApp), key: "q"))

        statusItem?.menu = menu
    }

    private func addModeItem(_ title: String, to menu: NSMenu) {
        let item = NSMenuItem(title: title, action: #selector(changeMode(_:)), keyEquivalent: "")
        item.target = self
        item.representedObject = title
        item.state = model.outboundMode == title ? .on : .off
        menu.addItem(item)
    }

    private func menuItem(_ title: String, action: Selector, key: String) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: action, keyEquivalent: key)
        item.target = self
        return item
    }

    private func ensureWindow() -> NSWindow {
        if let window { return window }
        let content = ContentView(model: model)
        let hosting = NSHostingController(rootView: content)
        let newWindow = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 980, height: 640),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        newWindow.title = "Codex Proxy Manager"
        newWindow.contentViewController = hosting
        newWindow.center()
        window = newWindow
        return newWindow
    }

    private func runManagerSync(_ args: [String]) -> String {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        process.arguments = [managerPath] + args
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = pipe
        do {
            try process.run()
            process.waitUntilExit()
            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            return String(data: data, encoding: .utf8) ?? ""
        } catch {
            return "Error: \(error.localizedDescription)"
        }
    }

    @objc private func showPanel() {
        let panel = ensureWindow()
        NSApp.activate(ignoringOtherApps: true)
        panel.makeKeyAndOrderFront(nil)
        Task { await model.refresh(); rebuildMenu() }
    }

    @objc private func showImport() {
        model.activeTab = "import"
        showPanel()
    }

    @objc private func selectProfile(_ sender: NSMenuItem) {
        model.selectedID = sender.representedObject as? String
        rebuildMenu()
    }

    @objc private func toggleProxy() {
        Task {
            await model.toggleProxy()
            rebuildMenu()
        }
    }

    @objc private func pingAll() {
        Task {
            await model.pingAll { [weak self] in
                self?.rebuildMenu()
            }
            rebuildMenu()
        }
    }

    @objc private func toggleSpeed() {
        model.showSpeed.toggle()
        statusItem?.button?.title = model.showSpeed ? " \(model.speedText)" : ""
        rebuildMenu()
    }

    @objc private func changeMode(_ sender: NSMenuItem) {
        guard let mode = sender.representedObject as? String else { return }
        model.outboundMode = mode
        if mode == "直接连接" {
            Task { await model.perform("direct", ["proxy-off"]); rebuildMenu() }
        } else if mode == "全局连接" {
            Task { await model.startSelected(systemProxy: true); rebuildMenu() }
        } else {
            Task { await model.perform("rule", ["proxy-on"]); rebuildMenu() }
        }
        rebuildMenu()
    }

    @objc private func backToClash() {
        Task {
            await model.perform("clash", ["proxy-clash"])
            rebuildMenu()
        }
    }

    @objc private func quitApp() {
        _ = runManagerSync(["stop", "--only-own-system-proxy"])
        NSApp.terminate(nil)
    }
}

@main
struct ProxyManagerNativeApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var appDelegate

    var body: some Scene {
        Settings {
            EmptyView()
        }
    }
}
