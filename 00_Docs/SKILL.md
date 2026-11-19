---
name: Ebook to PDF Specialist
description: 電子書籍ビューアーを1ページずつスクリーンショット・クロップ・PDF化する特化型スキル。Playwrightでの自動ページめくりと、精密な背景除去を組み合わせて高品質なPDFを生成します。
---

# Ebook to PDF Specialist

Apryse WebViewerベースの電子書籍ビューアーから、高画質なPDFを自動生成するためのスキルです。

## 🚀 ワークフロー概要

1. **Scraping**: Playwrightを使用して、指定されたURLから1ページずつスクリーンショットを取得。
2. **Cropping**: `Column-Max Intensity分析`（Version 13）を使用して、ページ外側の灰色背景やサイドバーを完全に除去。
3. **Assembly**: Pillowを使用して、全てのページを1つのPDFに結合。

## 📂 構成

- `scripts/export_pdf.sh`: 開いているビューアーから Apryse WebViewer 経由で PDF 本体を直接抜き出す最短入口。
- `scripts/export_pdf.js`: CDP 接続 + `getFileData({ downloadType: 'pdf' })` で PDF を直接保存する実装。
- `scripts/make_pdf.js`: 開いている本を `bookId` で特定し、raw/cropped/pdf まで一気に実行する入口。
- `scripts/scraper.js`: Playwrightボット（`ArrowDown` でページ移動）。
- `scripts/crop_pages.py`: 精密クロップユーティリティ（1145px/2740px検知）。
- `scripts/create_pdf.py`: PDF結合スクリプト。
- `KNOWLEDGE_v1.2.md`: 失敗パターンと安定化ルールの知識ベース。
- `RUNBOOK_resume_capture.md`: 中断後に途中ページから再開するための実行手順。
- `PROMPT_TEMPLATE.md`: Antigravity向けの実行テンプレート。

## 📖 使い方 (AIエージェント向け)

### 1. スクリーンショットの取得
> [!CAUTION]
> **重要: ブラウザの操作制限**
> すでにユーザーがログインして電子テキストを開いている場合、絶対に `open_browser_url` やページのリロードを行わないでください。これを行うと、ログインセッションが切れたり、閲覧中のページ位置がリセットされたりして、作業が中断されます。
> 既存のタブの状態を維持し、`read_browser_page` や `capture_browser_screenshot`、およびスクリプトによる操作のみを行ってください。

> [!CAUTION]
> **CRITICAL STABILITY RULE**: For hijacked browser sessions (`connectOverCDP`), **NEVER** use `page.reload()`. Reloading often breaks the viewer's internal state, leading to permanent loading indicators or redundant duplicate pages. Keep the current tab/session intact, wait generously for renders, and prefer the Apryse viewer API (`document.querySelector('apryse-webviewer').instance.Core.documentViewer.setCurrentPage(...)`) over keyboard-only navigation.

## Prerequisites
- **Remote Debugging**: Chromium must be launched with `--remote-debugging-port=9222`.
- **Active Session**: The ebook must be opened to the target starting page manually by the user.
- **Resume Safety**: When resuming from the middle of a book, verify the viewer API reports the correct total page count before capturing. For ID 1237, the total is 368 pages.

まず、対象ビューアーにログインした状態で電子テキストを開き、以下のスクリプトを実行してスクリーンショットを貯める。
※ `ArrowDown` を使用してページをめくります。

```bash
# 例: 1ページ目から50ページ目まで取得
node scripts/scraper.js --url "https://<ebook-viewer-domain>/html/..." --start 1 --end 50 --output "./ebook_raw"
```

途中再開や「bookId を開いてあるので同じようにPDF化して」のような依頼では、通常はこちらを優先する。

```bash
# 例: 1236 を開いてある状態で、そのまま Desktop に PDF を直接保存
<WORKSPACE_SCRIPTS>/export_pdf.sh 1236
```

Apryse から直接 PDF を抜けるときは、この方法を最優先にする。スクリーンショット方式は、直接抽出できない例外ケースだけで使う。

```bash
# 例: 1241 を開いてある状態で、1ページ目から最後まで raw/cropped/pdf を一気に作る
node <WORKSPACE_SCRIPTS>/make_pdf.js 1241 1
```

### 2. 精密クロップ
取得した画像を、電子テキスト専用のロジックでクロップする。
この工程で、右側のサイドバーや背景の灰色がすべて除去される。

```bash
python3 scripts/crop_pages.py --input "./ebook_raw" --output "./ebook_cropped"
```

### 3. PDF化
クロップ済みの画像を結合する。

```bash
python3 scripts/create_pdf.py --input "./ebook_cropped" --name "BookTitle_Ver1.0x.pdf"
```

## 🛡 Standard v1.2 (Latest Best Practice)

ID 1280 の抽出で確立された、ツールバーを排除しつつ完璧な位置情報を維持する現時点での最高設定です。

### 1. キャプチャ (Scraping)
- **No Reload**: セッションを維持したまま 20-30 秒単位でゆっくり待つ。ビューアーが重いときほど、リロードより待機の方が安全。
- **Primary Navigation**: `apryse-webviewer.instance.Core.documentViewer.setCurrentPage(pageNum)` を優先し、`PageDown` はフォールバックにする。
- **Preferred Entry Point**: `bookId` がユーザー依頼に含まれるときは `scripts/make_pdf.js` を優先する。これにより、正しいタブ選択・raw/cropped/pdf の一気通し・命名の標準化ができる。
- **Safe Focus**: キーボードフォールバックが必要なときだけ、**「ページ上部中央 (viewport.width/2, 100)」** を 1回クリックしてフォーカスを取得する。これによりツールバーを隠さずに操作できる。
- **Viewer Guard**: 取得前に `documentViewer.getCurrentPage()` / `getPageCount()` / `getZoomLevel()` をログし、スピナーが消えて renderable な canvas が出るまで待つ。
- **Fail Closed**: 開始ページへ安全にジャンプできない場合は続行せず停止する。誤ページ・ローディング画面の保存を避ける。
- **Shadow Guard**: 直前ページと同一ファイルサイズの場合、スタックと判断して viewer state を再確認してからリトライする。

### 2. 精密クロップ (Precision Cropping)
- **Top Crop (ct=98)**: 「閲覧・注釈・図形」の帯を完全に除去する magic number。
- **Fixed Bottom Alignment**: ページごとに高さを変えず、全ページの 32枚サンプルから得た **「統計的コンセンサス (Median)」** を全ページに一律適用する。これにより、PDFにした際の下端のブレを 1px レベルで解消する。
- **Margins**: 左右・下端に 3-5px のバッファを設け、影の残存を物理的に遮断する。

### 3. PDF組み立て (Assembly)
- `create_pdf.py` を使用し、全ての画像を RGB 変換して結合。DPI などの変数は基本デフォルトで最適化済み。

## 🛠 メンテナンス・注意点

- **輝度しきい値**: 背景が明るすぎる（影が薄い）場合は `crop_pages.py` の `threshold`（デフォルト240）を調整。
- **Magic Number**: テキストの種類（科目）によってツールバーの高さが変わる可能性があるため、初動の 10枚で `y=98` が適切か再検証を推奨。
- **Resume Workflow**: 途中ページからの再開は `RUNBOOK_resume_capture.md` の順番を崩さない。特に「viewer API で総ページ数確認」「reload禁止」「start page へ fail-closed で移動」を必須とする。

*作成日: 2026-04-06 / 作成者: Antigravity AI*
