import AppKit
import AVFoundation
import PDFKit
import SwiftUI
import UniformTypeIdentifiers

@main
struct EbookConverterAudioLabApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var model = AppModel()

    var body: some Scene {
        WindowGroup {
            ContentView(model: model)
                .frame(minWidth: 980, minHeight: 680)
                .onAppear {
                    appDelegate.model = model
                    model.refreshDockerStatus()
                    model.refreshTTSModels()
                }
        }
        .commands {
            CommandGroup(replacing: .newItem) {}
        }
    }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    weak var model: AppModel?

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }

    func applicationWillTerminate(_ notification: Notification) {
        model?.cancelJob()
    }
}

@MainActor
final class AppModel: ObservableObject {
    enum RunState {
        case idle
        case running
        case completed
        case failed
    }

    enum OutputFormat: String, CaseIterable, Identifiable {
        case mp3
        case m4b

        var id: String { rawValue }
    }

    enum PageTurnDirection: String, CaseIterable, Identifiable {
        case left
        case right

        var id: String { rawValue }

        var label: String {
            switch self {
            case .left: return "← 左キー"
            case .right: return "→ 右キー"
            }
        }
    }

    enum AudioEngineMode: String, CaseIterable, Identifiable {
        case local
        case docker

        var id: String { rawValue }

        var label: String {
            switch self {
            case .local: return "Macローカル (Docker不要)"
            case .docker: return "Docker (ebook2audiobook)"
            }
        }
    }

    struct TTSModelItem: Identifiable, Decodable, Hashable {
        let id: String
        let name: String
        let engine: String
        let voice: String
        let language: String
        let installed: Bool
    }

    enum Device: String, CaseIterable, Identifiable {
        case automatic = ""
        case cpu = "CPU"
        case cuda = "CUDA"
        case rocm = "ROCM"
        case xpu = "XPU"
        case jetson = "JETSON"

        var id: String { rawValue }

        var label: String {
            rawValue.isEmpty ? "auto / CPU" : rawValue
        }
    }

    private struct UserVisibleError: LocalizedError {
        let message: String

        var errorDescription: String? {
            message
        }
    }

    @Published var ebookURL: URL?
    @Published var sourceURLText = ""
    @Published var pageTurnDirection: PageTurnDirection = .right
    @Published var audioEngineMode: AudioEngineMode = .local
    @Published var availableTTSModels: [TTSModelItem] = [
        TTSModelItem(id: "macos:Kyoko", name: "macOS標準: Kyoko (日本語・女性)", engine: "say", voice: "Kyoko", language: "jpn", installed: true),
        TTSModelItem(id: "edge:ja-JP-NanamiNeural", name: "Neural: Nanami (日本語・女性・高音質)", engine: "edge", voice: "ja-JP-NanamiNeural", language: "jpn", installed: false),
    ]
    @Published var selectedTTSModelID = "macos:Kyoko"
    @Published var customModelURLText = ""
    @Published var voiceURL: URL?
    @Published var customModelURL: URL?
    @Published var language = "jpn"
    @Published var outputFormat: OutputFormat = .mp3
    @Published var ttsEngine = ""
    @Published var device: Device = .automatic
    @Published var dockerImage = "athomasson2/ebook2audiobook:cpu"
    @Published var dryRun = false
    @Published var sampleMode = false
    @Published var samplePageCount = "8"

    @Published private(set) var runState: RunState = .idle
    @Published private(set) var statusMessage = "Idle"
    @Published private(set) var logText = "Ready."
    @Published private(set) var artifactURL: URL?
    @Published private(set) var fetchedPDFURL: URL?
    @Published private(set) var outputDirectory: URL?
    @Published private(set) var isPlaying = false
    @Published private(set) var isFetchingURL = false
    @Published private(set) var dockerReady = false
    @Published private(set) var dockerStatus = "Checking Docker..."
    @Published private(set) var elapsedText = "-"
    @Published private(set) var progressText = "推定中"
    @Published private(set) var remainingText = "推定中"
    @Published private(set) var progressFraction: Double?

    private var process: Process?
    private var fetchProcess: Process?
    private var player: AVPlayer?
    private var activeOutputDirectory: URL?
    private var activeDryRun = false
    private var cancelRequested = false
    private var jobStartedAt: Date?
    private var timerTask: Task<Void, Never>?
    private var preflightTask: Task<Void, Never>?
    private var fetchedFromSourceURL: String?

    private var applicationSupportRoot: URL {
        if let baseURL = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first {
            return baseURL.appendingPathComponent("EbookConverter Audio Lab", isDirectory: true)
        }
        return URL(fileURLWithPath: NSHomeDirectory(), isDirectory: true)
            .appendingPathComponent("Library", isDirectory: true)
            .appendingPathComponent("Application Support", isDirectory: true)
            .appendingPathComponent("EbookConverter Audio Lab", isDirectory: true)
    }

    private var outputRoot: URL {
        applicationSupportRoot
            .appendingPathComponent("output", isDirectory: true)
            .appendingPathComponent("audiobooks", isDirectory: true)
            .appendingPathComponent("native_jobs", isDirectory: true)
    }

    private var cacheRoot: URL {
        URL(fileURLWithPath: NSHomeDirectory(), isDirectory: true)
            .appendingPathComponent(".cache", isDirectory: true)
            .appendingPathComponent("ebookconverter", isDirectory: true)
            .appendingPathComponent("ebook2audiobook", isDirectory: true)
    }

    private var fetchedPDFRoot: URL {
        if let desktopURL = FileManager.default.urls(for: .desktopDirectory, in: .userDomainMask).first {
            return desktopURL
        }
        return URL(fileURLWithPath: NSHomeDirectory(), isDirectory: true)
            .appendingPathComponent("Desktop", isDirectory: true)
    }

    var trimmedSourceURL: String {
        sourceURLText.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    var canFetchURL: Bool {
        !trimmedSourceURL.isEmpty && !isRunning
    }

    var canRun: Bool {
        ebookURL != nil && !isRunning
    }

    var isRunning: Bool {
        process != nil || preflightTask != nil || fetchProcess != nil || isFetchingURL
    }

    func chooseEbook() {
        if let chosen = chooseFile(allowedFileTypes: ["pdf", "epub", "mobi", "azw3"], title: "Choose Ebook") {
            ebookURL = chosen
            fetchedFromSourceURL = nil
        }
    }

    func clearEbook() {
        ebookURL = nil
        fetchedPDFURL = nil
        fetchedFromSourceURL = nil
    }

    func openFetchedPDF() {
        guard let fetchedPDFURL else {
            return
        }
        NSWorkspace.shared.open(fetchedPDFURL)
    }

    func launchLoginBrowser() {
        let chromePath = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        guard FileManager.default.isExecutableFile(atPath: chromePath) else {
            fail("Google Chrome が見つかりませんでした: \(chromePath)")
            return
        }

        let home = ProcessInfo.processInfo.environment["HOME"] ?? NSHomeDirectory()
        let profileDir = URL(fileURLWithPath: home, isDirectory: true)
            .appendingPathComponent(".cache", isDirectory: true)
            .appendingPathComponent("ebookconverter-chrome", isDirectory: true)

        do {
            try FileManager.default.createDirectory(at: profileDir, withIntermediateDirectories: true)
        } catch {
            fail("Chrome プロファイルフォルダの作成に失敗しました: \(error.localizedDescription)")
            return
        }

        var arguments = [
            "--remote-debugging-port=9223",
            "--user-data-dir=\(profileDir.path)",
        ]
        if let parsed = URL(string: trimmedSourceURL),
           let scheme = parsed.scheme?.lowercased(),
           (scheme == "http" || scheme == "https"),
           parsed.host != nil {
            arguments.append(trimmedSourceURL)
        }

        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: chromePath)
        proc.arguments = arguments

        do {
            try proc.run()
            statusMessage = "ログイン用ブラウザ起動済み"
            appendLog("\nLaunched debuggable Chrome (--remote-debugging-port=9223).\nログイン後、「URLからPDF作成」を押してください。\n")
        } catch {
            fail("ログイン用ブラウザの起動に失敗しました: \(error.localizedDescription)")
        }
    }

    func chooseVoice() {
        voiceURL = chooseFile(allowedFileTypes: ["wav", "mp3", "m4a", "flac", "ogg"], title: "Choose Voice Sample")
    }

    func chooseCustomModel() {
        customModelURL = chooseFile(allowedFileTypes: ["zip"], title: "Choose Custom Model")
    }

    func clearVoice() {
        voiceURL = nil
    }

    func clearCustomModel() {
        customModelURL = nil
    }

    func refreshDockerStatus() {
        guard let docker = resolveDockerCommand() else {
            dockerReady = false
            dockerStatus = "Docker not found"
            return
        }

        dockerStatus = "Checking Docker..."
        Task { [weak self] in
            guard let self else {
                return
            }

            if let version = await self.dockerInfoVersion(dockerCommand: docker) {
                dockerReady = true
                dockerStatus = "Docker ready \(version)"
            } else {
                dockerReady = false
                dockerStatus = "Docker is installed but not ready"
            }
        }
    }

    func fetchPDFfromURL() {
        let urlString = trimmedSourceURL
        guard !urlString.isEmpty else {
            statusMessage = "ソースURLを入力してください。"
            return
        }

        cancelRequested = false
        runState = .running
        isFetchingURL = true
        statusMessage = "URLからPDFを取得中..."
        progressFraction = nil
        progressText = "取得中"
        remainingText = "-"
        logText = "Fetching PDF from URL: \(urlString)\n"
        startProgressClock()

        preflightTask = Task { [weak self] in
            guard let self else {
                return
            }

            do {
                let mergedURL = try await self.performURLFetch(urlString: urlString)
                self.preflightTask = nil
                self.isFetchingURL = false
                self.stopProgressClock()
                if self.cancelRequested {
                    self.runState = .idle
                    self.statusMessage = "Canceled"
                    return
                }
                self.ebookURL = mergedURL
                self.fetchedPDFURL = mergedURL
                self.fetchedFromSourceURL = urlString
                self.outputDirectory = mergedURL.deletingLastPathComponent()
                self.runState = .completed
                self.statusMessage = "PDF取得完了"
                self.progressText = "100%"
                self.remainingText = "00:00"
                self.appendLog("\nMerged PDF saved to: \(mergedURL.path)\n")
            } catch {
                self.preflightTask = nil
                self.isFetchingURL = false
                if self.cancelRequested {
                    self.finishCanceledPreflight()
                } else {
                    self.fail(error.localizedDescription)
                }
            }
        }
    }

    func startJob() {
        guard let ebookURL else {
            statusMessage = "先に電子書籍ファイルを選択するか、URLからPDFを作成してください。"
            return
        }

        if audioEngineMode == .local {
            startLocalAudiobookJob(ebookURL: ebookURL)
            return
        }

        startJobWithResolvedEbook(ebookURL)
    }

    func refreshTTSModels() {
        guard let pythonCommand = resolvePythonCommand(),
              let scriptURL = resolveLocalAudiobookScript()
        else {
            return
        }

        Task.detached { [weak self] in
            let proc = Process()
            proc.executableURL = URL(fileURLWithPath: pythonCommand)
            proc.arguments = [scriptURL.path, "--list-models"]
            let pipe = Pipe()
            proc.standardOutput = pipe
            proc.standardError = Pipe()
            do {
                try proc.run()
                let data = pipe.fileHandleForReading.readDataToEndOfFile()
                proc.waitUntilExit()
                guard proc.terminationStatus == 0 else { return }
                let decoded = try JSONDecoder().decode([TTSModelItem].self, from: data)
                await MainActor.run {
                    guard let self else { return }
                    self.availableTTSModels = decoded
                    if !decoded.contains(where: { $0.id == self.selectedTTSModelID }),
                       let first = decoded.first {
                        self.selectedTTSModelID = first.id
                    }
                }
            } catch {
                return
            }
        }
    }

    func downloadModelTarget(_ target: String) {
        let trimmed = target.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            statusMessage = "ダウンロードするモデルまたはURLを指定してください。"
            return
        }
        guard let pythonCommand = resolvePythonCommand(),
              let scriptURL = resolveLocalAudiobookScript()
        else {
            fail("python3 または local_audiobook.py が見つかりませんでした。")
            return
        }

        cancelRequested = false
        runState = .running
        statusMessage = "音声モデルをダウンロード中..."
        progressFraction = nil
        progressText = "DL中"
        remainingText = "-"
        logText = "Downloading TTS model: \(trimmed)\n"
        startProgressClock()

        runPythonSubprocess(
            pythonCommand: pythonCommand,
            arguments: [scriptURL.path, "--download-model", trimmed],
            workingDirectory: applicationSupportRoot
        ) { [weak self] exitCode in
            guard let self else { return }
            self.stopProgressClock()
            if exitCode == 0 {
                self.runState = .completed
                self.statusMessage = "モデルダウンロード完了"
                self.progressText = "100%"
                self.remainingText = "00:00"
                self.refreshTTSModels()
            } else {
                self.fail("モデルダウンロードに失敗しました (exit \(exitCode))。")
            }
        }
    }

    private func startLocalAudiobookJob(ebookURL: URL) {
        guard let pythonCommand = resolvePythonCommand(),
              let scriptURL = resolveLocalAudiobookScript()
        else {
            fail("python3 または local_audiobook.py が見つかりませんでした。")
            return
        }

        let jobID = makeJobID()
        let outputDirectory = outputRoot.appendingPathComponent(jobID, isDirectory: true)
        do {
            try prepareRuntimeDirectories(outputDirectory: outputDirectory)
        } catch {
            fail("Failed to prepare runtime directories: \(error.localizedDescription)")
            return
        }

        activeOutputDirectory = outputDirectory
        activeDryRun = dryRun
        cancelRequested = false
        artifactURL = nil
        self.outputDirectory = outputDirectory
        player = nil
        isPlaying = false

        let conversionEbookURL: URL
        do {
            conversionEbookURL = try prepareEbookForConversion(ebookURL: ebookURL, outputDirectory: outputDirectory)
        } catch {
            fail(error.localizedDescription)
            return
        }

        let arguments = [
            scriptURL.path,
            "--ebook", conversionEbookURL.path,
            "--output-dir", outputDirectory.path,
            "--language", language.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? "jpn" : language.trimmingCharacters(in: .whitespacesAndNewlines),
            "--output-format", outputFormat.rawValue,
            "--model", selectedTTSModelID,
        ]

        logText = "$ \(shellJoined([pythonCommand] + arguments))\n\n"
        runState = .running
        statusMessage = "ローカル音声変換中 (Docker不要)..."
        startProgressClock()

        if dryRun {
            finishJob(exitCode: 0)
            return
        }

        runPythonSubprocess(
            pythonCommand: pythonCommand,
            arguments: arguments,
            workingDirectory: applicationSupportRoot
        ) { [weak self] exitCode in
            self?.finishJob(exitCode: exitCode)
        }
    }

    private func runPythonSubprocess(
        pythonCommand: String,
        arguments: [String],
        workingDirectory: URL,
        onFinish: @escaping @MainActor (Int32) -> Void
    ) {
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: pythonCommand)
        proc.arguments = arguments
        proc.currentDirectoryURL = workingDirectory

        var environment = ProcessInfo.processInfo.environment
        environment["PATH"] = appPath()
        environment["PYTHONUNBUFFERED"] = "1"
        proc.environment = environment

        let pipe = Pipe()
        proc.standardOutput = pipe
        proc.standardError = pipe
        pipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty, let chunk = String(data: data, encoding: .utf8) else {
                return
            }
            Task { @MainActor in
                self?.appendLog(chunk)
            }
        }

        proc.terminationHandler = { [weak self] terminated in
            pipe.fileHandleForReading.readabilityHandler = nil
            Task { @MainActor in
                self?.process = nil
                onFinish(terminated.terminationStatus)
            }
        }

        do {
            try proc.run()
            self.process = proc
        } catch {
            fail("プロセスの起動に失敗しました: \(error.localizedDescription)")
        }
    }

    private func startJobWithResolvedEbook(_ ebookURL: URL) {

        let resolvedDockerCommand = resolveDockerCommand()
        guard dryRun || resolvedDockerCommand != nil else {
            fail("Docker not found. Install and start Docker Desktop, then press Docker再確認.")
            return
        }
        let dockerCommand = resolvedDockerCommand ?? "docker"

        if device != .automatic,
           device != .cpu,
           dockerImage.trimmingCharacters(in: .whitespacesAndNewlines) == "athomasson2/ebook2audiobook:cpu" {
            fail("--device \(device.rawValue) requires a matching GPU/runtime Docker image.")
            return
        }

        activeOutputDirectory = nil
        activeDryRun = dryRun
        cancelRequested = false
        artifactURL = nil
        outputDirectory = nil
        player = nil
        isPlaying = false

        if dryRun {
            startConversionJob(dockerCommand: dockerCommand, ebookURL: ebookURL)
            return
        }

        preflightTask?.cancel()
        runState = .running
        statusMessage = "Checking Docker..."
        progressText = "推定中"
        remainingText = "推定中"
        progressFraction = nil
        elapsedText = "-"
        logText = "Checking Docker Desktop...\n"

        preflightTask = Task { [weak self] in
            guard let self else {
                return
            }

            let ready = await self.waitForDockerReady(dockerCommand: dockerCommand)
            preflightTask = nil

            if Task.isCancelled || cancelRequested {
                finishCanceledPreflight()
                return
            }

            guard ready else {
                fail("Docker Desktop did not become ready. Start Docker Desktop, wait until it finishes starting, then try again.")
                return
            }

            startConversionJob(dockerCommand: dockerCommand, ebookURL: ebookURL)
        }
    }

    private func startConversionJob(dockerCommand: String, ebookURL: URL) {
        let jobID = makeJobID()
        let outputDirectory = outputRoot.appendingPathComponent(jobID, isDirectory: true)

        do {
            try prepareRuntimeDirectories(outputDirectory: outputDirectory)
        } catch {
            fail("Failed to prepare runtime directories: \(error.localizedDescription)")
            return
        }

        activeOutputDirectory = outputDirectory
        activeDryRun = dryRun
        cancelRequested = false
        artifactURL = nil
        self.outputDirectory = outputDirectory
        player = nil
        isPlaying = false

        let conversionEbookURL: URL
        do {
            conversionEbookURL = try prepareEbookForConversion(ebookURL: ebookURL, outputDirectory: outputDirectory)
        } catch {
            fail(error.localizedDescription)
            return
        }

        let command = buildCommand(dockerCommand: dockerCommand, ebookURL: conversionEbookURL, outputDirectory: outputDirectory)
        if logText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || logText == "Ready." {
            logText = "$ \(shellJoined(command))\n\n"
        } else {
            appendLog("\n$ \(shellJoined(command))\n\n")
        }
        runState = .running
        statusMessage = dryRun ? "Dry-run running..." : "Converting..."
        startProgressClock()

        if dryRun {
            finishJob(exitCode: 0)
            return
        }

        let process = Process()
        process.executableURL = URL(fileURLWithPath: command[0])
        process.arguments = Array(command.dropFirst())
        process.currentDirectoryURL = applicationSupportRoot

        var environment = ProcessInfo.processInfo.environment
        environment["PATH"] = appPath()
        environment["PYTHONUNBUFFERED"] = "1"
        environment["NODE_DISABLE_COLORS"] = "1"
        process.environment = environment

        let outputPipe = Pipe()
        process.standardOutput = outputPipe
        process.standardError = outputPipe
        outputPipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty, let chunk = String(data: data, encoding: .utf8) else {
                return
            }
            Task { @MainActor in
                self?.appendLog(chunk)
            }
        }

        process.terminationHandler = { [weak self] terminatedProcess in
            outputPipe.fileHandleForReading.readabilityHandler = nil
            Task { @MainActor in
                self?.finishJob(exitCode: terminatedProcess.terminationStatus)
            }
        }

        do {
            try process.run()
            self.process = process
        } catch {
            fail("Failed to start conversion: \(error.localizedDescription)")
        }
    }

    func cancelJob() {
        cancelRequested = true
        preflightTask?.cancel()
        preflightTask = nil
        isFetchingURL = false
        if let fetchProcess, fetchProcess.isRunning {
            fetchProcess.terminate()
        }
        if let process, process.isRunning {
            process.terminate()
        }
    }

    func togglePlayback() {
        guard let artifactURL else {
            return
        }

        if isPlaying {
            player?.pause()
            isPlaying = false
            return
        }

        player = AVPlayer(url: artifactURL)
        player?.play()
        isPlaying = true
    }

    func openArtifact() {
        guard let artifactURL else {
            return
        }
        NSWorkspace.shared.open(artifactURL)
    }

    func openOutputDirectory() {
        guard let outputDirectory else {
            NSWorkspace.shared.open(outputRoot)
            return
        }
        NSWorkspace.shared.open(outputDirectory)
    }

    private func chooseFile(allowedFileTypes: [String], title: String) -> URL? {
        let panel = NSOpenPanel()
        panel.title = title
        panel.allowsMultipleSelection = false
        panel.canChooseDirectories = false
        panel.canChooseFiles = true
        panel.allowedContentTypes = allowedFileTypes.map { UTType(filenameExtension: $0) ?? .data }
        return panel.runModal() == .OK ? panel.url : nil
    }

    private func buildCommand(dockerCommand: String, ebookURL: URL, outputDirectory: URL) -> [String] {
        let image = dockerImage.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            ? "athomasson2/ebook2audiobook:cpu"
            : dockerImage.trimmingCharacters(in: .whitespacesAndNewlines)
        let modelsDirectory = cacheRoot.appendingPathComponent("models", isDirectory: true)
        let tmpDirectory = cacheRoot.appendingPathComponent("tmp", isDirectory: true)

        var command = [
            dockerCommand,
            "run",
            "--rm",
            "-i",
            "-e",
            "USER=root",
        ]
        command += dockerDeviceArgs()
        command += mountArgs(hostURL: ebookURL.deletingLastPathComponent(), containerPath: "/mnt/ebook", readOnly: true)
        command += mountArgs(hostURL: outputDirectory, containerPath: "/app/audiobooks")
        command += mountArgs(hostURL: modelsDirectory, containerPath: "/app/models")
        command += mountArgs(hostURL: tmpDirectory, containerPath: "/app/tmp")

        if let voiceURL {
            command += mountArgs(hostURL: voiceURL.deletingLastPathComponent(), containerPath: "/mnt/voice", readOnly: true)
        }

        if let customModelURL {
            command += mountArgs(hostURL: customModelURL.deletingLastPathComponent(), containerPath: "/mnt/custom_model", readOnly: true)
        }

        command += [
            image,
            "--script_mode",
            "full_docker",
            "--headless",
            "--ebook",
            "/mnt/ebook/\(ebookURL.lastPathComponent)",
            "--language",
            language.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? "jpn" : language,
            "--output_dir",
            "/app/audiobooks",
            "--output_format",
            outputFormat.rawValue,
        ]

        let engine = ttsEngine.trimmingCharacters(in: .whitespacesAndNewlines)
        if !engine.isEmpty {
            command += ["--tts_engine", engine]
        }

        if device != .automatic {
            command += ["--device", device.rawValue]
        }

        if let voiceURL {
            command += ["--voice", "/mnt/voice/\(voiceURL.lastPathComponent)"]
        }

        if let customModelURL {
            command += ["--custom_model", "/mnt/custom_model/\(customModelURL.lastPathComponent)"]
        }

        return command
    }

    private func prepareEbookForConversion(ebookURL: URL, outputDirectory: URL) throws -> URL {
        guard sampleMode else {
            return ebookURL
        }

        guard ebookURL.pathExtension.lowercased() == "pdf" else {
            throw UserVisibleError(message: "短い範囲だけ試す機能はPDF入力だけに対応しています。")
        }

        let rawPageCount = samplePageCount.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let requestedPageCount = Int(rawPageCount), requestedPageCount > 0 else {
            throw UserVisibleError(message: "試用ページ数は1以上の整数で入力してください。")
        }

        return try makeSamplePDF(
            originalURL: ebookURL,
            outputDirectory: outputDirectory,
            requestedPageCount: requestedPageCount
        )
    }

    private func makeSamplePDF(originalURL: URL, outputDirectory: URL, requestedPageCount: Int) throws -> URL {
        guard let sourceDocument = PDFDocument(url: originalURL) else {
            throw UserVisibleError(message: "PDFを読み込めませんでした: \(originalURL.path)")
        }

        let availablePageCount = sourceDocument.pageCount
        guard availablePageCount > 0 else {
            throw UserVisibleError(message: "PDFにページがありません: \(originalURL.path)")
        }

        let pageCount = min(requestedPageCount, availablePageCount)
        let sampleDocument = PDFDocument()

        for pageIndex in 0..<pageCount {
            guard let sourcePage = sourceDocument.page(at: pageIndex),
                  let copiedPage = sourcePage.copy() as? PDFPage
            else {
                throw UserVisibleError(message: "PDFの\(pageIndex + 1)ページ目をコピーできませんでした。")
            }
            sampleDocument.insert(copiedPage, at: pageIndex)
        }

        let originalName = originalURL.deletingPathExtension().lastPathComponent
        let safeBaseName = originalName.replacingOccurrences(
            of: #"[^A-Za-z0-9._-]+"#,
            with: "_",
            options: .regularExpression
        )
        let fileName = "sample_first_\(pageCount)_pages_\(safeBaseName.isEmpty ? "ebook" : safeBaseName).pdf"
        let sampleURL = outputDirectory.appendingPathComponent(fileName)

        guard sampleDocument.write(to: sampleURL) else {
            throw UserVisibleError(message: "試用PDFを書き出せませんでした: \(sampleURL.path)")
        }

        appendLog("Sample mode: using first \(pageCount) of \(availablePageCount) PDF pages.\n")
        appendLog("Sample PDF: \(sampleURL.path)\n")
        return sampleURL
    }

    private func prepareRuntimeDirectories(outputDirectory: URL) throws {
        try FileManager.default.createDirectory(at: outputDirectory, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(
            at: cacheRoot.appendingPathComponent("models", isDirectory: true),
            withIntermediateDirectories: true
        )
        try FileManager.default.createDirectory(
            at: cacheRoot.appendingPathComponent("tmp", isDirectory: true),
            withIntermediateDirectories: true
        )
    }

    private func resolveDockerCommand() -> String? {
        findExecutable([
            "/usr/local/bin/docker",
            "/opt/homebrew/bin/docker",
            "/Applications/Docker.app/Contents/Resources/bin/docker",
        ])
    }

    private func resolvePythonCommand() -> String? {
        let home = ProcessInfo.processInfo.environment["HOME"] ?? NSHomeDirectory()
        return findExecutable([
            "\(home)/.pyenv/shims/python3",
            "/opt/homebrew/bin/python3",
            "/usr/local/bin/python3",
            "/usr/bin/python3",
        ])
    }

    private func resolveURLFetcherScript() -> URL? {
        if let resourceURL = Bundle.main.resourceURL?.appendingPathComponent("url_pdf_fetcher.py"),
           FileManager.default.isExecutableFile(atPath: resourceURL.path) || FileManager.default.fileExists(atPath: resourceURL.path) {
            return resourceURL
        }
        return nil
    }

    private func resolveLocalAudiobookScript() -> URL? {
        if let resourceURL = Bundle.main.resourceURL?.appendingPathComponent("local_audiobook.py"),
           FileManager.default.isExecutableFile(atPath: resourceURL.path) || FileManager.default.fileExists(atPath: resourceURL.path) {
            return resourceURL
        }
        return nil
    }

    private func performURLFetch(urlString: String) async throws -> URL {
        guard let parsed = URL(string: urlString),
              let scheme = parsed.scheme?.lowercased(),
              scheme == "http" || scheme == "https",
              parsed.host != nil
        else {
            throw UserVisibleError(message: "有効な http:// または https:// のURLを入力してください。")
        }

        guard let pythonCommand = resolvePythonCommand() else {
            throw UserVisibleError(message: "python3 が見つかりませんでした。")
        }

        guard let scriptURL = resolveURLFetcherScript() else {
            throw UserVisibleError(message: "url_pdf_fetcher.py がアプリ内に見つかりませんでした。")
        }

        try FileManager.default.createDirectory(at: fetchedPDFRoot, withIntermediateDirectories: true)
        let hostPart = (parsed.host ?? "web").replacingOccurrences(
            of: #"[^A-Za-z0-9._-]+"#,
            with: "_",
            options: .regularExpression
        )
        let outputURL = fetchedPDFRoot.appendingPathComponent("\(makeJobID())_\(hostPart).pdf")

        return try await withCheckedThrowingContinuation { continuation in
            let proc = Process()
            proc.executableURL = URL(fileURLWithPath: pythonCommand)
            proc.arguments = [
                scriptURL.path,
                "--url", urlString,
                "--output", outputURL.path,
                "--direction", pageTurnDirection.rawValue,
            ]
            proc.currentDirectoryURL = fetchedPDFRoot

            var environment = ProcessInfo.processInfo.environment
            environment["PATH"] = appPath()
            environment["PYTHONUNBUFFERED"] = "1"
            proc.environment = environment

            let pipe = Pipe()
            proc.standardOutput = pipe
            proc.standardError = pipe
            pipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
                let data = handle.availableData
                guard !data.isEmpty, let chunk = String(data: data, encoding: .utf8) else {
                    return
                }
                Task { @MainActor in
                    self?.appendLog(chunk)
                }
            }

            proc.terminationHandler = { [weak self] terminated in
                pipe.fileHandleForReading.readabilityHandler = nil
                Task { @MainActor in
                    self?.fetchProcess = nil
                    if terminated.terminationStatus == 0,
                       FileManager.default.fileExists(atPath: outputURL.path) {
                        continuation.resume(returning: outputURL)
                    } else if self?.cancelRequested == true {
                        continuation.resume(throwing: UserVisibleError(message: "Canceled"))
                    } else {
                        continuation.resume(throwing: UserVisibleError(message: "URLからのPDF取得に失敗しました (exit \(terminated.terminationStatus))。ログを確認してください。"))
                    }
                }
            }

            do {
                self.fetchProcess = proc
                try proc.run()
            } catch {
                self.fetchProcess = nil
                continuation.resume(throwing: UserVisibleError(message: "PDF取得プロセスの起動に失敗しました: \(error.localizedDescription)"))
            }
        }
    }

    private func waitForDockerReady(dockerCommand: String) async -> Bool {
        if let version = await dockerInfoVersion(dockerCommand: dockerCommand) {
            dockerReady = true
            dockerStatus = "Docker ready \(version)"
            appendLog("Docker ready \(version).\n")
            return true
        }

        dockerReady = false
        dockerStatus = "Starting Docker Desktop..."
        statusMessage = "Starting Docker Desktop..."
        appendLog("Docker is installed but the daemon is not running. Opening Docker Desktop...\n")
        openDockerDesktop()

        for attempt in 1...90 {
            if Task.isCancelled || cancelRequested {
                return false
            }

            try? await Task.sleep(for: .seconds(2))

            if let version = await dockerInfoVersion(dockerCommand: dockerCommand) {
                dockerReady = true
                dockerStatus = "Docker ready \(version)"
                statusMessage = "Docker ready"
                appendLog("Docker ready \(version).\n")
                return true
            }

            let elapsedSeconds = attempt * 2
            dockerStatus = "Waiting for Docker Desktop... \(elapsedSeconds)s"
            statusMessage = "Waiting for Docker Desktop..."
            if attempt == 1 || elapsedSeconds % 10 == 0 {
                appendLog("Still waiting for Docker Desktop... \(elapsedSeconds)s\n")
            }
        }

        dockerReady = false
        dockerStatus = "Docker is installed but not ready"
        return false
    }

    private func dockerInfoVersion(dockerCommand: String) async -> String? {
        let path = appPath()
        return await Task.detached(priority: .utility) {
            Self.runDockerInfoVersion(dockerCommand: dockerCommand, path: path)
        }.value
    }

    private nonisolated static func runDockerInfoVersion(dockerCommand: String, path: String) -> String? {
        let process = Process()
        let pipe = Pipe()
        let semaphore = DispatchSemaphore(value: 0)

        process.executableURL = URL(fileURLWithPath: dockerCommand)
        process.arguments = ["info", "--format", "{{.ServerVersion}}"]
        process.standardOutput = pipe
        process.standardError = pipe
        var environment = ProcessInfo.processInfo.environment
        environment["PATH"] = path
        process.environment = environment
        process.terminationHandler = { _ in
            semaphore.signal()
        }

        do {
            try process.run()
        } catch {
            return nil
        }

        if semaphore.wait(timeout: .now() + 5) == .timedOut {
            process.terminate()
            return nil
        }

        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        guard process.terminationStatus == 0 else {
            return nil
        }

        let version = String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return version.isEmpty ? nil : version
    }

    private func openDockerDesktop() {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/open")
        process.arguments = ["-ga", "Docker"]
        process.standardOutput = nil
        process.standardError = nil
        try? process.run()
    }

    private func dockerDeviceArgs() -> [String] {
        switch device {
        case .cuda:
            return ["--gpus", "all"]
        case .rocm:
            return ["--device=/dev/kfd", "--device=/dev/dri"]
        case .xpu:
            return ["--device=/dev/dri"]
        case .jetson:
            return ["--runtime", "nvidia"]
        case .automatic, .cpu:
            return []
        }
    }

    private func mountArgs(hostURL: URL, containerPath: String, readOnly: Bool = false) -> [String] {
        let suffix = readOnly ? ":ro" : ""
        return ["-v", "\(hostURL.path):\(containerPath)\(suffix)"]
    }

    private func finishJob(exitCode: Int32) {
        process = nil
        stopProgressClock()

        if cancelRequested {
            runState = .idle
            statusMessage = "Canceled"
            progressText = "-"
            remainingText = "-"
            appendLog("\nCanceled.\n")
            return
        }

        if exitCode != 0 {
            runState = .failed
            statusMessage = "Failed (exit \(exitCode))"
            remainingText = "-"
            appendLog("\nFailed with exit \(exitCode).\n")
            return
        }

        if activeDryRun {
            runState = .completed
            statusMessage = "Dry-run completed"
            progressText = "dry-run"
            remainingText = "-"
            appendLog("\nDry-run completed.\n")
            return
        }

        guard let activeOutputDirectory else {
            runState = .failed
            statusMessage = "Output directory was not recorded."
            return
        }

        if let artifact = findAudioArtifact(in: activeOutputDirectory) {
            artifactURL = artifact
            runState = .completed
            statusMessage = "Generated \(artifact.lastPathComponent)"
            progressFraction = 1
            progressText = "100%"
            remainingText = "00:00"
            return
        }

        runState = .failed
        statusMessage = "Completed, but no audio file was found."
        remainingText = "-"
    }

    private func findAudioArtifact(in directory: URL) -> URL? {
        let extensions = Set(["aac", "flac", "m4a", "m4b", "mp3", "ogg", "opus", "wav"])
        guard let enumerator = FileManager.default.enumerator(
            at: directory,
            includingPropertiesForKeys: [.contentModificationDateKey, .isRegularFileKey]
        ) else {
            return nil
        }

        var bestURL: URL?
        var bestDate = Date.distantPast

        for case let url as URL in enumerator {
            guard extensions.contains(url.pathExtension.lowercased()) else {
                continue
            }

            let values = try? url.resourceValues(forKeys: [.contentModificationDateKey, .isRegularFileKey])
            guard values?.isRegularFile == true else {
                continue
            }

            let date = values?.contentModificationDate ?? Date.distantPast
            if date > bestDate {
                bestDate = date
                bestURL = url
            }
        }

        return bestURL
    }

    private func findExecutable(_ candidates: [String]) -> String? {
        for candidate in candidates where FileManager.default.isExecutableFile(atPath: candidate) {
            return candidate
        }
        return nil
    }

    private func appPath() -> String {
        let home = ProcessInfo.processInfo.environment["HOME"] ?? NSHomeDirectory()
        return [
            "/usr/local/bin",
            "/opt/homebrew/bin",
            "\(home)/.pyenv/shims",
            "/usr/bin",
            "/bin",
            "/usr/sbin",
            "/sbin",
        ].joined(separator: ":")
    }

    private func makeJobID() -> String {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyyMMdd-HHmmss"
        return "\(formatter.string(from: Date()))-\(UUID().uuidString.prefix(8))"
    }

    private func appendLog(_ chunk: String) {
        logText += chunk
        if logText.count > 20000 {
            logText = String(logText.suffix(20000))
        }
        updateProgress(from: chunk)
        updateTimingLabels()
    }

    private func fail(_ message: String) {
        stopProgressClock()
        preflightTask?.cancel()
        preflightTask = nil
        process = nil
        runState = .failed
        statusMessage = message
        remainingText = "-"
        appendLog("\n\(message)\n")
    }

    private func finishCanceledPreflight() {
        stopProgressClock()
        preflightTask = nil
        process = nil
        runState = .idle
        statusMessage = "Canceled"
        progressText = "-"
        remainingText = "-"
        appendLog("\nCanceled.\n")
    }

    private func startProgressClock() {
        timerTask?.cancel()
        jobStartedAt = Date()
        elapsedText = "00:00"
        progressFraction = nil
        progressText = "推定中"
        remainingText = "推定中"

        timerTask = Task { [weak self] in
            while !Task.isCancelled {
                await MainActor.run {
                    self?.updateTimingLabels()
                }
                try? await Task.sleep(for: .seconds(1))
            }
        }
    }

    private func stopProgressClock() {
        timerTask?.cancel()
        timerTask = nil
        updateTimingLabels()
    }

    private func updateTimingLabels() {
        guard let jobStartedAt else {
            elapsedText = "-"
            return
        }

        let elapsed = max(0, Date().timeIntervalSince(jobStartedAt))
        elapsedText = formatDuration(elapsed)

        guard runState == .running else {
            return
        }

        guard let progressFraction, progressFraction > 0 else {
            progressText = "推定中"
            remainingText = "推定中"
            return
        }

        let clampedProgress = min(max(progressFraction, 0), 1)
        progressText = "\(Int((clampedProgress * 100).rounded()))%"

        if clampedProgress >= 1 {
            remainingText = "00:00"
            return
        }

        let estimatedTotal = elapsed / clampedProgress
        remainingText = formatDuration(max(0, estimatedTotal - elapsed))
    }

    private func updateProgress(from chunk: String) {
        if let sentenceProgress = lastSentenceProgress(in: chunk) {
            progressFraction = max(progressFraction ?? 0, sentenceProgress)
        }
    }

    private func lastSentenceProgress(in text: String) -> Double? {
        let pattern = #"(?m)(?:^|\r|\n)\s*([0-9]{1,3}(?:\.[0-9]+)?)%:\s*:?\s*([0-9]{1,7})/([0-9]{1,7})(?![0-9])"#
        guard let regex = try? NSRegularExpression(pattern: pattern) else {
            return nil
        }

        let nsRange = NSRange(text.startIndex..<text.endIndex, in: text)
        let matches = regex.matches(in: text, range: nsRange)
        for match in matches.reversed() {
            guard
                let percentRange = Range(match.range(at: 1), in: text),
                let currentRange = Range(match.range(at: 2), in: text),
                let totalRange = Range(match.range(at: 3), in: text),
                let percent = Double(text[percentRange]),
                let current = Double(text[currentRange]),
                let total = Double(text[totalRange]),
                total > 0,
                current >= 0,
                current <= total,
                (0...100).contains(percent)
            else {
                continue
            }

            let ratio = current / total
            return min(max(max(ratio, percent / 100), 0), 1)
        }

        return nil
    }

    private func formatDuration(_ seconds: TimeInterval) -> String {
        let totalSeconds = max(0, Int(seconds.rounded()))
        let hours = totalSeconds / 3600
        let minutes = (totalSeconds % 3600) / 60
        let seconds = totalSeconds % 60

        if hours > 0 {
            return String(format: "%d:%02d:%02d", hours, minutes, seconds)
        }
        return String(format: "%02d:%02d", minutes, seconds)
    }

    private func shellJoined(_ command: [String]) -> String {
        command.map(shellQuoted).joined(separator: " ")
    }

    private func shellQuoted(_ value: String) -> String {
        if value.range(of: #"^[A-Za-z0-9_./:=@%+-]+$"#, options: .regularExpression) != nil {
            return value
        }
        return "'\(value.replacingOccurrences(of: "'", with: "'\\''"))'"
    }
}

struct ContentView: View {
    enum Destination: String, CaseIterable, Identifiable {
        case urlToPDF
        case audioLab
        case logs

        var id: String { rawValue }

        var title: String {
            switch self {
            case .urlToPDF: return "URL → PDF 作成"
            case .audioLab: return "音声変換 (Audio Lab)"
            case .logs: return "環境・実行ログ"
            }
        }

        var symbol: String {
            switch self {
            case .urlToPDF: return "doc.text.magnifyingglass"
            case .audioLab: return "waveform"
            case .logs: return "terminal"
            }
        }
    }

    @ObservedObject var model: AppModel
    @State private var destination: Destination? = .urlToPDF

    var body: some View {
        NavigationSplitView {
            List(selection: $destination) {
                Section("ワークスペース") {
                    Label(Destination.urlToPDF.title, systemImage: Destination.urlToPDF.symbol)
                        .tag(Destination.urlToPDF)
                    Label(Destination.audioLab.title, systemImage: Destination.audioLab.symbol)
                        .tag(Destination.audioLab)
                }
                Section("システム") {
                    Label(Destination.logs.title, systemImage: Destination.logs.symbol)
                        .tag(Destination.logs)
                }
            }
            .navigationTitle("WebTPDF Mac")
            .navigationSplitViewColumnWidth(min: 195, ideal: 215, max: 260)
            .safeAreaInset(edge: .bottom) {
                VStack(alignment: .leading, spacing: 4) {
                    Label(
                        model.isRunning ? "処理中..." : model.statusMessage,
                        systemImage: model.isRunning ? "record.circle" : (model.dockerReady ? "checkmark.circle" : "pause.circle")
                    )
                    .font(.callout)
                    .lineLimit(1)
                    Text(model.audioEngineMode == .local ? "MacローカルTTS Ready (Docker不要)" : model.dockerStatus)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding()
            }
        } detail: {
            Group {
                switch destination ?? .urlToPDF {
                case .urlToPDF:
                    urlToPDFView
                case .audioLab:
                    audioLabView
                case .logs:
                    systemLogsView
                }
            }
            .navigationTitle((destination ?? .urlToPDF).title)
            .toolbar {
                ToolbarItemGroup {
                    if model.isRunning {
                        ProgressView()
                            .controlSize(.small)
                    }
                    Button {
                        model.launchLoginBrowser()
                    } label: {
                        Label("ログイン用ブラウザ", systemImage: "safari")
                    }
                    .disabled(model.isRunning)

                    Button {
                        model.refreshDockerStatus()
                    } label: {
                        Label("Docker再確認", systemImage: "arrow.clockwise")
                    }

                    Button {
                        model.openOutputDirectory()
                    } label: {
                        Label("出力フォルダ", systemImage: "folder")
                    }

                    if model.isRunning {
                        Button(role: .destructive) {
                            model.cancelJob()
                        } label: {
                            Label("停止", systemImage: "stop.fill")
                        }
                        .keyboardShortcut(".", modifiers: [.command])
                    }
                }
            }
        }
    }

    private var urlToPDFView: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                VStack(alignment: .leading, spacing: 8) {
                    Text("Webページから1枚のPDFを作成")
                        .font(.title2.bold())
                    Text("対象URLを開き、指定方向のカーソルキーでページ送りしながら全ページを結合してデスクトップに保存します。")
                        .foregroundStyle(.secondary)
                }

                HStack(spacing: 12) {
                    topologyCard("ソースURL", symbol: "globe", detail: model.trimmedSourceURL.isEmpty ? "未入力" : "入力済み")
                    Image(systemName: "chevron.right").foregroundStyle(.secondary)
                    topologyCard("ページ送り", symbol: model.pageTurnDirection == .left ? "arrow.left.square" : "arrow.right.square", detail: model.pageTurnDirection.label)
                    Image(systemName: "chevron.right").foregroundStyle(.secondary)
                    topologyCard("Desktop保存", symbol: "doc.richtext", detail: model.fetchedPDFURL?.lastPathComponent ?? "待機中")
                }
                .padding(20)
                .frame(maxWidth: .infinity)
                .background(.quaternary.opacity(0.35), in: RoundedRectangle(cornerRadius: 12))

                GroupBox("URL取得設定") {
                    VStack(alignment: .leading, spacing: 14) {
                        VStack(alignment: .leading, spacing: 6) {
                            Text("ソースURL（PDF / ページ送り対象サイト）")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            TextField("https://... (PDF / ページ送り対象URL)", text: $model.sourceURLText)
                                .textFieldStyle(.roundedBorder)
                        }

                        HStack(spacing: 12) {
                            Text("ページ送り方向")
                                .font(.callout)
                                .foregroundStyle(.secondary)
                            Picker("ページ送り方向", selection: $model.pageTurnDirection) {
                                ForEach(AppModel.PageTurnDirection.allCases) { dir in
                                    Text(dir.label).tag(dir)
                                }
                            }
                            .pickerStyle(.segmented)
                            .labelsHidden()
                            .frame(maxWidth: 220)
                            Spacer()
                        }

                        HStack(spacing: 10) {
                            Button("URLからPDF作成（Desktop保存）") {
                                model.fetchPDFfromURL()
                            }
                            .buttonStyle(.borderedProminent)
                            .disabled(!model.canFetchURL)

                            Button("ログイン用ブラウザを開く") {
                                model.launchLoginBrowser()
                            }
                            .disabled(model.isRunning)

                            if model.isRunning {
                                Button("キャンセル", role: .destructive) {
                                    model.cancelJob()
                                }
                            }
                        }

                        Text("認証が必要なサイト（O'Reilly等）は「ログイン用ブラウザを開く」からログイン後に実行してください。")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    .padding(8)
                }

                GroupBox("準備状況・保存結果") {
                    VStack(spacing: 0) {
                        StatusRow(title: "ステータス", value: model.statusMessage, symbol: "info.circle")
                        Divider()
                        StatusRow(title: "経過時間", value: model.elapsedText, symbol: "clock")
                        Divider()
                        StatusRow(title: "保存済みPDF", value: model.fetchedPDFURL?.path ?? "未作成", symbol: "doc.fill")
                    }
                    if model.isRunning {
                        ProgressView()
                            .padding(.top, 8)
                    }
                    if model.fetchedPDFURL != nil {
                        HStack {
                            Button("DesktopのPDFを開く") {
                                model.openFetchedPDF()
                            }
                            .buttonStyle(.bordered)
                            Spacer()
                        }
                        .padding(.top, 8)
                    }
                }

                logBox(minHeight: 180)
            }
            .padding(28)
        }
    }

    private var audioLabView: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                VStack(alignment: .leading, spacing: 8) {
                    Text("電子書籍をオーディオブックに変換")
                        .font(.title2.bold())
                    Text("ローカルの PDF / EPUB / MOBI または取得済みPDFを `ebook2audiobook` で音声化します。")
                        .foregroundStyle(.secondary)
                }

                inputGroup

                HStack {
                    Button("音声変換を開始 (mp3/m4b)") {
                        model.startJob()
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(!model.canRun)

                    Button("キャンセル") {
                        model.cancelJob()
                    }
                    .disabled(!model.isRunning)
                }

                GroupBox("変換結果") {
                    VStack(spacing: 0) {
                        StatusRow(title: "ステータス", value: model.statusMessage, symbol: "waveform")
                        Divider()
                        StatusRow(title: "経過 / 進捗 / 残り", value: "\(model.elapsedText) · \(model.progressText) · \(model.remainingText)", symbol: "gauge.with.dots.needle.33percent")
                        Divider()
                        StatusRow(title: "生成ファイル", value: model.artifactURL?.lastPathComponent ?? "-", symbol: "music.note")
                        Divider()
                        StatusRow(title: "出力フォルダ", value: model.outputDirectory?.path ?? "-", symbol: "folder")
                    }
                    if let progressFraction = model.progressFraction {
                        ProgressView(value: progressFraction)
                            .padding(.top, 8)
                    } else if model.isRunning {
                        ProgressView()
                            .padding(.top, 8)
                    }
                    HStack {
                        Button(model.isPlaying ? "停止" : "再生") {
                            model.togglePlayback()
                        }
                        .disabled(model.artifactURL == nil)

                        Button("ファイルを開く") {
                            model.openArtifact()
                        }
                        .disabled(model.artifactURL == nil)

                        Spacer()
                    }
                    .padding(.top, 8)
                }

                logBox(minHeight: 160)
            }
            .padding(28)
        }
    }

    private var systemLogsView: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                VStack(alignment: .leading, spacing: 8) {
                    Text("環境・実行ログ")
                        .font(.title2.bold())
                    Text("Docker 接続状態と直近の実行ログを確認します。")
                        .foregroundStyle(.secondary)
                }

                GroupBox("環境ステータス") {
                    VStack(spacing: 0) {
                        StatusRow(title: "Docker", value: model.dockerStatus, symbol: "shippingbox")
                        Divider()
                        StatusRow(title: "取得済みPDF", value: model.fetchedPDFURL?.path ?? "-", symbol: "doc.fill")
                        Divider()
                        StatusRow(title: "出力フォルダ", value: model.outputDirectory?.path ?? "-", symbol: "folder")
                    }
                }

                logBox(minHeight: 360)
            }
            .padding(28)
        }
    }

    private func topologyCard(_ title: String, symbol: String, detail: String) -> some View {
        VStack(spacing: 6) {
            Image(systemName: symbol)
                .font(.title2)
                .foregroundStyle(.tint)
            Text(title)
                .font(.headline)
            Text(detail)
                .font(.caption)
                .foregroundStyle(.secondary)
                .lineLimit(1)
                .truncationMode(.middle)
        }
        .frame(maxWidth: .infinity)
    }

    private func logBox(minHeight: CGFloat) -> some View {
        GroupBox("ログ") {
            ScrollView(.vertical, showsIndicators: true) {
                Text(model.logText)
                    .font(.system(.caption, design: .monospaced))
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(8)
            }
            .frame(minHeight: minHeight)
        }
    }

    private var inputGroup: some View {
        GroupBox("入力") {
            VStack(spacing: 12) {
                Picker("実行エンジン", selection: $model.audioEngineMode) {
                    ForEach(AppModel.AudioEngineMode.allCases) { mode in
                        Text(mode.label).tag(mode)
                    }
                }
                .pickerStyle(.segmented)

                FilePickerRow(
                    title: "電子書籍ファイル",
                    url: model.ebookURL,
                    buttonTitle: "選択",
                    action: { model.chooseEbook() },
                    clearAction: model.ebookURL == nil ? nil : { model.clearEbook() }
                )

                languageAndFormatRow

                if model.audioEngineMode == .local {
                    localModelSection
                } else {
                    TextField("TTS エンジン 任意", text: $model.ttsEngine)

                    Picker("デバイス", selection: $model.device) {
                        ForEach(AppModel.Device.allCases) { device in
                            Text(device.label).tag(device)
                        }
                    }

                    TextField("Docker イメージ", text: $model.dockerImage)

                    FilePickerRow(
                        title: "音声サンプル",
                        url: model.voiceURL,
                        buttonTitle: "選択",
                        action: { model.chooseVoice() },
                        clearAction: model.voiceURL == nil ? nil : { model.clearVoice() }
                    )

                    FilePickerRow(
                        title: "カスタムモデル",
                        url: model.customModelURL,
                        buttonTitle: "選択",
                        action: { model.chooseCustomModel() },
                        clearAction: model.customModelURL == nil ? nil : { model.clearCustomModel() }
                    )
                }

                sampleOptions

                Toggle("dry-run", isOn: $model.dryRun)
            }
            .padding(8)
        }
    }

    private var localModelSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                Picker("音声モデル", selection: $model.selectedTTSModelID) {
                    ForEach(model.availableTTSModels) { item in
                        Text(item.installed ? "\(item.name) [済]" : "\(item.name) [未DL]")
                            .tag(item.id)
                    }
                }

                Button("モデルDL") {
                    model.downloadModelTarget(model.selectedTTSModelID)
                }
                .disabled(model.isRunning)
            }

            HStack(spacing: 8) {
                TextField("新しいモデルURL (https://... .zip / .onnx 等)", text: $model.customModelURLText)
                    .textFieldStyle(.roundedBorder)

                Button("URLからモデルDL") {
                    model.downloadModelTarget(model.customModelURLText)
                }
                .disabled(model.isRunning || model.customModelURLText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .padding(10)
        .background(Color(nsColor: .controlBackgroundColor))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private var languageAndFormatRow: some View {
        HStack(spacing: 12) {
            TextField("jpn", text: $model.language)
                .frame(maxWidth: .infinity)

            Picker("出力形式", selection: $model.outputFormat) {
                ForEach(AppModel.OutputFormat.allCases) { format in
                    Text(format.rawValue).tag(format)
                }
            }
            .frame(maxWidth: .infinity)
        }
    }

    private var sampleOptions: some View {
        VStack(alignment: .leading, spacing: 8) {
            Toggle("短い範囲だけ試す（PDF先頭ページ）", isOn: $model.sampleMode)

            HStack(spacing: 8) {
                Text("先頭")
                    .foregroundStyle(.secondary)
                TextField("8", text: $model.samplePageCount)
                    .frame(width: 70)
                    .disabled(!model.sampleMode)
                Text("ページだけ変換")
                    .foregroundStyle(.secondary)
            }

            Text("ONの場合、元PDFは変更せず、一時PDFを出力フォルダ内に作って変換します。")
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(10)
        .background(Color(nsColor: .controlBackgroundColor))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

struct StatusRow: View {
    let title: String
    let value: String
    let symbol: String

    var body: some View {
        HStack {
            Label(title, systemImage: symbol)
            Spacer()
            Text(value)
                .foregroundStyle(.secondary)
                .lineLimit(1)
                .truncationMode(.middle)
                .textSelection(.enabled)
        }
        .padding(.vertical, 8)
        .padding(.horizontal, 4)
    }
}

struct FilePickerRow: View {
    let title: String
    let url: URL?
    let buttonTitle: String
    let action: () -> Void
    var clearAction: (() -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title)
                .font(.caption)
                .foregroundStyle(.secondary)
            HStack {
                Button(buttonTitle, action: action)
                Text(url?.path ?? "未選択")
                    .lineLimit(1)
                    .truncationMode(.middle)
                    .foregroundStyle(url == nil ? .secondary : .primary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                if let clearAction {
                    Button("クリア", action: clearAction)
                }
            }
        }
    }
}

struct KeyValueRow: View {
    let title: String
    let value: String

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title)
                .font(.caption)
                .foregroundStyle(.secondary)
            Text(value)
                .lineLimit(2)
                .truncationMode(.middle)
                .textSelection(.enabled)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(10)
        .background(Color(nsColor: .controlBackgroundColor))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}
