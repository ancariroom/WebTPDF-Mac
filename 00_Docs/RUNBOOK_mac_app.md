# RUNBOOK: Mac App

## 目的
`EbookConverter Audio Lab.app` から PDF / EPUB を選び、ネイティブ SwiftUI の画面で audiobook 変換を実行する。

## ビルド

```bash
cd /Users/naoki/Git/Company/05_Products/EbookConverter
zsh 30_UI/mac_app/build_mac_app.sh
```

出力先:

```text
40_Ops/apps/EbookConverter Audio Lab.app
```

## 起動

```bash
open "40_Ops/apps/EbookConverter Audio Lab.app"
```

アプリは Docker Desktop の `docker run` を直接起動し、ログ、出力先、生成された音声ファイルの再生を画面内で扱う。配布先のMacに Node / Python は不要。

出力先:

```text
~/Library/Application Support/EbookConverter Audio Lab/output/audiobooks/native_jobs/
```

## 配布パッケージ作成

```bash
cd /Users/naoki/Git/Company/05_Products/EbookConverter
zsh 30_UI/mac_app/package_mac_app.sh
```

出力先:

```text
40_Ops/dist/EbookConverter-Audio-Lab-0.1.0.dmg
40_Ops/dist/EbookConverter-Audio-Lab-0.1.0.zip
40_Ops/dist/SHA256SUMS.txt
```

`.dmg` にはアプリ本体、`Applications` ショートカット、簡易READMEが入る。

## 署名 / notarize

デフォルトは ad-hoc 署名なので、身近な人へ渡して試す用途向け。macOS のGatekeeperで止まる場合は、受け取った側で右クリックして `開く` を選ぶ。

Developer ID 署名を使う場合:

```bash
CODESIGN_IDENTITY="Developer ID Application: Example Name (TEAMID)" \
  zsh 30_UI/mac_app/package_mac_app.sh
```

notarytool の keychain profile を設定済みの場合:

```bash
CODESIGN_IDENTITY="Developer ID Application: Example Name (TEAMID)" \
NOTARY_PROFILE="notarytool-profile-name" \
  zsh 30_UI/mac_app/package_mac_app.sh
```

## 進捗表示

- `Elapsed`: 変換開始からの経過時間。
- `Progress`: 音声生成本体の `current/total` 形式を拾えた場合に表示する。PDF / EPUB 前処理中の一時的な `100%` は全体進捗として扱わない。
- `Remaining`: 音声生成本体の進捗が取れた場合だけ推定残り時間を表示する。進捗が取れない間は `推定中`。

## 短い範囲だけ試す

PDF入力では `短い範囲だけ試す（PDF先頭ページ）` をONにすると、元PDFを変更せず、出力フォルダ内に先頭Nページだけの一時PDFを作って変換する。

- デフォルトは先頭8ページ。
- EPUB / MOBI / AZW3 ではこの機能は使わない。
- 長いPDFで声質や読み上げ品質だけを確認したいときに使う。

## 前提

- 配布版の実行には Docker Desktop が必要。
- Docker Desktop がインストール済みだが起動していない場合、アプリは変換開始時に Docker Desktop を開いて ready になるまで最大約3分待つ。
- Docker がない場合でも画面起動と `dry-run` は確認できる。
- PDF / EPUB は non-DRM の合法入手済みファイルだけを対象にする。
