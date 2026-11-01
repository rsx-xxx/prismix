import AVFoundation
import Network
import SwiftUI
import WebKit

let port = 8443

struct Host: Identifiable, Hashable {
    let id: String
    let name: String
    let addr: String
}

/// The page is served with the host's self-signed certificate: trust it for the one host the user picked, nothing else.
final class Trust: NSObject, URLSessionDelegate, WKNavigationDelegate {
    var host = ""
    func accept(_ ch: URLAuthenticationChallenge, _ done: (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        if ch.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust,
           ch.protectionSpace.host == host, let t = ch.protectionSpace.serverTrust {
            done(.useCredential, URLCredential(trust: t))
        } else { done(.performDefaultHandling, nil) }
    }
    func urlSession(_ s: URLSession, didReceive ch: URLAuthenticationChallenge, completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) { accept(ch, completionHandler) }
    func webView(_ w: WKWebView, didReceive ch: URLAuthenticationChallenge, completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) { accept(ch, completionHandler) }
}

@MainActor final class Model: ObservableObject {
    @Published var hosts: [Host] = []
    @Published var current: Host?
    @Published var busy = false
    @Published var error = ""
    let trust = Trust()
    private var browser: NWBrowser?
    private var seen = Set<String>()

    init() { if let a = UserDefaults.standard.string(forKey: "host") { Task { await connect(a) } } }

    /// Prismix announces `_prismix._tcp` on the LAN; each hit is resolved to an address and asked who it is.
    func discover() {
        browser?.cancel(); hosts = []; seen = []
        let b = NWBrowser(for: .bonjour(type: "_prismix._tcp", domain: nil), using: .tcp)
        b.browseResultsChangedHandler = { [weak self] results, _ in
            let eps = results.map(\.endpoint)
            Task { @MainActor in for e in eps { self?.resolve(e) } }
        }
        b.start(queue: .main)
        browser = b
    }

    private func resolve(_ ep: NWEndpoint) {
        let c = NWConnection(to: ep, using: .tcp)
        c.stateUpdateHandler = { [weak self] st in
            guard case .ready = st else { return }
            if case let .hostPort(h, _)? = c.currentPath?.remoteEndpoint {
                var a = "\(h)"
                if let i = a.firstIndex(of: "%") { a = String(a[..<i]) }
                Task { @MainActor in await self?.probe(a) }
            }
            c.cancel()
        }
        c.start(queue: .main)
    }

    private func probe(_ addr: String) async {
        guard let h = await hello(addr), seen.insert(h.id).inserted else { return }
        hosts.append(h)
    }

    func hello(_ addr: String) async -> Host? {
        trust.host = addr
        guard let url = URL(string: "https://\(addr):\(port)/remote/hello") else { return nil }
        let s = URLSession(configuration: .ephemeral, delegate: trust, delegateQueue: nil)
        var req = URLRequest(url: url); req.timeoutInterval = 4
        guard let (data, _) = try? await s.data(for: req),
              let j = try? JSONSerialization.jsonObject(with: data) as? [String: Any], j["app"] as? String == "prismix"
        else { return nil }
        return Host(id: j["id"] as? String ?? addr, name: j["name"] as? String ?? addr, addr: addr)
    }

    func connect(_ raw: String) async {
        let a = Model.address(raw)
        busy = true; error = ""
        defer { busy = false }
        guard !a.isEmpty, let h = await hello(a) else { error = "No Prismix found at \(a.isEmpty ? "that address" : a)."; return }
        UserDefaults.standard.set(a, forKey: "host")
        current = h
    }

    func leave() { UserDefaults.standard.removeObject(forKey: "host"); current = nil; discover() }

    /// Accepts an IP, a host name, or a scanned link: https://host:8443/remote, prismix://host/remote (`?ip=` wins for .local names).
    static func address(_ s: String) -> String {
        let t = s.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let u = URLComponents(string: t.contains("://") ? t : "https://\(t)") else { return "" }
        if let ip = u.queryItems?.first(where: { $0.name == "ip" })?.value, !ip.isEmpty { return ip }
        return u.host ?? ""
    }
}

@main struct PrismixApp: App {
    @StateObject private var model = Model()
    var body: some Scene {
        WindowGroup {
            Group {
                if let h = model.current { PlayerView(host: h, trust: model.trust, leave: model.leave).ignoresSafeArea() }
                else { ConnectView().environmentObject(model) }
            }
            .onOpenURL { u in Task { await model.connect(u.absoluteString) } }
            .task { try? AVAudioSession.sharedInstance().setCategory(.playback, mode: .default); try? AVAudioSession.sharedInstance().setActive(true) }
        }
    }
}

/// iPhone: one column. iPad (regular width): computers in a sidebar, connect options beside them.
struct ConnectView: View {
    @EnvironmentObject var model: Model
    @Environment(\.horizontalSizeClass) private var size
    @State private var typed = ""
    @State private var scanning = false
    @State private var pick: Host?

    var body: some View {
        if size == .regular {
            NavigationSplitView {
                hostList.navigationTitle("Prismix")
            } detail: {
                ScrollView { options.frame(maxWidth: 480).padding(32) }
            }
            .task { model.discover() }
        } else {
            NavigationStack {
                List { Section("Computers on this Wi-Fi") { rows }; Section("Other ways") { options } }
                    .navigationTitle("Prismix")
            }
            .task { model.discover() }
        }
    }

    private var hostList: some View {
        List(selection: $pick) { rows }
            .onChange(of: pick) { _, h in if let h { Task { await model.connect(h.addr) } } }
            .overlay { if model.hosts.isEmpty { ContentUnavailableView("Looking for Prismix", systemImage: "wifi", description: Text("Open Prismix on your computer, on the same Wi-Fi.")) } }
    }

    @ViewBuilder private var rows: some View {
        ForEach(model.hosts) { h in
            Button { Task { await model.connect(h.addr) } } label: { Label(h.name, systemImage: "desktopcomputer") }.tag(h)
        }
    }

    @ViewBuilder private var options: some View {
        VStack(alignment: .leading, spacing: 14) {
            Button { scanning = true } label: { Label("Scan the QR code", systemImage: "qrcode.viewfinder").frame(maxWidth: .infinity) }.buttonStyle(.borderedProminent).controlSize(.large)
            TextField("Address, like 192.168.1.20", text: $typed).textFieldStyle(.roundedBorder).keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                .onSubmit { Task { await model.connect(typed) } }
            Button("Connect") { Task { await model.connect(typed) } }.buttonStyle(.bordered).disabled(typed.isEmpty || model.busy)
            if model.busy { ProgressView() }
            if !model.error.isEmpty { Text(model.error).foregroundStyle(.red).font(.footnote) }
            Button("Search again") { model.discover() }
        }
        .sheet(isPresented: $scanning) {
            Scanner { code in scanning = false; Task { await model.connect(code) } }.ignoresSafeArea()
        }
    }
}

struct Scanner: UIViewControllerRepresentable {
    let found: (String) -> Void
    func makeUIViewController(context: Context) -> ScanController { ScanController(found) }
    func updateUIViewController(_ c: ScanController, context: Context) {}
}

final class ScanController: UIViewController, AVCaptureMetadataOutputObjectsDelegate {
    private let session = AVCaptureSession()
    private let found: (String) -> Void
    private var done = false
    init(_ f: @escaping (String) -> Void) { found = f; super.init(nibName: nil, bundle: nil) }
    required init?(coder: NSCoder) { nil }
    override func viewDidLoad() {
        super.viewDidLoad()
        guard let dev = AVCaptureDevice.default(for: .video), let input = try? AVCaptureDeviceInput(device: dev), session.canAddInput(input) else { return }
        session.addInput(input)
        let out = AVCaptureMetadataOutput()
        session.addOutput(out)
        out.setMetadataObjectsDelegate(self, queue: .main)
        out.metadataObjectTypes = [.qr]
        let layer = AVCaptureVideoPreviewLayer(session: session)
        layer.videoGravity = .resizeAspectFill
        layer.frame = view.bounds
        view.layer.addSublayer(layer)
        Task.detached { [session] in session.startRunning() }
    }
    override func viewDidLayoutSubviews() { super.viewDidLayoutSubviews(); view.layer.sublayers?.first?.frame = view.bounds }
    func metadataOutput(_ o: AVCaptureMetadataOutput, didOutput objs: [AVMetadataObject], from: AVCaptureConnection) {
        guard !done, let s = (objs.first as? AVMetadataMachineReadableCodeObject)?.stringValue else { return }
        done = true; found(s)
    }
}

/// The phone page, served by the computer. `PrismixShell` is what the page looks for to know it runs inside the app.
struct PlayerView: UIViewRepresentable {
    let host: Host
    let trust: Trust
    let leave: () -> Void

    func makeCoordinator() -> Coordinator { Coordinator(leave) }

    func makeUIView(context: Context) -> WKWebView {
        trust.host = host.addr
        let cfg = WKWebViewConfiguration()
        cfg.allowsInlineMediaPlayback = true
        cfg.mediaTypesRequiringUserActionForPlayback = []
        cfg.allowsAirPlayForMediaPlayback = true
        let name = UIDevice.current.userInterfaceIdiom == .pad ? "iPad" : "iPhone"
        let js = """
        window.PrismixShell = { deviceName: () => '\(name)', tap: () => webkit.messageHandlers.prismix.postMessage('tap'),
          switchHost: () => webkit.messageHandlers.prismix.postMessage('switch'), setBars: () => {} };
        document.documentElement.dataset.platform = '\(name == "iPad" ? "ipad" : "ios")';
        """
        cfg.userContentController.addUserScript(WKUserScript(source: js, injectionTime: .atDocumentStart, forMainFrameOnly: true))
        cfg.userContentController.add(context.coordinator, name: "prismix")
        let w = WKWebView(frame: .zero, configuration: cfg)
        w.navigationDelegate = trust
        w.scrollView.contentInsetAdjustmentBehavior = .never
        w.scrollView.bounces = false
        w.isOpaque = false
        w.backgroundColor = UIColor(red: 0.07, green: 0.07, blue: 0.06, alpha: 1)
        w.load(URLRequest(url: URL(string: "https://\(host.addr):\(port)/remote")!))
        return w
    }
    func updateUIView(_ w: WKWebView, context: Context) {}

    final class Coordinator: NSObject, WKScriptMessageHandler {
        let leave: () -> Void
        init(_ l: @escaping () -> Void) { leave = l }
        func userContentController(_ c: WKUserContentController, didReceive m: WKScriptMessage) {
            switch m.body as? String {
            case "tap": UIImpactFeedbackGenerator(style: .light).impactOccurred()
            case "switch": leave()
            default: break
            }
        }
    }
}
