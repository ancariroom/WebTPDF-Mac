# EbookConverter - Multi-Site Ebook to PDF Tool

## 概要
EbookConverter は、対応する電子書籍ビューアーから高画質な画像を抽出し、精密なクロップ処理を経て、最終的に単一のPDFファイルへと変換するツールです。あわせて、`ebook-convert.js audiobook` から upstream の [DrewThomasson/ebook2audiobook](https://github.com/DrewThomasson/ebook2audiobook) を Docker 経由で起動する headless 導線も用意しています。

## 🏛 構成
- **core/**: 共通の画像処理、PDF結合、ブラウザ制御ロジック。
- **adapters/**: 各書籍サイト固有の操作ロジック。
- **bin/**: メインの実行コマンド。
- **scripts/**: PDF / audiobook の補助ランチャー。
- **40_Ops/output/**: 生成されたPDF / オーディオブックのデフォルト保存先。

## 🚀 実行手順

### 1. ブラウザの準備
変換対象の書籍サイトを Chromium（`--remote-debugging-port=9222`）で開き、ログインを完了させた状態にしてください。

### 2. PDFへの変換
標準の CLI を使用して実行します。

```bash
node 20_Backend/bin/ebook-convert.js --id 1241 --start 1
```

### 3. ローカルEPUB / 画像フォルダの余白カットPDF化
ローカルにある non-DRM EPUB や、スキャン画像フォルダを直接PDFにできます。外側の白/透明余白は自動で詰めます。

```bash
node 20_Backend/bin/ebook-convert.js local-pdf \
  --input "/path/to/book.epub" \
  --output "/path/to/book.pdf"
```

見開き画像フォルダは右ページ→左ページの順で1ページずつに分解できます。

```bash
node 20_Backend/bin/ebook-convert.js local-pdf \
  --input "/path/to/manga_volume" \
  --output "/path/to/manga_volume.pdf" \
  --split-spreads auto
```

- `--split-spreads never|auto|always`: 見開き分割。通常EPUBは `never`、見開き画像フォルダは `auto`。
- `--page-size 836:1200`: 分割後ページの縦横比。まんが単ページ化では既定値のままでよい。
- `--skip-blank-threshold 0.025`: 白い誘導ページや空白ページを半ページ単位で除外するしきい値。
- `--skip-hash <sha256>`: 特定の広告/募集ページなど、完全一致画像を除外したい場合に使う。

### 4. オーディオブック化
`ebook2audiobook` は別プロジェクトですが、このリポジトリからは `audiobook` サブコマンドで起動できます。内部では `run_ebook2audiobook.py` が Docker を立ち上げます。

```bash
node 20_Backend/bin/ebook-convert.js audiobook \
  --ebook "/path/to/book.epub" \
  --language jpn \
  --output-format m4b
```

- `--dry-run` で実際の Docker コマンドを確認できる
- `--image` で CPU / GPU イメージを切り替えられる
- デフォルト出力先は `40_Ops/output/audiobooks/`
- 追加オプションや運用例は `00_Docs/RUNBOOK_ebook2audiobook.md`
- 音声化対象は non-DRM の合法入手済み電子書籍に限定してください
- 直接 wrapper を叩く場合は `python3 20_Backend/scripts/run_ebook2audiobook.py ...` を使う

### 5. ブラウザUIで試す
PDF / EPUB をアップロードして、そのままブラウザで音声再生したいときはローカル UI を使えます。

```bash
python3 30_UI/audiobook_ui.py
```

起動後に `http://127.0.0.1:8765` を開き、`mp3` で試すとブラウザ再生しやすくなります。操作の詳細は `00_Docs/RUNBOOK_audiobook_ui.md` を参照してください。

### 6. Macアプリとして起動する
ネイティブ SwiftUI の `.app` から Audio Lab を起動できます。

```bash
zsh 30_UI/mac_app/build_mac_app.sh
open "40_Ops/apps/WebTPDF Mac.app"
```

アプリは Docker Desktop を直接起動し、ログ、出力先、音声再生を画面内で扱います。配布先のMacに Node / Python は不要です。詳細は `00_Docs/RUNBOOK_mac_app.md` を参照してください。

PDFライブラリの「フォルダーを選択」で表示する場所を変更できます。初期値はDesktopです。ダッシュボードの「最近のPDF」は、行ごとに削除するか「履歴を消去」でまとめて外せます。PDF本体はライブラリに残ります。音声モデルの選択・ダウンロードは音声変換、Ollamaの操作はローカルLLMにあります。ツールバーの「日本語」「English」で直接切り替えられます。

**English:** Use **Choose Folder** in **PDF Library** to browse another folder. Remove individual recent entries or use **Clear History** on the dashboard to hide them from the recent list. Your PDF files stay in the library. Audio Lab manages voices and Local LLM manages Ollama models. Select **日本語** or **English** directly in the toolbar.

長いPDFでまず声質だけ確認したい場合は、アプリ内の `短い範囲だけ試す（PDF先頭ページ）` をONにすると、先頭Nページだけの一時PDFを作って変換できます。

人に渡す `.dmg` / `.zip` を作る場合:

```bash
zsh 30_UI/mac_app/package_mac_app.sh
```

成果物は `40_Ops/dist/` に出力されます。

## 📝 開発・運用上の注意
- **アダプターの追加**: 新しいサイトに対応させる場合は、`adapters/` 配下に対応するアダプターを追加し、`ebook-convert.js` に登録してください。
- **クロップ設定**: ツールバーの高さなどが特殊な場合は、`core/image_processor.py` の `--top-crop` オプションを調整してください。

## Public release

- `../../PUBLIC_RELEASE_CHECKLIST.md`

## Working principles

- Think before coding
- Simplicity first
- Surgical changes
- Goal-driven execution

---
*最終更新: 2026-04-13*
*責任部門: Development / Research*
