# RUNBOOK: Audio Lab UI

## 目的
`PDF` / `EPUB` / `MOBI` をブラウザからアップロードして、`ebook2audiobook` の出力をその場で確認する。

## 起動

```bash
cd /path/to/WebTPDF-Mac
python3 30_UI/audiobook_ui.py
```

既定では `http://127.0.0.1:8765` で待ち受ける。

ネイティブ Mac アプリとして使う場合:

```bash
zsh 30_UI/mac_app/build_mac_app.sh
open "40_Ops/apps/EbookConverter Audio Lab.app"
```

SwiftUI アプリは WebView を使わず、`ebook-convert.js audiobook` を直接起動する。詳細は `RUNBOOK_mac_app.md` を参照する。

## 使い方

1. `電子書籍ファイル` に `PDF` か `EPUB` を入れる。
1. まずは `出力形式` を `mp3` にする。
1. `言語` は必要に応じて `jpn` / `eng` を切り替える。
1. `変換を開始` を押す。
1. 完了したら右側の `Artifact` の `download` か `audio` プレーヤーで確認する。

## 補足

- ブラウザ再生を優先するなら `mp3` が無難。
- `m4b` は audiobook 向けの保存には向いているが、ブラウザ再生互換は環境差がある。
- `docker` が見つからない場合は、システム状態に `Docker missing` と出る。
- `dry-run` を使うと、実行されるコマンドだけを確認できる。
