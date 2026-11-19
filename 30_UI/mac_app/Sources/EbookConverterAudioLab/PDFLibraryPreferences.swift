import Foundation

struct PDFLibraryPreferences {
    var defaults: UserDefaults = .standard
    private let folderKey = "pdfLibrary.folder"
    private let hiddenKey = "pdfLibrary.hiddenRecentPaths"

    var folder: URL {
        if let path = defaults.string(forKey: folderKey), path.hasPrefix("/") {
            return URL(fileURLWithPath: path, isDirectory: true).standardizedFileURL
        }
        return FileManager.default.urls(for: .desktopDirectory, in: .userDomainMask).first
            ?? FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Desktop", isDirectory: true)
    }

    var hiddenRecentPaths: Set<String> {
        Set(defaults.stringArray(forKey: hiddenKey) ?? [])
    }

    func saveFolder(_ folder: URL) {
        defaults.set(folder.standardizedFileURL.path, forKey: folderKey)
    }

    func saveHiddenRecentPaths(_ paths: Set<String>) {
        defaults.set(paths.sorted(), forKey: hiddenKey)
    }

    static func pdfs(in folder: URL) throws -> [URL] {
        let keys: Set<URLResourceKey> = [.isRegularFileKey, .contentModificationDateKey]
        let contents = try FileManager.default.contentsOfDirectory(
            at: folder, includingPropertiesForKeys: Array(keys), options: [.skipsHiddenFiles]
        )
        let entries = contents.compactMap { url -> (url: URL, modified: Date)? in
            guard url.pathExtension.lowercased() == "pdf",
                  let values = try? url.resourceValues(forKeys: keys),
                  values.isRegularFile == true else { return nil }
            return (url.standardizedFileURL, values.contentModificationDate ?? .distantPast)
        }
        return entries.sorted {
            if $0.modified != $1.modified { return $0.modified > $1.modified }
            return $0.url.path < $1.url.path
        }.map(\.url)
    }
}
