# WebTPDF Mac

[English](#english) | [日本語](#日本語)

---

<a id="english"></a>

## English

**WebTPDF Mac** is a native macOS (SwiftUI) desktop application for turning web pages into clean PDFs, compressing PDFs, and converting documents into audiobooks locally or via cloud TTS APIs.

### Features

- **Dashboard**: Check runtime environment readiness at a glance, view recent PDFs, and jump directly to each workflow.
- **URL → PDF**:
  - Capture paginated web pages and fixed-layout viewers into a single merged PDF.
  - Supports authenticated pages via Google Chrome CDP (`--remote-debugging-port=9222`, launchable directly from the app).
  - Configurable page-turn direction (`←` / `→`), custom save folder, and an **execution queue** with automatic sequential processing and pause/resume controls.
- **PDF Squeezer (Compression)**:
  - Compress individual PDFs or entire folders in batch using PyMuPDF.
  - Three presets: **Light (150 dpi)**, **Balanced (120 dpi)**, and **Strong (96 dpi)**, saving either `*_compressed.pdf` or replacing originals in place.
- **Audio Lab (Audiobook Conversion)**:
  - Convert `.pdf`, `.epub`, `.txt`, and `.md` files into `.mp3` or `.m4b` audiobooks.
  - Supports **macOS `say`**, **Neural `edge-tts`** (free, no API key required), **OpenAI TTS** (`gpt-4o-mini-tts` / `tts-1`), and **ElevenLabs** (`eleven_multilingual_v2`).
- **PDF Library**: Browse PDFs in a selected folder, open them in Preview/Finder, or send them straight to Audio Lab.
- **AI Connections & Runtime Setup**:
  - Manage OpenAI / ElevenLabs TTS API keys and local **Ollama** models.
  - Built-in diagnostics and one-click install/update buttons for Python packages, Playwright Chromium, `ffmpeg`, and Ollama, plus in-app self-update (`git pull` & rebuild).

### Requirements

- **macOS 13.0+** with Xcode Command Line Tools (`swiftc`)
- **Python 3** (`requests`, `beautifulsoup4`, `pypdf`, `pymupdf`, `playwright`, `pillow`, optional `edge-tts`)
- **Google Chrome** (for CDP authenticated browser capture)
- **`ffmpeg`** (for MP3/M4B audio encoding)
- **Ollama** *(optional)* for local LLM management

### Quick Start

```bash
git clone https://github.com/nanonigit/WebTPDF-Mac.git
cd WebTPDF-Mac
zsh 30_UI/mac_app/build_mac_app.sh
open "40_Ops/apps/WebTPDF Mac.app"
```

Once launched, open **System → Runtime** to verify dependencies and install any missing tools with one click.

### Project Structure

```text
WebTPDF-Mac/
├── 20_Backend/
│   └── scripts/
│       ├── url_pdf_fetcher.py      # URL pagination & fixed-layout PDF capture
│       ├── pdf_squeezer.py         # PDF image/stream compression
│       ├── local_audiobook.py      # macOS say / edge-tts / OpenAI / ElevenLabs TTS
│       └── test_url_pdf_fetcher.py # Unit tests for URL PDF fetcher
├── 30_UI/
│   └── mac_app/
│       ├── Sources/                # SwiftUI macOS app source
│       ├── Resources/              # App icon assets
│       ├── build_mac_app.sh        # Build script -> 40_Ops/apps/WebTPDF Mac.app
│       └── package_mac_app.sh      # Release packaging script (.dmg / .zip)
└── 40_Ops/
    └── apps/                       # Built macOS .app bundle output (gitignored)
```

---

<a id="日本語"></a>

## 日本語

**WebTPDF Mac** は、Web ページの PDF 化、PDF の軽量化（圧縮）、および電子書籍・PDF の音声ブック（MP3 / M4B）変換をひとつの画面で行えるネイティブ macOS（SwiftUI）アプリです。

### 主な機能

- **ダッシュボード**: 実行環境の準備状況、最近の PDF 履歴、各機能へのショートカットを一覧表示します。
- **URL → PDF 作成**:
  - ページ送り型の Web コンテンツや固定レイアウトビューアーを 1 つの PDF に結合して保存します。
  - ログインが必要なサイト向けに、アプリ内から起動できる専用 Google Chrome（CDP `--remote-debugging-port=9222`）連携に対応しています。
  - ページ送り方向（`←` / `→`）の切り替え、保存先フォルダの指定、追加と同時に自動で順番に処理する **実行キュー（一時停止・再開対応）** を備えています。
- **PDF 圧縮 (Squeezer)**:
  - PyMuPDF を用いて、単体 PDF またはフォルダ内の全 PDF を一括で軽量化します。
  - **軽め (150 dpi・高画質)** / **標準 (120 dpi・推奨)** / **強め (96 dpi・最小)** の 3 段階プリセットを選択でき、別名保存（`*_compressed.pdf`）と上書き保存を切り替えられます。
- **音声変換 (Audio Lab)**:
  - `.pdf` / `.epub` / `.txt` / `.md` を `.mp3` または `.m4b` 形式のオーディオブックに変換します。
  - **macOS 標準音声 (`say`)**、**Neural `edge-tts`**（無料・API キー不要）、**OpenAI TTS**（`gpt-4o-mini-tts` / `tts-1`）、**ElevenLabs**（`eleven_multilingual_v2`）に対応しています。
- **PDF ライブラリ**: 指定フォルダ内の PDF 一覧表示、ファイル起動、Audio Lab への受け渡しができます。
- **AI 接続 & 実行環境管理**:
  - OpenAI / ElevenLabs の音声 API キー設定と、ローカル **Ollama** モデルの管理・テストが行えます。
  - 必要な Python パッケージ・Playwright・`ffmpeg`・Ollama の診断と導入／更新ボタン、およびアプリ自体のワンクリックアップデート機能を備えています。

### 動作要件

- **macOS 13.0 以降**（Xcode Command Line Tools / `swiftc`）
- **Python 3**（`requests`, `beautifulsoup4`, `pypdf`, `pymupdf`, `playwright`, `pillow`, 任意で `edge-tts`）
- **Google Chrome**（認証が必要なサイトの CDP 取得用）
- **`ffmpeg`**（MP3 / M4B 音声エンコード用）
- **Ollama**（任意：ローカル LLM 連携用）

### クイックスタート

```bash
git clone https://github.com/nanonigit/WebTPDF-Mac.git
cd WebTPDF-Mac
zsh 30_UI/mac_app/build_mac_app.sh
open "40_Ops/apps/WebTPDF Mac.app"
```

起動後、左メニューの **「システム」→「実行環境」** から必要なツールが揃っているか確認し、不足があれば画面内のボタンから導入できます。

### ディレクトリ構成

```text
WebTPDF-Mac/
├── 20_Backend/
│   └── scripts/
│       ├── url_pdf_fetcher.py      # URL ページ送り取得 & PDF 結合
│       ├── pdf_squeezer.py         # PDF 圧縮スクリプト
│       ├── local_audiobook.py      # macOS say / edge-tts / OpenAI / ElevenLabs 音声変換
│       └── test_url_pdf_fetcher.py # URL 取得の単体テスト
├── 30_UI/
│   └── mac_app/
│       ├── Sources/                # SwiftUI macOS アプリ本体
│       ├── Resources/              # アプリアイコン等
│       ├── build_mac_app.sh        # .app ビルドスクリプト
│       └── package_mac_app.sh      # 配布用パッケージ (.dmg / .zip) 作成スクリプト
└── 40_Ops/
    └── apps/                       # ビルド済み .app 出力先（Git 管理外）
```
