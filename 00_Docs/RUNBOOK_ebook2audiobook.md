# RUNBOOK: ebook2audiobook

## 目的
`ebook2audiobook` を EbookConverter から使える形で導入し、電子書籍を音声ファイルに変換する。

この runbook は headless 実行を前提にしている。GUI を使いたい場合は upstream の公式 README を参照する。
EbookConverter からは `node 20_Backend/bin/ebook-convert.js audiobook ...` を推奨入口にしている。必要に応じて `python3 20_Backend/scripts/run_ebook2audiobook.py ...` を直接呼んでもよい。

## 前提
- Docker がインストール済みであること
- 入力する電子書籍が non-DRM の合法入手済みファイルであること
- 変換したい本が `.epub` / `.mobi` / `.pdf` など upstream が対応する形式であること

## 既定の保存先
- 出力: `Company/05_Products/EbookConverter/40_Ops/output/audiobooks/`
- モデルキャッシュ: `~/.cache/ebookconverter/ebook2audiobook/models/`
- 音声サンプルキャッシュ: `~/.cache/ebookconverter/ebook2audiobook/voices/`
- 一時ディレクトリ: `~/.cache/ebookconverter/ebook2audiobook/tmp/`

## もっとも短い実行例

```bash
node 20_Backend/bin/ebook-convert.js audiobook \
  --ebook "/path/to/book.epub" \
  --language jpn \
  --output-format m4b
```

## 典型的な実行例

### 1. 日本語の本を標準音声で変換

```bash
node 20_Backend/bin/ebook-convert.js audiobook \
  --ebook "/path/to/book.epub" \
  --language jpn
```

### 2. 音声クローンを使う

```bash
node 20_Backend/bin/ebook-convert.js audiobook \
  --ebook "/path/to/book.epub" \
  --language jpn \
  --voice "/path/to/voice_sample.wav"
```

### 3. GPU イメージを使う

```bash
node 20_Backend/bin/ebook-convert.js audiobook \
  --ebook "/path/to/book.epub" \
  --language jpn \
  --image "athomasson2/ebook2audiobook:cu128" \
  --device cuda
```

## 補足
- EPUB は章構造の判定が揺れることがあるので、不要な前書きや付録は先に整理した方が結果が安定する。
- 出力形式は `m4b` を既定にしている。必要なら `--output-format mp3` などに変える。
- `--device` は `cpu` / `cuda` のように小文字で指定しても、upstream が受け付ける `CPU` / `CUDA` へ正規化される。
- `--device cuda` / `rocm` / `xpu` / `jetson` は Docker 側に必要な runtime/device 引数も自動で足す。MPS は Docker から露出しないため、この wrapper では使わない。
- GPU / ROCM / XPU / JETSON を使う場合は、`--device` と対応する `--image` を必ずセットで指定する。未指定だと CPU イメージとの不整合を避けるため停止する。
- 変換に失敗したときは、まず `--dry-run` で docker コマンドが正しいかを確認する。
- `docker` が PATH にない場合でも、Docker Desktop の標準パスは自動検出する。見つからない場合は、実行前に明示的なエラーで停止する。

## 事前確認

```bash
node 20_Backend/bin/ebook-convert.js audiobook --help
```
