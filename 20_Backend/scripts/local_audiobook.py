#!/usr/bin/env python3
"""Convert PDF / EPUB / TXT into mp3 or m4b audiobooks locally on macOS without Docker."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup

MODELS_DIR = Path.home() / ".cache" / "webtpdf" / "tts_models"

CATALOG_MODELS = [
    {
        "id": "macos:Kyoko",
        "name": "macOS標準: Kyoko (日本語・女性)",
        "engine": "say",
        "voice": "Kyoko",
        "language": "jpn",
        "languages": ["jpn"],
    },
    {
        "id": "macos:Otoya",
        "name": "macOS標準: Otoya (日本語・男性)",
        "engine": "say",
        "voice": "Otoya",
        "language": "jpn",
        "languages": ["jpn"],
    },
    {
        "id": "macos:Samantha",
        "name": "macOS標準: Samantha (英語)",
        "engine": "say",
        "voice": "Samantha",
        "language": "eng",
        "languages": ["eng"],
    },
    {
        "id": "edge:ja-JP-NanamiNeural",
        "name": "Neural: Nanami (日本語・女性・高音質)",
        "engine": "edge",
        "voice": "ja-JP-NanamiNeural",
        "language": "jpn",
        "languages": ["jpn"],
    },
    {
        "id": "edge:ja-JP-KeitaNeural",
        "name": "Neural: Keita (日本語・男性・高音質)",
        "engine": "edge",
        "voice": "ja-JP-KeitaNeural",
        "language": "jpn",
        "languages": ["jpn"],
    },
    {
        "id": "edge:en-US-JennyNeural",
        "name": "Neural: Jenny (英語・高音質)",
        "engine": "edge",
        "voice": "en-US-JennyNeural",
        "language": "eng",
        "languages": ["eng"],
    },
    {
        "id": "edge:en-US-AndrewMultilingualNeural",
        "name": "Neural: Andrew Multilingual (多言語・高音質)",
        "engine": "edge",
        "voice": "en-US-AndrewMultilingualNeural",
        "language": "eng",
        "languages": ["jpn", "eng", "zho", "kor", "fra", "deu", "spa"],
    },
    {
        "id": "openai:gpt-4o-mini-tts",
        "name": "API: OpenAI gpt-4o-mini-tts (自然・多言語)",
        "engine": "openai",
        "voice": "coral",
        "language": "jpn",
        "languages": ["jpn", "eng", "zho", "kor", "fra", "deu", "spa"],
    },
    {
        "id": "openai:tts-1",
        "name": "API: OpenAI tts-1 (高速・低コスト)",
        "engine": "openai",
        "voice": "nova",
        "language": "jpn",
        "languages": ["jpn", "eng", "zho", "kor", "fra", "deu", "spa"],
    },
    {
        "id": "elevenlabs:eleven_multilingual_v2",
        "name": "API: ElevenLabs Multilingual v2 (高表現力)",
        "engine": "elevenlabs",
        "voice": "21m00Tcm4TlvDq8ikWAM",
        "language": "jpn",
        "languages": ["jpn", "eng", "zho", "kor", "fra", "deu", "spa"],
    },
]


def log(message: str) -> None:
    """Print flushed progress output."""
    print(message, flush=True)


def is_edge_tts_installed() -> bool:
    """Return True if edge_tts Python package is importable."""
    try:
        __import__("edge_tts")
        return True
    except ImportError:
        return False


def installed_macos_voices() -> set[str]:
    """Return installed macOS `say` voice names."""
    try:
        out = subprocess.check_output(["/usr/bin/say", "-v", "?"], text=True, stderr=subprocess.DEVNULL)
        return {line.split()[0] for line in out.splitlines() if line.strip()}
    except Exception:
        return set()


def list_models_payload() -> list[dict[str, object]]:
    """Return all built-in, downloadable, and custom-downloaded TTS models."""
    edge_ok = is_edge_tts_installed()
    say_voices = installed_macos_voices()
    items: list[dict[str, object]] = []

    for entry in CATALOG_MODELS:
        if entry["engine"] == "say":
            installed = (entry["voice"] in say_voices) if say_voices else True
        elif entry["engine"] == "openai":
            installed = bool(os.environ.get("OPENAI_API_KEY", "").strip())
        elif entry["engine"] == "elevenlabs":
            installed = bool(os.environ.get("ELEVENLABS_API_KEY", "").strip())
        else:
            installed = edge_ok
        items.append({**entry, "installed": installed})

    if MODELS_DIR.is_dir():
        for path in sorted(MODELS_DIR.iterdir()):
            if path.name.startswith("."):
                continue
            items.append(
                {
                    "id": f"custom:{path.name}",
                    "name": f"カスタム: {path.name}",
                    "engine": "custom",
                    "voice": str(path),
                    "language": "jpn",
                    "languages": ["jpn", "eng"],
                    "installed": True,
                }
            )
    return items


def download_model(target: str) -> None:
    """Download/install a catalog model (edge-tts) or a custom model URL into MODELS_DIR."""
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    if target.startswith("edge:") or target == "edge-tts":
        log("Installing Neural TTS model runtime (edge-tts)...")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet", "edge-tts"],
            check=True,
        )
        log("Model ready: Neural TTS (edge-tts)")
        return

    parsed = urlparse(target.strip())
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        filename = Path(parsed.path).name or "custom_model.bin"
        dest = MODELS_DIR / filename
        log(f"Downloading model from {target} -> {dest}...")
        urllib.request.urlretrieve(target, dest)
        if dest.suffix.lower() == ".zip":
            extract_dir = MODELS_DIR / dest.stem
            extract_dir.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(dest, "r") as zf:
                zf.extractall(extract_dir)
            dest.unlink(missing_ok=True)
            log(f"Extracted custom model to {extract_dir}")
        else:
            log(f"Saved custom model to {dest}")
        return

    if target.startswith("macos:"):
        log(f"macOS built-in voice is already available: {target}")
        return

    raise RuntimeError(f"Unknown model target: {target}")


def extract_pdf_chapters(pdf_path: Path) -> list[tuple[str, str]]:
    """Extract text per page from a PDF file."""
    import fitz

    chapters: list[tuple[str, str]] = []
    with fitz.open(str(pdf_path)) as doc:
        for idx, page in enumerate(doc, start=1):
            text = (page.get_text() or "").strip()
            if text:
                chapters.append((f"Page {idx}", text))
    return chapters


def extract_epub_chapters(epub_path: Path) -> list[tuple[str, str]]:
    """Extract readable text per spine item from a non-DRM EPUB archive."""
    chapters: list[tuple[str, str]] = []
    with zipfile.ZipFile(epub_path, "r") as zf:
        container_xml = zf.read("META-INF/container.xml")
        container_root = ET.fromstring(container_xml)
        rootfile = container_root.find(".//{*}rootfile")
        if rootfile is None or "full-path" not in rootfile.attrib:
            raise RuntimeError("Invalid EPUB: missing rootfile in container.xml")

        opf_path = rootfile.attrib["full-path"]
        opf_dir = str(Path(opf_path).parent)
        opf_root = ET.fromstring(zf.read(opf_path))

        manifest: dict[str, str] = {}
        for item in opf_root.findall(".//{*}manifest/{*}item"):
            item_id = item.attrib.get("id")
            href = item.attrib.get("href")
            if item_id and href:
                manifest[item_id] = href

        spine_ids = [
            itemref.attrib["idref"]
            for itemref in opf_root.findall(".//{*}spine/{*}itemref")
            if "idref" in itemref.attrib
        ]

        for idx, idref in enumerate(spine_ids, start=1):
            href = manifest.get(idref)
            if not href:
                continue
            member_path = href if opf_dir in {"", "."} else f"{opf_dir}/{href}"
            if member_path not in zf.namelist():
                continue
            raw_html = zf.read(member_path)
            soup = BeautifulSoup(raw_html, "html.parser")
            for tag in soup(["script", "style"]):
                tag.decompose()
            text = "\n".join(line.strip() for line in soup.get_text("\n").splitlines() if line.strip())
            if text:
                chapters.append((f"Chapter {idx}", text))
    return chapters


def extract_chapters(ebook_path: Path) -> list[tuple[str, str]]:
    """Extract ordered text sections from PDF, EPUB, or plain text."""
    ext = ebook_path.suffix.lower()
    if ext == ".pdf":
        return extract_pdf_chapters(ebook_path)
    if ext == ".epub":
        return extract_epub_chapters(ebook_path)
    if ext in {".txt", ".md"}:
        content = ebook_path.read_text(encoding="utf-8", errors="replace").strip()
        return [(ebook_path.stem, content)] if content else []
    raise RuntimeError(f"Unsupported format for local TTS without Docker: {ext} (supported: .pdf, .epub, .txt, .md)")


def resolve_say_voice(language: str, requested_voice: str) -> str | None:
    """Pick a macOS `say` voice matching the language or explicit user request."""
    if requested_voice.strip():
        return requested_voice.strip()

    lang_lower = language.strip().lower()
    preferred = ["Kyoko", "Otoya"] if lang_lower.startswith(("jpn", "ja")) else ["Samantha", "Daniel"]
    installed = installed_macos_voices()
    for candidate in preferred:
        if candidate in installed:
            return candidate
    return None


def synthesize_say(text: str, out_aiff: Path, voice: str | None) -> None:
    """Synthesize text to an AIFF file using macOS native `say`."""
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", delete=False) as tf:
        tf.write(text)
        txt_path = Path(tf.name)

    try:
        cmd = ["/usr/bin/say", "-f", str(txt_path), "-o", str(out_aiff)]
        if voice:
            cmd.extend(["-v", voice])
        subprocess.run(cmd, check=True)
    finally:
        txt_path.unlink(missing_ok=True)


async def _synthesize_edge_async(text: str, out_mp3: Path, voice: str) -> None:
    import edge_tts

    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(str(out_mp3))


def synthesize_edge(text: str, out_mp3: Path, voice: str) -> None:
    """Synthesize text to an MP3 file using edge-tts."""
    if not is_edge_tts_installed():
        download_model(f"edge:{voice}")
    asyncio.run(_synthesize_edge_async(text, out_mp3, voice))


def split_text_chunks(text: str, max_chars: int = 3500) -> list[str]:
    """Split long text on sentence/newline boundaries for cloud TTS API limits."""
    cleaned = text.strip()
    if not cleaned:
        return []
    if len(cleaned) <= max_chars:
        return [cleaned]

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for para in cleaned.splitlines():
        line = para.strip()
        if not line:
            continue
        while len(line) > max_chars:
            if current:
                chunks.append("\n".join(current))
                current = []
                current_len = 0
            chunks.append(line[:max_chars])
            line = line[max_chars:]
        if current_len + len(line) + 1 > max_chars and current:
            chunks.append("\n".join(current))
            current = [line]
            current_len = len(line)
        else:
            current.append(line)
            current_len += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


def _concat_mp3_chunks(chunk_paths: list[Path], out_mp3: Path) -> None:
    """Concatenate one or more MP3 chunks into out_mp3."""
    if len(chunk_paths) == 1:
        chunk_paths[0].replace(out_mp3)
        return
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        with out_mp3.open("wb") as dst:
            for p in chunk_paths:
                dst.write(p.read_bytes())
        return
    with tempfile.TemporaryDirectory(prefix="api_tts_concat_") as tmp_dir:
        concat_list = Path(tmp_dir) / "inputs.txt"
        concat_list.write_text("\n".join(f"file '{p.as_posix()}'" for p in chunk_paths), encoding="utf-8")
        subprocess.run(
            [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list), "-c", "copy", str(out_mp3)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )


def synthesize_openai(text: str, out_mp3: Path, model_name: str, voice: str) -> None:
    """Synthesize text to MP3 via OpenAI Audio Speech API."""
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY が未設定です。「AI接続」画面から API キーを設定してください。")
    chosen_voice = os.environ.get("OPENAI_TTS_VOICE", "").strip() or voice or "coral"
    chunks = split_text_chunks(text, max_chars=3500)
    if not chunks:
        return

    with tempfile.TemporaryDirectory(prefix="openai_tts_") as tmp_dir:
        tmp_root = Path(tmp_dir)
        chunk_paths: list[Path] = []
        for idx, chunk in enumerate(chunks, start=1):
            payload = json.dumps({
                "model": model_name,
                "input": chunk,
                "voice": chosen_voice,
                "response_format": "mp3",
            }).encode("utf-8")
            req = urllib.request.Request(
                "https://api.openai.com/v1/audio/speech",
                data=payload,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=120) as resp:
                    part = tmp_root / f"chunk_{idx:04d}.mp3"
                    part.write_bytes(resp.read())
                    chunk_paths.append(part)
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:300]
                raise RuntimeError(f"OpenAI TTS API エラー ({exc.code}): {detail}") from exc
        _concat_mp3_chunks(chunk_paths, out_mp3)


def synthesize_elevenlabs(text: str, out_mp3: Path, model_id: str, default_voice_id: str) -> None:
    """Synthesize text to MP3 via ElevenLabs Text-to-Speech API."""
    api_key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ELEVENLABS_API_KEY が未設定です。「AI接続」画面から API キーを設定してください。")
    voice_id = os.environ.get("ELEVENLABS_VOICE_ID", "").strip() or default_voice_id or "21m00Tcm4TlvDq8ikWAM"
    chunks = split_text_chunks(text, max_chars=3800)
    if not chunks:
        return

    with tempfile.TemporaryDirectory(prefix="elevenlabs_tts_") as tmp_dir:
        tmp_root = Path(tmp_dir)
        chunk_paths: list[Path] = []
        for idx, chunk in enumerate(chunks, start=1):
            payload = json.dumps({
                "text": chunk,
                "model_id": model_id,
            }).encode("utf-8")
            req = urllib.request.Request(
                f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
                data=payload,
                headers={
                    "xi-api-key": api_key,
                    "Content-Type": "application/json",
                    "Accept": "audio/mpeg",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=120) as resp:
                    part = tmp_root / f"chunk_{idx:04d}.mp3"
                    part.write_bytes(resp.read())
                    chunk_paths.append(part)
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:300]
                raise RuntimeError(f"ElevenLabs API エラー ({exc.code}): {detail}") from exc
        _concat_mp3_chunks(chunk_paths, out_mp3)


def find_ffmpeg() -> str | None:
    """Locate ffmpeg binary if installed."""
    candidates = [
        str(Path.home() / ".local" / "bin" / "ffmpeg"),
        "/opt/homebrew/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
        shutil.which("ffmpeg"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def convert_audio(audio_files: list[Path], output_file: Path, output_format: str) -> None:
    """Concatenate synthesized audio segments into mp3 or m4b."""
    ffmpeg = find_ffmpeg()
    output_file.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="local_tts_concat_") as tmp_dir:
        tmp_root = Path(tmp_dir)
        if ffmpeg:
            concat_list = tmp_root / "inputs.txt"
            concat_list.write_text(
                "\n".join(f"file '{p.as_posix()}'" for p in audio_files),
                encoding="utf-8",
            )
            if output_format == "mp3":
                cmd = [
                    ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
                    "-vn", "-c:a", "libmp3lame", "-q:a", "4", str(output_file),
                ]
            else:
                cmd = [
                    ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
                    "-vn", "-c:a", "aac", "-b:a", "128k", "-f", "mp4", str(output_file),
                ]
            subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            return

        if output_format == "m4b" and len(audio_files) == 1 and audio_files[0].suffix == ".aiff":
            subprocess.run(
                ["/usr/bin/afconvert", "-f", "m4af", "-d", "aac", str(audio_files[0]), str(output_file)],
                check=True,
            )
            return

        raise RuntimeError("ffmpeg not found. Install ffmpeg (brew install ffmpeg) or use m4b.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Local macOS audiobook converter without Docker.")
    parser.add_argument("--list-models", action="store_true", help="Print available/installed models as JSON")
    parser.add_argument("--download-model", default="", help="Download/install a model ID or URL")
    parser.add_argument("--ebook", default="", help="Input PDF/EPUB/TXT path")
    parser.add_argument("--output-dir", default="", help="Output directory")
    parser.add_argument("--language", default="jpn", help="Language code (default: jpn)")
    parser.add_argument("--output-format", choices=["mp3", "m4b"], default="mp3")
    parser.add_argument("--model", default="", help="Model ID (e.g. macos:Kyoko, edge:ja-JP-NanamiNeural)")
    parser.add_argument("--voice", default="", help="Optional macOS `say` voice name")
    args = parser.parse_args()

    if args.list_models:
        print(json.dumps(list_models_payload(), ensure_ascii=False), flush=True)
        return 0

    if args.download_model:
        try:
            download_model(args.download_model)
            return 0
        except Exception as exc:
            print(f"Error: {exc}", file=sys.stderr, flush=True)
            return 1

    if not args.ebook or not args.output_dir:
        print("Error: --ebook and --output-dir are required.", file=sys.stderr, flush=True)
        return 1

    ebook_path = Path(args.ebook).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    if not ebook_path.is_file():
        print(f"Error: Ebook not found: {ebook_path}", file=sys.stderr, flush=True)
        return 1

    chapters = extract_chapters(ebook_path)
    if not chapters:
        print(
            "Error: PDF/EPUBからテキストを抽出できませんでした（スキャン画像のみのPDFはDocker版またはOCRが必要です）。",
            file=sys.stderr,
            flush=True,
        )
        return 1

    selected_model = args.model.strip()
    use_edge = selected_model.startswith("edge:")
    use_openai = selected_model.startswith("openai:")
    use_elevenlabs = selected_model.startswith("elevenlabs:")
    edge_voice = selected_model.split(":", 1)[1] if use_edge else ""
    openai_model = selected_model.split(":", 1)[1] if use_openai else ""
    eleven_model = selected_model.split(":", 1)[1] if use_elevenlabs else ""
    say_voice_name = selected_model.split(":", 1)[1] if selected_model.startswith("macos:") else args.voice
    say_voice = None if (use_edge or use_openai or use_elevenlabs) else resolve_say_voice(args.language, say_voice_name)

    if use_openai:
        engine_label = f"OpenAI TTS API ({openai_model})"
    elif use_elevenlabs:
        engine_label = f"ElevenLabs API ({eleven_model})"
    elif use_edge:
        engine_label = f"Neural edge-tts ({edge_voice})"
    else:
        engine_label = f"macOS say ({say_voice or 'default'})"
    log(f"Audio Lab TTS: {len(chapters)} section(s), model={engine_label}")

    with tempfile.TemporaryDirectory(prefix="local_tts_parts_") as tmp_dir:
        tmp_root = Path(tmp_dir)
        audio_files: list[Path] = []
        total = len(chapters)

        for idx, (title, text) in enumerate(chapters, start=1):
            pct = (idx - 1) / total * 100.0
            log(f"{pct:.2f}%: {idx - 1}/{total} Synthesizing {title}...")
            if use_openai:
                part_file = tmp_root / f"part_{idx:04d}.mp3"
                synthesize_openai(text, part_file, openai_model, os.environ.get("OPENAI_TTS_VOICE", "coral"))
            elif use_elevenlabs:
                part_file = tmp_root / f"part_{idx:04d}.mp3"
                synthesize_elevenlabs(text, part_file, eleven_model, "21m00Tcm4TlvDq8ikWAM")
            elif use_edge:
                part_file = tmp_root / f"part_{idx:04d}.mp3"
                synthesize_edge(text, part_file, edge_voice)
            else:
                part_file = tmp_root / f"part_{idx:04d}.aiff"
                synthesize_say(text, part_file, say_voice)
            audio_files.append(part_file)

        out_path = output_dir / f"{ebook_path.stem}.{args.output_format}"
        log(f"Encoding {out_path.name}...")
        convert_audio(audio_files, out_path, args.output_format)
        log(f"100.00%: {total}/{total} Completed -> {out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
